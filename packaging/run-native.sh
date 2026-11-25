#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
mkdir -p -- runtime
exec 9>runtime/polling.lock
if ! flock -n 9; then
    echo "A bot launched from this checkout is already running." >&2
    exit 1
fi
if [[ -L runtime/current ]]; then
    python="$(readlink -f -- runtime/current)/bin/python"
elif [[ -x .venv/bin/python ]]; then
    python=.venv/bin/python
else
    python=venv/bin/python
fi
exec "$python" -u main.py "$@"
