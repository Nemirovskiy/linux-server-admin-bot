"""Load and verify enabled Python blocks."""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable
from importlib import import_module
from importlib.machinery import PathFinder
from pathlib import Path
from typing import TYPE_CHECKING

from bot.api import PluginDefinitionError
from bot.plugins.models import PluginDescriptor

if TYPE_CHECKING:
    from bot.api import Block, PluginContext


class PluginLoader:
    def load_class(self, descriptor: PluginDescriptor) -> type[Block]:
        from bot.api import Block

        module_name, class_name = descriptor.entry_point.split(":")
        if descriptor.entry is not None:
            self._verify_module(module_name, descriptor.module_file)
            provider = descriptor.entry.load()
        else:
            module = import_module(module_name)
            provider = getattr(module, class_name, None)
        if (
            not inspect.isclass(provider)
            or not issubclass(provider, Block)
            or inspect.isabstract(provider)
            or provider.__module__ != module_name
        ):
            raise PluginDefinitionError(f"{descriptor.entry_point} must reference a concrete Block")
        if (
            descriptor.module_file is not None
            and Path(inspect.getfile(provider)).resolve() != descriptor.module_file
        ):
            raise PluginDefinitionError(
                "Loaded class does not belong to the installed distribution"
            )
        if getattr(provider, "metadata", None) != descriptor.metadata:
            raise PluginDefinitionError("Class metadata does not match the manifest")
        if provider.extra_actions != descriptor.extra_actions:
            raise PluginDefinitionError("Class callback actions do not match the manifest")
        return provider

    @staticmethod
    def _verify_module(module_name: str, expected: Path | None) -> None:
        # Resolve the module path without executing package initialization.
        search = None
        prefix = ""
        for part in module_name.split("."):
            prefix = f"{prefix}.{part}" if prefix else part
            spec = PathFinder.find_spec(prefix, search)
            if spec is None:
                raise PluginDefinitionError(f"Module path is not available: {module_name}")
            if prefix != module_name and spec.submodule_search_locations is None:
                raise PluginDefinitionError(f"Entry point parent is not a package: {prefix}")
            search = spec.submodule_search_locations
        cached = sys.modules.get(module_name)
        origin = getattr(cached, "__file__", spec.origin) if cached is not None else spec.origin
        if origin is None or Path(origin).resolve() != expected:
            raise PluginDefinitionError(
                f"Module path does not belong to the installed distribution: {module_name}"
            )

    def create_instance(
        self,
        descriptor: PluginDescriptor,
        make_context: Callable[[PluginDescriptor], PluginContext],
    ) -> Block:
        from bot.blocks.base import Block as LegacyBlock

        provider = self.load_class(descriptor)
        context = make_context(descriptor)
        if context.plugin_id != descriptor.metadata.id:
            raise PluginDefinitionError("Context plugin ID does not match the manifest")
        if issubclass(provider, LegacyBlock):
            return provider._from_context(context)
        return provider(context)
