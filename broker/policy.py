"""Deny-by-default policy for system callers, actions, and container targets."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from broker.protocol import CONTAINER_ID, IDENTIFIER, BrokerError
from broker.security import Caller, trusted

POLICY_PATH = Path("/etc/lsab/broker/permissions.toml")
MAX_POLICY = 64 * 1024


@dataclass(frozen=True)
class Grant:
    enabled: bool
    allow: frozenset[str]
    containers: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Policy:
    callers: frozenset[int]
    plugins: dict[str, Grant]

    @classmethod
    def parse(cls, raw: bytes, owners: dict[str, str]) -> "Policy":
        document = tomllib.loads(raw.decode("utf-8"))
        if set(document) - {"policy", "plugins"}:
            raise ValueError("Unknown policy section")
        settings = document.get("policy")
        if not isinstance(settings, dict) or set(settings) != {"version", "default", "caller_uids"}:
            raise ValueError("Invalid policy header")
        if (
            not isinstance(settings["version"], int)
            or isinstance(settings["version"], bool)
            or settings["version"] != 1
            or settings["default"] != "deny"
        ):
            raise ValueError("Unsupported policy version or default")
        uids = settings["caller_uids"]
        if not isinstance(uids, list) or any(
            not isinstance(uid, int) or isinstance(uid, bool) or not 0 < uid < 2**32 - 1
            for uid in uids
        ):
            raise ValueError("Invalid caller UIDs")
        plugins = document.get("plugins", {})
        if not isinstance(plugins, dict):
            raise ValueError("Invalid plugins table")
        return cls(
            frozenset(uids),
            {name: _grant(name, fields, owners) for name, fields in plugins.items()},
        )

    def authorize_caller(self, caller: Caller) -> None:
        if caller.uid not in self.callers:
            raise BrokerError(
                "PERMISSION_DENIED", "This system user is not authorized for the broker."
            )

    def authorize(self, plugin_id: str, action: str) -> Grant:
        grant = self.plugins.get(plugin_id)
        if grant is None or not grant.enabled or action not in grant.allow:
            raise BrokerError("PERMISSION_DENIED", f"Grant the {action} permission to {plugin_id}.")
        return grant


def _grant(name: str, fields: Any, owners: dict[str, str]) -> Grant:
    if not IDENTIFIER.fullmatch(name) or not isinstance(fields, dict):
        raise ValueError("Invalid plugin grant")
    if set(fields) - {"enabled", "allow", "containers"}:
        raise ValueError("Unknown grant field")
    enabled, allow, containers = (
        fields.get("enabled", False),
        fields.get("allow", []),
        fields.get("containers", []),
    )
    if not isinstance(enabled, bool) or not isinstance(allow, list):
        raise ValueError("Invalid enabled or allow field")
    if any(not isinstance(action, str) or owners.get(action) != name for action in allow):
        raise ValueError("Policy references an unknown or unrelated action")
    if (
        not isinstance(containers, list)
        or len(containers) > 256
        or any(
            not isinstance(value, str) or not CONTAINER_ID.fullmatch(value) for value in containers
        )
    ):
        raise ValueError("Invalid container allowlist")
    if containers and name != "docker":
        raise ValueError("Container restrictions are only supported for Docker")
    return Grant(enabled, frozenset(allow), frozenset(containers))


def load_policy(path: Path, owners: dict[str, str]) -> Policy:
    try:
        trusted(path)
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            raw = stream.read(MAX_POLICY + 1)
        if len(raw) > MAX_POLICY:
            raise ValueError("Policy is too large")
        return Policy.parse(raw, owners)
    except (OSError, ValueError, RecursionError) as exc:
        raise BrokerError(
            "INVALID_POLICY", "Broker policy is missing, invalid or not root-protected."
        ) from exc
