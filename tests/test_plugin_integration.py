import sys

from telegram.ext import Application, ExtBot

import bot.blocks
from main import BotApplication


async def test_public_api_block_is_discovered_and_started(tmp_path, monkeypatch, offline_telegram):
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
    (tmp_path / f"{module_name}.plugin.toml").write_text(
        f"""[plugin]
id = "example"
name = "Example"
description = "Example status"
icon = "📌"
version = "1.0.0"
entry_point = "bot.blocks.{module_name}:ExampleBlock"
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
        assert not app.blocks
        await app.start()
    finally:
        sys.modules.pop(f"bot.blocks.{module_name}", None)

    (block,) = app.blocks
    assert block.render().text == "📌 *Example*\n\nexample"
    assert block.events == ["setup", "start"]
    commands = ExtBot.set_my_commands.await_args.args[0]
    assert (commands[-1].command, commands[-1].description) == ("example", "Example status")

    await app.stop()
    await app.stop()
    assert block.events == ["setup", "start", "stop"]
    Application.shutdown.assert_awaited_once()


async def test_failed_block_does_not_register_routes_or_prevent_polling(
    tmp_path, monkeypatch, offline_telegram
):
    from unittest.mock import AsyncMock, Mock

    from telegram.ext import CallbackQueryHandler, Updater

    from bot.plugins.models import PluginState
    from config import settings

    async def fail_start(self):
        raise RuntimeError("Failed to start")

    config = tmp_path / "blocks.toml"
    config.write_text("[blocks]\ncpu = true\nmemory = true\n")
    monkeypatch.setattr("bot.blocks.cpu.CpuBlock.start", fail_start)
    app = BotApplication(config)
    await app.initialize()
    await app.start()

    assert app.plugin_manager.get_plugin("cpu").state is PluginState.FAILED
    assert [block.metadata.id for block in app.blocks] == ["memory"]
    Updater.start_polling.assert_awaited_once()
    commands = ExtBot.set_my_commands.await_args.args[0]
    assert {item.command for item in commands}.isdisjoint({"cpu"})
    assert "memory" in {item.command for item in commands}
    assert len(app.health_monitor.checks) == 1
    callback = next(
        handler.callback
        for handler in app.app.handlers[0]
        if isinstance(handler, CallbackQueryHandler)
    )
    query = Mock(data="cmd_cpu", answer=AsyncMock(), edit_message_text=AsyncMock())
    update = Mock(callback_query=query, effective_user=Mock(id=settings.allowed_user_ids[0]))
    await callback(update, Mock())
    assert query.edit_message_text.await_args.args[0] == "This block is unavailable."
    await app.stop()


async def test_all_builtin_blocks_can_be_loaded_in_config_order(
    tmp_path, monkeypatch, offline_telegram
):
    from bot.plugins.models import PluginHealth
    from bot.plugins.sources import BuiltinPluginSource

    names = [descriptor.metadata.id for descriptor in BuiltinPluginSource().discover()]
    names.reverse()
    config = tmp_path / "blocks.toml"
    config.write_text("[blocks]\n" + "".join(f"{name} = true\n" for name in names))
    monkeypatch.setattr("bot.blocks.docker.DockerBlock.dependency_issue", lambda self: None)
    monkeypatch.setattr("bot.blocks.crowdsec.CrowdSecBlock.dependency_issue", lambda self: None)
    app = BotApplication(config)
    await app.initialize()
    await app.start()

    assert len(names) == 10
    assert [block.metadata.id for block in app.plugin_manager.running_blocks()] == names
    assert all(
        record.health is PluginHealth.HEALTHY
        for record in app.plugin_manager.registry.list_running()
    )
    assert len(app.health_monitor.checks) == 4
    await app.stop()
