import json
import os
import time
from unittest.mock import Mock

import pytest

from bot.plugins.config import PluginConfig
from bot.runtime_status import config_digest, process_identity, read_status
from cli.management import PluginManagement
from cli.services import ServiceError


@pytest.fixture
def deployment(tmp_path):
    config = PluginConfig(tmp_path / "blocks.toml")
    management = PluginManagement(config, ready_timeout=0.02)
    first = "[blocks]\ncpu = true # deployed baseline\n"
    config.path.write_text(first)
    original = management.snapshot().text
    config.path.write_text(original)
    management.directory.mkdir(parents=True)

    def publish(text, run_id, *, broken=False):
        switches = config.validate(text)
        data = {
            "ready": True,
            "pid": os.getpid(),
            "process_identity": process_identity(os.getpid()),
            "run_id": run_id,
            "updated_at": time.time(),
            "config_hash": config_digest(text),
            "plugins": {
                name: {
                    "enabled": enabled,
                    "state": "running" if enabled else "discovered",
                    "health": "unavailable" if broken and enabled else "healthy",
                    "has_issue": bool(broken and enabled),
                }
                for name, enabled in switches.items()
            },
        }
        (management.directory / "running.toml").write_text(text)
        (management.directory / "status.json").write_text(json.dumps(data))
        return data

    controller = Mock()
    controller.status.return_value = {"backend": "systemd", "state": "active", "pid": os.getpid()}
    management.diagnose = Mock(return_value=[])
    publish(original, "original")
    return management, controller, original, publish


def test_apply_waits_for_runtime_and_keeps_previous_config(deployment):
    management, controller, original, publish = deployment
    management.change({"disk": True})
    desired = management.config.snapshot()
    assert management.status(controller)["pending_restart"]
    controller.restart.side_effect = lambda: publish(management.config.snapshot(), "new")

    result = management.apply(controller)

    assert result["config_hash"] == config_digest(desired)
    assert not management.status(controller)["pending_restart"]
    assert (management.directory / "blocks.last-good.toml").read_text() == desired
    assert next(management.directory.glob("blocks-*.toml")).read_text() == original
    management.rollback()
    assert management.config.snapshot() == original
    assert management.status(controller)["pending_restart"]


def test_failed_precheck_does_not_restart(deployment):
    management, controller, _, _ = deployment
    management.diagnose.return_value = [
        {"id": "docker", "ok": False, "error": None, "dependency_issue": "Socket missing"}
    ]
    with pytest.raises(ValueError, match="docker: Socket missing"):
        management.apply(controller)
    controller.restart.assert_not_called()


def test_failed_readiness_restores_and_waits_for_previous_config(deployment):
    management, controller, original, publish = deployment
    management.change({"disk": True})
    runs = iter(("broken", "recovered"))

    def restart():
        run = next(runs)
        return publish(management.config.snapshot(), run, broken=run == "broken")

    controller.restart.side_effect = restart

    with pytest.raises(ServiceError, match="Previous configuration restored and service is ready"):
        management.apply(controller)
    assert management.config.snapshot() == original
    assert controller.restart.call_count == 2
    assert read_status(management.directory)["run_id"] == "recovered"


def test_previous_readiness_report_is_not_enough(deployment):
    management, controller, _, _ = deployment
    management.ready_timeout = 0
    with pytest.raises(ServiceError, match="Recovery failed: Timed out"):
        management.apply(controller)
    assert controller.restart.call_count == 2


def test_recovery_preserves_external_edits(deployment):
    management, controller, _, _ = deployment
    management.change({"disk": True})
    external = "[blocks]\ncpu = false # someone edited this while restarting\n"

    def restart():
        management.config.path.write_text(external)
        raise ServiceError("Start failed")

    controller.restart.side_effect = restart

    with pytest.raises(ServiceError, match="current contents were preserved"):
        management.apply(controller)
    assert management.config.path.read_text() == external
    assert controller.restart.call_count == 1


def test_status_rejects_stale_snapshot_and_pid_reuse(deployment, monkeypatch):
    management, _, _, _ = deployment
    path = management.directory / "status.json"
    snapshot = json.loads(path.read_text())
    monkeypatch.setattr("bot.runtime_status.process_identity", lambda _: "other process")
    assert read_status(management.directory) is None
    monkeypatch.setattr(
        "bot.runtime_status.process_identity", lambda _: snapshot["process_identity"]
    )
    assert read_status(management.directory)
    snapshot["updated_at"] -= 99
    path.write_text(json.dumps(snapshot))
    assert read_status(management.directory) is None


async def test_runtime_publisher_reports_startup_configuration_and_invalidates_on_stop(tmp_path):
    from bot.plugins.models import PluginHealth, PluginRecord, PluginState
    from bot.runtime_status import RuntimeStatus

    config = tmp_path / "blocks.toml"
    text = "[blocks]\ncpu = true\n"
    record = PluginRecord(
        "cpu",
        "builtin",
        enabled=True,
        state=PluginState.RUNNING,
        health=PluginHealth.HEALTHY,
        error="secret detail",
    )
    manager = Mock(config_text=text, list_plugins=Mock(return_value=(record,)))
    writer = RuntimeStatus(config, manager)
    writer.start()
    try:
        status = read_status(writer.directory)
        assert status["config_hash"] == config_digest(text)
        assert status["plugins"]["cpu"]["has_issue"]
        assert "secret detail" not in (writer.directory / "status.json").read_text()
        assert (writer.directory / "running.toml").read_text() == text
    finally:
        await writer.close()
    assert read_status(writer.directory) is None


def test_rollback_can_restore_baseline_before_first_apply(deployment):
    management, controller, original, _ = deployment
    management.change({"disk": True})
    (management.directory / "status.json").unlink()
    management.rollback()
    assert management.config.snapshot() == original
    controller.restart.assert_not_called()
