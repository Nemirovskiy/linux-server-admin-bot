"""Build the scoped services available to each plugin."""

from logging import getLogger

from bot.api import AlertProtocol, NullBroker, PluginContext, SystemProtocol
from bot.plugins.models import PluginDescriptor


class PluginContextFactory:
    def __init__(self, system: SystemProtocol, alerts: AlertProtocol) -> None:
        self.system = system
        self.alerts = alerts

    def __call__(self, descriptor: PluginDescriptor) -> PluginContext:
        return PluginContext(
            plugin_id=descriptor.metadata.id,
            config={},
            logger=getLogger(descriptor.entry_point.partition(":")[0]),
            system=self.system,
            alerts=self.alerts,
            broker=NullBroker(),
        )
