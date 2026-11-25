"""Coordinate discovery, configuration, and each block's lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Iterator
from logging import getLogger
from typing import TYPE_CHECKING

from bot.api import PluginDefinitionError
from bot.broker_client import BrokerClient
from bot.commands import CORE_COMMANDS
from bot.plugins.config import PluginConfig
from bot.plugins.loader import PluginLoader
from bot.plugins.models import (
    DiscoveryFailure,
    PluginDescriptor,
    PluginHealth,
    PluginRecord,
    PluginState,
)
from bot.plugins.registry import PluginRegistry
from bot.plugins.sources.base import PluginSource
from bot.plugins.validator import PluginValidator

if TYPE_CHECKING:
    from bot.api import Alert, Block, PluginContext

logger = getLogger(__name__)


class PluginManager:
    def __init__(
        self,
        sources: Iterable[PluginSource],
        config: PluginConfig,
        reserved_commands: Iterable[str] = CORE_COMMANDS,
        context_factory: Callable[[PluginDescriptor], PluginContext] | None = None,
        *,
        operation_timeout: float = 15,
    ) -> None:
        self.sources = tuple(sources)
        self.config = config
        self.context_factory = context_factory
        self.operation_timeout = operation_timeout
        self.registry = PluginRegistry()
        self.loader = PluginLoader()
        self.validator = PluginValidator(reserved_commands)
        self._startup_ids: tuple[str, ...] = ()
        self.defaults: dict[str, bool] = {}
        self.switches: dict[str, bool] = {}
        self.conflicts: list[PluginRecord] = []
        self.config_text: str | None = None

    def discover(self, *, sync: bool = True, configuration: str | None = None) -> None:
        if any(record.instance is not None for record in self.list_plugins()):
            raise RuntimeError("Stop the plugins before rediscovering")
        self.registry = PluginRegistry()
        self.conflicts.clear()
        action_owners: dict[str, PluginRecord] = {}
        items = (item for source in self.sources for item in source.discover())
        for item in sorted(items, key=lambda item: item.source != "builtin"):
            self._register(item, action_owners)

        self.defaults = {
            record.plugin_id: bool(record.descriptor and record.descriptor.default_enabled is True)
            for record in self.list_plugins()
        }
        if configuration is None:
            revision = (
                self.config.update_switches({}, self.defaults)
                if sync
                else self.config.preview(self.defaults)
            )
            self.config_text, self.switches = revision.text, revision.switches
        else:
            self.config_text = configuration
            self.switches = self.config.validate(configuration)
        for name in sorted(self.switches.keys() - self.defaults.keys()):
            logger.warning("Configured block '%s' is missing; keeping its setting", name)
            self.registry.register(
                PluginRecord(
                    name,
                    "missing",
                    enabled=self.switches[name],
                    health=PluginHealth.UNAVAILABLE,
                    dependency_issue="Plugin package or built-in manifest is missing.",
                )
            )
        self._startup_ids = tuple(
            name for name, enabled in self.switches.items() if enabled and name in self.defaults
        )
        for record in self._ordered_records():
            record.enabled = True

    def _register(
        self, item: PluginDescriptor | DiscoveryFailure, action_owners: dict[str, PluginRecord]
    ) -> None:
        name = item.plugin_id if isinstance(item, DiscoveryFailure) else item.metadata.id
        descriptor = item if isinstance(item, PluginDescriptor) else None
        record = PluginRecord(name, item.source, descriptor, distribution=item.distribution)
        if self.registry.exists(name):
            owner = self.get_plugin(name)
            message = f"Duplicate plugin ID: {name} ({owner.source}, {item.source})"
            if owner.source != "builtin" or record.source != "external":
                self._fail(owner, "discovery", message)
            self._fail(record, "discovery", message)
            self.conflicts.append(record)
            return
        self.registry.register(record)
        if isinstance(item, DiscoveryFailure):
            self._fail(record, "discovery", item.error)
            return
        try:
            self.validator.validate(item)
        except PluginDefinitionError as exc:
            self._fail(record, "validation", exc)
            return
        collisions = {
            action: action_owners[action] for action in item.actions if action in action_owners
        }
        if collisions:
            for action, owner in collisions.items():
                message = f"Duplicate callback action: {action} ({owner.plugin_id}, {name})"
                if owner.source != "builtin" or record.source != "external":
                    self._fail(owner, "validation", message)
                self._fail(record, "validation", message)
            return
        action_owners.update(dict.fromkeys(item.actions, record))

    def list_plugins(self) -> tuple[PluginRecord, ...]:
        return self.registry.list_all()

    def get_plugin(self, plugin_id: str) -> PluginRecord:
        return self.registry.get(plugin_id)

    def enable(self, plugin_id: str) -> None:
        self._set_enabled(plugin_id, True)

    def disable(self, plugin_id: str) -> None:
        self._set_enabled(plugin_id, False)

    def _set_enabled(self, plugin_id: str, enabled: bool) -> None:
        record = self.get_plugin(plugin_id)
        self.config.set_enabled(plugin_id, enabled)
        record.enabled = enabled

    def _ordered_records(self) -> Iterator[PluginRecord]:
        return (self.get_plugin(name) for name in self._startup_ids)

    def startup_records(self) -> tuple[PluginRecord, ...]:
        return tuple(self._ordered_records())

    def running_blocks(self) -> tuple[Block, ...]:
        return tuple(
            record.instance
            for record in self._ordered_records()
            if record.state is PluginState.RUNNING and record.instance is not None
        )

    async def load_enabled(self) -> None:
        for record in self._ordered_records():
            self._load(record)

    async def diagnose(self, plugin_id: str) -> PluginRecord:
        record = self.get_plugin(plugin_id)
        self._load(record)
        return await self.check(plugin_id)

    def _load(self, record: PluginRecord) -> None:
        if self.context_factory is None:
            raise RuntimeError("A plugin context factory is required to load blocks")
        if record.state not in (PluginState.DISCOVERED, PluginState.STOPPED):
            return
        descriptor = record.descriptor
        if descriptor is None:
            return
        try:
            factory = self.context_factory
            if descriptor.source == "external" and descriptor.required_permissions:
                context = factory(descriptor)
                available = (
                    context.broker.permissions
                    if isinstance(context.broker, BrokerClient)
                    else frozenset()
                )
                missing = set(descriptor.required_permissions) - available
                if missing:
                    permissions = ", ".join(sorted(missing))
                    self._update_health(
                        record,
                        f"Required broker permissions unavailable for external plugins: {permissions}",
                    )
                    return

                def approved_context(_descriptor: PluginDescriptor) -> PluginContext:
                    return context

                factory = approved_context

            record.instance = self.loader.create_instance(descriptor, factory)
            record.state = PluginState.LOADED
        except Exception as exc:
            self._fail(record, "load", exc)

    async def setup_all(self) -> None:
        await self._advance("setup", PluginState.LOADED, PluginState.READY)

    async def start_all(self) -> None:
        await self._advance("start", PluginState.READY, PluginState.RUNNING)

    async def _advance(self, hook: str, before: PluginState, after: PluginState) -> None:
        for record in self._ordered_records():
            if record.state is before and record.instance is not None:
                try:
                    async with asyncio.timeout(self.operation_timeout):
                        await getattr(record.instance, hook)()
                    record.state = after
                except Exception as exc:
                    self._fail(record, hook, exc)
                    await self._stop(record)

    async def stop_all(self) -> None:
        for record in reversed(self.startup_records()):
            if record.instance is not None:
                await self._stop(record)

    async def _stop(self, record: PluginRecord) -> None:
        block = record.instance
        if block is None:
            return
        try:
            async with asyncio.timeout(self.operation_timeout):
                await block.stop()
            if record.state is not PluginState.FAILED:
                record.state = PluginState.STOPPED
                record.health = PluginHealth.UNKNOWN
                record.dependency_issue = None
        except Exception as exc:
            if record.state is PluginState.FAILED:
                logger.exception("Plugin %s: cleanup failed", record.plugin_id)
            else:
                self._fail(record, "stop", exc)
        finally:
            record.instance = None

    async def check(self, plugin_id: str) -> PluginRecord:
        record = self.get_plugin(plugin_id)
        block = record.instance
        if block is None:
            return record
        try:
            async with asyncio.timeout(self.operation_timeout):
                issue = await asyncio.to_thread(block.dependency_issue)
            self._update_health(record, issue)
        except Exception as exc:
            self._update_health(record, self._message(exc), PluginHealth.DEGRADED)
        return record

    async def check_enabled(self) -> None:
        await asyncio.gather(*(self.check(record.plugin_id) for record in self._ordered_records()))

    def alert_checks(self) -> tuple[Callable[[], Alert | None], ...]:
        return tuple(
            self._alert_check(record)
            for record in self._ordered_records()
            if record.state is PluginState.RUNNING
            and record.metadata
            and record.metadata.sends_alerts
        )

    def _alert_check(self, record: PluginRecord) -> Callable[[], Alert | None]:
        block = record.instance
        assert block is not None

        def poll() -> Alert | None:
            try:
                alert = block.poll_alert()
            except Exception as exc:
                self._update_health(record, self._message(exc), PluginHealth.DEGRADED)
                raise
            self._update_health(record, block.last_dependency_issue)
            return alert

        return poll

    @staticmethod
    def _update_health(
        record: PluginRecord, issue: str | None, health: PluginHealth | None = None
    ) -> None:
        if issue and record.dependency_issue != issue:
            logger.warning("Plugin %s: %s", record.plugin_id, issue)
        record.dependency_issue = issue
        record.health = health or (PluginHealth.UNAVAILABLE if issue else PluginHealth.HEALTHY)

    @staticmethod
    def _message(exc: Exception) -> str:
        return str(exc) or type(exc).__name__

    def _fail(self, record: PluginRecord, phase: str, error: Exception | str) -> None:
        detail = error if isinstance(error, str) else self._message(error)
        record.state = PluginState.FAILED
        record.health = PluginHealth.UNAVAILABLE
        record.error = f"{phase}: {detail}"
        logger.error("Plugin %s: %s", record.plugin_id, record.error)
