"""Telegram views returned by blocks."""

from dataclasses import dataclass, field
from io import BytesIO

from telegram import InlineKeyboardMarkup

from bot.utils import get_back_to_main_keyboard


@dataclass(frozen=True)
class BlockView:
    """A MarkdownV2 message with a keyboard and an optional chart."""

    text: str
    keyboard: InlineKeyboardMarkup = field(default_factory=get_back_to_main_keyboard)
    photo: BytesIO | None = None
    chart_caption: str = ""
