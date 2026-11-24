import json
from subprocess import CompletedProcess
from unittest.mock import Mock

import pytest

from cli.services import (
    CONTAINER,
    UNIT,
    DockerComposeController,
    ServiceError,
    SystemdUserController,
)


def test_systemd_only_targets_user_service_and_matching_config(tmp_path, monkeypatch):
    result = CompletedProcess(
        [],
        0,
        f"LoadState=loaded\nWorkingDirectory={tmp_path}\nEnvironment=HOST_PROC_PATH=/proc\n",
        "",
    )
    run = Mock(return_value=result)
    monkeypatch.setattr("cli.services.subprocess.run", run)
    controller = SystemdUserController()

    controller.ensure_target(tmp_path / "blocks.toml")
    with pytest.raises(ServiceError, match="selected configuration"):
        controller.ensure_target(tmp_path / "other.toml")
    controller.restart()
    assert run.call_args.args[0] == ["systemctl", "--user", "restart", UNIT]
    controller.logs(follow=True)
    assert run.call_args.args[0][-1] == "--follow"
    assert not run.call_args.kwargs["capture_output"]


def test_docker_apply_checks_bind_mount_and_preserves_compose_overrides(tmp_path, monkeypatch):
    compose = tmp_path / "compose.yml"
    override = tmp_path / "socket.yml"
    source = tmp_path / "config"
    data = {
        "services": {
            CONTAINER: {
                "environment": {"BLOCKS_CONFIG": "/data/blocks.toml"},
                "volumes": [{"type": "bind", "source": str(source), "target": "/data"}],
            }
        },
    }
    run = Mock(return_value=CompletedProcess([], 0, json.dumps(data), ""))
    monkeypatch.setattr("cli.services.subprocess.run", run)
    controller = DockerComposeController(tmp_path, [compose, override])
    controller.ensure_target(source / "blocks.toml")
    with pytest.raises(ServiceError, match="selected configuration"):
        controller.ensure_target(tmp_path / "blocks.toml")
    controller.restart()
    assert run.call_args.args[0] == [
        "docker",
        "compose",
        "--project-directory",
        str(tmp_path),
        "-f",
        str(compose),
        "-f",
        str(override),
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
        CONTAINER,
    ]
