"""Plugin definitions and runtime state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import TYPE_CHECKING

from bot.api import PluginMetadata

if TYPE_CHECKING:
    from bot.api import Block


class PluginState(Enum):
    DISCOVERED = auto()
    LOADED = auto()
    READY = auto()
    RUNNING = auto()
    STOPPED = auto()
    FAILED = auto()


class PluginHealth(Enum):
    UNKNOWN = auto()
    HEALTHY = auto()
    DEGRADED = auto()
    UNAVAILABLE = auto()


@dataclass(frozen=True)
class DistributionInfo:
    name: str
    version: str
    requires_python: str | None
    requires_dist: tuple[str, ...]
    author: str | None = None
    license: str | None = None
    origin: str | None = None


@dataclass(frozen=True)
class PluginDescriptor:
    metadata: PluginMetadata
    source: str
    entry_point: str
    default_enabled: bool = False
    extra_actions: tuple[str, ...] = ()
    distribution: DistributionInfo | None = None
    required_permissions: tuple[str, ...] = ()
    optional_permissions: tuple[str, ...] = ()
    entry: EntryPoint | None = field(default=None, compare=False, repr=False)
    module_file: Path | None = None

    @property
    def actions(self) -> tuple[str, ...]:
        return (f"cmd_{self.metadata.id}", *self.extra_actions)


@dataclass
class PluginRecord:
    plugin_id: str
    source: str
    descriptor: PluginDescriptor | None = None
    enabled: bool = False
    instance: Block | None = None
    state: PluginState = PluginState.DISCOVERED
    health: PluginHealth = PluginHealth.UNKNOWN
    error: str | None = None
    dependency_issue: str | None = None
    distribution: DistributionInfo | None = None

    @property
    def metadata(self) -> PluginMetadata | None:
        return self.descriptor.metadata if self.descriptor else None


@dataclass(frozen=True)
class DiscoveryFailure:
    plugin_id: str
    source: str
    error: str
    distribution: DistributionInfo | None = None
