import os
import subprocess
import sys
from pathlib import Path

from bot.api import PluginMetadata
from bot.plugins.config import PluginConfig
from bot.plugins.manager import PluginManager
from bot.plugins.models import PluginDescriptor, PluginState


def test_discovery_and_switches_run_without_runtime_dependencies(tmp_path):
    project = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("TELEGRAM_BOT_TOKEN", None)
    env.pop("TELEGRAM_ALLOWED_USER_IDS", None)
    env["PYTHONPATH"] = str(project)
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from pathlib import Path
from bot.plugins.config import PluginConfig
from bot.plugins.manager import PluginManager
from bot.plugins.sources import BuiltinPluginSource

config = PluginConfig(Path('blocks.toml'))
manager = PluginManager([BuiltinPluginSource()], config)
manager.discover()
assert len(manager.list_plugins()) == 9
assert manager.get_plugin('cpu').metadata.name == 'CPU'
assert manager.get_plugin('cpu').enabled
assert not manager.get_plugin('docker').enabled
manager.enable('docker')
assert config.is_enabled('docker')
manager.disable('cpu')
assert not config.is_enabled('cpu')
assert not any(name.startswith('bot.blocks.') for name in sys.modules)
assert 'config.settings' not in sys.modules
assert 'telegram' not in sys.modules
""",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        timeout=10,
    )


def test_duplicates_reserved_names_callbacks_and_api_versions_are_recorded(tmp_path):
    def descriptor(name, *, actions=(), api_version="1.0"):
        return PluginDescriptor(
            PluginMetadata(name, name, "Description", "1.0.0", "⚙️", api_version=api_version),
            "test",
            f"bot.blocks.{name}:Example",
            extra_actions=actions,
        )

    class Source:
        def discover(self):
            yield descriptor("cpu")
            yield descriptor("cpu")
            yield descriptor("status")
            yield descriptor("menu", actions=("menu_main",))
            yield descriptor("future", api_version="2.0")
            yield descriptor("nested", actions=("nested_select", "nested_select"))
            yield descriptor("memory")

    manager = PluginManager([Source()], PluginConfig(tmp_path / "blocks.toml"), ["status"])
    manager.discover()

    assert manager.get_plugin("memory").state is PluginState.DISCOVERED
    failed = {record.plugin_id: record.error for record in manager.registry.list_failed()}
    assert "Duplicate plugin ID" in failed["cpu"]
    assert "Reserved command" in failed["status"]
    assert "Reserved callback" in failed["menu"]
    assert "Unsupported Plugin API" in failed["future"]
    assert "Duplicate callback" in failed["nested"]
