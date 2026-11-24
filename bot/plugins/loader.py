"""Load and verify enabled Python blocks."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from importlib import import_module
from typing import TYPE_CHECKING

from bot.api import PluginDefinitionError
from bot.plugins.models import PluginDescriptor

if TYPE_CHECKING:
    from bot.api import Block, PluginContext


class PluginLoader:
    def load_class(self, descriptor: PluginDescriptor) -> type[Block]:
        from bot.api import Block

        module_name, class_name = descriptor.entry_point.split(":")
        module = import_module(module_name)
        provider = getattr(module, class_name, None)
        if (
            not inspect.isclass(provider)
            or not issubclass(provider, Block)
            or inspect.isabstract(provider)
            or provider.__module__ != module_name
        ):
            raise PluginDefinitionError(f"{descriptor.entry_point} must reference a concrete Block")
        if getattr(provider, "metadata", None) != descriptor.metadata:
            raise PluginDefinitionError("Class metadata does not match the manifest")
        if provider.extra_actions != descriptor.extra_actions:
            raise PluginDefinitionError("Class callback actions do not match the manifest")
        return provider

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
