"""Plugin descriptor sources."""

from bot.plugins.sources.base import PluginSource
from bot.plugins.sources.builtin import BuiltinPluginSource

__all__ = ["BuiltinPluginSource", "PluginSource"]
