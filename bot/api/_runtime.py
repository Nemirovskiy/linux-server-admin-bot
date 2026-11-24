"""Plugin API exports that depend on the bot runtime."""

from bot.api.block import Block
from bot.api.broker import BrokerResult, NullBroker
from bot.api.context import PluginContext
from bot.api.protocols import (
    AlertProtocol,
    BrokerProtocol,
    LoginInfo,
    ServiceStatus,
    SystemProtocol,
    TemperatureReading,
    UptimeInfo,
)
from bot.api.views import BlockView
from bot.models import Alert, CPUMetrics, DiskMetrics, MemoryMetrics, NetworkMetrics, ProcessInfo
from config import AlertType

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
    "PluginContext",
    "ProcessInfo",
    "ServiceStatus",
    "SystemProtocol",
    "TemperatureReading",
    "UptimeInfo",
]
