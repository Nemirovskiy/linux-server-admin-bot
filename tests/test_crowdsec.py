from datetime import UTC, datetime, timedelta
from logging import getLogger
from unittest.mock import Mock

from bot.api import BrokerResult, NullBroker, PluginContext
from bot.blocks.crowdsec import CrowdSecBlock


def make_block(alerts=None):
    context = PluginContext(
        "crowdsec", {}, getLogger("test.crowdsec"), Mock(), alerts or Mock(), NullBroker()
    )
    return CrowdSecBlock(context)


def test_summary_shows_details_only_if_there_are_alerts():
    block = make_block()
    block._protection_status = Mock(return_value="LAPI доступен")
    block._events = Mock(return_value=[])
    keyboard = block.render().keyboard.inline_keyboard
    assert all(button.callback_data != "crowdsec_details" for row in keyboard for button in row)

    decision = {"scope": "Ip", "type": "ban", "value": "192.0.2.5"}
    event = {"id": 1, "scenario": "test/scenario", "decisions": [decision, decision]}
    block._events.return_value = [event]
    overview = block.render()
    assert "IP с решением ban: 1" in overview.text
    assert overview.keyboard.inline_keyboard[0][0].callback_data == "crowdsec_details"
    assert "test/scenario" in block._details().text


def test_polling_reports_new_detections_once_and_skips_old_and_manual_events():
    alerts = Mock()
    block = make_block(alerts)
    recent = datetime.now(UTC).isoformat()
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    block._events = Mock(
        return_value=[
            {"id": 4, "scenario": "test/simulated", "created_at": recent, "simulated": True},
            {"id": 3, "scenario": "manual", "created_at": recent, "kind": "cscli"},
            {"id": 2, "scenario": "test/new", "created_at": recent},
            {"id": 1, "scenario": "test/old", "created_at": old},
        ]
    )

    assert block.check_alert() is alerts.create_custom_alert.return_value
    message = alerts.create_custom_alert.call_args.args[1]
    assert "Новых алертов: 1" in message
    assert "test/new" in message
    assert block.check_alert() is None
    alerts.create_custom_alert.assert_called_once()


def test_crowdsec_calls_broker_with_registered_actions(monkeypatch):
    block = make_block()
    operation = Mock(
        side_effect=(
            BrokerResult(True, "ok", data=[]),
            BrokerResult(True, "ok"),
            BrokerResult(True, "ok", data={"count": 2}),
        )
    )
    monkeypatch.setattr(block, "execute_privileged_sync", operation)
    assert block._events() == []
    assert "зарегистрировано компонентов блокировки: 2" in block._protection_status()
    assert operation.call_args_list[0].args == ("crowdsec.alerts.read",)
    assert operation.call_args_list[0].kwargs == {"period": "24h"}
    assert [call.args[0] for call in operation.call_args_list] == [
        "crowdsec.alerts.read",
        "crowdsec.status.read",
        "crowdsec.bouncers.read",
    ]


def test_crowdsec_shows_broker_denial():
    block = make_block()
    assert "native broker" in block.dependency_issue()
    assert "Сводка недоступна" in block.summary()
