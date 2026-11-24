"""Transport contract and the fixed native sudo transport."""

import subprocess
from pathlib import Path
from typing import Protocol

from broker.protocol import MAX_RESULT, BrokerError

HELPER = Path("/usr/local/libexec/lsab-broker/lsab-broker")


class Transport(Protocol):
    def exchange(self, payload: bytes) -> bytes: ...


class SudoTransport:
    def exchange(self, payload: bytes) -> bytes:
        try:
            process = subprocess.run(
                ["/usr/bin/sudo", "-n", "--", str(HELPER)],
                input=payload,
                capture_output=True,
                timeout=35,
                check=False,
                cwd="/",
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
            )
        except subprocess.TimeoutExpired as exc:
            raise BrokerError(
                "TIMEOUT", "Broker timed out; check the outcome before retrying."
            ) from exc
        except OSError as exc:
            raise BrokerError(
                "BROKER_UNAVAILABLE", "Installed sudo broker is unavailable."
            ) from exc
        if process.returncode:
            raise BrokerError(
                "BROKER_UNAVAILABLE",
                "Broker could not start. Check installation and the sudo grant.",
            )
        if len(process.stdout) > MAX_RESULT + 1:
            raise BrokerError("RESULT_TOO_LARGE", "Broker response exceeds the size limit.")
        return process.stdout
