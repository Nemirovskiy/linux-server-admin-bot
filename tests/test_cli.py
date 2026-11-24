import json
import os
import subprocess
import sys
from pathlib import Path

from cli.main import main

PROJECT = Path(__file__).resolve().parents[1]


def test_cli_catalog_dry_run_and_diagnostics_without_telegram(tmp_path):
    env = os.environ.copy()
    env.pop("TELEGRAM_BOT_TOKEN", None)
    env.pop("TELEGRAM_ALLOWED_USER_IDS", None)
    env["PYTHONPATH"] = str(PROJECT)
    env["MPLCONFIGDIR"] = str(tmp_path / "mpl")
    config = tmp_path / "blocks.toml"
    invocation = [sys.executable, "-m", "cli", "--config", str(config), "--json"]

    def invoke(*args):
        return subprocess.run(
            [*invocation, *args],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )

    rows = json.loads(invoke("plugins", "list").stdout)
    assert len(rows) == 10
    assert next(row for row in rows if row["id"] == "cpu")["enabled"]
    assert not next(row for row in rows if row["id"] == "docker")["enabled"]
    preview = json.loads(invoke("plugins", "enable", "docker", "crowdsec", "--dry-run").stdout)
    assert preview["changes"] == {
        "docker": {"before": False, "after": True},
        "crowdsec": {"before": False, "after": True},
    }
    assert not config.exists()
    assert not (tmp_path / ".lsab").exists()

    config.write_text("[blocks]\ncpu = false\n")
    checked = json.loads(invoke("plugins", "doctor", "cpu").stdout)
    assert checked == [
        {
            "id": "cpu",
            "enabled": False,
            "state": "loaded",
            "health": "healthy",
            "error": None,
            "dependency_issue": None,
            "ok": True,
        }
    ]
    assert config.read_text() == "[blocks]\ncpu = false\n"


def test_bad_second_id_leaves_entire_batch_unchanged(tmp_path, capsys):
    config = tmp_path / "blocks.toml"
    config.write_text("[blocks]\ncpu = true # keep\n")
    args = ["--config", str(config), "--json", "plugins"]
    assert main([*args, "disable", "cpu", "typo"]) == 1
    assert "Unknown block" in json.loads(capsys.readouterr().out)["error"]
    assert config.read_text() == "[blocks]\ncpu = true # keep\n"

    assert main([*args, "disable", "cpu", "docker"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["switches"] == {"cpu": False, "docker": False}
    assert "cpu = false # keep" in config.read_text()


def test_simultaneous_cli_updates_keep_both_changes(tmp_path):
    config = tmp_path / "blocks.toml"
    config.write_text('["blocks"]\n"cpu" = true # cpu comment\n"memory" = true # memory comment\n')
    command = [sys.executable, "-m", "cli", "--config", str(config), "plugins", "disable"]
    with (
        subprocess.Popen([*command, "cpu"], cwd=PROJECT, stdout=subprocess.PIPE) as first,
        subprocess.Popen([*command, "memory"], cwd=PROJECT, stdout=subprocess.PIPE) as second,
    ):
        first.communicate(timeout=10)
        second.communicate(timeout=10)
        assert first.returncode == second.returncode == 0
    text = config.read_text()
    assert '"cpu" = false # cpu comment' in text
    assert '"memory" = false # memory comment' in text


def test_interactive_requires_a_terminal(tmp_path, capsys):
    config = tmp_path / "blocks.toml"
    assert main(["--config", str(config), "--json", "plugins", "configure"]) == 1
    assert "interactive terminal" in json.loads(capsys.readouterr().out)["error"]
    assert not config.exists()


def test_tui_select_save_and_cancel():
    from unittest.mock import Mock

    from cli.tui import configure

    screen = Mock()
    screen.size.return_value = (12, 60)
    plugins = [
        {"id": "cpu", "enabled": True, "metadata": {"name": "CPU"}},
        {"id": "disk", "enabled": False, "metadata": {"name": "Disk"}},
    ]
    screen.key.side_effect = ["down", " ", "s"]
    assert configure(screen, plugins) == {"disk": True}
    screen.key.side_effect = [" ", "q"]
    assert configure(screen, plugins) is None
