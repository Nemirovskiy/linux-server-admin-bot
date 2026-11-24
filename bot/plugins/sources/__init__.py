"""Plugin descriptor sources."""

from bot.plugins.sources.base import PluginSource
from bot.plugins.sources.builtin import BuiltinPluginSource
from bot.plugins.sources.entrypoints import EntryPointPluginSource

__all__ = ["BuiltinPluginSource", "EntryPointPluginSource", "PluginSource", "installed_sources"]


def installed_sources() -> tuple[PluginSource, ...]:
    return BuiltinPluginSource(), EntryPointPluginSource()
