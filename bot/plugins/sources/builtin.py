"""Discover manifests shipped alongside builtin Python modules."""

import tomllib
from collections.abc import Iterator
from pathlib import Path

import bot.blocks
from bot.api import PluginMetadata
from bot.plugins.models import DiscoveryFailure, PluginDescriptor

_SUFFIX = ".plugin.toml"


class BuiltinPluginSource:
    def discover(self) -> Iterator[PluginDescriptor | DiscoveryFailure]:
        for directory in bot.blocks.__path__:
            for path in sorted(Path(directory).glob(f"*{_SUFFIX}")):
                yield self._read_manifest(path)

    def _read_manifest(self, path: Path) -> PluginDescriptor | DiscoveryFailure:
        module = path.name.removesuffix(_SUFFIX)
        plugin_id = module
        try:
            with path.open("rb") as stream:
                data = tomllib.load(stream)
            if set(data) != {"plugin"} or not isinstance(data["plugin"], dict):
                raise ValueError("Expected a single [plugin] table")
            fields = data["plugin"]
            if isinstance(fields.get("id"), str) and fields["id"]:
                plugin_id = fields["id"]
            else:
                raise ValueError("Missing or invalid plugin ID")
            entry_point = fields.pop("entry_point")
            if (
                not isinstance(entry_point, str)
                or entry_point.partition(":")[0] != f"bot.blocks.{module}"
            ):
                raise ValueError(f"Entry point must reference bot.blocks.{module}")
            default_enabled = fields.pop("default_enabled", False)
            actions = fields.pop("extra_actions", [])
            permissions = fields.pop("permissions", [])
            if not isinstance(actions, list) or not isinstance(permissions, list):
                raise ValueError("extra_actions and permissions must be arrays")
            return PluginDescriptor(
                metadata=PluginMetadata(**fields, permissions=tuple(permissions)),
                source="builtin",
                entry_point=entry_point,
                default_enabled=default_enabled,
                extra_actions=tuple(actions),
            )
        except (KeyError, OSError, TypeError, ValueError) as exc:
            return DiscoveryFailure(plugin_id, "builtin", f"{path.name}: {exc}")
