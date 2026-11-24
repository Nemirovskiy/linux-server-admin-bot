"""Run fixed argv with bounded output, time and a minimal environment."""

import contextlib
import os
import selectors
import signal
import subprocess
import time
from pathlib import Path

from broker.actions import DOCKER, DOCKER_CONFIG
from broker.protocol import MAX_RESULT, BrokerError
from broker.security import trusted

ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "HOME": "/"}


def executable(path: str) -> str:
    try:
        configured = Path(path)
        trusted(configured.parent, directory=True)
        resolved = configured.resolve(strict=True)
        trusted(resolved)
        if not os.access(resolved, os.X_OK):
            raise PermissionError(path)
    except FileNotFoundError as exc:
        raise BrokerError("DEPENDENCY_MISSING", f"Install {Path(path).name} at {path}.") from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise BrokerError("EXECUTION_FAILED", "The configured executable is not trusted.") from exc
    return str(resolved)


def run_command(argv: tuple[str, ...], timeout: float) -> bytes:
    command = (executable(argv[0]), *argv[1:])
    if argv[0] == DOCKER[0]:
        _check_docker_config()
    try:
        with subprocess.Popen(
            command,
            cwd="/",
            env=ENV,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        ) as child:
            try:
                return _capture(child, timeout)
            except BaseException:
                if child.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(child.pid, signal.SIGKILL)
                raise
    except OSError as exc:
        raise BrokerError("EXECUTION_FAILED", "Could not run the system operation.") from exc


def _check_docker_config() -> None:
    directory = Path(DOCKER_CONFIG)
    try:
        trusted(directory, directory=True)
        if next(directory.iterdir(), None) is not None:
            raise ValueError("Docker client configuration directory must be empty")
    except (OSError, ValueError) as exc:
        raise BrokerError(
            "INVALID_POLICY", "Docker requires the installed, empty broker configuration directory."
        ) from exc


def _capture(child: subprocess.Popen, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    output = bytearray()
    size = 0
    with selectors.DefaultSelector() as selector:
        selector.register(child.stdout, selectors.EVENT_READ, True)
        selector.register(child.stderr, selectors.EVENT_READ, False)
        while selector.get_map():
            ready = selector.select(max(0, deadline - time.monotonic()))
            if not ready or time.monotonic() >= deadline:
                raise BrokerError(
                    "TIMEOUT", "System operation timed out; check its outcome before retrying."
                )
            for key, _ in ready:
                data = os.read(key.fd, 16384)
                if not data:
                    selector.unregister(key.fileobj)
                size += len(data)
                if size > MAX_RESULT:
                    raise BrokerError("RESULT_TOO_LARGE", "System output exceeded the size limit.")
                if key.data:
                    output.extend(data)
    try:
        code = child.wait(timeout=max(0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired as exc:
        raise BrokerError(
            "TIMEOUT", "System operation timed out; check its outcome before retrying."
        ) from exc
    if code:
        raise BrokerError(
            "EXECUTION_FAILED", "System operation failed; check its service and configuration."
        )
    return bytes(output)
