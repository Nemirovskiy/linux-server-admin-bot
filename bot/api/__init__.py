"""Public interfaces for Linux Server Admin Bot plugins."""

from importlib import import_module
from typing import TYPE_CHECKING

from bot.api.exceptions import PluginDefinitionError
from bot.api.metadata import PluginMetadata
from bot.api.version import PLUGIN_API_VERSION

if TYPE_CHECKING:
    from bot.api._runtime import (
        Alert,
        AlertProtocol,
        AlertType,
        Block,
        BlockView,
        BrokerProtocol,
        BrokerResult,
        CPUMetrics,
        DiskMetrics,
        LoginInfo,
        MemoryMetrics,
        NetworkMetrics,
        NullBroker,
        PluginContext,
        ProcessInfo,
        ServiceStatus,
        SystemProtocol,
        TemperatureReading,
        UptimeInfo,
    )

__all__ = [
    "Alert",
    "AlertProtocol",
    "AlertType",
    "Block",
    "BlockView",
    "BrokerProtocol",
    "BrokerResult",
    "CPUMetrics",
    "DiskMetrics",
    "LoginInfo",
    "MemoryMetrics",
    "NetworkMetrics",
    "NullBroker",
    "PLUGIN_API_VERSION",
    "PluginContext",
    "PluginDefinitionError",
    "PluginMetadata",
    "ProcessInfo",
    "ServiceStatus",
    "SystemProtocol",
    "TemperatureReading",
    "UptimeInfo",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module("bot.api._runtime"), name)
    globals()[name] = value
    return value
