import sys
from logging import getLogger
from unittest.mock import Mock

import pytest

import bot.blocks
from bot.api import NullBroker, PluginContext
from bot.plugins.config import PluginConfig
from bot.plugins.manager import PluginManager
from bot.plugins.models import PluginHealth, PluginState
from bot.plugins.sources import BuiltinPluginSource


@pytest.fixture
def local_plugins(tmp_path, monkeypatch):
    monkeypatch.setattr(bot.blocks, "__path__", [str(tmp_path)])
    modules = []

    def write(name, implementation="", *, manifest_name=None, code=None, enabled=True):
        modules.append(f"bot.blocks.{name}")
        (tmp_path / f"{name}.plugin.toml").write_text(
            f"""[plugin]
id = "{name}"
name = "{manifest_name or name}"
description = "Test block"
icon = "⚙️"
version = "1.0.0"
entry_point = "bot.blocks.{name}:TestBlock"
default_enabled = {str(enabled).lower()}
"""
        )
        if code is None:
            code = f"""
from bot.api import Block, PluginMetadata

class TestBlock(Block):
    metadata = PluginMetadata("{name}", "{name}", "Test block", "1.0.0", "⚙️")
    def summary(self):
        return "Ready"
{implementation}
"""
        (tmp_path / f"{name}.py").write_text(code)

    yield write
    for module in modules:
        sys.modules.pop(module, None)


def make_manager(tmp_path, **kwargs):
    def context(descriptor):
        name = descriptor.metadata.id
        return PluginContext(name, {}, getLogger(name), Mock(), Mock(), NullBroker())

    manager = PluginManager(
        [BuiltinPluginSource()], PluginConfig(tmp_path / "blocks.toml"), (), context, **kwargs
    )
    manager.discover()
    return manager


async def test_manager_isolates_invalid_manifests_imports_and_constructors(tmp_path, local_plugins):
    local_plugins("disabled", code="raise AssertionError('disabled code executed')", enabled=False)
    local_plugins("brokenimport", code="raise ImportError('Missing library')")
    local_plugins(
        "brokeninit", "    def __init__(self, context): raise RuntimeError('Cannot create')"
    )
    local_plugins("mismatch", manifest_name="Different")
    local_plugins("healthy")
    (tmp_path / "bad.plugin.toml").write_text("[plugin]\ninvalid=")
    manager = make_manager(tmp_path)

    assert "bot.blocks.healthy" not in sys.modules
    await manager.load_enabled()
    await manager.setup_all()
    await manager.start_all()
    await manager.check_enabled()

    assert "bot.blocks.disabled" not in sys.modules
    assert manager.get_plugin("disabled").state is PluginState.DISCOVERED
    assert manager.get_plugin("bad").state is PluginState.FAILED
    assert "Missing library" in manager.get_plugin("brokenimport").error
    assert "Cannot create" in manager.get_plugin("brokeninit").error
    assert "does not match" in manager.get_plugin("mismatch").error
    assert [block.context.plugin_id for block in manager.running_blocks()] == ["healthy"]
    assert manager.get_plugin("healthy").health is PluginHealth.HEALTHY
    await manager.stop_all()


async def test_hooks_cleanup_in_reverse_and_keep_other_plugins_running(tmp_path, local_plugins):
    events = tmp_path / "events"
    for name, phase in (("alpha", "stop"), ("beta", "setup"), ("gamma", "start"), ("omega", "")):

        def hook(method, name=name, phase=phase):
            body = f"        with open({str(events)!r}, 'a') as f: f.write('{name}.{method}\\n')"
            if method == phase:
                body += f"\n        raise RuntimeError('{method} failure')"
            return f"    async def {method}(self):\n{body}"

        local_plugins(name, "\n".join(hook(method) for method in ("setup", "start", "stop")))
    manager = make_manager(tmp_path)
    await manager.load_enabled()
    await manager.setup_all()
    await manager.start_all()

    assert [block.context.plugin_id for block in manager.running_blocks()] == ["alpha", "omega"]
    assert manager.get_plugin("beta").instance is None
    assert manager.get_plugin("gamma").instance is None
    manager.disable("omega")
    assert not manager.get_plugin("omega").enabled
    assert manager.get_plugin("omega").state is PluginState.RUNNING

    await manager.stop_all()
    await manager.stop_all()
    assert events.read_text().splitlines() == [
        "alpha.setup",
        "beta.setup",
        "beta.stop",
        "gamma.setup",
        "omega.setup",
        "alpha.start",
        "gamma.start",
        "gamma.stop",
        "omega.start",
        "omega.stop",
        "alpha.stop",
    ]
    assert manager.get_plugin("omega").state is PluginState.STOPPED
    assert manager.get_plugin("alpha").error == "stop: stop failure"


async def test_timeout_and_missing_dependency_have_separate_states(tmp_path, local_plugins):
    local_plugins(
        "slow", "    async def setup(self):\n        import asyncio\n        await asyncio.sleep(1)"
    )
    local_plugins("missing", "    def dependency_issue(self): return 'Missing executable'")
    manager = make_manager(tmp_path, operation_timeout=0.2)

    await manager.load_enabled()
    await manager.setup_all()
    await manager.start_all()
    await manager.check_enabled()

    assert manager.get_plugin("slow").error == "setup: TimeoutError"
    missing = manager.get_plugin("missing")
    assert missing.state is PluginState.RUNNING
    assert missing.health is PluginHealth.UNAVAILABLE
    assert missing.dependency_issue == "Missing executable"
    missing.instance.dependency_issue = lambda: None
    assert (await manager.check("missing")).health is PluginHealth.HEALTHY
    await manager.stop_all()
