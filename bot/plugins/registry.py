"""In-process catalog of plugin definitions and runtime state."""

from bot.api import PluginDefinitionError
from bot.plugins.models import PluginRecord, PluginState


class PluginRegistry:
    def __init__(self) -> None:
        self._records: dict[str, PluginRecord] = {}

    def register(self, record: PluginRecord) -> None:
        if self.exists(record.plugin_id):
            raise PluginDefinitionError(f"Duplicate plugin ID: {record.plugin_id}")
        self._records[record.plugin_id] = record

    def unregister(self, plugin_id: str) -> PluginRecord:
        return self._records.pop(plugin_id)

    def get(self, plugin_id: str) -> PluginRecord:
        return self._records[plugin_id]

    def exists(self, plugin_id: str) -> bool:
        return plugin_id in self._records

    def list_all(self) -> tuple[PluginRecord, ...]:
        return tuple(self._records.values())

    def list_enabled(self) -> tuple[PluginRecord, ...]:
        return tuple(record for record in self.list_all() if record.enabled)

    def list_running(self) -> tuple[PluginRecord, ...]:
        return tuple(record for record in self.list_all() if record.state is PluginState.RUNNING)

    def list_failed(self) -> tuple[PluginRecord, ...]:
        return tuple(record for record in self.list_all() if record.state is PluginState.FAILED)
