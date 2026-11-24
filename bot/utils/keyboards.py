"""
Inline keyboard layouts for the bot.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from lsab_api.presentation import get_back_to_main_keyboard as get_back_to_main_keyboard
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import EMOJI

if TYPE_CHECKING:
    from bot.api import Block


def get_main_menu_keyboard(blocks: Sequence[Block]) -> InlineKeyboardMarkup:
    """Show permanent actions and buttons for the enabled information blocks."""
    buttons = [
        InlineKeyboardButton(
            f"{block.metadata.icon} {block.metadata.name}", callback_data=f"cmd_{block.metadata.id}"
        )
        for block in blocks
    ]
    keyboard = [
        [InlineKeyboardButton(f"{EMOJI['chart']} System Status", callback_data="cmd_status")],
        *(buttons[index:index + 2] for index in range(0, len(buttons), 2)),
        [
            InlineKeyboardButton(f"{EMOJI['alert']} Alerts", callback_data="cmd_alerts"),
            InlineKeyboardButton(f"{EMOJI['help']} Help", callback_data="cmd_help"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)
