"""Shared behavior for independently loaded Telegram information blocks."""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING

from telegram import CallbackQuery, InlineKeyboardMarkup, Message, Update
from telegram.constants import MessageLimit
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot.models import Alert
from bot.utils import escape_markdown, get_back_to_main_keyboard, standard_handler
from config import get_logger

if TYPE_CHECKING:
    from bot.services import AlertManager, SystemMonitor

logger = get_logger(__name__)


@dataclass(frozen=True)
class BlockView:
    text: str
    keyboard: InlineKeyboardMarkup = field(default_factory=get_back_to_main_keyboard)
    photo: BytesIO | None = None
    chart_caption: str = ""


async def safe_delete_message(message: Message) -> None:
    try:
        await message.delete()
    except BadRequest as exc:
        logger.debug("Could not delete message: %s", exc)


class Block(ABC):
    """Override summary for a simple block, and render or submenu for richer screens."""

    name: str
    title: str
    icon: str
    description: str
    enabled_by_default = False
    extra_actions: tuple[str, ...] = ()
    sends_alerts = False

    def __init__(self, monitor: "SystemMonitor", alerts: "AlertManager") -> None:
        self.monitor = monitor
        self.alerts = alerts

    @abstractmethod
    def summary(self) -> str:
        """Return a short, plain-text status summary."""

    def render(self) -> BlockView:
        return self.view(escape_markdown(self.summary()))

    def view(
        self,
        body: str,
        *,
        keyboard: InlineKeyboardMarkup | None = None,
        photo: BytesIO | None = None,
        chart_caption: str = "",
    ) -> BlockView:
        """Wrap a MarkdownV2 body in the common block layout."""
        return BlockView(
            f"{self.icon} *{escape_markdown(self.title)}*\n\n{body.rstrip()}",
            keyboard if keyboard is not None else get_back_to_main_keyboard(),
            photo,
            chart_caption,
        )

    @property
    def alert_threshold(self) -> int | None:
        return None

    def check_alert(self) -> Alert | None:
        return None

    @standard_handler
    async def command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        view = await asyncio.to_thread(self.render)
        message = update.effective_message
        await message.reply_text(view.text, parse_mode="MarkdownV2", reply_markup=view.keyboard)
        if view.photo is not None:
            await message.reply_photo(photo=view.photo, caption=view.chart_caption)

    async def callback(self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE) -> None:
        action, _, value = query.data.partition("|")
        if action == f"cmd_{self.name}":
            await self.show_view(query, await asyncio.to_thread(self.render))
        else:
            await self.submenu(query, action, value)

    async def submenu(self, query: CallbackQuery, action: str, value: str) -> None:
        raise ValueError(f"Unknown block action: {action}")

    async def show_view(self, query: CallbackQuery, view: BlockView) -> None:
        if view.photo is not None and len(view.text) <= MessageLimit.CAPTION_LENGTH:
            await query.message.reply_photo(
                photo=view.photo,
                caption=view.text,
                parse_mode="MarkdownV2",
                reply_markup=view.keyboard,
            )
            await safe_delete_message(query.message)
            return

        if query.message.photo:
            await query.message.reply_text(
                view.text, parse_mode="MarkdownV2", reply_markup=view.keyboard
            )
            await safe_delete_message(query.message)
        else:
            await query.edit_message_text(
                view.text, parse_mode="MarkdownV2", reply_markup=view.keyboard
            )
        if view.photo is not None:
            await query.message.reply_photo(photo=view.photo, caption=view.chart_caption)
