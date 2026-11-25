import fcntl
import json
from unittest.mock import Mock
from zipfile import ZipFile

import pytest

from bot.plugins.config import PluginConfig
from bot.runtime_status import config_digest
from cli.installer import Installer
from cli.installer_sources import inspect_wheel, resolve
from cli.management import PluginManagement
from cli.services import ServiceError


@pytest.fixture
def stage(tmp_path, monkeypatch):
    project = tmp_path / "checkout"
    project.mkdir()
    (project / "main.py").touch()
    config = PluginConfig(project / "blocks.toml")
    original = "[blocks]\ndemo = true # preserve\n"
    config.path.write_text(original)
    controller = Mock(kind="systemd")
    controller.status.return_value = {"state": "active", "pid": 77}
    management = PluginManagement(config)
    installer = Installer(project, management, controller)
    installer.environments.mkdir(parents=True)
    base = installer.environments / "env-base"
    candidate = installer.environments / "env-candidate"
    for env in (base, candidate):
        env.mkdir()
        (env / "pyvenv.cfg").touch()
    installer._link("current", base)
    monkeypatch.setattr(
        "cli.installer.read_status",
        lambda _: {"pid": 77, "config_hash": config_digest(original), "run_id": "old"},
    )
    monkeypatch.setattr(installer, "_inventory", lambda *_: {"defaults": {"demo": False}})
    return installer, controller, management, base, candidate, original


def test_failed_activation_restores_environment_config_and_data(stage):
    installer, controller, management, base, candidate, original = stage
    path = installer.config.path.parent / "data/plugins/demo"
    path.mkdir(parents=True)
    content = path / "state"
    content.write_text("original")
    revision = installer.config.plan_switches({"demo": False}, {"demo": False})
    txn = installer._transaction("update", "demo", base)

    def readiness(*args, environment):
        if environment == candidate:
            content.write_text("partial migration")
            raise ServiceError("new bot did not become ready")

    management._wait_ready = Mock(side_effect=readiness)
    with pytest.raises(ServiceError, match="Previous environment restored"):
        installer._activate(txn, candidate, revision, {"enabled": []}, backup="demo")
    assert installer.active == base
    assert installer.config.path.read_text() == original
    assert content.read_text() == "original"
    assert txn["status"] == "rolled_back"
    assert controller.stop.call_count == controller.start.call_count == 2


def test_successful_switch_records_previous_environment(stage):
    installer, controller, management, base, candidate, _ = stage
    management._wait_ready = Mock()
    revision = installer.config.plan_switches({"demo": False}, {"demo": False})
    txn = installer._transaction("remove", "demo", base)
    installer._activate(txn, candidate, revision, {"enabled": []})
    assert installer.active == candidate
    assert installer.pointer("previous") == base
    assert not installer.config.is_enabled("demo")
    assert installer.history()[0]["status"] == "completed"
    assert management._wait_ready.call_args.kwargs["environment"] == candidate
    assert controller.stop.call_count == controller.start.call_count == 1


def test_interrupted_activation_and_file_lock(stage, monkeypatch):
    installer, _, management, base, candidate, original = stage
    management._wait_ready = Mock()
    monkeypatch.setattr(installer, "_guard", lambda: None)
    revision = installer.config.plan_switches({"demo": False}, {"demo": False})
    installer.config.replace(revision.text, revision.original)
    installer._link("current", candidate)
    txn = installer._transaction("update", "demo", base)
    txn.update(
        previous=str(base),
        candidate=str(candidate),
        before=original,
        after=revision.text,
        config=str(installer.config.path),
        restart=True,
        previous_pointer="",
        backend="systemd",
    )
    installer._save(txn, "applying")
    with pytest.raises(ValueError, match="environment recover"):
        installer._assert_idle()
    with (installer.root / "installer.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(ValueError, match="Another plugin installation"), installer.lock():
            pytest.fail("lock must prevent a second installer")
    assert installer.recover()["status"] == "rolled_back"
    assert installer.active == base
    assert installer.config.snapshot() == original


def test_manual_launcher_lock_prevents_switch(stage):
    installer, controller, _, base, candidate, _ = stage
    controller.kind = "manual"
    revision = installer.config.plan_switches({}, {"demo": False})
    txn = installer._transaction("install", "demo", base)
    with (installer.root / "polling.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(ServiceError, match="manually started bot"):
            installer._activate(txn, candidate, revision, {"enabled": []})
    assert installer.active == base


def wheel(tmp_path, *, api=">=1.1,<2", extra_path=None):
    target = tmp_path / "demo_package-0.2.0-py3-none-any.whl"
    with ZipFile(target, "w") as archive:
        archive.writestr(
            "demo_package-0.2.0.dist-info/METADATA",
            f"Name: demo-package\nVersion: 0.2.0\nRequires-Python: >=3.11\nRequires-Dist: lsab-api{api}\n",
        )
        archive.writestr(
            "demo_package-0.2.0.dist-info/entry_points.txt",
            "[lsab.plugins]\ndemo=demo_package.plugin:Example\n",
        )
        archive.writestr(
            "demo_package/plugin.toml",
            '[plugin]\nid="demo"\nname="Demo"\ndescription="Description"\nicon="D"\n'
            'api_version="1.1"\n[permissions]\noptional=["demo.metrics.read"]\n[data]\nschema_version=2\n',
        )
        archive.writestr("demo_package/plugin.py", "# fixture plugin")
        if extra_path:
            archive.writestr(extra_path, "invalid")
    return target


def test_wheel_preview_reads_permissions_schema_and_digest(tmp_path):
    path = wheel(tmp_path)
    plan = inspect_wheel(path, resolve(str(path)))
    assert plan["version"] == "0.2.0"
    assert plan["optional"] == ["demo.metrics.read"]
    assert plan["schema"] == 2
    assert len(plan["sha256"]) == 64


@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"api": ">=2"}, "Requires lsab-api>=2"),
        ({"extra_path": "bot/handlers/basic.py"}, "protected application package"),
        ({"extra_path": "../escape"}, "Unsafe wheel member path"),
    ],
)
def test_invalid_wheel_is_rejected_before_any_install(tmp_path, options, error):
    path = wheel(tmp_path, **options)
    with pytest.raises(ValueError, match=error):
        inspect_wheel(path, resolve(str(path)))


def test_source_validation_rejects_unpinned_git_and_credentials():
    with pytest.raises(ValueError, match="--ref"):
        resolve("https://github.com/example/plugin")
    with pytest.raises(ValueError, match="credentials"):
        resolve("https://user:secret@github.com/example/plugin", "v1")
    assert resolve("https://github.com/example/plugin", "v1").kind == "git"
    assert (
        resolve(
            "https://github.com/example/plugin/releases/download/v1/demo-1-py3-none-any.whl"
        ).kind
        == "release"
    )
    assert resolve("demo-package==1.2").kind == "index"


def test_export_contains_locked_artifacts(stage, tmp_path, monkeypatch):
    installer, _, _, base, _, _ = stage
    monkeypatch.setattr(installer, "_guard", lambda: None)
    monkeypatch.setattr(installer, "_inventory", lambda *_: {"packages": {"demo-package": "0.2.0"}})
    artifact = wheel(tmp_path)
    record = inspect_wheel(artifact, resolve(str(artifact)))
    installer._remember(artifact, record["sha256"])
    (base / "lsab-installed-plugins.json").write_text(json.dumps({"demo": record}))
    output = tmp_path / "docker-plugins"

    assert installer.export(output)["plugins"] == ["demo"]
    assert (output / "wheels" / artifact.name).read_bytes() == artifact.read_bytes()
    assert record["sha256"] in (output / "wheels.sha256").read_text()
    assert (output / "constraints.txt").read_text() == "demo-package==0.2.0\n"


def test_shared_distribution_cannot_be_removed_as_one_block(stage, monkeypatch):
    installer, _, _, _, _, _ = stage
    inventory = {
        "catalog": [
            {"id": "demo", "source": "external", "distribution": "shared-plugin", "version": "1.0"},
            {
                "id": "another",
                "source": "external",
                "distribution": "shared_plugin",
                "version": "1.0",
            },
        ]
    }
    monkeypatch.setattr(installer, "_inventory", lambda *_args: inventory)
    with pytest.raises(ValueError, match="multiple blocks"):
        installer.remove("demo", dry_run=True)
