"""Authorize, execute and audit a single broker request."""

import fcntl
import os
import syslog
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from broker.actions import ACTIONS, DOCKER, OWNERS, Action
from broker.executor import executable, run_command
from broker.policy import POLICY_PATH, Policy, load_policy
from broker.protocol import MAX_RESULT, VERSION, BrokerError, Request, json_bytes
from broker.security import Caller, sudo_caller, trusted

_CONTROL = {"broker.status", "broker.actions", "broker.grants"}
_LOCK_DIR = Path("/run/lsab-broker")


def audit(record: dict[str, Any]) -> None:
    syslog.openlog("lsab-broker", syslog.LOG_PID, syslog.LOG_AUTHPRIV)
    syslog.syslog(
        syslog.LOG_INFO,
        json_bytes(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                **record,
            }
        ).decode("ascii"),
    )


@contextmanager
def mutation_lock() -> Iterator[None]:
    try:
        _LOCK_DIR.mkdir(mode=0o700, exist_ok=True)
        trusted(_LOCK_DIR, directory=True)
        fd = os.open(_LOCK_DIR / "docker.lock", os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
        with os.fdopen(fd, "rb+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise BrokerError("BUSY", "Another Docker operation is in progress.") from exc
            yield
    except (OSError, ValueError) as exc:
        raise BrokerError("EXECUTION_FAILED", "Cannot acquire the broker operation lock.") from exc


def _available(action: Action) -> bool:
    try:
        executable(action.argv[0])
    except BrokerError:
        return False
    return True


def _control(request: Request, policy: Policy) -> Any:
    if request.params:
        raise BrokerError("INVALID_PARAMS", "This diagnostic accepts no parameters.")
    if request.action == "broker.status":
        return {"protocol_version": VERSION, "mode": "sudo"}
    if request.action == "broker.actions":
        return [
            {
                "id": name,
                "plugin": action.plugin,
                "mutates": action.mutates,
                "parameter": action.params or None,
                "executable_available": _available(action),
            }
            for name, action in ACTIONS.items()
        ]
    return {
        name: {
            "enabled": grant.enabled,
            "allow": sorted(grant.allow) if grant.enabled else [],
            "container_count": len(grant.containers),
        }
        for name, grant in policy.plugins.items()
    }


def _execute(request: Request, policy: Policy, runner: Callable) -> Any:
    if request.plugin_id == "broker" and request.action in _CONTROL:
        return _control(request, policy)
    action = ACTIONS.get(request.action)
    if action is None:
        raise BrokerError("UNKNOWN_ACTION", "The requested action is not registered.")
    if action.plugin != request.plugin_id:
        raise BrokerError("PERMISSION_DENIED", "The action is not assigned to this plugin.")
    grant = policy.authorize(request.plugin_id, request.action)
    argv = action.command(request.params)
    container_id = request.params.get("container_id")
    if (
        action.mutates or (container_id is not None and grant.containers)
    ) and container_id not in grant.containers:
        raise BrokerError(
            "PERMISSION_DENIED", "This container ID is outside the granted allowlist."
        )
    if action.mutates:
        with mutation_lock():
            found = runner(
                (*DOCKER, "container", "inspect", "--format", "{{.Id}}", container_id), 3
            )
            if found.strip() != container_id.encode("ascii"):
                raise BrokerError(
                    "PERMISSION_DENIED", "The Docker target no longer matches the granted ID."
                )
            return action.output(runner(argv, action.timeout))
    data = action.output(runner(argv, action.timeout))
    if request.action == "docker.containers.inspect" and data["id"] != container_id:
        raise BrokerError(
            "PERMISSION_DENIED", "The Docker target no longer matches the requested ID."
        )
    if request.action == "docker.containers.read" and grant.containers:
        data = [row for row in data if row["id"] in grant.containers]
    return data


def dispatch(
    raw: bytes,
    *,
    identify: Callable[[], Caller] = sudo_caller,
    policy_loader: Callable[[], Policy] | None = None,
    runner: Callable = run_command,
    auditor: Callable[[dict], None] = audit,
) -> bytes:
    start = time.monotonic()
    request = None
    caller = None
    try:
        caller = identify()
        request = Request.parse(raw)
        policy = policy_loader() if policy_loader else load_policy(POLICY_PATH, OWNERS)
        policy.authorize_caller(caller)
        data = _execute(request, policy, runner)
        response = _reply(request, "OK", "Operation completed.", start, data)
        if len(response) > MAX_RESULT:
            raise BrokerError("RESULT_TOO_LARGE", "Broker response exceeds the size limit.")
        result = "OK"
    except BrokerError as exc:
        result = exc.code
        response = _reply(request, result, str(exc), start)
    except Exception:
        result = "EXECUTION_FAILED"
        response = _reply(request, result, "Could not complete the broker operation.", start)
    auditor(
        {
            "request_id": request.request_id if request else None,
            "system_user": caller.user if caller else None,
            "system_uid": caller.uid if caller else None,
            "claimed_plugin_id": request.plugin_id if request else None,
            "action": (
                request.action
                if request and (request.action in ACTIONS or request.action in _CONTROL)
                else None
            ),
            "result": result,
            "duration_ms": int((time.monotonic() - start) * 1000),
        }
    )
    return response


def _reply(
    request: Request | None, code: str, message: str, start: float, data: Any = None
) -> bytes:
    return json_bytes(
        {
            "protocol_version": VERSION,
            "request_id": request.request_id if request else None,
            "success": code == "OK",
            "code": code,
            "message": message,
            "data": data,
            "duration_ms": int((time.monotonic() - start) * 1000),
        }
    )
