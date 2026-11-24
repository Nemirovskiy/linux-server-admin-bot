"""Plugin metadata used for discovery, routing, and presentation."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PluginMetadata:
    """Declare a plugin ID, display name, and capabilities."""

    id: str
    name: str
    description: str
    version: str
    icon: str
    api_version: str = "1.0"
    sends_alerts: bool = False
    permissions: tuple[str, ...] = ()
