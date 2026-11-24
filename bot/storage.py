"""Atomic local file replacement shared by configuration and runtime state."""

import os
from collections.abc import Callable
from pathlib import Path
from tempfile import NamedTemporaryFile


def atomic_write(
    path: Path,
    text: str,
    mode: int = 0o600,
    before_replace: Callable[[], None] | None = None,
    *,
    durable: bool = True,
) -> None:
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), mode)
            stream.write(text)
            stream.flush()
            if durable:
                os.fsync(stream.fileno())
            if before_replace is not None:
                before_replace()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
