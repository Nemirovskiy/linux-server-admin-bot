"""Result and default implementation for privileged actions."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BrokerResult:
    success: bool
    message: str
    code: str = "OK"
    data: Any = None
    duration_ms: int = 0

    def __post_init__(self) -> None:
        if not self.success and self.code == "OK":
            object.__setattr__(self, "code", "EXECUTION_FAILED")


class NullBroker:
    """Deny every request without running an operation."""

    async def execute(self, action: str, params: Mapping[str, Any]) -> BrokerResult:
        return self.execute_sync(action, params)

    def execute_sync(self, action: str, params: Mapping[str, Any]) -> BrokerResult:
        return BrokerResult(
            False,
            "Privileged actions are unavailable. Configure the native broker.",
            "BROKER_UNAVAILABLE",
        )
