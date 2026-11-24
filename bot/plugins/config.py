"""Preserve the local blocks.toml and update it under an exclusive lock."""

import fcntl
import json
import os
import re
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile

_SWITCH_LINE = re.compile(
    r"^(?P<key>[ \t]*(?:blocks\.)?[a-zA-Z0-9_-]+[ \t]*=[ \t]*)"
    r"(?P<value>true|false)(?=[ \t]*(?:#|\r?$))",
    re.MULTILINE,
)
_TABLE = re.compile(r"^[ \t]*\[blocks\][ \t]*(?:#|\r?$)", re.MULTILINE)


def _literal(enabled: bool) -> str:
    return "true" if enabled else "false"


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

    def load(self) -> dict[str, bool]:
        try:
            return self.validate(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def is_enabled(self, plugin_id: str) -> bool:
        return self.load().get(plugin_id, False)

    def sync(self, defaults: Mapping[str, bool]) -> dict[str, bool]:
        def update(values: dict[str, bool], creating: bool) -> None:
            for name, enabled in defaults.items():
                values.setdefault(name, enabled if creating else False)

        return self._update(update)

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("Plugin switch must be a boolean")
        self._update(lambda values, _: values.__setitem__(plugin_id, enabled))

    def _update(self, edit: Callable[[dict[str, bool], bool], None]) -> dict[str, bool]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_name(".blocks-config.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                with self.path.open("r", encoding="utf-8", newline="") as stream:
                    original = stream.read()
                    mode = os.fstat(stream.fileno()).st_mode & 0o777
            except FileNotFoundError:
                original = None
                mode = 0o600

            if original is not None:
                text = original
            elif self.template is not None:
                with self.template.open("r", encoding="utf-8", newline="") as stream:
                    text = stream.read()
            else:
                text = "[blocks]\n"

            previous = self.validate(text)
            switches = previous.copy()
            edit(switches, original is None)
            if previous != switches:
                text = self._render(text, previous, switches)
            if text != original:
                self._replace(text, original, mode)
            return switches

    def _render(self, text: str, old: dict[str, bool], new: dict[str, bool]) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match["key"].split("=")[0].strip().removeprefix("blocks.")
            if name in old and old[name] != new[name]:
                return match["key"] + _literal(new[name])
            return match[0]

        updated = _SWITCH_LINE.sub(replace, text)
        newline = "\r\n" if "\r\n" in text else "\n"
        prefix = "" if _TABLE.search(text) else "blocks."
        additions = [
            (
                f"{prefix}{json.dumps(name, ensure_ascii=False)} = {_literal(new[name])}"
                if not re.fullmatch(r"[A-Za-z0-9_-]+", name)
                else f"{prefix}{name} = {_literal(new[name])}"
            )
            for name in new
            if name not in old
        ]
        if additions:
            updated += (
                ("" if updated.endswith("\n") else newline) + newline.join(additions) + newline
            )
        try:
            if self.validate(updated) == new:
                return updated
        except ValueError:
            pass

        # Legal inline tables and quoted switch names may require reformatting.
        rows = [
            f"{json.dumps(name, ensure_ascii=False)} = {_literal(enabled)}"
            for name, enabled in new.items()
        ]
        return newline.join(("[blocks]", *rows, ""))

    def _replace(self, text: str, original: str | None, mode: int) -> None:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=self.path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            try:
                os.fchmod(stream.fileno(), mode)
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
                try:
                    current = self.path.read_bytes().decode("utf-8")
                except FileNotFoundError:
                    current = None
                if current != original:
                    raise ValueError("Blocks configuration changed during the update. Retry.")
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
