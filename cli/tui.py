"""Keyboard-driven editing, with an ANSI fallback for Linux builds without curses."""

from __future__ import annotations

import os
import select
import sys
import termios
import tty
from collections.abc import Callable, Mapping
from typing import Protocol, TypeVar


class Screen(Protocol):
    def size(self) -> tuple[int, int]: ...
    def clear(self) -> None: ...
    def line(self, y: int, text: str, selected: bool = False) -> None: ...
    def refresh(self) -> None: ...
    def key(self) -> str: ...


T = TypeVar("T")


def open_configure(plugins: list[dict]) -> dict[str, bool] | None:
    return open_terminal(lambda screen: configure(screen, plugins))


def open_terminal(work: Callable[[Screen], T]) -> T:
    try:
        import curses
    except ImportError:
        screen = AnsiScreen()
        previous = termios.tcgetattr(screen.fd)
        try:
            tty.setcbreak(screen.fd)
            sys.stdout.write("\x1b[?1049h\x1b[?25l")
            return work(screen)
        finally:
            termios.tcsetattr(screen.fd, termios.TCSADRAIN, previous)
            sys.stdout.write("\x1b[0m\x1b[?25h\x1b[?1049l")
            sys.stdout.flush()
    try:
        return curses.wrapper(lambda window: work(CursesScreen(window, curses)))
    except curses.error as exc:
        raise ValueError(
            "Could not open the terminal interface. Check TERM and terminal size."
        ) from exc


def configure(screen: Screen, plugins: list[dict]) -> dict[str, bool] | None:
    if not plugins:
        return {}
    switches = {plugin["id"]: plugin["enabled"] for plugin in plugins}
    original = switches.copy()
    selected = 0
    while True:
        changed = {name: enabled for name, enabled in switches.items() if enabled != original[name]}
        _draw(screen, plugins, switches, selected, len(changed))
        key = screen.key().lower()
        if key == "up":
            selected = (selected - 1) % len(plugins)
        elif key == "down":
            selected = (selected + 1) % len(plugins)
        elif key == " ":
            name = plugins[selected]["id"]
            switches[name] = not switches[name]
        elif key in {"a", "n"}:
            switches = dict.fromkeys(switches, key == "a")
        elif key == "s":
            return changed
        elif key in {"q", "escape"}:
            return None


def _draw(
    screen: Screen,
    plugins: list[dict],
    switches: Mapping[str, bool],
    selected: int,
    changed_count: int,
) -> None:
    screen.clear()
    height, _ = screen.size()
    visible = max(1, height - 5)
    first = min(max(0, selected - visible + 1), max(0, len(plugins) - visible))

    screen.line(0, "Plugin Manager  |  Up/Down select  Space toggle  S save  Q cancel")
    for index in range(first, min(len(plugins), first + visible)):
        plugin = plugins[index]
        name = plugin["id"]
        label = (plugin["metadata"] or {}).get("name", name)
        check = "x" if switches[name] else " "
        screen.line(index - first + 1, f"[{check}] {name:<14} {label}", index == selected)
    plugin = plugins[selected]
    description = (plugin["metadata"] or {}).get("description", plugin.get("error") or "")
    screen.line(height - 3, description)
    screen.line(height - 2, f"{changed_count} pending  |  A enable all  N disable all")
    screen.refresh()


class CursesScreen:
    def __init__(self, window, curses) -> None:
        self.window = window
        self.curses = curses

    def size(self) -> tuple[int, int]:
        return self.window.getmaxyx()

    def clear(self) -> None:
        self.window.erase()

    def line(self, y: int, text: str, selected: bool = False) -> None:
        height, width = self.size()
        if 0 <= y < height and width > 1:
            self.window.addnstr(y, 0, text, width - 1, self.curses.A_REVERSE if selected else 0)

    def refresh(self) -> None:
        self.window.refresh()

    def key(self) -> str:
        value = self.window.getch()
        if value == self.curses.KEY_UP:
            return "up"
        if value == self.curses.KEY_DOWN:
            return "down"
        if value == 27:
            return "escape"
        return chr(value) if 0 <= value < 128 else ""


class AnsiScreen:
    def __init__(self) -> None:
        self.fd = sys.stdin.fileno()

    def size(self) -> tuple[int, int]:
        size = os.get_terminal_size(sys.stdout.fileno())
        return size.lines, size.columns

    def clear(self) -> None:
        sys.stdout.write("\x1b[2J\x1b[H")

    def line(self, y: int, text: str, selected: bool = False) -> None:
        height, width = self.size()
        if 0 <= y < height and width > 1:
            style = "\x1b[7m" if selected else ""
            sys.stdout.write(f"\x1b[{y + 1};1H{style}{text[:width - 1]}\x1b[0m")

    def refresh(self) -> None:
        sys.stdout.flush()

    def key(self) -> str:
        value = os.read(self.fd, 1)
        if value != b"\x1b":
            return value.decode("ascii", errors="ignore") if value else "q"
        if not select.select([self.fd], [], [], 0.08)[0]:
            return "escape"
        prefix = os.read(self.fd, 1)
        if prefix in {b"[", b"O"} and select.select([self.fd], [], [], 0.08)[0]:
            return {b"A": "up", b"B": "down"}.get(os.read(self.fd, 1), "")
        return ""
