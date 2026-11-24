"""Compatibility imports and constructor for blocks written before Plugin API v1."""

from logging import getLogger
from typing import Self

from bot.api import AlertProtocol, NullBroker, PluginContext, PluginMetadata, SystemProtocol
from bot.api import Block as PublicBlock
from bot.api.block import safe_delete_message
from bot.api.views import BlockView

__all__ = ["Block", "BlockView", "safe_delete_message"]


class Block(PublicBlock):
    """Accept the legacy class attributes and the (monitor, alerts) constructor."""

    name: str
    title: str
    icon: str
    description: str
    sends_alerts = False

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        cls.metadata = PluginMetadata(
            id=getattr(cls, "name", ""),
            name=getattr(cls, "title", ""),
            description=getattr(cls, "description", ""),
            version="1.0.0",
            icon=getattr(cls, "icon", ""),
            sends_alerts=cls.sends_alerts,
        )

    def __init__(self, monitor: SystemProtocol, alerts: AlertProtocol) -> None:
        super().__init__(
            PluginContext(
                self.name, {}, getLogger(type(self).__module__), monitor, alerts, NullBroker()
            )
        )
        self.monitor = monitor
        self.alerts = alerts

    @classmethod
    def _from_context(cls, context: PluginContext) -> Self:
        block = cls(context.system, context.alerts)
        block.context = context
        return block
