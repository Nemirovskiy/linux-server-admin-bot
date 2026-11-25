"""Read and atomically edit the local blocks.toml."""

import fcntl
import json
import re
import stat
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from bot.storage import atomic_write

_BOOLEAN = re.compile(r"\b(?:true|false)\b")


def _literal(enabled: bool) -> str:
    return "true" if enabled else "false"


def _key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else json.dumps(name, ensure_ascii=False)


@dataclass(frozen=True)
class ConfigRevision:
    original: str | None
    text: str
    switches: dict[str, bool]


class PluginConfig:
    def __init__(self, path: Path, template: Path | None = None) -> None:
        self.path = path.resolve()
        self.template = template

    @staticmethod
    def validate(text: str) -> dict[str, bool]:
        document = tomllib.loads(text)
        if set(document) != {"blocks"} or not isinstance(document["blocks"], dict):
            raise ValueError("Expected a single [blocks] table")
        switches = document["blocks"]
        for name, enabled in switches.items():
            if not isinstance(enabled, bool):
                raise ValueError(f"Block '{name}' must be true or false")
        return switches

    def snapshot(self) -> str | None:
        try:
            return self.path.read_bytes().decode("utf-8")
        except FileNotFoundError:
            return None

    def load(self) -> dict[str, bool]:
        original = self.snapshot()
        return {} if original is None else self.validate(original)

    def is_enabled(self, plugin_id: str) -> bool:
        return self.load().get(plugin_id, False)

    def sync(self, defaults: Mapping[str, bool]) -> dict[str, bool]:
        return self.update_switches({}, defaults).switches

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        self.update_switches({plugin_id: enabled}, {})

    def preview(self, defaults: Mapping[str, bool]) -> ConfigRevision:
        return self._plan(self.snapshot(), {}, defaults)

    def plan_switches(
        self, updates: Mapping[str, bool], defaults: Mapping[str, bool]
    ) -> ConfigRevision:
        return self._plan(self.snapshot(), updates, defaults)

    def remove(self, *, expected: str | None) -> None:
        with self._lock():
            if self.snapshot() != expected:
                raise ValueError("Blocks configuration changed during the update. Retry.")
            self.path.unlink(missing_ok=True)

    def update_switches(
        self,
        updates: Mapping[str, bool],
        defaults: Mapping[str, bool],
        *,
        preview: ConfigRevision | None = None,
    ) -> ConfigRevision:
        if any(not isinstance(enabled, bool) for enabled in updates.values()):
            raise TypeError("Plugin switches must be booleans")
        with self._lock():
            original = self.snapshot()
            if preview is not None and original != preview.original:
                raise ValueError("Blocks configuration changed during the update. Retry.")
            revision = self._plan(original, updates, defaults)
            if revision.text != original:
                self._replace(revision.text, original)
            return revision

    def replace(self, text: str, expected: str | None) -> None:
        self.validate(text)
        with self._lock():
            self._replace(text, expected)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_name(".blocks-config.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _plan(
        self, original: str | None, updates: Mapping[str, bool], defaults: Mapping[str, bool]
    ) -> ConfigRevision:
        text = original
        if text is None:
            text = self.template.read_bytes().decode("utf-8") if self.template else "[blocks]\n"
        switches = self.validate(text)
        for name in tuple(switches):
            if name in updates and switches[name] != updates[name]:
                switches[name] = updates[name]
                text = self._replace_switch(text, switches, updates[name])

        additions = {
            name: enabled if original is None else False
            for name, enabled in defaults.items()
            if name not in switches
        }
        additions.update(
            (name, enabled) for name, enabled in updates.items() if name not in switches
        )
        if additions:
            previous_count = len(switches)
            switches.update(additions)
            text = self._append_switches(
                text, switches, additions, inline_comma=bool(previous_count)
            )
        return ConfigRevision(original, text, switches)

    def _matches(self, text: str, expected: dict[str, bool]) -> bool:
        try:
            return self.validate(text) == expected
        except ValueError:
            return False

    def _replace_switch(self, text: str, expected: dict[str, bool], enabled: bool) -> str:
        for match in _BOOLEAN.finditer(text):
            if match[0] == _literal(enabled):
                continue
            candidate = text[: match.start()] + _literal(enabled) + text[match.end() :]
            if self._matches(candidate, expected):
                return candidate
        raise ValueError("Could not edit the block switch while preserving comments")

    def _append_switches(
        self,
        text: str,
        expected: dict[str, bool],
        additions: dict[str, bool],
        *,
        inline_comma: bool,
    ) -> str:
        newline = "\r\n" if "\r\n" in text else "\n"
        separator = "" if text.endswith("\n") else newline
        rows = [f"{_key(name)} = {_literal(enabled)}" for name, enabled in additions.items()]
        for prefix in ("", "blocks."):
            candidate = text + separator + newline.join(prefix + row for row in rows) + newline
            if self._matches(candidate, expected):
                return candidate
        for match in reversed(tuple(re.finditer(r"}", text))):
            insert = (", " if inline_comma else " ") + ", ".join(rows) + " "
            candidate = text[: match.start()] + insert + text[match.start() :]
            if self._matches(candidate, expected):
                return candidate
        raise ValueError("Could not append block switches while preserving comments")

    def _replace(self, text: str, expected: str | None) -> None:
        def check_revision() -> None:
            if self.snapshot() != expected:
                raise ValueError("Blocks configuration changed during the update. Retry.")

        try:
            mode = stat.S_IMODE(self.path.stat().st_mode) & 0o777
        except FileNotFoundError:
            mode = 0o600
        atomic_write(self.path, text, mode, check_revision)
