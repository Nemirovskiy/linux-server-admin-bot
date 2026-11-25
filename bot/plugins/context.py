"""Build the scoped services available to each plugin."""

import tomllib
from logging import getLogger
from pathlib import Path

from bot.api import AlertProtocol, NullBroker, PluginContext, SystemProtocol
from bot.broker_client import BrokerClient
from bot.plugins.models import PluginDescriptor
from broker.transport import SudoTransport, Transport


class PluginContextFactory:
    def __init__(
        self,
        system: SystemProtocol,
        alerts: AlertProtocol,
        transport: Transport | None = None,
        *,
        config_path: Path | None = None,
        create_data: bool = True,
    ) -> None:
        self.system = system
        self.alerts = alerts
        self.transport = transport
        self.config_path = config_path
        self.create_data = create_data

    def __call__(self, descriptor: PluginDescriptor) -> PluginContext:
        metadata = descriptor.metadata
        config, data_dir = self._paths(descriptor)
        broker = (
            BrokerClient(metadata.id, self.transport, frozenset(metadata.permissions))
            if self.transport is not None and metadata.permissions and descriptor.source == "builtin"
            else NullBroker()
        )
        return PluginContext(
            plugin_id=descriptor.metadata.id,
            config=config,
            logger=getLogger(descriptor.entry_point.partition(":")[0]),
            system=self.system,
            alerts=self.alerts,
            broker=broker,
            data_dir=data_dir,
        )

    def _paths(self, descriptor: PluginDescriptor) -> tuple[dict, Path | None]:
        if self.config_path is None:
            return {}, None
        base = self.config_path.resolve().parent
        settings = base / "plugins" / f"{descriptor.metadata.id}.toml"
        data_dir = base / "data" / "plugins" / descriptor.metadata.id
        if settings.resolve() != settings or data_dir.resolve() != data_dir:
            raise ValueError("Plugin settings and data paths must not traverse symlinks")
        config = _read_settings(settings)
        if self.create_data and descriptor.source == "external":
            data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        return config, data_dir


def _read_settings(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            raw = stream.read(65537)
    except FileNotFoundError:
        return {}
    if len(raw) > 65536:
        raise ValueError(f"Plugin settings exceed the size limit: {path}")
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Invalid plugin settings in {path}: {exc}") from exc
    if set(parsed) != {"settings"} or not isinstance(parsed["settings"], dict):
        raise ValueError(f"Expected a [settings] table in {path}")
    return parsed["settings"]


def default_context_factory(
    config_path: Path | None = None, *, create_data: bool = True
) -> PluginContextFactory:
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
        config_path=config_path,
        create_data=create_data,
    )
