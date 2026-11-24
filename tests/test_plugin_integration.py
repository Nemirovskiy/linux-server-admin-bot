import sys
from unittest.mock import AsyncMock, Mock

import pytest

import bot.blocks
from main import BotApplication


async def test_public_api_block_is_discovered_and_started(tmp_path, monkeypatch):
    module_name = "test_plugin_api_example"
    (tmp_path / f"{module_name}.py").write_text(
        """
from bot.api import Block, PluginMetadata

class ExampleBlock(Block):
    metadata = PluginMetadata("example", "Example", "Example status", "1.0.0", "📌")

    def summary(self):
        return self.context.plugin_id

    async def setup(self):
        self.events = ["setup"]

    async def start(self):
        self.events.append("start")

    async def stop(self):
        self.events.append("stop")
"""
    )
    monkeypatch.setattr(bot.blocks, "__path__", [*bot.blocks.__path__, str(tmp_path)])
    config = tmp_path / "blocks.toml"
    config.write_text("[blocks]\nexample = true\n")
    app = BotApplication(config)
    try:
        await app.initialize(check_only=True)
        assert not hasattr(app.blocks[0], "events")
        await app.initialize()
    finally:
        sys.modules.pop(f"bot.blocks.{module_name}", None)

    (block,) = app.blocks
    assert block.render().text == "📌 *Example*\n\nexample"
    telegram = AsyncMock()
    app.app = telegram

    await app.start()
    assert block.events == ["setup", "start"]
    commands = telegram.bot.set_my_commands.await_args.args[0]
    assert (commands[-1].command, commands[-1].description) == ("example", "Example status")

    await app.stop()
    await app.stop()
    assert block.events == ["setup", "start", "stop"]
    telegram.shutdown.assert_awaited_once()


async def test_failed_plugin_start_releases_prepared_blocks_and_telegram(tmp_path):
    app = BotApplication(tmp_path / "blocks.toml")
    telegram = AsyncMock()
    app.app = telegram
    order = []
    first = Mock(
        setup=AsyncMock(),
        start=AsyncMock(),
        stop=AsyncMock(side_effect=lambda: order.append("first")),
    )
    second = Mock(
        setup=AsyncMock(),
        start=AsyncMock(side_effect=RuntimeError("start failed")),
        stop=AsyncMock(side_effect=lambda: order.append("second")),
    )
    app.blocks = (first, second)

    with pytest.raises(RuntimeError, match="start failed"):
        await app.start()

    assert order == ["second", "first"]
    telegram.updater.start_polling.assert_not_awaited()
    telegram.stop.assert_awaited_once()
    telegram.shutdown.assert_awaited_once()
