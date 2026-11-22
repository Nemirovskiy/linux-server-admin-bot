from unittest.mock import AsyncMock, Mock

import pytest
from telegram.ext import CallbackQueryHandler, CommandHandler

from bot.blocks import select_blocks
from config import settings
from main import BotApplication


def test_invalid_block_switches(tmp_path):
    path = tmp_path / "blocks.toml"
    defaults = {"cpu": True, "docker": False}

    path.write_text('[blocks]\ncpu = "false"\n')
    with pytest.raises(ValueError, match="must be true or false"):
        select_blocks(path, defaults)

    path.write_text("[blocks]\ncup = false\n")
    with pytest.raises(ValueError, match="Unknown blocks: cup"):
        select_blocks(path, defaults)


async def test_switches_control_commands_menu_status_and_alerts(tmp_path, monkeypatch):
    path = tmp_path / "blocks.toml"
    path.write_text("[blocks]\ncpu = false\nmemory = false\ndocker = false\n")
    monkeypatch.setattr("bot.blocks.docker.DockerManager", Mock(side_effect=AssertionError))
    app = BotApplication(path)
    await app.initialize()

    handlers = app.app.handlers[0]
    commands = {
        command
        for handler in handlers
        if isinstance(handler, CommandHandler)
        for command in handler.commands
    }
    assert {"start", "status", "alerts", "disk"} <= commands
    assert not {"docker", "cpu", "memory"} & commands
    assert len(app.health_monitor.checks) == 1

    callback = next(
        handler.callback for handler in handlers if isinstance(handler, CallbackQueryHandler)
    )
    basic = callback.__self__.basic
    button_ids = {
        button.callback_data for row in basic.menu_keyboard().inline_keyboard for button in row
    }
    assert {"cmd_status", "cmd_alerts", "cmd_disk"} <= button_ids
    assert not {"cmd_docker", "cmd_cpu", "cmd_memory"} & button_ids
    assert "/docker" not in await basic.help_text()
    assert "Disk:" in await basic.alerts_text()
    assert "CPU:" not in await basic.alerts_text()

    for action in ("cmd_docker", "docker_start|old-container", "cmd_cpu"):
        query = Mock(data=action, answer=AsyncMock(), edit_message_text=AsyncMock())
        update = Mock(callback_query=query, effective_user=Mock(id=settings.allowed_user_ids[0]))
        await callback(update, Mock())
        assert query.edit_message_text.await_args.args[0] == "This block is disabled."


async def test_status_and_alerts_work_with_all_blocks_disabled(tmp_path):
    path = tmp_path / "blocks.toml"
    names = ("cpu", "memory", "disk", "network", "top", "temp", "uptime", "services", "docker")
    path.write_text("[blocks]\n" + "".join(f"{name} = false\n" for name in names))
    app = BotApplication(path)
    await app.initialize()

    assert app.blocks == ()
    assert app.health_monitor is None
    handlers = app.app.handlers[0]
    basic = next(
        handler.callback.__self__
        for handler in handlers
        if isinstance(handler, CommandHandler) and "status" in handler.commands
    )
    assert "No information blocks enabled" in await basic.status_text()
    assert "No active alerts" in await basic.alerts_text()


async def test_status_uses_shared_format_and_isolates_an_unavailable_block(tmp_path, monkeypatch):
    path = tmp_path / "blocks.toml"
    names = ("disk", "network", "top", "temp", "uptime", "services", "docker")
    path.write_text("[blocks]\n" + "".join(f"{name} = false\n" for name in names))
    monkeypatch.setattr("bot.services.SystemMonitor.get_cpu_metrics", Mock(side_effect=OSError))
    monkeypatch.setattr(
        "bot.services.SystemMonitor.get_memory_metrics", Mock(return_value=Mock(percent=42.5))
    )
    app = BotApplication(path)
    await app.initialize()
    handlers = app.app.handlers[0]
    basic = next(
        handler.callback.__self__
        for handler in handlers
        if isinstance(handler, CommandHandler) and "status" in handler.commands
    )

    status = await basic.status_text()
    assert "*CPU:* Unavailable" in status
    assert r"*Memory:* 42\.5%" in status
    assert "Docker" not in status


async def test_docker_can_be_enabled_independently(tmp_path, monkeypatch):
    path = tmp_path / "blocks.toml"
    path.write_text("[blocks]\ndocker = true\n")
    manager = Mock()
    factory = Mock(return_value=manager)
    monkeypatch.setattr("bot.blocks.docker.DockerManager", factory)
    app = BotApplication(path)
    await app.initialize()

    factory.assert_called_once()
    manager.is_available.assert_not_called()
    handlers = app.app.handlers[0]
    assert any(
        "docker" in handler.commands for handler in handlers if isinstance(handler, CommandHandler)
    )
    callback = next(
        handler.callback for handler in handlers if isinstance(handler, CallbackQueryHandler)
    )
    assert "docker_detail" in callback.__self__.block_actions


async def test_new_file_is_discovered_and_adds_its_nested_button(tmp_path, monkeypatch):
    import sys

    import bot.blocks

    module_name = "example_test_block"
    (tmp_path / f"{module_name}.py").write_text(
        """
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from bot.blocks import Block

class ExampleBlock(Block):
    name = "example_test_block"
    title = "Example"
    icon = "📌"
    description = "Example status"
    extra_actions = ("example_test_block_details",)

    def summary(self):
        return "Available"

    def render(self):
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Details", callback_data="example_test_block_details|more")
        ]])
        return self.view("Overview", keyboard=keyboard)

    async def submenu(self, query, action, value):
        await self.show_view(query, self.view(f"Details {value}"))
"""
    )
    monkeypatch.setattr(bot.blocks, "__path__", [*bot.blocks.__path__, str(tmp_path)])
    path = tmp_path / "blocks.toml"
    path.write_text(f"[blocks]\n{module_name} = true\n")
    app = BotApplication(path)
    try:
        await app.initialize()
    finally:
        sys.modules.pop(f"bot.blocks.{module_name}", None)

    block = app.blocks[0]
    assert block.name == module_name
    assert block.render().keyboard.inline_keyboard[0][0].callback_data.endswith("|more")
    handlers = app.app.handlers[0]
    assert any(
        module_name in handler.commands
        for handler in handlers
        if isinstance(handler, CommandHandler)
    )

    callback = next(
        handler.callback for handler in handlers if isinstance(handler, CallbackQueryHandler)
    )
    query = Mock(
        data=f"{module_name}_details|more",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        message=Mock(photo=()),
    )
    update = Mock(callback_query=query, effective_user=Mock(id=settings.allowed_user_ids[0]))
    await callback(update, Mock())
    assert query.edit_message_text.await_args.args[0] == "📌 *Example*\n\nDetails more"
