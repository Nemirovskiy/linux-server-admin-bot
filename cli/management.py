"""Shared operations for both command-line and interactive use."""

from __future__ import annotations

import asyncio
import fcntl
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

from bot.plugins.config import ConfigRevision, PluginConfig
from bot.plugins.manager import PluginManager
from bot.plugins.models import PluginRecord
from bot.plugins.sources import installed_sources
from bot.runtime_status import config_digest, read_status, state_directory
from bot.storage import atomic_write
from cli.services import ServiceController, ServiceError

_ACTIVE = {"active", "running"}


class PluginManagement:
    def __init__(self, config: PluginConfig, *, ready_timeout: float = 45) -> None:
        self.config = config
        self.directory = state_directory(config.path)
        self.ready_timeout = ready_timeout

    def manager(
        self, configuration: str | None = None, *, diagnostic: bool = False
    ) -> PluginManager:
        factory = None
        if diagnostic:
            from bot.plugins.context import default_context_factory

            factory = default_context_factory(self.config.path, create_data=False)
        manager = PluginManager(installed_sources(), self.config, context_factory=factory)
        manager.discover(sync=False, configuration=configuration)
        return manager

    def snapshot(self) -> ConfigRevision:
        manager = self.manager()
        return self.config.preview(manager.defaults)

    def plugins(self, runtime: dict | None = None) -> list[dict]:
        manager = self.manager()
        return [
            *(self.describe(record, runtime) for record in manager.list_plugins()),
            *(self.describe(record) for record in manager.conflicts),
        ]

    @staticmethod
    def describe(record: PluginRecord, runtime: dict | None = None) -> dict:
        actual = runtime["plugins"].get(record.plugin_id) if runtime else None
        if actual is None:
            execution = "unknown"
        elif record.enabled != actual["enabled"]:
            execution = "pending restart"
        elif not actual["enabled"]:
            execution = "disabled"
        elif actual["has_issue"] and actual["state"] == "running":
            execution = actual["health"]
        else:
            execution = actual["state"]
        return {
            "id": record.plugin_id,
            "source": record.source,
            "enabled": record.enabled,
            "execution": execution,
            "runtime": actual,
            "metadata": asdict(record.metadata) if record.metadata else None,
            "entry_point": record.descriptor.entry_point if record.descriptor else None,
            "error": record.error,
            "dependency_issue": record.dependency_issue,
            "state": record.state.name.lower(),
            "health": record.health.name.lower(),
            "distribution": asdict(record.distribution) if record.distribution else None,
            "required_permissions": (
                record.descriptor.required_permissions if record.descriptor else ()
            ),
            "optional_permissions": (
                record.descriptor.optional_permissions if record.descriptor else ()
            ),
        }

    def change(
        self,
        updates: Mapping[str, bool],
        *,
        dry_run: bool = False,
        expected: ConfigRevision | None = None,
        controller: ServiceController | None = None,
    ) -> dict:
        if dry_run:
            return self._change(updates, dry_run=True, expected=expected)
        with self._lock():
            if controller:
                controller.ensure_target(self.config.path)
            result = self._change(updates, dry_run=False, expected=expected)
            if controller:
                result["runtime"] = self._apply(controller)
                result["applied"] = True
            return result

    def _change(
        self, updates: Mapping[str, bool], *, dry_run: bool, expected: ConfigRevision | None
    ) -> dict:
        manager = self.manager()
        for name in updates:
            manager.get_plugin(name)
        if not dry_run:
            if not (self.directory / "blocks.last-good.toml").exists():
                baseline = self._running_snapshot(read_status(self.directory))
                if baseline is not None:
                    atomic_write(self.directory / "blocks.last-good.toml", baseline)
            self.config.update_switches(updates, manager.defaults, preview=expected)
        changes = {
            name: {"before": manager.switches.get(name, False), "after": enabled}
            for name, enabled in updates.items()
            if manager.switches.get(name, False) != enabled
        }
        return {"dry_run": dry_run, "switches": dict(updates), "changes": changes, "applied": False}

    def diagnose(
        self, plugin_id: str | None = None, *, configuration: str | None = None
    ) -> list[dict]:
        async def run() -> list[dict]:
            manager = self.manager(configuration, diagnostic=True)
            if plugin_id is None:
                await manager.load_enabled()
                await manager.check_enabled()
                records = manager.startup_records()
            else:
                records = (await manager.diagnose(plugin_id),)
            return [
                {
                    "id": record.plugin_id,
                    "enabled": record.enabled,
                    "state": record.state.name.lower(),
                    "health": record.health.name.lower(),
                    "error": record.error,
                    "dependency_issue": record.dependency_issue,
                    "ok": not (record.error or record.dependency_issue),
                }
                for record in records
            ]

        return asyncio.run(run())

    def status(self, controller: ServiceController) -> dict:
        service = controller.status()
        runtime = read_status(self.directory)
        pid = service.get("pid")
        if runtime and pid and pid != runtime["pid"]:
            runtime = None
        revision = self.snapshot()
        return {
            "service": service,
            "runtime": runtime,
            "pending_restart": bool(
                runtime and runtime["config_hash"] != config_digest(revision.text)
            ),
            "plugins": self.plugins(runtime),
        }

    def apply(self, controller: ServiceController) -> dict:
        with self._lock():
            controller.ensure_target(self.config.path)
            return self._apply(controller)

    def rollback(self, controller: ServiceController | None = None) -> dict:
        with self._lock():
            if controller:
                controller.ensure_target(self.config.path)
            current = self.config.snapshot()
            running_path = self.directory / "running.toml"
            running = self._running_snapshot(read_status(self.directory))
            if running is not None and running != current:
                self.config.replace(running, current)
                path = running_path
            else:
                candidates = (self.directory / "blocks.last-good.toml", *self._history())
                for path in candidates:
                    previous = self._read_backup(path)
                    if previous is not None and previous != current:
                        self.config.replace(previous, current)
                        break
                else:
                    raise ValueError("No previous verified configuration to restore")
            result = {"restored": str(path), "applied": False}
            if controller:
                result["runtime"] = self._apply(controller)
                result["applied"] = True
            return result

    def _apply(self, controller: ServiceController) -> dict:
        revision = self.snapshot()
        failures = [row for row in self.diagnose(configuration=revision.text) if not row["ok"]]
        if failures:
            details = "; ".join(
                f"{row['id']}: {row['error'] or row['dependency_issue']}" for row in failures
            )
            raise ValueError(f"Configuration check failed. Service was not restarted. {details}")
        service = controller.status()
        before = read_status(self.directory)
        prior = self._previous_running(before, service)
        if service["state"] in _ACTIVE and prior is None:
            raise ServiceError(
                "The active service has no verified configuration to recover. Restart it once with this version before using apply."
            )
        if self.config.snapshot() != revision.original:
            raise ValueError("Blocks configuration changed during validation. Retry.")
        if prior is not None:
            atomic_write(self.directory / "blocks.last-good.toml", prior)
        if revision.text != revision.original:
            self.config.replace(revision.text, revision.original)

        previous_run = before["run_id"] if before else None
        try:
            controller.restart()
            expected_plugins = {
                record.plugin_id for record in self.manager(revision.text).startup_records()
            }
            ready = self._wait_ready(controller, revision.text, previous_run, expected_plugins)
        except (OSError, ValueError, ServiceError, KeyboardInterrupt) as exc:
            self._recover(controller, revision, prior, previous_run, exc)

        if prior is not None and prior != revision.text:
            self._archive(prior)
        atomic_write(self.directory / "blocks.last-good.toml", revision.text)
        return {"config_hash": ready["config_hash"], "pid": ready["pid"]}

    def _recover(
        self,
        controller: ServiceController,
        revision: ConfigRevision,
        prior: str | None,
        previous_run: str | None,
        failure: BaseException,
    ) -> Never:
        try:
            if self.config.snapshot() != revision.text:
                raise ValueError(
                    "Configuration changed externally; its current contents were preserved"
                )
            if prior is None:
                controller.stop()
            else:
                failed_status = read_status(self.directory)
                failed_run = failed_status["run_id"] if failed_status else previous_run
                self.config.replace(prior, revision.text)
                controller.restart()
                self._wait_ready(controller, prior, failed_run, set())
                atomic_write(self.directory / "blocks.last-good.toml", prior)
        except (OSError, ValueError, ServiceError, KeyboardInterrupt) as exc:
            raise ServiceError(f"Apply failed: {failure}. Recovery failed: {exc}") from exc
        outcome = (
            "Previous configuration restored and service is ready"
            if prior is not None
            else "Service stopped; no previous verified configuration"
        )
        raise ServiceError(f"Apply failed: {failure}. {outcome}") from failure

    def _wait_ready(
        self,
        controller: ServiceController,
        text: str,
        previous_run: str | None,
        expected_plugins: set[str],
    ) -> dict:
        deadline = time.monotonic() + self.ready_timeout
        digest = config_digest(text)
        while time.monotonic() < deadline:
            runtime = read_status(self.directory)
            if runtime and runtime["run_id"] != previous_run and runtime["config_hash"] == digest:
                service = controller.status()
                if service["state"] in _ACTIVE and (
                    not service.get("pid") or service["pid"] == runtime["pid"]
                ):
                    plugins = runtime["plugins"]
                    failed = sorted(
                        name
                        for name in expected_plugins
                        if plugins.get(name, {}).get("state") != "running"
                        or plugins[name].get("health") != "healthy"
                        or plugins[name].get("has_issue")
                    )
                    if failed:
                        raise ServiceError(f"Blocks failed to become ready: {', '.join(failed)}")
                    return runtime
            time.sleep(0.25)
        raise ServiceError("Timed out waiting for a fresh application readiness report")

    def _previous_running(self, runtime: dict | None, service: dict) -> str | None:
        if (
            runtime
            and service["state"] in _ACTIVE
            and (not service.get("pid") or service["pid"] == runtime["pid"])
        ):
            running = self._running_snapshot(runtime)
            if running is not None:
                return running
        return self._read_backup(self.directory / "blocks.last-good.toml")

    def _running_snapshot(self, runtime: dict | None) -> str | None:
        if runtime is None:
            return None
        running = self._read_backup(self.directory / "running.toml")
        if running is not None and config_digest(running) == runtime["config_hash"]:
            return running
        return None

    def _read_backup(self, path: Path) -> str | None:
        try:
            text = path.read_bytes().decode("utf-8")
            self.config.validate(text)
            return text
        except FileNotFoundError:
            return None

    def _history(self) -> list[Path]:
        return sorted(self.directory.glob("blocks-*.toml"), reverse=True)

    def _archive(self, text: str) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        atomic_write(self.directory / f"blocks-{stamp}.toml", text)
        for old in self._history()[5:]:
            old.unlink()

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (self.directory / "operation.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
