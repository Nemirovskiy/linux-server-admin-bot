import stat

import pytest

from bot.plugins.config import PluginConfig


def test_config_updates_preserve_comments_order_mode_and_missing_switches(tmp_path):
    path = tmp_path / "blocks.toml"
    path.write_bytes(b"[blocks]\r\n# missing module\r\nold = true\r\ncpu = false # keep\r\n")
    path.chmod(0o640)
    config = PluginConfig(path)

    config.set_enabled("cpu", True)
    assert config.sync({"cpu": True, "disk": True, "temp": True}) == {
        "old": True,
        "cpu": True,
        "disk": False,
        "temp": False,
    }
    assert path.read_bytes() == (
        b"[blocks]\r\n# missing module\r\nold = true\r\ncpu = true # keep\r\n"
        b"disk = false\r\ntemp = false\r\n"
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_config_sync_and_update_accept_inline_tables(tmp_path):
    path = tmp_path / "blocks.toml"
    path.write_text("blocks = { cpu = true, disk = false }\n")
    config = PluginConfig(path)
    config.sync({"cpu": True, "disk": False, "temp": False})
    config.set_enabled("disk", True)
    assert config.load() == {"cpu": True, "disk": True, "temp": False}


def test_config_refuses_to_overwrite_simultaneous_manual_edit(tmp_path, monkeypatch):
    path = tmp_path / "blocks.toml"
    path.write_text("[blocks]\ncpu = false\n")
    manual = "[blocks]\ncpu = false # edited concurrently\n"
    monkeypatch.setattr("bot.storage.os.fsync", lambda _: path.write_text(manual))

    with pytest.raises(ValueError, match="changed during the update"):
        PluginConfig(path).set_enabled("cpu", True)

    assert path.read_text() == manual
    assert set(tmp_path.iterdir()) == {path, tmp_path / ".blocks-config.lock"}
