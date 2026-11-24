"""Build the scoped services available to each plugin."""

from logging import getLogger

from bot.api import AlertProtocol, NullBroker, PluginContext, SystemProtocol
from bot.broker_client import BrokerClient
from bot.plugins.models import PluginDescriptor
from broker.transport import SudoTransport, Transport


class PluginContextFactory:
    def __init__(
        self, system: SystemProtocol, alerts: AlertProtocol, transport: Transport | None = None
    ) -> None:
        self.system = system
        self.alerts = alerts
        self.transport = transport

    def __call__(self, descriptor: PluginDescriptor) -> PluginContext:
        metadata = descriptor.metadata
        broker = (
            BrokerClient(metadata.id, self.transport, frozenset(metadata.permissions))
            if self.transport is not None and metadata.permissions
            else NullBroker()
        )
        return PluginContext(
            plugin_id=descriptor.metadata.id,
            config={},
            logger=getLogger(descriptor.entry_point.partition(":")[0]),
            system=self.system,
            alerts=self.alerts,
            broker=broker,
        )


def default_context_factory() -> PluginContextFactory:
    from bot.services import AlertManager, SystemMonitor
    from config import settings

    return PluginContextFactory(
        SystemMonitor(proc_path=settings.host_proc_path, sys_path=settings.host_sys_path),
        AlertManager(
            cpu_threshold=settings.cpu_alert_threshold,
            memory_threshold=settings.memory_alert_threshold,
            disk_threshold=settings.disk_alert_threshold,
            cooldown_seconds=settings.alert_cooldown,
        ),
        SudoTransport() if settings.lsab_broker == "sudo" else None,
    )
