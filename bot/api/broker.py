"""Result and default implementation for privileged actions."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BrokerResult:
    success: bool
    message: str


class NullBroker:
    """Deny every request without running an operation."""

    async def execute(self, action: str, params: Mapping[str, Any]) -> BrokerResult:
        return BrokerResult(success=False, message="Privileged actions are unavailable.")
