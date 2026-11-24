"""Public base class for Telegram information blocks."""

import asyncio
from abc import ABC, abstractmethod
from io import BytesIO
from logging import getLogger
from typing import Any, ClassVar

from telegram import CallbackQuery, InlineKeyboardMarkup, Message, Update
from telegram.constants import MessageLimit
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from lsab_api.broker import BrokerResult
from lsab_api.context import PluginContext
from lsab_api.metadata import PluginMetadata
from lsab_api.metrics import Alert
from lsab_api.presentation import escape_markdown, get_back_to_main_keyboard
from lsab_api.views import BlockView

logger = getLogger(__name__)


async def safe_delete_message(message: Message) -> None:
    try:
        await message.delete()
    except BadRequest as exc:
        logger.debug("Could not delete message: %s", exc)


class Block(ABC):
    """Override summary for a simple block, and render or submenu for richer screens."""

    metadata: ClassVar[PluginMetadata]
    extra_actions: tuple[str, ...] = ()
    combine_photo = False

    def __init__(self, context: PluginContext) -> None:
        self.context = context
        self._last_dependency_issue: str | None = None

    async def setup(self) -> None:  # noqa: B027
        """Prepare resources before the block starts."""

    async def start(self) -> None:  # noqa: B027
        """Start work that needs the running application."""

    async def stop(self) -> None:  # noqa: B027
        """Release resources, including after a partial setup or start."""

    async def execute_privileged(self, action: str, **params: Any) -> BrokerResult:
        return await self.context.broker.execute(action, params)

    def execute_privileged_sync(self, action: str, **params: Any) -> BrokerResult:
        """Use the synchronous broker interface from a block's worker thread."""
        execute = getattr(self.context.broker, "execute_sync", None)
        if not callable(execute):
            return BrokerResult(
                False, "Synchronous broker access is unavailable.", "BROKER_UNAVAILABLE"
            )
        return execute(action, params)

    @abstractmethod
    def summary(self) -> str:
        """Return a short, plain-text summary."""

    def status_section(self) -> str:
        """Return this block's MarkdownV2 section for the overall status."""
        title = escape_markdown(self.metadata.name)
        return f"{self.metadata.icon} *{title}:* {escape_markdown(self.summary())}"

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
            f"{self.metadata.icon} *{escape_markdown(self.metadata.name)}*\n\n{body.rstrip()}",
            keyboard if keyboard is not None else get_back_to_main_keyboard(),
            photo,
            chart_caption,
        )

    @property
    def alert_threshold(self) -> int | None:
        return None

    def check_alert(self) -> Alert | None:
        return None

    def dependency_issue(self) -> str | None:
        """Return an actionable error for a missing external dependency."""
        return None

    @property
    def last_dependency_issue(self) -> str | None:
        return self._last_dependency_issue

    def poll_alert(self) -> Alert | None:
        issue = self.dependency_issue()
        if issue != self._last_dependency_issue:
            if issue:
                self.context.logger.warning("%s: %s", self.metadata.id, issue)
            else:
                self.context.logger.info(
                    "%s: dependency available again", self.metadata.id
                )
            self._last_dependency_issue = issue
        if issue is not None:
            return None
        return self.check_alert()

    async def command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        view = await asyncio.to_thread(self.render)
        message = update.effective_message
        if self.combine_photo and self.has_caption(view):
            await message.reply_photo(
                photo=view.photo,
                caption=view.text,
                parse_mode="MarkdownV2",
                reply_markup=view.keyboard,
            )
            return
        await message.reply_text(
            view.text, parse_mode="MarkdownV2", reply_markup=view.keyboard
        )
        if view.photo is not None:
            await message.reply_photo(photo=view.photo, caption=view.chart_caption)

    async def callback(
        self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        action, _, value = query.data.partition("|")
        if action == f"cmd_{self.metadata.id}":
            await self.show_view(query, await asyncio.to_thread(self.render))
        else:
            await self.submenu(query, action, value)

    async def submenu(self, query: CallbackQuery, action: str, value: str) -> None:
        raise ValueError(f"Unknown block action: {action}")

    @staticmethod
    def has_caption(view: BlockView) -> bool:
        return view.photo is not None and len(view.text) <= MessageLimit.CAPTION_LENGTH

    async def show_view(self, query: CallbackQuery, view: BlockView) -> None:
        if self.has_caption(view):
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
            await query.message.reply_photo(
                photo=view.photo, caption=view.chart_caption
            )
