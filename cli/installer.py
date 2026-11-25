"""Prepare plugin environments, activate validated candidates, and recover failures."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from bot.plugins.config import ConfigRevision
from bot.runtime_status import config_digest, read_status
from bot.storage import atomic_write
from cli.installer_sources import (
    Source,
    command,
    fetch,
    inspect_wheel,
    newest_git_ref,
    pip,
    resolve,
    sha256,
)
from cli.management import PluginManagement
from cli.plugin_permissions import revoke
from cli.services import ServiceController, ServiceError
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

PROTECTED = {
    "pip",
    "setuptools",
    "wheel",
    "lsab-api",
    "linux-server-admin-bot",
    "python-telegram-bot",
}


class Installer:
    def __init__(self, project: Path, management: PluginManagement, controller: ServiceController):
        self.project = project.resolve()
        self.management = management
        self.config = management.config
        self.controller = controller
        self.root = self.project / "runtime"
        self.environments = self.root / "environments"
        self.transactions = self.root / "transactions"
        self.cache = self.root / "cache"

    @property
    def active(self) -> Path:
        return self.pointer("current") or Path(sys.prefix).resolve()

    def pointer(self, name: str) -> Path | None:
        path = self.root / name
        if not path.is_symlink():
            if path.exists():
                raise ValueError(f"Expected a symlink: {path}")
            return None
        target = path.resolve()
        if not (target / "pyvenv.cfg").is_file():
            raise ValueError(f"Managed environment is missing: {target}")
        return target

    def _guard(self) -> None:
        if os.geteuid() == 0 or sys.prefix == sys.base_prefix:
            raise ValueError("Run the plugin installer as the bot user in its virtual environment")
        if self.controller.kind == "docker" or os.environ.get("LSAB_INSTALLER_CONTAINER") == "1":
            raise ValueError(
                "For Docker, export the lock from a native environment and rebuild the image"
            )
        if not (self.project / "main.py").is_file():
            raise ValueError("Select the bot checkout with --project-dir")

    @contextmanager
    def lock(self) -> Iterator[None]:
        self._guard()
        self.root.mkdir(mode=0o700, exist_ok=True)
        with (self.root / "installer.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Another plugin installation is in progress") from exc
            with self.management._lock():
                yield

    def _inventory(self, env: Path, text: str | None = None, *, check: bool = False) -> dict:
        with tempfile.TemporaryDirectory(prefix="lsab-probe-") as directory:
            output = Path(directory) / "result.json"
            request = {
                "config": str(self.config.path),
                "template": str(self.config.template) if self.config.template else None,
                "configuration": text,
                "check": check,
                "output": str(output),
            }
            command(
                [str(env / "bin/python"), "-m", "cli.installer_probe"],
                input_text=json.dumps(request),
            )
            return json.loads(output.read_text())

    @staticmethod
    def _records(env: Path) -> dict:
        path = env / "lsab-installed-plugins.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    @staticmethod
    def _installed(inventory: dict) -> dict:
        rows = [row for row in inventory["catalog"] if row["source"] == "external"]
        by_id = {}
        duplicates = set()
        for row in rows:
            if row["id"] in by_id:
                duplicates.add(row["id"])
            by_id[row["id"]] = row
        return {key: row for key, row in by_id.items() if key not in duplicates}

    @staticmethod
    def _single_block(inventory: dict, plugin_id: str, distribution: str) -> None:
        if any(
            row["source"] == "external"
            and row["id"] != plugin_id
            and row["distribution"]
            and canonicalize_name(row["distribution"]) == distribution
            for row in inventory["catalog"]
        ):
            raise ValueError(
                "Distribution provides multiple blocks; manage them as one package outside this installer"
            )

    def source_for(
        self,
        plugin_id: str,
        version: str | None = None,
        ref: str | None = None,
        *,
        pre: bool = False,
    ) -> Source:
        installed = self._installed(self._inventory(self.active))
        if plugin_id not in installed:
            raise ValueError(f"Unknown external plugin: {plugin_id}")
        record = self._records(self.active).get(plugin_id)
        source = Source(**record["source"]) if record else None
        if source is None:
            origin = installed[plugin_id]["origin"]
            if origin:
                raise ValueError(
                    "Use update --source with a new artifact for this unmanaged direct installation"
                )
            source = Source("index", installed[plugin_id]["distribution"])
        if source.kind == "index":
            name = Requirement(source.value).name
            return resolve(f"{name}=={Version(version)}" if version else name)
        if source.kind == "git":
            return resolve(
                ("git+" if source.value.startswith("file:") else "") + source.value,
                ref or version or newest_git_ref(source, pre=pre),
            )
        if source.kind == "directory" and not version and not ref:
            return source
        raise ValueError("This source needs a new wheel or release URL via update --source")

    def prepare(
        self,
        source: Source,
        work: Path,
        *,
        operation: str,
        plugin_id: str | None = None,
        expected_hash: str | None = None,
        pre: bool = False,
        preflight: bool = True,
    ) -> tuple[dict, Path, dict]:
        base = self.active
        inventory = self._inventory(base)
        wheel, source = fetch(base / "bin/python", source, work, pre=pre)
        record = inspect_wheel(wheel, source)
        if expected_hash and expected_hash.lower() != record["sha256"]:
            raise ValueError("Wheel SHA256 does not match the expected digest")
        name, key = record["distribution"], record["id"]
        installed = self._installed(inventory)
        old = installed.get(key)
        if any(row["id"] == key and row["source"] == "builtin" for row in inventory["catalog"]):
            raise ValueError(f"Plugin ID is reserved: {key}")
        if operation == "install" and any(
            row["source"] == "external" and row["id"] == key for row in inventory["catalog"]
        ):
            raise ValueError(f"{key} is already installed; use update")
        if operation != "install" and (
            key != plugin_id or old is None or canonicalize_name(old["distribution"]) != name
        ):
            raise ValueError("Replacement must keep the installed plugin ID and distribution")
        if old:
            self._single_block(inventory, key, name)
        if name in PROTECTED or (name in inventory["packages"] and not old):
            raise ValueError(f"Protected or unrelated distribution: {name}")

        constraints = work / "constraints.txt"
        self._constraints(inventory, constraints, name)
        changes = []
        if preflight:
            report = work / "pip-report.json"
            pip(
                base / "bin/python",
                "install",
                "--dry-run",
                "--report",
                str(report),
                "-c",
                str(constraints),
                "--upgrade",
                str(wheel),
            )
            changes = [
                {"name": item["metadata"]["name"], "version": item["metadata"]["version"]}
                for item in json.loads(report.read_text()).get("install", [])
            ]
        previous = set((old or {}).get("required", []) + (old or {}).get("optional", []))
        plan = {
            **record,
            "operation": operation,
            "base": str(base),
            "previous_version": old["version"] if old else None,
            "new_permissions": sorted(set(record["required"] + record["optional"]) - previous),
            "schema_changed": bool(old and old["schema"] != record["schema"]),
            "service_backend": self.controller.kind,
            "packages": changes,
        }
        return plan, wheel, inventory

    @staticmethod
    def _constraints(inventory: dict, path: Path, mutable: str = "") -> None:
        path.write_text(
            "".join(
                f"{name}=={version}\n"
                for name, version in sorted(inventory["packages"].items())
                if name != mutable
            ),
            encoding="utf-8",
        )

    def apply(
        self,
        plan: dict,
        wheel: Path,
        inventory: dict,
        *,
        allow_migration: bool = False,
        approve_permissions: Callable[[dict], str] | None = None,
    ) -> dict:
        key = plan["id"]
        if plan["schema_changed"] and not allow_migration:
            raise ValueError("Data schema changes require --allow-migration and a backup")
        with self.lock():
            self._assert_idle()
            base = self.active
            if (
                str(base) != plan["base"]
                or self._inventory(base)["packages"] != inventory["packages"]
            ):
                raise ValueError("Installed packages changed since the preview; plan again")
            txn = self._transaction(plan["operation"], key, base)
            txn.update(
                previous_version=plan["previous_version"],
                target_version=plan["version"],
                new_permissions=plan["new_permissions"],
                source=plan["source"],
            )
            try:
                candidate = self._clone(base, txn)
                constraints = Path(txn["directory"]) / "constraints.txt"
                self._constraints(inventory, constraints, plan["distribution"])
                with constraints.open("a") as stream:
                    for package in plan["packages"]:
                        if canonicalize_name(package["name"]) != plan["distribution"]:
                            stream.write(f"{package['name']}=={package['version']}\n")
                cached = self._remember(wheel, plan["sha256"])
                pip(
                    candidate / "bin/python",
                    "install",
                    "--upgrade",
                    "-c",
                    str(constraints),
                    str(cached),
                )
                if plan["source"]["editable"]:
                    pip(
                        candidate / "bin/python",
                        "install",
                        "--force-reinstall",
                        "--no-deps",
                        "-e",
                        plan["source"]["value"],
                    )
                elif inventory["packages"].get(plan["distribution"]) == plan["version"]:
                    pip(
                        candidate / "bin/python",
                        "install",
                        "--force-reinstall",
                        "--no-deps",
                        str(cached),
                    )
                records = self._records(base)
                previous = records.get(key, {})
                records[key] = {**plan, "installed_at": _now()}
                _write(candidate / "lsab-installed-plugins.json", records)
                txn["records_before"] = self._records(base)
                txn["records_after"] = records
                uses_broker = plan.get("broker_operations") or previous.get("broker_operations")
                switches = {key: False} if plan["operation"] == "install" or uses_broker else {}
                revision = self._revision(candidate, switches)
                checked = self._validate(candidate, revision, inventory, plan["distribution"])
                row = self._installed(checked).get(key)
                if not row or row["state"] == "failed" or row["version"] != plan["version"]:
                    raise ValueError(f"Candidate plugin failed discovery: {row}")
                self._activate(
                    txn,
                    candidate,
                    revision,
                    checked,
                    backup=key if plan["schema_changed"] else None,
                    revoke_plugins=(key,) if previous.get("broker_operations") else (),
                )
            except BaseException:
                self._failed(txn)
                raise
            self._prune()
            return self._installation_result(txn, plan, approve_permissions)

    def _installation_result(
        self, txn: dict, plan: dict, approve_permissions: Callable[[dict], str] | None
    ) -> dict:
        result = self._public(txn)
        if plan.get("broker_operations"):
            result["broker_approval"] = (
                approve_permissions(plan) if approve_permissions else "approval_required"
            )
        return result

    def remove(self, plugin_id: str, *, dry_run: bool = False, purge: bool = False) -> dict:
        inventory = self._inventory(self.active)
        old = self._installed(inventory).get(plugin_id)
        if not old:
            raise ValueError(f"Unknown external plugin: {plugin_id}")
        name = canonicalize_name(old["distribution"])
        managed = self._records(self.active).get(plugin_id, {})
        self._single_block(inventory, plugin_id, name)
        plan = {
            "operation": "remove",
            "id": plugin_id,
            "distribution": name,
            "previous_version": old["version"],
            "purge": purge,
            "permissions": old["required"] + old["optional"],
            "revoke_broker_permissions": bool(managed.get("broker_operations")),
        }
        if dry_run:
            return plan
        if name in PROTECTED:
            raise ValueError(f"Protected distribution: {name}")
        with self.lock():
            self._assert_idle()
            base = self.active
            if self._inventory(base)["packages"] != inventory["packages"]:
                raise ValueError("Installed packages changed since the preview; plan again")
            txn = self._transaction("remove", plugin_id, base)
            txn.update(previous_version=old["version"], target_version=None, purge=purge)
            try:
                candidate = self._clone(base, txn)
                pip(candidate / "bin/python", "uninstall", "-y", name)
                records = self._records(base)
                txn["records_before"] = records.copy()
                records.pop(plugin_id, None)
                txn["records_after"] = records
                _write(candidate / "lsab-installed-plugins.json", records)
                revision = self._revision(candidate, {plugin_id: False})
                checked = self._validate(candidate, revision, inventory, name)
                revoked = bool(txn["records_before"].get(plugin_id, {}).get("broker_operations"))
                self._activate(
                    txn,
                    candidate,
                    revision,
                    checked,
                    backup=plugin_id if purge else None,
                    purge=purge,
                    revoke_plugins=(plugin_id,) if revoked else (),
                )
            except BaseException:
                self._failed(txn)
                raise
            self._prune()
            return self._public(txn)

    def rollback(self) -> dict:
        with self.lock():
            self._assert_idle()
            base = self.active
            previous = self.pointer("previous")
            if previous is None or previous == base:
                raise ValueError("No previous environment to restore")
            now, then = self._installed(self._inventory(base)), self._installed(
                self._inventory(previous)
            )
            for key in now.keys() & then.keys():
                if now[key]["schema"] != then[key]["schema"]:
                    raise ValueError(
                        "Data schema differs; restore the documented data backup before rolling back"
                    )
            prior_records = self._records(previous)
            current_records = self._records(base)
            changed_rights = {
                key
                for key, record in (prior_records | current_records).items()
                if prior_records.get(key, {}).get("sha256")
                != current_records.get(key, {}).get("sha256")
                and (
                    record.get("broker_operations")
                    or prior_records.get(key, {}).get("broker_operations")
                )
            }
            revision = self._revision(
                previous, dict.fromkeys((now.keys() - then.keys()) | changed_rights, False)
            )
            checked = self._validate(previous, revision)
            txn = self._transaction("rollback", None, base)
            try:
                self._activate(
                    txn, previous, revision, checked,
                    revoke_plugins=tuple(sorted(changed_rights & current_records.keys())),
                )
            except BaseException:
                self._failed(txn)
                raise
            return self._public(txn)

    def prior_artifact(self, plugin_id: str) -> dict:
        for txn in self._read_history():
            old = txn.get("records_before", {}).get(plugin_id)
            current = self._records(self.active).get(plugin_id)
            if txn["status"] == "completed" and old and old != current:
                return old
        raise ValueError("No previous cached version for this plugin")

    def _clone(self, base: Path, txn: dict) -> Path:
        self.environments.mkdir(mode=0o700, parents=True, exist_ok=True)
        candidate = self.environments / f"env-{txn['transaction_id']}"
        txn["candidate"] = str(candidate)
        self._save(txn, "preparing")
        paths = "import sysconfig,json; print(json.dumps([sysconfig.get_path(k) for k in ('purelib','platlib')]))"
        sources = [
            Path(path).resolve()
            for path in json.loads(command([str(base / "bin/python"), "-c", paths]))
        ]
        size = sum(
            file.stat().st_size
            for directory in set(sources)
            for file in directory.rglob("*")
            if file.is_file() and not file.is_symlink()
        )
        if shutil.disk_usage(self.environments).free < size + max(size // 4, 64 * 1024**2):
            raise ValueError("Insufficient free disk space for a replacement environment")
        command([str(base / "bin/python"), "-m", "venv", "--without-pip", str(candidate)])
        targets = json.loads(command([str(candidate / "bin/python"), "-c", paths]))
        copied = set()
        for src, dst in zip(sources, targets, strict=True):
            key = src, Path(dst).resolve()
            if key not in copied:
                shutil.copytree(
                    *key,
                    symlinks=True,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
                copied.add(key)
        launcher = candidate / "bin" / "lsab"
        atomic_write(launcher, '#!/bin/sh\nexec "$(dirname -- "$0")/python" -m cli "$@"\n', 0o755)
        return candidate

    def _revision(self, candidate: Path, updates: dict[str, bool]) -> ConfigRevision:
        inventory = self._inventory(candidate)
        return self.config.plan_switches(updates, inventory["defaults"])

    def _validate(
        self,
        env: Path,
        revision: ConfigRevision,
        before: dict | None = None,
        mutable: str = "",
    ) -> dict:
        pip(env / "bin/python", "check")
        after = self._inventory(env, revision.text, check=True)
        if before:
            changed = [
                name
                for name, version in before["packages"].items()
                if name != mutable and after["packages"].get(name) != version
            ]
            if changed:
                raise ValueError("Existing dependencies changed: " + ", ".join(sorted(changed)))
        failed = [row for row in after["diagnostics"] if not row["ok"]]
        if failed:
            details = "; ".join(
                f"{row['id']}: {row['error'] or row['dependency_issue']}" for row in failed
            )
            raise ValueError("Candidate configuration failed checks: " + details)
        return after

    def _activate(self, txn, candidate, revision, inventory, **options):
        if self.controller.kind == "manual":
            with self._manual_guard():
                self._switch(txn, candidate, revision, inventory, **options)
        else:
            self._switch(txn, candidate, revision, inventory, **options)

    @contextmanager
    def _manual_guard(self):
        with (self.root / "polling.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ServiceError(
                    "Stop the manually started bot before switching environments"
                ) from exc
            yield

    def _switch(
        self, txn, candidate, revision, inventory, *, backup=None, purge=False, revoke_plugins=()
    ):
        if self.config.snapshot() != revision.original:
            raise ValueError("Configuration changed during preparation")
        running, runtime = self._running_service()
        txn.update(
            previous=str(self.active),
            candidate=str(candidate),
            config=str(self.config.path),
            before=revision.original,
            after=revision.text,
            restart=running,
            previous_pointer=str(self.pointer("previous") or ""),
            backup_plugin=backup,
            backup_ready=False,
            backend=self.controller.kind,
        )
        self._save(txn, "applying")
        try:
            if running:
                self.controller.stop()
            if backup:
                self._backup(txn, backup)
                txn["backup_ready"] = True
                self._save(txn, "applying")
            if revision.text != revision.original:
                self.config.replace(revision.text, revision.original)
            self._link("current", candidate)
            if running:
                self.controller.start()
                self.management._wait_ready(
                    self.controller,
                    revision.text,
                    runtime["run_id"] if runtime else None,
                    set(inventory["enabled"]),
                    environment=candidate,
                )
            if purge:
                self._purge(backup)
            self._link("previous", Path(txn["previous"]))
            self._complete_switch(txn, revoke_plugins)
        except BaseException as exc:
            try:
                if txn.pop("pending_broker_revocations", None):
                    self._save(txn, "applying")
                self._restore(txn)
            except Exception as recovery:
                self._save(txn, "recovery_required")
                raise ServiceError(
                    f"Package activation failed: {exc}. Recovery failed: {recovery}"
                ) from exc
            raise ServiceError(
                f"Package activation failed: {exc}. Previous environment restored."
            ) from exc

    def _complete_switch(self, txn: dict, revoke_plugins) -> None:
        if revoke_plugins:
            txn["pending_broker_revocations"] = list(revoke_plugins)
            self._save(txn, "applying")
            for plugin in revoke_plugins:
                revoke(plugin)
            del txn["pending_broker_revocations"]
        self._save(txn, "completed")

    def _running_service(self) -> tuple[bool, dict | None]:
        service = self.controller.status()
        runtime = read_status(self.management.directory)
        running = service["state"] in {
            "active",
            "running",
            "activating",
            "reloading",
            "deactivating",
        }
        if self.controller.kind == "manual":
            if runtime:
                raise ServiceError("Stop the manually started bot before switching environments")
        else:
            self.controller.ensure_target(self.config.path)
            self.controller.ensure_environment_launcher(self.project)
            if running:
                if not runtime or service.get("pid") != runtime["pid"]:
                    raise ServiceError(
                        "Wait for the managed bot to publish a fresh readiness report"
                    )
                applied = self.config.preview(self._inventory(self.active)["defaults"]).text
                if runtime["config_hash"] != config_digest(applied):
                    raise ServiceError(
                        "Resolve pending block configuration changes before replacing the running environment"
                    )
        return running, runtime

    def _restore(self, txn: dict) -> None:
        if Path(txn["config"]) != self.config.path:
            raise ValueError("Use the same --config path as the interrupted transaction")
        if txn.get("backend", self.controller.kind) != self.controller.kind:
            raise ValueError("Use the same service backend as the interrupted transaction")
        actual = self.config.snapshot()
        if actual not in (txn["before"], txn["after"]):
            raise ValueError("Configuration changed externally; resolve it before recovery")
        if txn["restart"]:
            self.controller.ensure_target(self.config.path)
            self.controller.ensure_environment_launcher(self.project)
            self.controller.stop()
        if txn.get("backup_ready"):
            self._restore_data(txn)
        if actual != txn["before"]:
            if txn["before"] is None:
                self.config.remove(expected=actual)
            else:
                self.config.replace(txn["before"], actual)
        previous = Path(txn["previous"])
        self._link("current", previous)
        if txn["previous_pointer"]:
            self._link("previous", Path(txn["previous_pointer"]))
        else:
            (self.root / "previous").unlink(missing_ok=True)
        if txn["restart"]:
            self.controller.start()
            old = self.config.preview(self._inventory(previous)["defaults"]).text
            self.management._wait_ready(self.controller, old, None, set(), environment=previous)
        self._save(txn, "rolled_back")

    def recover(self) -> dict:
        with self.lock():
            for txn in self._read_history():
                if txn["status"] in {"applying", "recovery_required"}:
                    pending = txn.get("pending_broker_revocations")
                    if pending:
                        self._recover_revocations(txn, pending)
                    elif self.controller.kind == "manual":
                        with self._manual_guard():
                            if read_status(self.management.directory):
                                raise ServiceError(
                                    "Stop the manually started bot before recovering"
                                )
                            self._restore(txn)
                    else:
                        self._restore(txn)
                    self._prune()
                    return self._public(txn)
            raise ValueError("No interrupted activation to recover")

    def _recover_revocations(self, txn: dict, pending: list[str]) -> None:
        if (
            Path(txn["config"]) != self.config.path
            or txn["backend"] != self.controller.kind
            or Path(txn["candidate"]) != self.active
            or txn["after"] != self.config.snapshot()
        ):
            raise ValueError("Restore the interrupted transaction's environment and configuration first")
        self._complete_switch(txn, pending)

    def _data_paths(self, key: str) -> tuple[Path, Path]:
        base = self.config.path.parent
        paths = base / "plugins" / f"{key}.toml", base / "data" / "plugins" / key
        if any(path.resolve() != path for path in paths):
            raise ValueError("Plugin data or settings traverse a symlink")
        return paths

    def _backup(self, txn: dict, key: str) -> None:
        backup = Path(txn["directory"]) / "backup"
        backup.mkdir(mode=0o700)
        settings, data = self._data_paths(key)
        if settings.exists():
            shutil.copy2(settings, backup / "settings.toml")
        if data.exists():
            shutil.copytree(data, backup / "data", symlinks=True)

    def _purge(self, key: str) -> None:
        settings, data = self._data_paths(key)
        settings.unlink(missing_ok=True)
        if data.exists():
            shutil.rmtree(data)

    def _restore_data(self, txn: dict) -> None:
        key = txn["backup_plugin"]
        settings, data = self._data_paths(key)
        backup = Path(txn["directory"]) / "backup"
        if not backup.is_dir():
            raise ValueError("Plugin data backup is missing")
        self._purge(key)
        if (backup / "settings.toml").exists():
            settings.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / "settings.toml", settings)
        if (backup / "data").exists():
            shutil.copytree(backup / "data", data, symlinks=True)

    def _remember(self, wheel: Path, digest: str) -> Path:
        root = self.cache / digest
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = root / wheel.name
        if not path.exists():
            if self.cache_info()["bytes"] + wheel.stat().st_size > 512 * 1024**2:
                raise ValueError("Wheel cache is full; clean unused artifacts")
            with tempfile.NamedTemporaryFile(dir=root, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                try:
                    with wheel.open("rb") as stream:
                        shutil.copyfileobj(stream, temporary)
                    temporary.flush()
                    if sha256(temporary_path) != digest:
                        raise ValueError("Wheel integrity check failed")
                    os.replace(temporary_path, path)
                finally:
                    temporary_path.unlink(missing_ok=True)
        if sha256(path) != digest:
            raise ValueError("Cached wheel integrity check failed")
        return path

    def cache_info(self) -> dict:
        files = list(self.cache.glob("*/*.whl"))
        return {
            "directory": str(self.cache),
            "count": len(files),
            "bytes": sum(path.stat().st_size for path in files),
        }

    def clean_cache(self) -> dict:
        with self.lock():
            self._assert_idle()
            used = {
                record["sha256"]
                for env in (self.active, self.pointer("previous"))
                if env
                for record in self._records(env).values()
            }
            for txn in self._read_history()[:10]:
                for records in ("records_before", "records_after"):
                    used.update(record["sha256"] for record in txn.get(records, {}).values())
            removed = 0
            for path in self.cache.glob("*/*.whl"):
                if path.parent.name not in used:
                    path.unlink()
                    removed += 1
            return {"removed": removed, **self.cache_info()}

    def lockfile(self) -> dict:
        return {
            "packages": self._inventory(self.active)["packages"],
            "plugins": self._records(self.active),
        }

    def export(self, destination: Path) -> dict:
        destination = destination.resolve()
        with self.lock():
            self._assert_idle()
            data = self.lockfile()
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ValueError("Export directory already exists")
            with tempfile.TemporaryDirectory(prefix=".lsab-docker-", dir=destination.parent) as tmp:
                payload = Path(tmp) / "payload"
                wheels = payload / "wheels"
                wheels.mkdir(parents=True)
                requirements, checksums = [], []
                for key, record in sorted(data["plugins"].items()):
                    name, digest = record["wheel"], record["sha256"]
                    source = self.cache / digest / name
                    if sha256(source) != digest:
                        raise ValueError(f"Cached artifact failed integrity check: {key}")
                    shutil.copyfile(source, wheels / name)
                    requirements.append(f"./wheels/{name}")
                    checksums.append(f"{digest}  wheels/{name}")
                _write(payload / "plugins.lock", data)
                self._constraints(data, payload / "constraints.txt")
                (payload / "requirements.txt").write_text("\n".join(requirements) + "\n")
                (payload / "wheels.sha256").write_text(
                    "\n".join(checksums) + ("\n" if checksums else "")
                )
                payload.rename(destination)
            return {"directory": str(destination), "plugins": sorted(data["plugins"])}

    def environment(self, check: bool = False) -> dict:
        if check:
            pip(self.active / "bin/python", "check")
        return {
            "current": str(self.active),
            "previous": str(self.pointer("previous") or ""),
            "lock": self.lockfile(),
            "interrupted": [
                self._public(txn)
                for txn in self._read_history()
                if txn["status"] in {"applying", "recovery_required"}
            ],
        }

    def history(self) -> list[dict]:
        return [self._public(txn) for txn in self._read_history()]

    def _read_history(self):
        for path in sorted(self.transactions.glob("*/transaction.json"), reverse=True):
            yield json.loads(path.read_text())

    @staticmethod
    def _public(txn: dict) -> dict:
        return {
            key: value
            for key, value in txn.items()
            if key
            not in {
                "before",
                "after",
                "records_before",
                "records_after",
            }
        }

    def _transaction(self, operation: str, plugin_id: str | None, base: Path) -> dict:
        name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:10]
        path = self.transactions / name
        path.mkdir(mode=0o700, parents=True)
        txn = {
            "transaction_id": name,
            "operation": operation,
            "plugin_id": plugin_id,
            "directory": str(path),
            "previous": str(base),
            "updated_at": _now(),
        }
        self._save(txn, "planned")
        return txn

    @staticmethod
    def _save(txn: dict, status: str) -> None:
        txn.update(status=status, updated_at=_now())
        _write(Path(txn["directory"]) / "transaction.json", txn)

    def _failed(self, txn: dict) -> None:
        if txn["status"] in {"planned", "preparing"}:
            self._save(txn, "failed")
        self._prune()

    def _assert_idle(self) -> None:
        for txn in self._read_history():
            if txn["status"] in {"applying", "recovery_required"}:
                raise ValueError("Interrupted switch found; run plugins environment recover")
            if txn["status"] in {"planned", "preparing"}:
                self._save(txn, "failed")
        self._prune()

    def _prune(self) -> None:
        keep = {self.active, self.pointer("previous"), Path(sys.prefix).resolve()}
        for txn in self._read_history():
            if txn["status"] in {"applying", "recovery_required"}:
                keep.update(Path(txn[key]) for key in ("previous", "candidate"))
        for path in self.environments.glob("env-*"):
            if path not in keep and path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)

    def _link(self, name: str, target: Path) -> None:
        temporary = self.root / f".{name}-{uuid4().hex}"
        try:
            temporary.symlink_to(target, target_is_directory=True)
            os.replace(temporary, self.root / name)
            _sync_directory(self.root)
        finally:
            temporary.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write(path: Path, data) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    _sync_directory(path.parent)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
