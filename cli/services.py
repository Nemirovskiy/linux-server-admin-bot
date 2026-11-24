"""Service operations through the user's existing supervisor."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Protocol

UNIT = "linux-server-admin-bot.service"
CONTAINER = "linux-server-admin-bot"


class ServiceError(RuntimeError):
    pass


def _run(
    args: Sequence[str], *, check: bool = True, follow: bool = False
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            args,
            capture_output=not follow,
            text=True,
            timeout=None if follow else 60,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ServiceError(f"{args[0]} is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise ServiceError(f"{args[0]} timed out") from exc
    if check and result.returncode:
        raise ServiceError(
            (result.stderr or "").strip() or f"{args[0]} exited with {result.returncode}"
        )
    return result


class ServiceController(Protocol):
    kind: str

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def restart(self) -> None: ...
    def status(self) -> dict: ...
    def logs(self, follow: bool) -> str: ...
    def ensure_target(self, config_path: Path) -> None: ...


class SystemdUserController:
    kind = "systemd"

    def installed(self) -> bool:
        result = _run(
            ["systemctl", "--user", "show", UNIT, "-p", "LoadState", "--value"], check=False
        )
        return result.returncode == 0 and result.stdout.strip() == "loaded"

    def _show(self, *properties: str) -> dict[str, str]:
        args = ["systemctl", "--user", "show", UNIT]
        for prop in properties:
            args.extend(("-p", prop))
        return dict(line.split("=", 1) for line in _run(args).stdout.splitlines() if "=" in line)

    def start(self) -> None:
        _run(["systemctl", "--user", "start", UNIT])

    def stop(self) -> None:
        _run(["systemctl", "--user", "stop", UNIT])

    def restart(self) -> None:
        _run(["systemctl", "--user", "restart", UNIT])

    def status(self) -> dict:
        properties = self._show("LoadState", "ActiveState", "MainPID")
        state = properties.get("ActiveState", "unknown")
        if properties.get("LoadState") != "loaded":
            state = "not-installed"
        return {"backend": self.kind, "state": state, "pid": int(properties.get("MainPID") or 0)}

    def logs(self, follow: bool) -> str:
        args = ["journalctl", "--user", "-u", UNIT, "-n", "100", "--no-pager"]
        if follow:
            args.append("--follow")
        return _run(args, follow=follow).stdout or ""

    def ensure_target(self, config_path: Path) -> None:
        properties = self._show("WorkingDirectory", "Environment", "LoadState")
        base = properties.get("WorkingDirectory")
        if not base or properties.get("LoadState") != "loaded":
            raise ServiceError(f"Cannot determine the configuration used by {UNIT}")
        settings = dict(
            item.split("=", 1)
            for item in shlex.split(properties.get("Environment", ""))
            if "=" in item
        )
        target = Path(base) / settings.get("BLOCKS_CONFIG", "blocks.toml")
        if target.resolve() != config_path.resolve():
            raise ServiceError(f"{UNIT} uses {target}, selected configuration is {config_path}")


class DockerComposeController:
    kind = "docker"

    def __init__(self, project: Path, files: Sequence[Path]) -> None:
        self.project = project.resolve()
        self.command = ["docker", "compose", "--project-directory", str(self.project)]
        for file in files or (self.project / "docker-compose.yml",):
            self.command.extend(("-f", str(file.resolve())))

    def start(self) -> None:
        _run([*self.command, "up", "-d", CONTAINER])

    def stop(self) -> None:
        _run([*self.command, "stop", CONTAINER])

    def restart(self) -> None:
        _run([*self.command, "up", "-d", "--no-deps", "--force-recreate", CONTAINER])

    def status(self) -> dict:
        raw = _run([*self.command, "ps", "--all", "--format", "json", CONTAINER]).stdout.strip()
        rows = (
            json.loads(raw)
            if raw.startswith("[")
            else [json.loads(line) for line in raw.splitlines()]
        )
        state = rows[0].get("State", "unknown") if rows else "stopped"
        pid = 0
        if state == "running":
            container_id = rows[0].get("ID")
            if container_id:
                pid = int(
                    _run(
                        ["docker", "inspect", "--format", "{{.State.Pid}}", container_id]
                    ).stdout.strip()
                )
        return {"backend": self.kind, "state": state, "pid": pid}

    def logs(self, follow: bool) -> str:
        args = [*self.command, "logs", "--tail", "100", "--no-color"]
        if follow:
            args.append("--follow")
        return _run([*args, CONTAINER], follow=follow).stdout or ""

    def ensure_target(self, config_path: Path) -> None:
        data = json.loads(_run([*self.command, "config", "--format", "json"]).stdout)
        service = data.get("services", {}).get(CONTAINER, {})
        environment = service.get("environment") or {}
        config = PurePosixPath(environment.get("BLOCKS_CONFIG") or "/app/blocks.toml")
        mounts = [
            mount
            for mount in service.get("volumes", [])
            if mount.get("type") == "bind" and config.is_relative_to(mount["target"])
        ]
        if not mounts:
            raise ServiceError(
                "Compose does not bind-mount the service configuration onto this host"
            )
        mount = max(mounts, key=lambda entry: len(PurePosixPath(entry["target"]).parts))
        target = self.project / mount["source"] / config.relative_to(mount["target"])
        if target.resolve() != config_path.resolve():
            raise ServiceError(f"Compose uses {target}, selected configuration is {config_path}")


class ManualController:
    kind = "manual"

    def __init__(self, project: Path) -> None:
        self.log_path = project / os.environ.get("LOG_FILE", "logs/bot.log")

    def _unsupported(self) -> None:
        raise ServiceError(
            "Manual mode: control the bot from its original terminal, or select --service systemd or docker"
        )

    start = _unsupported
    stop = _unsupported
    restart = _unsupported

    def status(self) -> dict:
        return {"backend": self.kind, "state": "unmanaged", "pid": 0}

    def logs(self, follow: bool) -> str:
        args = ["tail", "-n", "100"]
        if follow:
            args.append("-F")
        return _run([*args, "--", str(self.log_path)], follow=follow).stdout or ""

    def ensure_target(self, config_path: Path) -> None:
        self._unsupported()


def service_controller(kind: str, project: Path, files: Sequence[Path]) -> ServiceController:
    if kind == "docker":
        return DockerComposeController(project, files)
    systemd = SystemdUserController()
    if kind == "systemd":
        return systemd
    if kind == "auto":
        try:
            if systemd.installed():
                return systemd
        except ServiceError:
            pass
    return ManualController(project)
