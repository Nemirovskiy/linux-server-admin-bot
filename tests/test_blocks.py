from io import BytesIO
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.error import TimedOut
from telegram.ext import CallbackQueryHandler, CommandHandler

from bot.blocks import BlockView
from bot.models import CPUMetrics, DiskMetrics, MemoryMetrics
from bot.plugins.config import PluginConfig
from config import settings
from main import BotApplication

pytestmark = pytest.mark.usefixtures("offline_telegram")


async def initialize_app(path):
    app = BotApplication(path)
    await app.initialize()
    await app.start()
    return app


def test_invalid_block_switches(tmp_path):
    path = tmp_path / "blocks.toml"
    defaults = {"cpu": True, "docker": False}

    path.write_text('[blocks]\ncpu = "false"\n')
    with pytest.raises(ValueError, match="must be true or false"):
        PluginConfig(path).sync(defaults)

    path.write_text("[blocks]\ncup = false\n")
    assert PluginConfig(path).sync(defaults) == {"cup": False, "cpu": False, "docker": False}


async def test_switches_control_commands_menu_status_and_alerts(tmp_path, monkeypatch):
    path = tmp_path / "blocks.toml"
    path.write_text("[blocks]\ncpu = false\nmemory = false\ndisk = true\ndocker = false\n")
    monkeypatch.setattr("bot.blocks.docker.DockerManager", Mock(side_effect=AssertionError))
    app = await initialize_app(path)

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
    app = await initialize_app(path)

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
    path.write_text("[blocks]\ncpu = true\nmemory = true\n")
    monkeypatch.setattr("bot.services.SystemMonitor.get_cpu_metrics", Mock(side_effect=OSError))
    monkeypatch.setattr(
        "bot.services.SystemMonitor.get_memory_metrics",
        Mock(
            return_value=MemoryMetrics(
                total=4 * 1024**3, used=2 * 1024**3, available=2 * 1024**3, percent=42.5
            )
        ),
    )
    app = await initialize_app(path)
    handlers = app.app.handlers[0]
    basic = next(
        handler.callback.__self__
        for handler in handlers
        if isinstance(handler, CommandHandler) and "status" in handler.commands
    )

    status = await basic.status_text()
    assert "*CPU:* Unavailable" in status
    assert "💾 *Memory*\n" + r"\[████░░░░░░\] 42\.5% 🟢" in status
    assert "Docker" not in status


async def test_status_command_and_button_show_compact_block_sections(tmp_path, monkeypatch):
    path = tmp_path / "blocks.toml"
    path.write_text("[blocks]\ncpu = true\nmemory = true\ndisk = true\nuptime = true\nnetwork = true\n")
    gib = 1024**3
    metrics = {
        "get_cpu_metrics": CPUMetrics(percent=83.7, count=2, per_cpu=[80.0, 87.4]),
        "get_memory_metrics": MemoryMetrics(total=4 * gib, used=gib, available=3 * gib, percent=25.0),
        "get_disk_metrics": DiskMetrics(total=10 * gib, used=8 * gib, free=2 * gib, percent=80.0),
        "get_uptime_info": {"uptime_seconds": 6 * 86400 + 3 * 3600 + 44 * 60},
    }
    for method, value in metrics.items():
        monkeypatch.setattr(f"bot.services.SystemMonitor.{method}", Mock(return_value=value))
    monkeypatch.setattr(
        "bot.blocks.network.NetworkBlock.summary", lambda self: "1 interface (eth_0)"
    )
    app = await initialize_app(path)
    handlers = app.app.handlers[0]
    command = next(
        handler.callback
        for handler in handlers
        if isinstance(handler, CommandHandler) and "status" in handler.commands
    )
    callback = next(
        handler.callback for handler in handlers if isinstance(handler, CallbackQueryHandler)
    )
    query = Mock(data="cmd_status", answer=AsyncMock(), edit_message_text=AsyncMock())
    update = Mock(
        callback_query=query,
        message=Mock(reply_text=AsyncMock()),
        effective_user=Mock(id=settings.allowed_user_ids[0]),
        effective_chat=None,
    )

    await command(update, Mock())
    await callback(update, Mock())

    expected = (
        "📊 *SYSTEM STATUS*\n\n"
        "🖥️ *CPU*\n"
        r"\[████████░░\] 83\.7% 🔴" "\n"
        "└ Cores: 2\n\n"
        "💾 *Memory*\n"
        r"\[██░░░░░░░░\] 25\.0% 🟢" "\n"
        r"└ 1\.0GB / 4\.0GB" "\n\n"
        "💿 *Disk*\n"
        r"\[████████░░\] 80\.0% 🟡" "\n"
        r"└ 8\.0GB / 10\.0GB" "\n\n"
        "🕐 *Uptime:* 6d 3h 44m\n\n"
        r"🌐 *Network:* 1 interface \(eth\_0\)"
    )
    update.message.reply_text.assert_awaited_once_with(expected, parse_mode="MarkdownV2")
    assert query.edit_message_text.await_args.args == (expected,)
    assert query.edit_message_text.await_args.kwargs["parse_mode"] == "MarkdownV2"


async def test_status_back_opens_menu_if_acknowledgement_times_out(tmp_path):
    path = tmp_path / "blocks.toml"
    path.write_text("[blocks]\n")
    app = await initialize_app(path)
    callback = next(
        handler.callback for handler in app.app.handlers[0] if isinstance(handler, CallbackQueryHandler)
    )
    query = Mock(
        data="cmd_status", answer=AsyncMock(), edit_message_text=AsyncMock(), message=Mock(photo=())
    )
    update = Mock(callback_query=query, effective_user=Mock(id=settings.allowed_user_ids[0]))
    await callback(update, Mock())
    assert "SYSTEM STATUS" in query.edit_message_text.await_args.args[0]

    query.data = "menu_main"
    query.answer.side_effect = TimedOut()
    query.edit_message_text.reset_mock()

    await callback(update, Mock())

    basic = callback.__self__.basic
    query.edit_message_text.assert_awaited_once_with(
        basic.menu_text(), parse_mode="MarkdownV2", reply_markup=basic.menu_keyboard()
    )


async def test_docker_can_be_enabled_independently(tmp_path, monkeypatch):
    path = tmp_path / "blocks.toml"
    path.write_text("[blocks]\ndocker = true\n")
    manager = Mock()
    factory = Mock(return_value=manager)
    monkeypatch.setattr("bot.blocks.docker.DockerManager", factory)
    app = await initialize_app(path)

    factory.assert_called_once()
    manager.is_available.assert_called_once()
    handlers = app.app.handlers[0]
    assert any(
        "docker" in handler.commands for handler in handlers if isinstance(handler, CommandHandler)
    )
    callback = next(
        handler.callback for handler in handlers if isinstance(handler, CallbackQueryHandler)
    )
    assert "docker_detail" in callback.__self__.block_actions


async def test_docker_page_routes_through_the_registered_submenu(tmp_path, monkeypatch):
    path = tmp_path / "blocks.toml"
    path.write_text("[blocks]\ndocker = true\n")
    manager = Mock()
    manager.is_available.return_value = True
    manager.get_containers_page.return_value = ([], 0, 0)
    monkeypatch.setattr("bot.blocks.docker.DockerManager", Mock(return_value=manager))
    app = await initialize_app(path)
    callback = next(
        handler.callback for handler in app.app.handlers[0] if isinstance(handler, CallbackQueryHandler)
    )
    query = Mock(
        data="docker_page|2", answer=AsyncMock(), edit_message_text=AsyncMock(), message=Mock(photo=())
    )
    update = Mock(callback_query=query, effective_user=Mock(id=settings.allowed_user_ids[0]))

    await callback(update, Mock())

    manager.get_containers_page.assert_called_once_with(page=2, per_page=10)
    assert "No containers found" in query.edit_message_text.await_args.args[0]


async def test_block_command_and_callback_present_chart(tmp_path, monkeypatch):
    path = tmp_path / "blocks.toml"
    path.write_text("[blocks]\ncpu = true\n")
    view = BlockView("CPU chart", photo=BytesIO(b"chart"), chart_caption="Caption")
    monkeypatch.setattr("bot.blocks.cpu.CpuBlock.render", lambda self: view)
    app = await initialize_app(path)
    command = next(
        handler.callback for handler in app.app.handlers[0]
        if isinstance(handler, CommandHandler) and "cpu" in handler.commands
    )
    callback = next(
        handler.callback for handler in app.app.handlers[0] if isinstance(handler, CallbackQueryHandler)
    )
    message = Mock(reply_text=AsyncMock(), reply_photo=AsyncMock(), delete=AsyncMock())
    query = Mock(data="cmd_cpu", answer=AsyncMock(), message=message)
    update = Mock(
        callback_query=query, message=message, effective_message=message, effective_chat=None,
        effective_user=Mock(id=settings.allowed_user_ids[0]),
    )

    await command(update, Mock())

    message.reply_text.assert_awaited_once_with(
        view.text, parse_mode="MarkdownV2", reply_markup=view.keyboard
    )
    message.reply_photo.assert_awaited_once_with(photo=view.photo, caption=view.chart_caption)
    message.reply_photo.reset_mock()

    await callback(update, Mock())

    message.reply_photo.assert_awaited_once_with(
        photo=view.photo, caption=view.text, parse_mode="MarkdownV2", reply_markup=view.keyboard
    )
    message.delete.assert_awaited_once()


async def test_enabled_block_sends_threshold_alert_once_during_cooldown(tmp_path, monkeypatch):
    path = tmp_path / "blocks.toml"
    path.write_text("[blocks]\ndisk = true\n")
    monkeypatch.setattr(
        "bot.services.SystemMonitor.get_disk_metrics",
        Mock(return_value=DiskMetrics(total=100, used=100, free=0, percent=100)),
    )
    app = await initialize_app(path)
    monitor = app.health_monitor
    send_message = AsyncMock()
    monitor.bot_app = Mock(bot=Mock(send_message=send_message))
    monitor.register_alert_chat(123)

    await monitor.check_system_health()
    await monitor.check_system_health()

    send_message.assert_awaited_once()
    assert send_message.await_args.kwargs["chat_id"] == 123
    assert send_message.await_args.kwargs["parse_mode"] == "MarkdownV2"
    assert r"Current: 100\.0%" in send_message.await_args.kwargs["text"]


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
    (tmp_path / f"{module_name}.plugin.toml").write_text(
        f'''[plugin]
id = "{module_name}"
name = "Example"
description = "Example status"
icon = "📌"
version = "1.0.0"
entry_point = "bot.blocks.{module_name}:ExampleBlock"
extra_actions = ["{module_name}_details"]
'''
    )
    monkeypatch.setattr(bot.blocks, "__path__", [*bot.blocks.__path__, str(tmp_path)])
    path = tmp_path / "blocks.toml"
    path.write_text(f"[blocks]\n{module_name} = true\n")
    try:
        app = await initialize_app(path)
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


def test_working_config_is_seeded_and_updated_without_replacing_values(tmp_path):
    template = tmp_path / "blocks.toml.example"
    template.write_text("[blocks]\ncpu = true\n")
    path = tmp_path / "config" / "blocks.toml"
    config = PluginConfig(path, template)
    assert config.sync({"cpu": False, "disk": False}) == {"cpu": True, "disk": False}
    assert path.read_text() == "[blocks]\ncpu = true\ndisk = false\n"

    original = "[blocks]\n# Only disk on this server\ndisk = true\ncpu = false"
    path.write_text(original)
    assert config.sync({"cpu": False, "disk": False, "example": False}) == {
        "disk": True, "cpu": False, "example": False
    }
    assert path.read_text() == original + "\nexample = false\n"

    path.write_text("[blocks]\ncpu = invalid\n")
    with pytest.raises(ValueError):
        config.sync({"cpu": False, "disk": False, "example": False})
    assert path.read_text() == "[blocks]\ncpu = invalid\n"


async def test_config_check_reports_missing_dependencies(tmp_path, monkeypatch):
    path = tmp_path / "blocks.toml"
    path.write_text("[blocks]\ndocker = true\n")
    monkeypatch.setattr(
        "bot.blocks.docker.DockerBlock.dependency_issue",
        lambda self: "Docker socket unavailable",
    )
    with pytest.raises(ValueError, match="docker: Docker socket unavailable"):
        await BotApplication(path).initialize(check_only=True)


def test_removed_crowdsec_switch_is_preserved_and_ignored(tmp_path):
    path = tmp_path / "blocks.toml"
    original = "[blocks]\ncpu = true\ncrowdsec = true\n"
    path.write_text(original)
    assert PluginConfig(path).sync({"cpu": False}) == {"cpu": True, "crowdsec": True}
    assert path.read_text() == original
