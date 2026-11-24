"""Dependencies scoped to an individual plugin."""

from collections.abc import Mapping
from dataclasses import dataclass
from logging import Logger
from types import MappingProxyType
from typing import Any

from bot.api.protocols import AlertProtocol, BrokerProtocol, SystemProtocol


@dataclass(frozen=True)
class PluginContext:
    plugin_id: str
    config: Mapping[str, Any]
    logger: Logger
    system: SystemProtocol
    alerts: AlertProtocol
    broker: BrokerProtocol

    def __post_init__(self) -> None:
        object.__setattr__(self, "config", MappingProxyType(dict(self.config)))
