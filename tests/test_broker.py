import json
import sys
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from bot.api.broker import NullBroker
from bot.broker_client import BrokerClient
from broker.actions import OWNERS
from broker.executor import run_command
from broker.policy import Policy
from broker.protocol import MAX_REQUEST, BrokerError, Request
from broker.security import Caller, sudo_caller, trusted
from broker.worker import dispatch

ID = "a" * 64
OTHER_ID = "b" * 64
REQUEST_ID = "f" * 32


def make_policy(uid=1000):
    return Policy.parse(
        f"""[policy]
version = 1
default = "deny"
caller_uids = [{uid}]
[plugins.docker]
enabled = true
allow = ["docker.containers.read", "docker.containers.inspect", "docker.containers.restart"]
containers = ["{ID}"]
[plugins.crowdsec]
enabled = true
allow = ["crowdsec.alerts.read"]
""".encode(),
        OWNERS,
    )


def request(plugin, action, **params):
    return Request(REQUEST_ID, plugin, action, params).encode()


@pytest.fixture
def worker():
    def send(raw, *, uid=1000, output=b"", policy=None):
        audit = Mock()
        runner = Mock(return_value=output)
        result = dispatch(
            raw,
            identify=lambda: Caller(uid, "bot"),
            policy_loader=lambda: policy or make_policy(),
            runner=runner,
            auditor=audit,
        )
        return json.loads(result), runner, audit

    return send


@pytest.mark.parametrize(
    ("raw", "uid", "code"),
    [
        (request("docker", "docker.containers.read"), 1001, "PERMISSION_DENIED"),
        (request("docker", "system.shell.run"), 1000, "UNKNOWN_ACTION"),
        (request("external", "docker.containers.read"), 1000, "PERMISSION_DENIED"),
        (request("docker", "docker.containers.stop", container_id=ID), 1000, "PERMISSION_DENIED"),
        (
            request("docker", "docker.containers.restart", container_id=OTHER_ID),
            1000,
            "PERMISSION_DENIED",
        ),
        (
            request("docker", "docker.containers.restart", container_id="--help"),
            1000,
            "INVALID_PARAMS",
        ),
        (
            request("docker", "docker.containers.restart", container_id="a" * 12),
            1000,
            "INVALID_PARAMS",
        ),
        (request("docker", "docker.containers.read", path="/etc/shadow"), 1000, "INVALID_PARAMS"),
        (request("crowdsec", "crowdsec.alerts.read", period="24h"), 1000, "UNKNOWN_ACTION"),
        (request("docker", "docker.containers.read") + b"x", 1000, "INVALID_PARAMS"),
        (
            request("docker", "docker.containers.read").replace(b'version":1', b'version":2'),
            1000,
            "INCOMPATIBLE_PROTOCOL",
        ),
        (b"x" * (MAX_REQUEST + 1), 1000, "INVALID_PARAMS"),
        (b'{"protocol_version":1,"protocol_version":1}', 1000, "INVALID_PARAMS"),
    ],
)
def test_denied_requests_never_execute_and_are_audited(worker, raw, uid, code):
    result, runner, auditor = worker(raw, uid=uid)
    assert result["code"] == code
    runner.assert_not_called()
    event = auditor.call_args.args[0]
    assert event["result"] == code
    assert event["system_uid"] == uid
    assert "params" not in event


def test_docker_scope_and_output_allowlist(worker):
    raw = b"\n".join(
        json.dumps(
            {"ID": container, "Names": "app", "State": "running", "Env": ["TOKEN=secret"]}
        ).encode()
        for container in (ID, OTHER_ID)
    )
    result, runner, auditor = worker(request("docker", "docker.containers.read"), output=raw)
    assert result["code"] == "OK"
    assert result["data"] == [
        {"id": ID, "name": "app", "image": "", "status": "running", "ports": ""}
    ]
    assert "secret" not in str(result) + str(auditor.call_args)
    runner.assert_called_once()


def test_docker_mutation_checks_identity_and_does_not_retry(monkeypatch):
    monkeypatch.setattr("broker.worker.mutation_lock", nullcontext)
    audit = Mock()
    runner = Mock(side_effect=(ID.encode() + b"\n", BrokerError("TIMEOUT", "timed out")))
    args = {
        "identify": lambda: Caller(1000, "bot"),
        "policy_loader": make_policy,
        "runner": runner,
        "auditor": audit,
    }
    result = json.loads(
        dispatch(request("docker", "docker.containers.restart", container_id=ID), **args)
    )
    assert result["code"] == "TIMEOUT"
    assert runner.call_count == 2
    assert runner.call_args.args[0][-3:] == ("container", "restart", ID)

    runner.reset_mock(side_effect=True)
    runner.return_value = OTHER_ID.encode()
    result = json.loads(
        dispatch(request("docker", "docker.containers.restart", container_id=ID), **args)
    )
    assert result["code"] == "PERMISSION_DENIED"
    runner.assert_called_once()


def test_retired_crowdsec_policy_preserves_docker_access():
    policy = make_policy()
    assert "crowdsec" not in policy.plugins
    assert "docker.containers.read" in policy.authorize("docker", "docker.containers.read").allow


def test_default_policy_rejects_unassigned_actions():
    with pytest.raises(ValueError, match="unrelated"):
        Policy.parse(
            b'[policy]\nversion=1\ndefault="deny"\ncaller_uids=[1000]\n'
            b'[plugins.crowdsec]\nenabled=true\nallow=["docker.containers.read"]',
            OWNERS,
        )


def test_identity_cannot_be_claimed_by_unprivileged_process(monkeypatch):
    monkeypatch.setattr("broker.security.os.getuid", lambda: 1000)
    monkeypatch.setattr("broker.security.os.geteuid", lambda: 1000)
    monkeypatch.setenv("SUDO_UID", "1000")
    monkeypatch.setenv("SUDO_USER", "bot")
    with pytest.raises(BrokerError, match="installed sudo"):
        sudo_caller()


def test_writable_parent_is_untrusted(tmp_path):
    candidate = tmp_path / "file"
    candidate.write_text("file")
    with pytest.raises(ValueError, match="Untrusted ownership"):
        trusted(candidate)


def test_executor_limits_timeout_and_output_without_root(monkeypatch):
    monkeypatch.setattr("broker.executor.executable", lambda path: path)
    python = sys.executable
    assert run_command((python, "-c", "print('ok')"), 2) == b"ok\n"
    with pytest.raises(BrokerError) as timeout:
        run_command((python, "-c", "import time; time.sleep(10)"), 0.05)
    assert timeout.value.code == "TIMEOUT"
    with pytest.raises(BrokerError) as limit:
        run_command((python, "-c", "import sys; sys.stderr.write('x' * 1200000)"), 2)
    assert limit.value.code == "RESULT_TOO_LARGE"


async def test_scoped_broker_client_async_and_null():
    transport = Mock()
    transport.exchange.side_effect = lambda raw: dispatch(
        raw,
        identify=lambda: Caller(1000, "bot"),
        policy_loader=make_policy,
        runner=lambda *_: b"",
        auditor=lambda _: None,
    )
    client = BrokerClient("docker", transport, frozenset({"docker.containers.read"}))
    result = await client.execute("docker.containers.read", {})
    assert result.success and result.data == []
    assert (await client.execute("docker.containers.restart", {})).code == "PERMISSION_DENIED"
    assert client.execute_sync("docker.containers.read", {}).code == "BROKER_UNAVAILABLE"
    assert (await NullBroker().execute("docker.containers.read", {})).code == "BROKER_UNAVAILABLE"
    transport.exchange.assert_called_once()


def test_client_does_not_accept_a_response_for_another_request():
    reply = {
        "protocol_version": 1,
        "request_id": "0" * 32,
        "success": True,
        "code": "OK",
        "message": "OK",
        "data": ["unexpected"],
        "duration_ms": 1,
    }
    transport = Mock(exchange=Mock(return_value=json.dumps(reply).encode()))
    client = BrokerClient("docker", transport, frozenset({"docker.containers.read"}))
    result = client.execute_sync("docker.containers.read", {})
    assert result.code == "BROKER_UNAVAILABLE"
    assert result.data is None
    transport.exchange.assert_called_once()


def test_config_scope_binds_the_manifest_id_and_permissions():
    from bot.plugins.context import PluginContextFactory
    from bot.plugins.sources import BuiltinPluginSource

    descriptors = {item.metadata.id: item for item in BuiltinPluginSource().discover()}
    transport = Mock()
    factory = PluginContextFactory(Mock(), Mock(), transport)
    docker = factory(descriptors["docker"]).broker
    assert docker.plugin_id == "docker"
    assert docker.permissions == frozenset(descriptors["docker"].metadata.permissions)
    assert isinstance(factory(descriptors["cpu"]).broker, NullBroker)
    assert factory(descriptors["docker"]).broker is not docker

    from dataclasses import replace

    # External manifests cannot reuse grants from an older installed helper.
    assert isinstance(factory(replace(descriptors["docker"], source="external")).broker, NullBroker)


def test_installed_admin_policy_grant_and_revoke_are_atomic(tmp_path, monkeypatch):
    from broker.admin import administer
    from broker.policy import load_policy

    file = tmp_path / "permissions.toml"
    file.write_text('[policy]\nversion=1\ndefault="deny"\ncaller_uids=[1000]\n')
    monkeypatch.setattr("broker.admin.POLICY_PATH", file)
    monkeypatch.setattr("broker.admin.trusted", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("broker.policy.trusted", lambda *_args, **_kwargs: None)

    with pytest.raises(SystemExit) as rejected:
        administer(["grant", "docker", "docker.containers.restart"])
    assert rejected.value.code == 1
    assert not load_policy(file, OWNERS).plugins
    administer(["grant", "docker", "docker.containers.restart", "--container", ID])
    grant = load_policy(file, OWNERS).plugins["docker"]
    assert grant.containers == {ID}
    assert grant.allow == {"docker.containers.restart"}
    administer(["revoke", "docker", "docker.containers.restart"])
    assert not load_policy(file, OWNERS).plugins["docker"].allow
    assert file.stat().st_mode & 0o777 == 0o600


def test_sudo_caller_requires_matching_system_account(monkeypatch):
    import types

    monkeypatch.setattr("broker.security.os.getuid", lambda: 0)
    monkeypatch.setattr("broker.security.os.geteuid", lambda: 0)
    monkeypatch.setattr(
        "broker.security.pwd.getpwuid", lambda _: types.SimpleNamespace(pw_name="serverbot")
    )
    monkeypatch.setenv("SUDO_UID", "1000")
    monkeypatch.setenv("SUDO_USER", "serverbot")
    assert sudo_caller() == Caller(1000, "serverbot")
    monkeypatch.setenv("SUDO_USER", "other")
    with pytest.raises(BrokerError, match="Invalid sudo caller"):
        sudo_caller()


def test_docker_executor_rejects_nonempty_configuration_before_spawning(tmp_path, monkeypatch):
    from broker.actions import DOCKER

    (tmp_path / "config.json").write_text('{"cliPluginsExtraDirs":["/tmp"]}')
    monkeypatch.setattr("broker.executor.DOCKER_CONFIG", str(tmp_path))
    monkeypatch.setattr("broker.executor.trusted", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("broker.executor.executable", lambda path: path)
    spawn = Mock()
    monkeypatch.setattr("broker.executor.subprocess.Popen", spawn)
    with pytest.raises(BrokerError) as failure:
        run_command((*DOCKER, "container", "ls"), 1)
    assert failure.value.code == "INVALID_POLICY"
    spawn.assert_not_called()


def test_external_command_uses_approved_artifact_and_typed_args(worker, monkeypatch):
    from dataclasses import replace

    from broker.admin import _format

    action = "crowdsec.decisions.list"
    spec = {
        "argv": ["/usr/bin/cscli", "decisions", "list", "--since", "{hours}h"],
        "parameters": {"hours": {"min": 1, "max": 168}},
    }
    policy = replace(
        make_policy(),
        external={"crowdsec": {"sha256": ID, "operations": {action: spec}}},
    )
    policy = Policy.parse(_format(policy), OWNERS)
    monkeypatch.setattr("broker.operations.executable", lambda path: path)
    for hours in (24, 48):
        result, runner, _ = worker(
            Request(REQUEST_ID, "crowdsec", action, {"hours": hours}, ID).encode(),
            policy=policy,
            output=b"decisions",
        )
        assert result["data"] == "decisions"
        runner.assert_called_once_with(
            ("/usr/bin/cscli", "decisions", "list", "--since", f"{hours}h"), 8
        )
    for params in (
        {"hours": True},
        {"hours": 169},
        {"hours": "--config"},
        {"hours": 24, "argv": []},
    ):
        result, runner, _ = worker(
            Request(REQUEST_ID, "crowdsec", action, params, ID).encode(), policy=policy
        )
        assert result["code"] == "INVALID_PARAMS"
        runner.assert_not_called()
    for artifact, active_policy in ((OTHER_ID, policy), (None, policy), (ID, make_policy())):
        result, runner, _ = worker(
            Request(REQUEST_ID, "crowdsec", action, {"hours": 24}, artifact).encode(),
            policy=active_policy,
        )
        assert result["code"] == "PERMISSION_DENIED"
        runner.assert_not_called()


def test_external_docker_alias_keeps_container_scope(worker, monkeypatch):
    from dataclasses import replace

    action = "demo.docker.restart"
    policy = replace(
        make_policy(),
        external={
            "demo": {
                "sha256": ID,
                "operations": {
                    action: {"docker_action": "docker.containers.restart", "containers": [ID]},
                },
            },
        },
    )
    monkeypatch.setattr("broker.worker.mutation_lock", nullcontext)
    result, runner, _ = worker(
        Request(REQUEST_ID, "demo", action, {"container_id": ID}, ID).encode(),
        policy=policy,
        output=ID.encode(),
    )
    assert result["success"]
    assert runner.call_args.args[0][-3:] == ("container", "restart", ID)
    result, runner, _ = worker(
        Request(REQUEST_ID, "demo", action, {"container_id": OTHER_ID}, ID).encode(), policy=policy
    )
    assert result["code"] == "PERMISSION_DENIED"
    runner.assert_not_called()


def test_external_approval_requires_terminal_confirmation_and_can_be_revoked(
    tmp_path, monkeypatch, capsys
):
    import io

    from broker.admin import _format, administer
    from broker.policy import load_policy

    path = tmp_path / "permissions.toml"
    path.write_bytes(_format(make_policy()))
    monkeypatch.setattr("broker.admin.POLICY_PATH", path)
    monkeypatch.setattr("broker.admin.trusted", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("broker.policy.trusted", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("broker.operations.executable", lambda path: path)
    monkeypatch.setattr(
        "broker.admin.run_command",
        lambda *_: json.dumps({"ID": ID, "Names": "web"}).encode(),
    )
    operation = "demo.status.read"
    proposal = json.dumps({
        "sha256": ID,
        "operations": {
            operation: {"argv": ["/usr/bin/cscli", "version"], "parameters": {}},
            "demo.restart": {"docker_action": "docker.containers.restart", "containers": [ID]},
        },
    })
    response = ["no"]
    monkeypatch.setattr("broker.admin.open", lambda *_args, **_kwargs: io.StringIO(response[0]), raising=False)
    args = ["approve-external", "demo", "--proposal", proposal]
    assert administer(args) == 2
    assert not load_policy(path, OWNERS).external
    stderr = capsys.readouterr().err
    assert f"Wheel SHA-256: {ID}" in stderr
    assert f"'web' ({ID})" in stderr
    assert "Restart the selected Docker containers" in stderr
    assert "/usr/bin/cscli version" in stderr
    assert '"operations"' not in stderr
    response[0] = "approve"
    assert administer(args) == 0
    assert operation in load_policy(path, OWNERS).external["demo"]["operations"]
    assert administer(["revoke-external", "demo"]) == 0
    assert not load_policy(path, OWNERS).external


def test_external_context_requires_matching_installed_wheel(tmp_path):
    from dataclasses import replace

    from bot.api import PluginMetadata
    from bot.plugins.context import PluginContextFactory
    from bot.plugins.models import DistributionInfo, PluginDescriptor

    action = "demo.status.read"
    operations = {action: {"argv": ["/usr/bin/cscli", "version"], "parameters": {}}}
    descriptor = PluginDescriptor(
        metadata=PluginMetadata("demo", "Demo", "Demo", "1.0", "1.1", permissions=(action,)),
        source="external",
        entry_point="demo:Block",
        distribution=DistributionInfo("Demo_Package", "1.0", None, (), artifact_sha256=ID),
        required_permissions=(action,),
        broker_operations=operations,
    )
    (tmp_path / "lsab-installed-plugins.json").write_text(json.dumps({"demo": {
        "sha256": ID,
        "version": "1.0",
        "distribution": "demo-package",
        "source": {"editable": False},
        "broker_operations": operations,
    }}))
    policy = replace(make_policy(), external={"demo": {"sha256": ID, "operations": operations}})
    transport = Mock()
    transport.exchange.side_effect = lambda raw: dispatch(
        raw, identify=lambda: Caller(1000, "bot"), policy_loader=lambda: policy, auditor=Mock()
    )
    factory = PluginContextFactory(Mock(), Mock(), transport, environment=tmp_path)
    broker = factory(descriptor).broker
    assert isinstance(broker, BrokerClient)
    assert broker.permissions == {action}
    assert broker.artifact == ID
    different = replace(descriptor.distribution, artifact_sha256=OTHER_ID)
    assert isinstance(factory(replace(descriptor, distribution=different)).broker, NullBroker)
