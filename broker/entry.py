"""Installed zipapp entry point."""

import os
import signal
import sys
from pathlib import Path

from broker.protocol import MAX_REQUEST, BrokerError
from broker.security import sudo_caller, trusted
from broker.transport import HELPER
from broker.worker import audit, dispatch

ADMIN = Path("/usr/local/sbin/lsab-broker-admin")


def _deadline(*_: object) -> None:
    raise TimeoutError


def main() -> int:
    try:
        invoked = Path(sys.argv[0]).absolute()
        if os.getuid() != 0 or os.geteuid() != 0 or invoked not in {HELPER, ADMIN}:
            raise ValueError
        trusted(invoked)
        if not sys.flags.isolated:
            raise ValueError
    except (ValueError, OSError):
        print("Use the root-protected installed broker entry point.", file=sys.stderr)
        return 1
    os.umask(0o077)
    args = sys.argv[1:]
    if invoked == ADMIN:
        from broker.admin import administer

        return administer(args)
    if args:
        return 1
    signal.signal(signal.SIGALRM, _deadline)
    signal.alarm(3)
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
    except (OSError, TimeoutError):
        try:
            caller = sudo_caller()
        except BrokerError:
            caller = None
        audit(
            {
                "result": "TIMEOUT",
                "action": None,
                "request_id": None,
                "system_uid": caller.uid if caller else None,
                "system_user": caller.user if caller else None,
            }
        )
        return 1
    finally:
        signal.alarm(0)
    sys.stdout.buffer.write(dispatch(raw) + b"\n")
    return 0
