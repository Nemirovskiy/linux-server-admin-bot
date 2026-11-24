"""Route buttons to the core screens and enabled information blocks."""
from telegram import Update
from telegram.error import TimedOut
from telegram.ext import ContextTypes

from bot.api.block import safe_delete_message
from bot.handlers.basic import BasicHandlers
from bot.utils import get_back_to_main_keyboard
from config import get_logger, settings

logger = get_logger(__name__)


class CallbackHandlers:
    def __init__(
        self, basic: BasicHandlers, disabled_actions: set[str], failed_actions: set[str]
    ) -> None:
        self.basic = basic
        self.disabled_actions = disabled_actions
        self.failed_actions = failed_actions
        self.block_actions = {
            action: block.callback
            for block in basic.blocks
            for action in (f"cmd_{block.metadata.id}", *block.extra_actions)
        }
        self.core_actions = {
            "cmd_status": basic.status_text,
            "cmd_alerts": basic.alerts_text,
            "cmd_help": basic.help_text,
        }

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        action = (query.data or "").partition("|")[0]
        try:
            await query.answer()
        except TimedOut:
            logger.warning("Timed out acknowledging callback %s", action)

        user = update.effective_user
        if not user or user.id not in settings.allowed_user_ids:
            await query.edit_message_text("🔒 Access denied.")
            return

        try:
            if action == "menu_main":
                await self._show_main_menu(query)
            elif action in self.core_actions:
                await query.edit_message_text(
                    await self.core_actions[action](),
                    parse_mode="MarkdownV2",
                    reply_markup=get_back_to_main_keyboard(),
                )
            elif action in self.block_actions:
                await self.block_actions[action](query, context)
            elif action in self.failed_actions:
                await query.edit_message_text(
                    "This block is unavailable.", reply_markup=get_back_to_main_keyboard()
                )
            elif action in self.disabled_actions:
                await query.edit_message_text(
                    "This block is disabled.", reply_markup=get_back_to_main_keyboard()
                )
            elif action != "noop":
                await query.edit_message_text("Unknown action.", reply_markup=get_back_to_main_keyboard())
        except Exception as exc:
            logger.exception("Error handling callback %s", action)
            await query.edit_message_text(
                f"❌ Error: {str(exc)[:200]}", reply_markup=get_back_to_main_keyboard()
            )

    async def _show_main_menu(self, query) -> None:
        message = self.basic.menu_text()
        keyboard = self.basic.menu_keyboard()
        if query.message.photo:
            await safe_delete_message(query.message)
            await query.message.chat.send_message(
                message, parse_mode="MarkdownV2", reply_markup=keyboard
            )
        else:
            await query.edit_message_text(
                message, parse_mode="MarkdownV2", reply_markup=keyboard
            )
