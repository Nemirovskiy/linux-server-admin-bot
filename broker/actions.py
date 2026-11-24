"""Fixed operations and allowlisted result fields for CrowdSec and Docker."""

import ipaddress
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from broker.protocol import CONTAINER_ID, BrokerError, json_loads

CSCLI = ("/usr/bin/cscli",)
DOCKER_CONFIG = "/etc/lsab/broker/docker"
DOCKER = (
    "/usr/bin/docker",
    "--config",
    DOCKER_CONFIG,
    "--host",
    "unix:///var/run/docker.sock",
)
_INSPECT = (
    '{"Id":{{json .Id}},"Name":{{json .Name}},"Image":{{json .Config.Image}},'
    '"State":{{json .State.Status}},"Health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},'
    '"Created":{{json .Created}},"Started":{{json .State.StartedAt}},'
    '"RestartCount":{{json .RestartCount}},"Ports":{{json .NetworkSettings.Ports}},'
    '"Networks":{{json .NetworkSettings.Networks}}}'
)


def _empty(_: bytes) -> None:
    return None


def _array(raw: bytes) -> list[dict]:
    values = json_loads(raw)
    if values is None:
        return []
    if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
        raise ValueError("Expected an array of records")
    return values


def _text(value: Any, size: int = 160) -> str:
    return value[:size] if isinstance(value, str) else ""


def _integer(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _ip(value: Any) -> str:
    try:
        return str(ipaddress.ip_address(value)) if isinstance(value, str) else ""
    except ValueError:
        return ""


def _decision(row: dict) -> dict:
    scope = _text(row.get("scope"), 32)
    return {
        "scope": scope,
        "type": _text(row.get("type"), 32),
        "origin": _text(row.get("origin"), 48),
        "value": _ip(row.get("value")) if scope.lower() == "ip" else "",
    }


def _decisions(raw: bytes) -> list[dict]:
    return [_decision(row) for row in _array(raw)]


def _alerts(raw: bytes) -> list[dict]:
    return [
        {
            "id": _integer(row.get("id")),
            "scenario": _text(row.get("scenario")),
            "created_at": _text(row.get("created_at"), 48),
            "events_count": _integer(row.get("events_count")),
            "kind": _text(row.get("kind"), 32),
            "simulated": row.get("simulated") is True,
            "decisions": [
                _decision(item) for item in row.get("decisions") or [] if isinstance(item, dict)
            ],
        }
        for row in _array(raw)
    ]


def _bouncers(raw: bytes) -> dict:
    return {"count": len(_array(raw))}


def _docker_rows(raw: bytes) -> list[dict]:
    rows = [json_loads(line) for line in raw.splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Expected Docker JSON objects")
    return rows


def _docker_list(raw: bytes) -> list[dict]:
    return [
        {
            "id": row["ID"],
            "name": _text(row.get("Names")),
            "image": _text(row.get("Image")),
            "status": _text(row.get("State"), 32),
            "ports": _text(row.get("Ports")),
        }
        for row in _docker_rows(raw)
        if isinstance(row.get("ID"), str) and CONTAINER_ID.fullmatch(row["ID"])
    ]


def _ports(bindings: dict) -> str:
    ports = []
    for port, hosts in bindings.items():
        if not re.fullmatch(r"[0-9]{1,5}/(?:tcp|udp|sctp)", port):
            continue
        host_port = (hosts or [{}])[0].get("HostPort", "")
        ports.append(f"{host_port} → {port}" if re.fullmatch(r"[0-9]{1,5}", host_port) else port)
    return ", ".join(ports[:3])


def _docker_inspect(raw: bytes) -> dict:
    (row,) = _docker_rows(raw)
    if not CONTAINER_ID.fullmatch(row["Id"]):
        raise ValueError("Invalid Docker container ID")
    networks = row.get("Networks") or {}
    ips = (_ip(network.get("IPAddress")) for network in networks.values())
    return {
        "id": row["Id"],
        "name": _text(row.get("Name")).lstrip("/"),
        "image": _text(row.get("Image")),
        "status": _text(row.get("State"), 32),
        "health": _text(row.get("Health"), 32),
        "created": _text(row.get("Created"), 19),
        "started": _text(row.get("Started"), 19),
        "restart_count": _integer(row.get("RestartCount")),
        "ip_address": next((ip for ip in ips if ip), ""),
        "ports": _ports(row.get("Ports") or {}),
    }


def _number(value: Any, suffix: str) -> float | None:
    try:
        number = float(_text(value).strip().removesuffix(suffix))
    except ValueError:
        return None
    return number if math.isfinite(number) and 0 <= number <= 1e12 else None


def _mib(value: str) -> float | None:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(B|kB|MB|GB|TB|KiB|MiB|GiB|TiB)", value.strip())
    if not match:
        return None
    number, unit = match.groups()
    powers = {
        "B": 1,
        "kB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
        "TiB": 1024**4,
    }
    amount = _number(number, "")
    return round(amount * powers[unit] / 1024**2, 2) if amount is not None else None


def _docker_stats(raw: bytes) -> dict:
    (row,) = _docker_rows(raw)
    used, _, limit = _text(row.get("MemUsage")).partition("/")
    return {
        "cpu_percent": _number(row.get("CPUPerc"), "%"),
        "memory_mb": _mib(used),
        "memory_limit_mb": _mib(limit),
        "memory_percent": _number(row.get("MemPerc"), "%"),
    }


@dataclass(frozen=True)
class Action:
    plugin: str
    argv: tuple[str, ...]
    output: Callable[[bytes], Any] = _empty
    params: str = ""
    mutates: bool = False
    timeout: int = 8

    def command(self, params: dict[str, Any]) -> tuple[str, ...]:
        expected = {self.params} if self.params else set()
        if set(params) != expected:
            raise BrokerError("INVALID_PARAMS", f"Expected parameters: {self.params or 'none'}.")
        if self.params == "period":
            if params["period"] != "24h":
                raise BrokerError("INVALID_PARAMS", "The supported alert period is 24h.")
        if self.params == "container_id":
            value = params["container_id"]
            if not isinstance(value, str) or not CONTAINER_ID.fullmatch(value):
                raise BrokerError(
                    "INVALID_PARAMS", "Expected a full lowercase hexadecimal container ID."
                )
            return (*self.argv, value)
        return self.argv


ACTIONS = {
    "crowdsec.status.read": Action("crowdsec", (*CSCLI, "lapi", "status")),
    "crowdsec.alerts.read": Action(
        "crowdsec",
        (*CSCLI, "alerts", "list", "--since", "24h", "--limit", "0", "-o", "json"),
        _alerts,
        "period",
    ),
    "crowdsec.decisions.read": Action(
        "crowdsec", (*CSCLI, "decisions", "list", "-o", "json"), _decisions
    ),
    "crowdsec.bouncers.read": Action(
        "crowdsec", (*CSCLI, "bouncers", "list", "-o", "json"), _bouncers
    ),
    "docker.containers.read": Action(
        "docker",
        (*DOCKER, "container", "ls", "--all", "--no-trunc", "--format", "{{json .}}"),
        _docker_list,
    ),
    "docker.containers.inspect": Action(
        "docker",
        (*DOCKER, "container", "inspect", "--format", _INSPECT),
        _docker_inspect,
        "container_id",
    ),
    "docker.containers.stats": Action(
        "docker",
        (*DOCKER, "stats", "--no-stream", "--format", "{{json .}}"),
        _docker_stats,
        "container_id",
    ),
    **{
        f"docker.containers.{verb}": Action(
            "docker",
            (*DOCKER, "container", verb),
            params="container_id",
            mutates=True,
            timeout=25,
        )
        for verb in ("start", "stop", "restart")
    },
}
OWNERS = {name: action.plugin for name, action in ACTIONS.items()}
