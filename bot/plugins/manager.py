"""Coordinate discovery, configuration, and each block's lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Iterator
from logging import getLogger
from typing import TYPE_CHECKING

from bot.api import PluginDefinitionError
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

    def discover(self) -> None:
        if any(record.instance is not None for record in self.list_plugins()):
            raise RuntimeError("Stop the plugins before rediscovering")
        self.registry = PluginRegistry()
        action_owners: dict[str, PluginRecord] = {}
        for source in self.sources:
            for item in source.discover():
                self._register(item, action_owners)

        defaults = {
            record.plugin_id: bool(record.descriptor and record.descriptor.default_enabled is True)
            for record in self.list_plugins()
        }
        config = self.config.sync(defaults)
        for name in config.keys() - defaults.keys():
            logger.warning("Configured block '%s' is missing; keeping its setting", name)
        self._startup_ids = tuple(
            name for name, enabled in config.items() if enabled and name in defaults
        )
        for record in self._ordered_records():
            record.enabled = True

    def _register(
        self, item: PluginDescriptor | DiscoveryFailure, action_owners: dict[str, PluginRecord]
    ) -> None:
        name = item.plugin_id if isinstance(item, DiscoveryFailure) else item.metadata.id
        if self.registry.exists(name):
            record = self.get_plugin(name)
            self._fail(
                record, "discovery", f"Duplicate plugin ID: {name} ({record.source}, {item.source})"
            )
            return
        record = PluginRecord(name, item.source)
        self.registry.register(record)
        if isinstance(item, DiscoveryFailure):
            self._fail(record, "discovery", item.error)
            return
        record.descriptor = item
        try:
            self.validator.validate(item)
        except PluginDefinitionError as exc:
            self._fail(record, "validation", exc)
            return
        for action in item.actions:
            if action in action_owners:
                owner = action_owners[action]
                message = f"Duplicate callback action: {action} ({owner.plugin_id}, {name})"
                self._fail(owner, "validation", message)
                self._fail(record, "validation", message)
            else:
                action_owners[action] = record

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
        if self.context_factory is None:
            raise RuntimeError("A plugin context factory is required to load blocks")
        for record in self._ordered_records():
            if record.state not in (PluginState.DISCOVERED, PluginState.STOPPED):
                continue
            descriptor = record.descriptor
            if descriptor is None:
                continue
            try:
                record.instance = self.loader.create_instance(descriptor, self.context_factory)
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
