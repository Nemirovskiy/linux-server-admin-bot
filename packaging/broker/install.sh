#!/bin/bash
# Run only from a verified bundle owned by root.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 077

if (( EUID != 0 )) || [[ $# != 1 || ! $1 =~ ^[a-z_][a-z0-9_-]{0,30}$ ]]; then
    echo "Usage as root from a trusted release bundle: install.sh BOT_ACCOUNT" >&2
    exit 1
fi

account=$1
bundle=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
helper_dir=/usr/local/libexec/lsab-broker
policy_dir=/etc/lsab/broker
sudoers_file=/etc/sudoers.d/lsab-broker

/usr/bin/python3 -I - "$bundle" <<'PY'
import stat
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    raise SystemExit("System Python 3.11 or newer is required")
bundle = Path(sys.argv[1])
files = [
    (bundle / "install.sh", False, False),
    (bundle / "lsab-broker", False, False),
    (Path("/usr/local/libexec/lsab-broker"), True, True),
    (Path("/usr/local/sbin"), True, True),
    (Path("/etc/lsab/broker"), True, True),
    (Path("/etc/lsab/broker/docker"), True, True),
    (Path("/etc/sudoers.d"), True, False),
]
for path, directory, missing_ok in files:
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            if missing_ok:
                break
            raise
        valid_kind = stat.S_ISDIR(info.st_mode) if part != path or directory else stat.S_ISREG(info.st_mode)
        if info.st_uid or info.st_mode & 0o022 or not valid_kind:
            raise SystemExit(f"Path must be root-owned and protected: {part}")
PY

if [[ ! -x /usr/bin/sudo ]] || ! command -v visudo >/dev/null || ! command -v runuser >/dev/null; then
    echo "Install sudo and util-linux before installing the broker." >&2
    exit 1
fi

uid=$(id -u -- "$account")
if (( uid == 0 )); then
    echo "Choose a dedicated non-root bot account." >&2
    exit 1
fi
if id -Gn -- "$account" | tr ' ' '\n' | grep -Fx docker >/dev/null ||
   runuser -u "$account" -- /usr/bin/test -w /var/run/docker.sock; then
    echo "Remove the bot account's direct Docker socket access before installing the broker." >&2
    exit 1
fi

# Replace installed executables atomically without opening any existing destination.
ensure_directory() {
    if [[ ! -d $1 ]]; then
        install -d -o root -g root -m "$2" "$1"
    fi
}
ensure_directory "$helper_dir" 0755
ensure_directory /usr/local/sbin 0755
ensure_directory "$policy_dir" 0750
ensure_directory "$policy_dir/docker" 0750
artifact_tmp=$(mktemp --tmpdir="$helper_dir" .broker.XXXXXX)
admin_tmp=$(mktemp --tmpdir=/usr/local/sbin .lsab-admin.XXXXXX)
sudoers_tmp=$(mktemp --tmpdir=/etc/sudoers.d .lsab-broker.XXXXXX)
trap 'rm -f -- "$artifact_tmp" "$admin_tmp" "$sudoers_tmp"' EXIT

install -o root -g root -m 0755 "$bundle/lsab-broker" "$artifact_tmp"
install -o root -g root -m 0750 "$bundle/lsab-broker" "$admin_tmp"
mv -fT -- "$artifact_tmp" "$helper_dir/lsab-broker"
mv -fT -- "$admin_tmp" /usr/local/sbin/lsab-broker-admin

if [[ ! -e "$policy_dir/permissions.toml" && ! -L "$policy_dir/permissions.toml" ]]; then
    policy_tmp=$(mktemp --tmpdir="$policy_dir" .permissions.XXXXXX)
    printf '[policy]\nversion = 1\ndefault = "deny"\ncaller_uids = [%s]\n' "$uid" > "$policy_tmp"
    mv -fT -- "$policy_tmp" "$policy_dir/permissions.toml"
fi
/usr/local/sbin/lsab-broker-admin check-policy

printf '%s ALL=(root) NOPASSWD: NOSETENV: %s ""\n' "$account" "$helper_dir/lsab-broker" > "$sudoers_tmp"
chmod 0440 "$sudoers_tmp"
visudo -cf "$sudoers_tmp"
mv -fT -- "$sudoers_tmp" "$sudoers_file"

echo "Broker installed. Existing policy is preserved; new installations grant no actions."
echo "For the bot, set LSAB_BROKER=sudo and grant individual operations as root."
