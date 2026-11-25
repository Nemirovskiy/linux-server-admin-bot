"""Declarative operations proposed by external plugins and approved by root."""

import re
from pathlib import Path, PurePosixPath

from broker.actions import ACTIONS
from broker.executor import executable
from broker.protocol import ACTION_NAME, CONTAINER_ID, IDENTIFIER, BrokerError

_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]{0,31})\}(h?)")
_LITERAL = re.compile(r"[A-Za-z0-9_./:=+,-]{1,160}")
_RESERVED_EXECUTABLES = {
    "ash",
    "bash",
    "busybox",
    "dash",
    "docker",
    "env",
    "find",
    "ksh",
    "node",
    "perl",
    "php",
    "podman",
    "ruby",
    "sh",
    "sudo",
    "xargs",
    "zsh",
}


def operations(plugin: str, values: object, *, approved: bool = False) -> dict:
    if not IDENTIFIER.fullmatch(plugin) or plugin in {"broker", "docker"}:
        raise ValueError("Invalid or reserved external broker plugin ID")
    if not isinstance(values, dict) or len(values) > 16:
        raise ValueError("Expected at most 16 broker operations")
    for name, spec in values.items():
        if (
            not ACTION_NAME.fullmatch(name)
            or len(name) > 96
            or not name.startswith(f"{plugin}.")
            or not isinstance(spec, dict)
        ):
            raise ValueError("Broker operations must use the plugin ID as their namespace")
        if set(spec) == {"docker_action", "containers"}:
            _docker(spec, approved=approved)
        elif set(spec) == {"argv", "parameters"}:
            _cli(spec)
        else:
            raise ValueError("Expected argv and parameters or a scoped Docker operation")
    return values


def _docker(spec: dict, *, approved: bool) -> None:
    action, containers = spec["docker_action"], spec["containers"]
    if not isinstance(action, str) or action not in ACTIONS:
        raise ValueError("Unknown Docker operation")
    if (
        not isinstance(containers, list)
        or not int(approved) <= len(containers) <= 32
        or any(not isinstance(item, str) or not CONTAINER_ID.fullmatch(item) for item in containers)
    ):
        raise ValueError("Approved Docker operations require explicit full container IDs")


def _cli(spec: dict) -> None:
    argv, params = spec["argv"], spec["parameters"]
    if (
        not isinstance(argv, list)
        or not 1 <= len(argv) <= 24
        or any(not isinstance(arg, str) or len(arg) > 160 for arg in argv)
        or not isinstance(params, dict)
        or len(params) > 8
    ):
        raise ValueError("Invalid command arguments or parameter declarations")
    path = PurePosixPath(argv[0])
    if (
        str(path) != argv[0]
        or path.parent
        not in map(PurePosixPath, ("/usr/bin", "/usr/sbin", "/usr/local/bin", "/usr/local/sbin"))
        or not _LITERAL.fullmatch(argv[0])
        or path.name in _RESERVED_EXECUTABLES
        or path.name.startswith(("python", "lsab-broker"))
    ):
        raise ValueError("Use the absolute path to a dedicated system executable")
    used = set()
    for arg in argv[1:]:
        match = _PLACEHOLDER.fullmatch(arg)
        if match:
            used.add(match[1])
        elif not _LITERAL.fullmatch(arg):
            raise ValueError("Use literal arguments or standalone parameter placeholders")
    if used != set(params):
        raise ValueError("Command placeholders must match the declared parameters")
    for rule in params.values():
        if (
            not isinstance(rule, dict)
            or set(rule) != {"min", "max"}
            or not _integer(rule["min"])
            or not _integer(rule["max"])
            or not 0 <= rule["min"] <= rule["max"] <= 1_000_000
        ):
            raise ValueError("Expected a bounded non-negative integer parameter")


def cli_command(spec: dict, params: dict) -> tuple[str, ...]:
    rules = spec["parameters"]
    if set(params) != set(rules) or any(
        not _integer(value) or not rules[key]["min"] <= value <= rules[key]["max"]
        for key, value in params.items()
    ):
        raise BrokerError("INVALID_PARAMS", "Parameters must match the approved integer ranges.")
    argv = list(spec["argv"])
    for index, arg in enumerate(argv[1:], 1):
        match = _PLACEHOLDER.fullmatch(arg)
        if match:
            argv[index] = f"{params[match[1]]}{match[2]}"
    return tuple(argv)


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def check_executable(spec: dict) -> None:
    if "argv" in spec:
        path = Path(spec["argv"][0])
        resolved = executable(str(path))
        # Refuse aliases to a different program, including interpreter symlinks.
        if resolved != str(path):
            raise ValueError("Specify the canonical system executable path")
