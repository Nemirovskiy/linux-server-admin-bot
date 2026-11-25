"""Strict, bounded JSON messages for the broker."""

import json
import re
from dataclasses import dataclass
from typing import Any

VERSION = 1
MAX_REQUEST = 8192
MAX_RESULT = 1024 * 1024
IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,31}")
ACTION_NAME = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,3}")
REQUEST_ID = re.compile(r"[0-9a-f]{12,32}")
CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
CODES = frozenset(
    {
        "OK",
        "PERMISSION_DENIED",
        "UNKNOWN_ACTION",
        "INVALID_PARAMS",
        "DEPENDENCY_MISSING",
        "EXECUTION_FAILED",
        "TIMEOUT",
        "BROKER_UNAVAILABLE",
        "INCOMPATIBLE_PROTOCOL",
        "INVALID_POLICY",
        "RESULT_TOO_LARGE",
        "BUSY",
    }
)


class BrokerError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("Non-finite JSON value")


def json_loads(data: bytes) -> Any:
    return json.loads(
        data.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_invalid_constant
    )


def json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode(
        "ascii"
    )


@dataclass(frozen=True)
class Request:
    request_id: str
    plugin_id: str
    action: str
    params: dict[str, Any]
    artifact: str | None = None

    @classmethod
    def parse(cls, raw: bytes) -> "Request":
        if len(raw) > MAX_REQUEST:
            raise BrokerError("INVALID_PARAMS", "Request exceeds the size limit.")
        try:
            data = json_loads(raw)
        except (ValueError, RecursionError) as exc:
            raise BrokerError("INVALID_PARAMS", "Invalid JSON request.") from exc
        fields = {"request_id", "plugin_id", "action", "params", "protocol_version"}
        if not isinstance(data, dict) or set(data) - {"artifact"} != fields:
            raise BrokerError("INVALID_PARAMS", "Invalid request fields.")
        version = data["protocol_version"]
        if not isinstance(version, int) or isinstance(version, bool) or version != VERSION:
            raise BrokerError("INCOMPATIBLE_PROTOCOL", "Unsupported broker protocol version.")
        for name, pattern in (
            ("request_id", REQUEST_ID),
            ("plugin_id", IDENTIFIER),
            ("action", ACTION_NAME),
        ):
            value = data[name]
            if not isinstance(value, str) or len(value) > 96 or not pattern.fullmatch(value):
                raise BrokerError("INVALID_PARAMS", f"Invalid {name}.")
        if not isinstance(data["params"], dict):
            raise BrokerError("INVALID_PARAMS", "Parameters must be an object.")
        artifact = data.get("artifact")
        if "artifact" in data and (
            not isinstance(artifact, str) or not CONTAINER_ID.fullmatch(artifact)
        ):
            raise BrokerError("INVALID_PARAMS", "Invalid artifact digest.")
        return cls(data["request_id"], data["plugin_id"], data["action"], data["params"], artifact)

    def encode(self) -> bytes:
        fields = {"protocol_version": VERSION, **vars(self)}
        if self.artifact is None:
            del fields["artifact"]
        return json_bytes(fields)
