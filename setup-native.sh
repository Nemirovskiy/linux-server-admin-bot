#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
project_dir=$(pwd -P)
service=linux-server-admin-bot.service

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
ExecStart="$unit_path/.venv/bin/python" -u "$unit_path/main.py"
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
