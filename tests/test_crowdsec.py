from datetime import UTC, datetime, timedelta
from logging import getLogger
from unittest.mock import Mock

from bot.api import NullBroker, PluginContext
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


def test_cscli_sudo_requires_explicit_opt_in(monkeypatch):
    run = Mock(return_value=Mock(stdout="[]"))
    monkeypatch.setattr("bot.blocks.crowdsec.subprocess.run", run)
    monkeypatch.setenv("CROWDSEC_CSCLI", "/usr/bin/cscli")
    monkeypatch.delenv("CROWDSEC_USE_SUDO", raising=False)
    assert make_block()._events() == []
    expected = [
        "/usr/bin/cscli",
        "alerts",
        "list",
        "--since",
        "24h",
        "--limit",
        "0",
        "-o",
        "json",
    ]
    assert run.call_args.kwargs["args"] == expected

    monkeypatch.setenv("CROWDSEC_USE_SUDO", "1")
    assert make_block()._events() == []
    assert run.call_args.kwargs["args"] == ["sudo", "-n", "--", *expected]


def test_cscli_runs_through_sudo_with_a_single_argument_list(tmp_path, monkeypatch):
    sudo = tmp_path / "sudo"
    sudo.write_text('#!/bin/sh\n[ "$1" = -n ] && [ "$2" = -- ] || exit 2\nshift 2\nexec "$@"\n')
    sudo.chmod(0o755)
    cscli = tmp_path / "cscli"
    cscli.write_text(
        '#!/bin/sh\n[ "$*" = "alerts list --since 24h --limit 0 -o json" ] || exit 3\n'
        'printf "[]\\n"\n'
    )
    cscli.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("CROWDSEC_USE_SUDO", "1")
    monkeypatch.setenv("CROWDSEC_CSCLI", str(cscli))

    assert make_block()._events() == []
