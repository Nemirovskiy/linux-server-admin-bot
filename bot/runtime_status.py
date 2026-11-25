"""Local readiness and plugin state shared with the CLI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from contextlib import suppress
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from bot import __version__
from bot.storage import atomic_write

if TYPE_CHECKING:
    from bot.plugins.manager import PluginManager

logger = getLogger(__name__)
HEARTBEAT_SECONDS = 3
MAX_AGE_SECONDS = 12


def state_directory(config_path: Path) -> Path:
    path = config_path.resolve()
    return path.parent / ".lsab" / path.name


def config_digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def process_identity(pid: int) -> str | None:
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] in {"Z", "X"}:
            return None
        return f"{boot_id}:{fields[19]}"
    except (OSError, IndexError):
        return None


def read_status(directory: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((directory / "status.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data["ready"] is not True:
            return None
        pid = data["pid"]
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return None
        if not isinstance(data["run_id"], str):
            return None
        if not -2 <= time.time() - data["updated_at"] <= MAX_AGE_SECONDS:
            return None
        identity = process_identity(pid)
        if identity is None or data["process_identity"] != identity:
            return None
        if not isinstance(data["plugins"], dict) or not isinstance(data["config_hash"], str):
            return None
        for record in data["plugins"].values():
            if not isinstance(record, dict) or not all(
                isinstance(record.get(field), kind)
                for field, kind in (
                    ("enabled", bool),
                    ("state", str),
                    ("health", str),
                    ("has_issue", bool),
                )
            ):
                return None
        return data
    except (OSError, KeyError, TypeError, ValueError):
        return None


class RuntimeStatus:
    def __init__(self, config_path: Path, manager: PluginManager) -> None:
        self.directory = state_directory(config_path)
        self.manager = manager
        self.started_at = time.time()
        self.run_id = uuid4().hex
        self.pid = os.getpid()
        self.identity = process_identity(self.pid)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            text = self.manager.config_text
            if text is None:
                raise ValueError("Missing startup configuration snapshot")
            atomic_write(self.directory / "running.toml", text)
            self._publish(ready=True)
        except (OSError, ValueError):
            logger.exception("Could not publish bot runtime status")
            return
        self._task = asyncio.create_task(self._heartbeat())

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        try:
            self._publish(ready=False)
        except OSError:
            logger.exception("Could not mark bot runtime status as stopped")

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                if not self._owns_status():
                    return
                self._publish(ready=True)
            except (OSError, ValueError):
                logger.exception("Could not refresh bot runtime status")

    def _owns_status(self) -> bool:
        try:
            path = self.directory / "status.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            return isinstance(data, dict) and data.get("run_id") == self.run_id
        except FileNotFoundError:
            return True
        except ValueError:
            return False

    def _publish(self, *, ready: bool) -> None:
        if not ready and not self._owns_status():
            return
        records = self.manager.list_plugins()
        status = {
            "pid": self.pid,
            "environment": str(Path(sys.prefix).resolve()),
            "process_identity": self.identity,
            "run_id": self.run_id,
            "ready": ready,
            "version": __version__,
            "started_at": self.started_at,
            "updated_at": time.time(),
            "config_hash": config_digest(self.manager.config_text or ""),
            "plugins": {
                record.plugin_id: {
                    "enabled": record.enabled,
                    "state": record.state.name.lower(),
                    "health": record.health.name.lower(),
                    "has_issue": bool(record.error or record.dependency_issue),
                }
                for record in records
            },
        }
        atomic_write(
            self.directory / "status.json",
            json.dumps(status, ensure_ascii=False) + "\n",
            durable=False,
        )
