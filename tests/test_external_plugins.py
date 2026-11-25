import json
import sys
from importlib.metadata import distributions
from unittest.mock import AsyncMock, Mock

import pytest
from lsab_api import Block
from telegram.ext import CommandHandler

from bot.api import Block as LegacyImport
from bot.plugins.config import PluginConfig
from bot.plugins.manager import PluginManager
from bot.plugins.models import PluginHealth, PluginState
from bot.plugins.sources import BuiltinPluginSource, EntryPointPluginSource
from cli.main import main as cli
from config import settings
from main import BotApplication


@pytest.fixture
def environment(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.syspath_prepend(str(site))
    packages = []

    def install(
        plugin_id,
        *,
        module=None,
        requires="lsab-api>=1.1,<2",
        metadata_version="0.2.0",
        requires_python=">=3.11",
        manifest_extra="",
        body=None,
        manifest=True,
    ):
        module = module or f"test_external_{plugin_id}"
        packages.append(module)
        package = site / module
        package.mkdir()
        (package / "__init__.py").write_text("loaded = True\n")
        if body is None:
            body = f"""
from lsab_api import Block, PluginMetadata

class Demo(Block):
    metadata = PluginMetadata(
        "{plugin_id}", "Test external", "Test description", "{metadata_version}", "📌", api_version="1.1"
    )
    def summary(self):
        return self.context.config.get("message", "Ready")

    async def setup(self):
        (self.context.data_dir / "state.txt").write_text("started")

    async def command(self, update, context):
        await update.message.reply_text(self.summary())
"""
        (package / "plugin.py").write_text(body)
        if manifest:
            (package / "plugin.toml").write_text(
                f"""[plugin]
id = "{plugin_id}"
name = "Test external"
description = "Test description"
icon = "📌"
api_version = "1.1"
default_enabled = false
{manifest_extra}
"""
            )
        dist = site / f"{module}-{metadata_version}.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(
            f"Name: {module.replace('_', '-')}\nVersion: {metadata_version}\n"
            f"Requires-Python: {requires_python}\nRequires-Dist: {requires}\nAuthor: Test Author\n"
        )
        (dist / "entry_points.txt").write_text(
            f"[lsab.plugins]\n{plugin_id} = {module}.plugin:Demo\n"
        )
        files = [package / "plugin.py", package / "__init__.py"]
        if manifest:
            files.append(package / "plugin.toml")
        (dist / "RECORD").write_text(
            "".join(f"{path.relative_to(site).as_posix()},,\n" for path in files)
        )
        return module

    def entries():
        for distribution in distributions(path=[site]):
            yield from distribution.entry_points.select(group="lsab.plugins")

    yield install, EntryPointPluginSource(entries)
    for name in packages:
        sys.modules.pop(name, None)
        sys.modules.pop(f"{name}.plugin", None)


def manager_for(path, source):
    manager = PluginManager((source, BuiltinPluginSource()), PluginConfig(path))
    manager.discover(sync=False)
    return manager


def test_catalog_discovery_is_offline_and_does_not_load_disabled_code(
    environment, tmp_path, capsys, monkeypatch
):
    install, source = environment
    module = install("demo", metadata_version="0.2.0rc1")
    alias = tmp_path / "site-alias"
    alias.symlink_to(tmp_path / "site", target_is_directory=True)
    monkeypatch.syspath_prepend(str(alias))
    config = tmp_path / "blocks.toml"

    assert cli(["--config", str(config), "plugins", "list", "--external", "--json"]) == 0
    (demo,) = json.loads(capsys.readouterr().out)
    assert demo["id"] == "demo" and demo["source"] == "external" and not demo["enabled"]
    assert demo["distribution"]["version"] == "0.2.0rc1"
    assert demo["distribution"]["author"] == "Test Author"
    assert module not in sys.modules
    assert f"{module}.plugin" not in sys.modules
    assert not config.exists()
    assert manager_for(config, source).get_plugin("demo").state is PluginState.DISCOVERED
    assert LegacyImport is Block


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"requires": "lsab-api>=2.0"}, "Requires lsab-api>=2.0"),
        (
            {"requires": "lsab-api>=1.1\nRequires-Dist: lsab-nonexistent-fixture>=1"},
            "Missing Python dependency",
        ),
        ({"requires_python": ">=99"}, "Requires Python >=99"),
        ({"manifest": False}, "does not record"),
        ({"manifest_extra": '[compatibility]\nplatforms = ["win32"]'}, "Unsupported platform"),
    ],
)
def test_incompatible_packages_are_reported_without_import(environment, tmp_path, fields, message):
    install, source = environment
    module = install("demo", **fields)
    manager = manager_for(tmp_path / "blocks.toml", source)
    record = manager.get_plugin("demo")
    assert record.state is PluginState.FAILED and message in record.error
    assert module not in sys.modules


def test_external_collisions_preserve_builtins_and_reject_both_external_claimants(
    environment, tmp_path
):
    install, source = environment
    modules = [
        install("docker", module="external_fake_docker"),
        install("hello", module="external_one"),
        install("hello", module="external_two"),
    ]
    manager = manager_for(tmp_path / "blocks.toml", source)
    assert manager.get_plugin("docker").source == "builtin"
    assert manager.get_plugin("docker").state is PluginState.DISCOVERED
    assert manager.get_plugin("hello").state is PluginState.FAILED
    assert {record.plugin_id for record in manager.conflicts} == {"docker", "hello"}
    assert not any(module in sys.modules for module in modules)


async def test_enabled_external_uses_shared_context_and_host_authorization(
    environment,
    tmp_path,
    offline_telegram,
):
    install, _ = environment
    module = install("demo")
    config = tmp_path / "blocks.toml"
    config.write_text("[blocks]\ndemo = true\n")
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "demo.toml").write_text('[settings]\nmessage = "Hello from TOML"\n')

    app = BotApplication(config)
    await app.initialize()
    assert module not in sys.modules
    await app.start()
    try:
        (demo,) = app.blocks
        assert isinstance(demo, Block)
        assert demo.summary() == "Hello from TOML"
        data = tmp_path / "data" / "plugins" / "demo" / "state.txt"
        assert data.read_text() == "started"
        handler = next(
            h for h in app.app.handlers[0] if isinstance(h, CommandHandler) and "demo" in h.commands
        )

        user_id = settings.allowed_user_ids[0]
        chat = Mock(reply_text=AsyncMock())
        update = Mock(message=chat, effective_user=Mock(id=user_id + 1), effective_chat=None)
        await handler.callback(update, Mock())
        assert "Access denied" in chat.reply_text.await_args.args[0]
        chat.reply_text.reset_mock()
        update.effective_user.id = user_id
        await handler.callback(update, Mock())
        chat.reply_text.assert_awaited_once_with("Hello from TOML")
    finally:
        await app.stop()
    assert data.is_file()


async def test_import_failure_missing_permissions_and_removed_package_are_isolated(
    environment,
    tmp_path,
):
    install, _ = environment
    broken = install("broken", body="raise ImportError('fixture import failure')")
    blocked = install("blocked", manifest_extra='[permissions]\nrequired = ["blocked.system.read"]')
    config = tmp_path / "blocks.toml"
    config.write_text("[blocks]\nbroken = true\nblocked = true\nremoved = true\n")
    app = BotApplication(config)
    with pytest.raises(ValueError, match="fixture import failure"):
        await app.initialize(check_only=True)
    manager = app.plugin_manager
    assert manager.get_plugin("broken").state is PluginState.FAILED
    record = manager.get_plugin("blocked")
    assert (
        record.health is PluginHealth.UNAVAILABLE
        and "blocked.system.read" in record.dependency_issue
    )
    assert f"{blocked}.plugin" not in sys.modules
    assert broken in sys.modules
    assert manager.get_plugin("removed").source == "missing"
    assert "removed = true" in config.read_text()


async def test_shadowed_module_is_rejected_before_import(environment, tmp_path, monkeypatch):
    install, _ = environment
    module = install("demo")
    shadow = tmp_path / "shadow"
    (shadow / module).mkdir(parents=True)
    (shadow / module / "__init__.py").write_text("raise RuntimeError('shadow executed')\n")
    (shadow / module / "plugin.py").write_text("raise RuntimeError('shadow executed')\n")
    monkeypatch.syspath_prepend(str(shadow))
    config = tmp_path / "blocks.toml"
    config.write_text("[blocks]\ndemo = true\n")
    with pytest.raises(ValueError, match="Module path does not belong"):
        await BotApplication(config).initialize(check_only=True)
    assert module not in sys.modules


def test_editable_install_ignores_its_generated_egg_info(environment, tmp_path, monkeypatch):
    import shutil

    install, _ = environment
    name = install("demo")
    site = tmp_path / "site"
    dist = next(site.glob("*.dist-info"))
    project = tmp_path / "project"
    source = project / "src"
    source.mkdir(parents=True)
    shutil.move(site / name, source / name)
    egg = source / f"{name}.egg-info"
    egg.mkdir()
    shutil.copyfile(dist / "entry_points.txt", egg / "entry_points.txt")
    shutil.copyfile(dist / "METADATA", egg / "PKG-INFO")
    (dist / "RECORD").write_text("")
    (dist / "direct_url.json").write_text(
        json.dumps({"url": project.as_uri(), "dir_info": {"editable": True}})
    )
    monkeypatch.syspath_prepend(str(source))

    items = [
        item
        for item in EntryPointPluginSource().discover()
        if item.distribution and item.distribution.name == name.replace("_", "-")
    ]
    assert len(items) == 1
    assert items[0].metadata.id == "demo"
    assert items[0].module_file == source / name / "plugin.py"
    assert name not in sys.modules
