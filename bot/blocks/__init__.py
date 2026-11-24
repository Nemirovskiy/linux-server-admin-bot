"""Builtin block package and legacy API imports."""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.blocks.base import Block, BlockView

__all__ = ["Block", "BlockView"]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module("bot.blocks.base"), name)
    globals()[name] = value
    return value
