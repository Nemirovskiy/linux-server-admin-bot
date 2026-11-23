"""Discover block files and validate the selected blocks at startup."""

import fcntl
import importlib
import inspect
import os
import pkgutil
import re
import tomllib
from collections.abc import Iterable
from pathlib import Path
from tempfile import NamedTemporaryFile

from bot.blocks.base import Block, BlockView

__all__ = ["Block", "BlockView", "discover_blocks", "select_blocks"]


def discover_blocks(reserved_commands: Iterable[str]) -> dict[str, type[Block]]:
    """Load each public block module shipped in this package."""
    blocks = {}
    reserved = set(reserved_commands)
    reserved_actions = {"menu_main", "noop", *(f"cmd_{name}" for name in reserved)}
    for module_info in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name):
        if module_info.ispkg or module_info.name == "base" or module_info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f".{module_info.name}", __name__)
        except Exception as exc:
            raise ValueError(f"Could not load block file {module_info.name}.py: {exc}") from exc
        providers = [
            provider
            for provider in vars(module).values()
            if inspect.isclass(provider)
            and provider.__module__ == module.__name__
            and issubclass(provider, Block)
            and not inspect.isabstract(provider)
        ]
        if len(providers) != 1:
            raise ValueError(f"{module_info.name}.py must define exactly one concrete Block")

        provider = providers[0]
        name = getattr(provider, "name", "")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name):
            raise ValueError(f"Invalid command name in {module_info.name}.py")
        if name in blocks or name in reserved:
            raise ValueError(f"Duplicate or reserved block name: {name}")
        for field in ("title", "icon", "description"):
            value = getattr(provider, field, None)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Block '{name}' must define {field}")
        if len(provider.description) > 256:
            raise ValueError(f"Block '{name}' description exceeds 256 characters")
        if any(not action.startswith(f"{name}_") for action in provider.extra_actions):
            raise ValueError(f"Nested actions for '{name}' must start with '{name}_'")
        blocks[name] = provider

    actions = set()
    for name, provider in blocks.items():
        for action in (f"cmd_{name}", *provider.extra_actions):
            if action in actions or action in reserved_actions:
                raise ValueError(f"Duplicate or reserved block action: {action}")
            actions.add(action)
    return blocks


def _switches(text: str, known: Iterable[str]) -> dict[str, bool]:
    document = tomllib.loads(text)

    if set(document) != {"blocks"} or not isinstance(document["blocks"], dict):
        raise ValueError("Expected a single [blocks] table")

    switches = document["blocks"]
    unknown = switches.keys() - known
    if unknown:
        raise ValueError(f"Unknown blocks: {', '.join(sorted(unknown))}")
    for name, enabled in switches.items():
        if not isinstance(enabled, bool):
            raise ValueError(f"Block '{name}' must be true or false")

    return switches


def select_blocks(
    path: Path, providers: Iterable[str], template: Path | None = None
) -> tuple[str, ...]:
    """Create or update a local file and return only its explicitly enabled blocks."""
    known = tuple(providers)
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / ".blocks-config.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with path.open("r", encoding="utf-8", newline="") as stream:
                original = stream.read()
                mode = os.fstat(stream.fileno()).st_mode & 0o777
        except FileNotFoundError:
            original = None
            mode = 0o600

        text = (
            original
            if original is not None
            else (template.read_text(encoding="utf-8") if template is not None else "[blocks]\n")
        )
        switches = _switches(text, known)
        missing = [name for name in known if name not in switches]
        if missing:
            newline = "\r\n" if "\r\n" in text else "\n"
            prefix = "" if re.search(r"^\s*\[blocks\]", text, re.MULTILINE) else "blocks."
            suffix = "" if text.endswith("\n") else newline
            text += suffix + "".join(f"{prefix}{name} = false{newline}" for name in missing)
            switches = _switches(text, known)

        if text != original:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                try:
                    os.fchmod(stream.fileno(), mode)
                    stream.write(text)
                    stream.flush()
                    os.fsync(stream.fileno())
                    current = path.read_bytes().decode("utf-8") if path.exists() else None
                    if current != original:
                        raise ValueError("Blocks configuration changed during the update. Retry.")
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)

    return tuple(name for name, enabled in switches.items() if enabled)
