"""Contract for discovery without importing plugin implementations."""

from collections.abc import Iterable
from typing import Protocol

from bot.plugins.models import DiscoveryFailure, PluginDescriptor


class PluginSource(Protocol):
    def discover(self) -> Iterable[PluginDescriptor | DiscoveryFailure]: ...
