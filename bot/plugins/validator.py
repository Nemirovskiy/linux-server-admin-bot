"""Validate registration and runtime contracts."""

import re
from collections.abc import Iterable

from bot.api import PLUGIN_API_VERSION, PluginDefinitionError, PluginMetadata
from bot.plugins.models import PluginDescriptor

_NUMBER = r"(?:0|[1-9][0-9]*)"
_VERSION = re.compile(rf"{_NUMBER}\.{_NUMBER}\.{_NUMBER}(?:-[\w.-]+)?(?:\+[\w.-]+)?", re.ASCII)
_API_VERSION = re.compile(rf"{_NUMBER}\.{_NUMBER}(?:\.{_NUMBER})?")
_ENTRY_POINT = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*")


class PluginValidator:
    def __init__(self, reserved_commands: Iterable[str]) -> None:
        self.reserved_commands = frozenset(reserved_commands)
        self.reserved_actions = {
            "menu_main",
            "noop",
            *(f"cmd_{name}" for name in self.reserved_commands),
        }

    def validate(self, descriptor: PluginDescriptor) -> None:
        metadata = descriptor.metadata
        name = metadata.id
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name):
            raise PluginDefinitionError(f"Invalid command name: {name}")
        if name in self.reserved_commands:
            raise PluginDefinitionError(f"Reserved command name: {name}")
        self._validate_metadata(metadata, external=descriptor.source == "external")
        if not isinstance(metadata.sends_alerts, bool) or not isinstance(
            descriptor.default_enabled, bool
        ):
            raise PluginDefinitionError("sends_alerts and default_enabled must be booleans")
        if not _ENTRY_POINT.fullmatch(descriptor.entry_point):
            raise PluginDefinitionError(f"Invalid entry point: {descriptor.entry_point}")
        self._validate_actions(descriptor)

    @staticmethod
    def _validate_metadata(metadata: PluginMetadata, *, external: bool) -> None:
        for field in ("name", "icon", "description"):
            value = getattr(metadata, field)
            if not isinstance(value, str) or not value.strip():
                raise PluginDefinitionError(f"Invalid metadata.{field}")
        if len(metadata.description) > 256:
            raise PluginDefinitionError("Description exceeds 256 characters")
        if not isinstance(metadata.version, str) or (
            not external and not _VERSION.fullmatch(metadata.version)
        ):
            raise PluginDefinitionError(f"Invalid plugin version: {metadata.version}")
        api_version = metadata.api_version
        if not isinstance(api_version, str) or not _API_VERSION.fullmatch(api_version):
            raise PluginDefinitionError(f"Invalid Plugin API version: {api_version}")
        required = tuple(map(int, api_version.split(".")))
        current = tuple(map(int, PLUGIN_API_VERSION.split(".")))
        if required[0] != current[0] or required > current[: len(required)]:
            raise PluginDefinitionError(
                f"Unsupported Plugin API {api_version} (current {PLUGIN_API_VERSION})"
            )
        if any(
            not isinstance(permission, str) or not permission.strip()
            for permission in metadata.permissions
        ):
            raise PluginDefinitionError("permissions must contain nonempty strings")

    def _validate_actions(self, descriptor: PluginDescriptor) -> None:
        name = descriptor.metadata.id
        for action in descriptor.extra_actions:
            if not isinstance(action, str) or not re.fullmatch(rf"{name}_[a-z0-9_]+", action):
                raise PluginDefinitionError(
                    f"Nested actions for '{name}' must start with '{name}_'"
                )
            if len(action.encode("utf-8")) > 64:
                raise PluginDefinitionError(f"Callback action exceeds 64 bytes: {action}")
        if len(set(descriptor.actions)) != len(descriptor.actions):
            raise PluginDefinitionError("Duplicate callback action")
        if self.reserved_actions.intersection(descriptor.actions):
            raise PluginDefinitionError("Reserved callback action")
