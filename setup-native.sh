#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
project_dir=$(pwd -P)
service=linux-server-admin-bot.service

if (( EUID == 0 )); then
    echo "Run this installer as the user who will run the bot, without sudo." >&2
    exit 1
fi

if ! command -v python3 >/dev/null ||
    ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "Install Python 3.11 or later with venv support, then rerun this script." >&2
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

echo "Installing Python dependencies in .venv"
if [[ ! -x .venv/bin/python ]]; then
    if ! python3 -m venv .venv; then
        echo "Could not create the virtual environment. Install venv support for Python 3." >&2
        exit 1
    fi
fi
if ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "The existing .venv uses an older Python. Move it aside and rerun this script." >&2
    exit 1
fi
.venv/bin/python -m pip install -r requirements.txt

echo "Checking block configuration and dependencies"
HOST_PROC_PATH=/proc HOST_SYS_PATH=/sys .venv/bin/python main.py --check-blocks

unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p -- "$unit_dir"

# Quote systemd paths independently of shell quoting.
unit_path=${project_dir//\\/\\\\}
unit_path=${unit_path//\"/\\\"}
unit_path=${unit_path//%/%%}
cat > "$unit_dir/$service" <<UNIT
[Unit]
Description=Linux Server Admin Bot

[Service]
WorkingDirectory="$unit_path"
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
echo "After editing blocks.toml: systemctl --user restart $service"

account=$(id -un)
if [[ $(loginctl show-user "$account" --property=Linger --value 2>/dev/null || true) != yes ]]; then
    echo "To start at boot without logging in: sudo loginctl enable-linger $account"
fi
