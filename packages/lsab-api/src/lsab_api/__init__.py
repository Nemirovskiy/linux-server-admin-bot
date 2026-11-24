"""Standalone Plugin API for Linux Server Admin Bot."""

from importlib import import_module
from typing import TYPE_CHECKING

from lsab_api.exceptions import PluginDefinitionError
from lsab_api.metadata import PluginMetadata
from lsab_api.version import PLUGIN_API_VERSION

if TYPE_CHECKING:
    from lsab_api.block import Block as Block
    from lsab_api.broker import BrokerResult as BrokerResult
    from lsab_api.broker import NullBroker as NullBroker
    from lsab_api.context import PluginContext as PluginContext
    from lsab_api.metrics import Alert as Alert
    from lsab_api.metrics import AlertType as AlertType
    from lsab_api.metrics import CPUMetrics as CPUMetrics
    from lsab_api.metrics import DiskMetrics as DiskMetrics
    from lsab_api.metrics import MemoryMetrics as MemoryMetrics
    from lsab_api.metrics import NetworkMetrics as NetworkMetrics
    from lsab_api.metrics import ProcessInfo as ProcessInfo
    from lsab_api.presentation import escape_markdown as escape_markdown
    from lsab_api.presentation import (
        get_back_to_main_keyboard as get_back_to_main_keyboard,
    )
    from lsab_api.protocols import AlertProtocol as AlertProtocol
    from lsab_api.protocols import BrokerProtocol as BrokerProtocol
    from lsab_api.protocols import LoginInfo as LoginInfo
    from lsab_api.protocols import ServiceStatus as ServiceStatus
    from lsab_api.protocols import SystemProtocol as SystemProtocol
    from lsab_api.protocols import TemperatureReading as TemperatureReading
    from lsab_api.protocols import UptimeInfo as UptimeInfo
    from lsab_api.views import BlockView as BlockView

_MODULES = {
    "block": ("Block",),
    "broker": ("BrokerResult", "NullBroker"),
    "context": ("PluginContext",),
    "metrics": (
        "Alert",
        "AlertType",
        "CPUMetrics",
        "DiskMetrics",
        "MemoryMetrics",
        "NetworkMetrics",
        "ProcessInfo",
    ),
    "presentation": ("escape_markdown", "get_back_to_main_keyboard"),
    "protocols": (
        "AlertProtocol",
        "BrokerProtocol",
        "LoginInfo",
        "ServiceStatus",
        "SystemProtocol",
        "TemperatureReading",
        "UptimeInfo",
    ),
    "views": ("BlockView",),
}
_EXPORTS = {name: module for module, names in _MODULES.items() for name in names}

__all__ = ["PLUGIN_API_VERSION", "PluginDefinitionError", "PluginMetadata", *_EXPORTS]


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"lsab_api.{module}"), name)
    globals()[name] = value
    return value
