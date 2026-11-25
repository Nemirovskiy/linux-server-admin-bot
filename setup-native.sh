#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
project_dir=$(pwd -P)
service=linux-server-admin-bot.service
broker_bundle=
skip_broker=false

if [[ $# == 1 && $1 == --skip-broker ]]; then
    skip_broker=true
elif [[ $# == 2 && $1 == --broker-bundle && $2 == /* ]]; then
    broker_bundle=$2
elif (( $# )); then
    echo "Usage: ./setup-native.sh [--skip-broker | --broker-bundle /path/to/root-owned-bundle]" >&2
    exit 1
fi

if (( EUID == 0 )); then
    echo "Run this installer as the user who will run the bot, without sudo." >&2
    exit 1
fi

if ! command -v python3 >/dev/null; then
    echo "Python 3 was not found. Install Python 3.11 or later and rerun." >&2
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "Python 3.11 or later is required." >&2
    exit 1
fi

print_venv_hint() {
    local version
    version=$("$1" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    echo "On Debian or Ubuntu: sudo apt install python${version}-venv" >&2
    echo "Then rerun ./setup-native.sh. Existing .env and blocks.toml settings are kept." >&2
}

if [[ ! -x .venv/bin/python ]]; then
    if ! python3 -m venv --help >/dev/null 2>&1 ||
        ! python3 -m ensurepip --version >/dev/null 2>&1; then
        echo "Python venv or ensurepip support is missing." >&2
        print_venv_hint python3
        exit 1
    fi
elif ! .venv/bin/python -m pip --version >/dev/null 2>&1 &&
    ! .venv/bin/python -m ensurepip --version >/dev/null 2>&1; then
    echo "The existing .venv has no pip, and ensurepip is unavailable." >&2
    print_venv_hint .venv/bin/python
    exit 1
fi

if ! command -v systemctl >/dev/null || ! systemctl --user show-environment >/dev/null; then
    echo "A user systemd session is required. Log in directly as the bot user and rerun." >&2
    exit 1
fi

if [[ ! -f .env ]]; then
    (umask 077; cp -- .env.example .env)
fi
if grep -Eq 'your_bot_token_here|^TELEGRAM_ALLOWED_USER_IDS=123456789,987654321([[:space:]]|$)' .env; then
    echo "Set TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_IDS in $project_dir/.env."
    exit 1
fi

install_broker() {
    /usr/bin/sudo -- /usr/bin/python3 -I -c '
import io
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

def run_installer(directory):
    installer = directory / "install.sh"
    if not installer.is_absolute() or ".." in installer.parts:
        raise SystemExit("Use an absolute bundle path without parent traversal")
    for path in (*reversed(installer.parents), installer):
        info = path.lstat()
        valid = stat.S_ISDIR(info.st_mode) if path != installer else stat.S_ISREG(info.st_mode)
        if info.st_uid or info.st_mode & 0o022 or not valid:
            raise SystemExit(f"Untrusted broker bundle path: {path}")
    return subprocess.run(
        ["/bin/bash", str(installer), sys.argv[2]],
        cwd="/",
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
    ).returncode

if sys.argv[1]:
    raise SystemExit(run_installer(Path(sys.argv[1])))

# Read the bundle through stdin so root never opens files in the working checkout.
limit = 4 * 1024 * 1024
payload = sys.stdin.buffer.read(limit + 1)
if len(payload) > limit:
    raise SystemExit("Broker bundle exceeds the size limit")
expected = {"install.sh", "lsab-broker"}
os.umask(0o077)
with tempfile.TemporaryDirectory(prefix=".lsab-broker-", dir="/root") as stage:
    directory = Path(stage)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive:
            if member.name not in expected or not member.isreg() or not 0 < member.size <= limit:
                raise SystemExit("Invalid broker bundle member")
            with archive.extractfile(member) as source, (directory / member.name).open("xb") as dest:
                content = source.read()
                if len(content) != member.size:
                    raise SystemExit("Incomplete broker bundle member")
                dest.write(content)
            expected.remove(member.name)
    if expected:
        raise SystemExit("Incomplete broker bundle")
    raise SystemExit(run_installer(directory))
' "$1" "$(id -un)"
}

build_and_install_broker() (
    bundle=$(mktemp -d)
    trap 'rm -rf -- "$bundle"' EXIT
    python3 packaging/broker/build.py "$bundle"
    echo "Installing the system broker from this checkout. Use a checkout you trust."
    tar -C "$bundle" -cf - install.sh lsab-broker | install_broker ""
)

if [[ $skip_broker == false ]]; then
    if [[ -n $broker_bundle ]]; then
        install_broker "$broker_bundle"
    elif [[ ! -x /usr/local/libexec/lsab-broker/lsab-broker ||
            ! -f /usr/local/sbin/lsab-broker-admin ]]; then
        build_and_install_broker
    fi
    # Validate the installed helper and this user's access before changing .env.
    python3 - <<'PY'
import json
import subprocess

request = {
    "protocol_version": 1,
    "request_id": "a" * 16,
    "plugin_id": "broker",
    "action": "broker.status",
    "params": {},
}
try:
    result = subprocess.run(
        ["/usr/bin/sudo", "-n", "--", "/usr/local/libexec/lsab-broker/lsab-broker"],
        input=json.dumps(request), text=True, stdout=subprocess.PIPE, timeout=10,
    )
    available = result.returncode == 0 and json.loads(result.stdout).get("success") is True
except (ValueError, AttributeError, OSError, subprocess.TimeoutExpired):
    available = False
if not available:
    raise SystemExit("Broker is unavailable for this account. Fix its installation or use --skip-broker.")
PY
    python3 - <<'PY'
import re
from pathlib import Path

env = Path(".env")
text = env.read_text()
text, count = re.subn(
    r"(?m)^[ \t]*(?:export[ \t]+)?LSAB_BROKER[ \t]*=.*$", "LSAB_BROKER=sudo", text
)
env.write_text(text if count else text.rstrip() + "\nLSAB_BROKER=sudo\n")
PY
fi

if [[ ! -x .venv/bin/python ]]; then
    if ! python3 -m venv .venv; then
        echo "Could not create .venv. Install venv support for the selected Python and rerun." >&2
        print_venv_hint python3
        exit 1
    fi
fi
if ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "The existing .venv uses an older Python. Move it aside and rerun this script." >&2
    exit 1
fi
if ! .venv/bin/python -c 'import sys; sys.exit(sys.prefix == sys.base_prefix)'; then
    echo ".venv is incomplete. Move it aside and rerun this script to recreate it." >&2
    exit 1
fi

if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    echo "Restoring pip in .venv"
    if ! .venv/bin/python -m ensurepip --upgrade ||
        ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
        echo "Could not restore pip in .venv. Install venv support for this Python and rerun." >&2
        print_venv_hint .venv/bin/python
        exit 1
    fi
fi

echo "Installing Python dependencies in .venv"
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps ./packages/lsab-api
.venv/bin/python -m pip install --no-deps -e .

echo "Checking block configuration and dependencies"
HOST_PROC_PATH=/proc HOST_SYS_PATH=/sys .venv/bin/python main.py --check-blocks

unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p -- "$unit_dir"

# WorkingDirectory takes a literal path. ExecStart arguments use quoting.
working_dir=${project_dir//%/%%}
unit_path=${project_dir//\\/\\\\}
unit_path=${unit_path//\"/\\\"}
unit_path=${unit_path//%/%%}
cat > "$unit_dir/$service" <<UNIT
[Unit]
Description=Linux Server Admin Bot

[Service]
WorkingDirectory=$working_dir
Environment=HOST_PROC_PATH=/proc HOST_SYS_PATH=/sys
ExecStart="$unit_path/packaging/run-native.sh"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable "$service"
systemctl --user restart "$service"
systemctl --user is-active --quiet "$service"

echo "Bot service started. Open Telegram and send /start."
echo "Logs: journalctl --user -u $service -f"
echo "Manage blocks: $project_dir/.venv/bin/lsab plugins list"

account=$(id -un)
if [[ $(loginctl show-user "$account" --property=Linger --value 2>/dev/null || true) != yes ]]; then
    echo "To start at boot without logging in: sudo loginctl enable-linger $account"
fi
