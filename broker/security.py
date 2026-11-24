"""Root ownership checks and identities supplied by sudo."""

import os
import pwd
import stat
from dataclasses import dataclass
from pathlib import Path

from broker.protocol import BrokerError


def trusted(path: Path, *, directory: bool = False) -> None:
    if not path.is_absolute():
        raise ValueError("Trusted paths must be absolute")
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        is_dir = parent != path or directory
        valid_type = stat.S_ISDIR(info.st_mode) if is_dir else stat.S_ISREG(info.st_mode)
        if info.st_uid != 0 or info.st_mode & 0o022 or not valid_type:
            raise ValueError(f"Untrusted ownership, mode or file type: {parent}")


@dataclass(frozen=True)
class Caller:
    uid: int
    user: str


def sudo_caller() -> Caller:
    if os.getuid() != 0 or os.geteuid() != 0:
        raise BrokerError(
            "PERMISSION_DENIED", "The broker requires its installed sudo entry point."
        )
    raw_uid = os.environ.get("SUDO_UID", "")
    if not raw_uid.isascii() or not raw_uid.isdecimal() or len(raw_uid) > 10:
        raise BrokerError("PERMISSION_DENIED", "Missing sudo caller identity.")
    uid = int(raw_uid)
    try:
        account = pwd.getpwuid(uid)
    except KeyError as exc:
        raise BrokerError("PERMISSION_DENIED", "Unknown sudo caller.") from exc
    if uid == 0 or os.environ.get("SUDO_USER") != account.pw_name:
        raise BrokerError("PERMISSION_DENIED", "Invalid sudo caller identity.")
    return Caller(uid, account.pw_name)
