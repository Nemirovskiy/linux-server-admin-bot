from dataclasses import FrozenInstanceError
from logging import getLogger
from unittest.mock import AsyncMock, Mock

import pytest

from bot.api import (
    PLUGIN_API_VERSION,
    Block,
    BlockView,
    BrokerResult,
    NullBroker,
    PluginContext,
    PluginMetadata,
)
from bot.blocks.base import BlockView as LegacyBlockView


class ExampleBlock(Block):
    metadata = PluginMetadata("example", "Example", "Example status", "2.3.4", "📌")

    def summary(self):
        return str(self.context.config["message"])


def test_metadata_view_and_context():
    config = {"message": "Ready!"}
    context = PluginContext(
        "example", config, getLogger("test.example"), Mock(), Mock(), NullBroker()
    )
    config["message"] = "Changed externally"
    block = ExampleBlock(context)

    assert PLUGIN_API_VERSION == "1.0.0"
    assert block.metadata.api_version == "1.0"
    assert block.metadata.version == "2.3.4"
    assert block.metadata.permissions == ()
    with pytest.raises(FrozenInstanceError):
        block.metadata.name = "Changed"
    with pytest.raises(TypeError):
        context.config["message"] = "Changed"
    view = block.render()
    assert isinstance(view, BlockView)
    assert LegacyBlockView is BlockView
    assert view.text == r"📌 *Example*" + "\n\n" + r"Ready\!"
    assert view.keyboard.inline_keyboard[0][0].callback_data == "menu_main"


async def test_privileged_actions_use_context_broker():
    broker = Mock(execute=AsyncMock(return_value=BrokerResult(True, "Done")))
    context = PluginContext("example", {}, getLogger("test.example"), Mock(), Mock(), broker)

    result = await ExampleBlock(context).execute_privileged("example.read", limit=2)

    assert result.success
    broker.execute.assert_awaited_once_with("example.read", {"limit": 2})


async def test_default_broker_denies_requests():
    context = PluginContext("example", {}, getLogger("test.example"), Mock(), Mock(), NullBroker())
    result = await ExampleBlock(context).execute_privileged("example.read", limit=2)

    assert not result.success
    assert result.message
