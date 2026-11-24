"""Unprivileged client scoped to a registered plugin's declared permissions."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from bot.api.broker import BrokerResult
from broker.protocol import CODES, MAX_RESULT, VERSION, BrokerError, Request, json_loads
from broker.transport import Transport


@dataclass(frozen=True)
class BrokerClient:
    plugin_id: str
    transport: Transport
    permissions: frozenset[str]

    async def execute(self, action: str, params: Mapping[str, Any]) -> BrokerResult:
        return await asyncio.to_thread(self.execute_sync, action, params)

    def execute_sync(self, action: str, params: Mapping[str, Any]) -> BrokerResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return BrokerResult(
                False, "Use the async broker interface in the event loop.", "BROKER_UNAVAILABLE"
            )
        if action not in self.permissions:
            return BrokerResult(
                False,
                f"Plugin {self.plugin_id} has not declared this permission.",
                "PERMISSION_DENIED",
            )
        try:
            request = Request(uuid4().hex, self.plugin_id, action, dict(params))
            payload = request.encode()
            Request.parse(payload)
        except (ValueError, TypeError, RecursionError, BrokerError):
            return BrokerResult(False, "Invalid broker request.", "INVALID_PARAMS")
        try:
            response = self.transport.exchange(payload)
            return _decode(response, request.request_id)
        except BrokerError as exc:
            return BrokerResult(False, str(exc), exc.code)


def _decode(raw: bytes, request_id: str) -> BrokerResult:
    if len(raw) > MAX_RESULT + 1:
        raise BrokerError("RESULT_TOO_LARGE", "Broker response exceeds the size limit.")
    try:
        data = json_loads(raw)
        if not isinstance(data, dict) or set(data) != {
            "protocol_version",
            "request_id",
            "success",
            "code",
            "message",
            "data",
            "duration_ms",
        }:
            raise ValueError
        if (
            data["protocol_version"] != VERSION
            or not isinstance(data["protocol_version"], int)
            or isinstance(data["protocol_version"], bool)
        ):
            raise BrokerError("INCOMPATIBLE_PROTOCOL", "Unsupported broker response version.")
        code, success = data["code"], data["success"]
        if (
            not isinstance(code, str)
            or code not in CODES
            or not isinstance(success, bool)
            or success != (code == "OK")
        ):
            raise ValueError
        if data["request_id"] != request_id and not (
            data["request_id"] is None and code == "PERMISSION_DENIED"
        ):
            raise ValueError
        message, duration = data["message"], data["duration_ms"]
        if not isinstance(message, str) or len(message) > 512:
            raise ValueError
        if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
            raise ValueError
    except (ValueError, RecursionError) as exc:
        raise BrokerError("BROKER_UNAVAILABLE", "Invalid broker response.") from exc
    return BrokerResult(success, message, code, data["data"], duration)
