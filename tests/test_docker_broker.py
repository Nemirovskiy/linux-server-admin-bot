from logging import getLogger
from unittest.mock import AsyncMock, Mock

from bot.api import BrokerResult, NullBroker, PluginContext
from bot.blocks.docker import DockerBlock

CONTAINER = "b" * 64


def block():
    return DockerBlock(
        PluginContext("docker", {}, getLogger("test.docker"), Mock(), Mock(), NullBroker())
    )


def test_docker_buttons_carry_full_id_within_telegram_limit(monkeypatch):
    docker = block()
    data = {
        "id": CONTAINER, "name": "service", "image": "service:stable", "status": "running",
    }
    sync = Mock(side_effect=[
        BrokerResult(True, "OK", data=[data]),
        BrokerResult(True, "OK", data=data),
        BrokerResult(True, "OK", data={"cpu_percent": 6.4}),
    ])
    monkeypatch.setattr(docker, "execute_privileged_sync", sync)
    overview = docker._container_list()
    entry = overview.keyboard.inline_keyboard[0][0].callback_data
    assert len(entry.encode()) <= 64
    detail = docker._container_detail(entry.split("|", 1)[1])
    assert "CPU" in detail.text
    assert all(
        len(button.callback_data.encode()) <= 64
        for row in detail.keyboard.inline_keyboard for button in row
    )
    assert sync.call_args_list[1].kwargs == {"container_id": CONTAINER}
    assert sync.call_args_list[2].kwargs == {"container_id": CONTAINER}


async def test_docker_denial_stays_visible_and_old_short_buttons_do_not_execute(monkeypatch):
    from bot.blocks.docker import _encode_id

    docker = block()
    operation = AsyncMock(return_value=BrokerResult(False, "Grant restart.", "PERMISSION_DENIED"))
    show = AsyncMock()
    monkeypatch.setattr(docker, "execute_privileged", operation)
    monkeypatch.setattr(docker, "show_view", show)
    query = Mock(edit_message_text=AsyncMock())
    await docker._handle_docker_action(query, CONTAINER[:12], "restart")
    operation.assert_not_awaited()
    assert "outdated" in show.await_args.args[1].text

    await docker._handle_docker_action(query, _encode_id(CONTAINER), "restart")
    operation.assert_awaited_once_with("docker.containers.restart", container_id=CONTAINER)
    assert "Grant restart" in show.await_args.args[1].text
    assert show.await_count == 2
