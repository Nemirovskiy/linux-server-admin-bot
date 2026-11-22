"""Permanent commands and the shared status, help and alert views."""
import asyncio
from collections.abc import Sequence

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.blocks import Block
from bot.services import AlertManager
from bot.utils import escape_markdown, get_main_menu_keyboard, standard_handler
from config import COMMANDS, EMOJI, get_logger

logger = get_logger(__name__)

CORE_COMMANDS = {
    "start": "Main menu",
    "status": "System status",
    "alerts": "Alert configuration",
    "help": "Help",
    "author": "Bot author",
}


class BasicHandlers:
    def __init__(self, alert_manager: AlertManager, blocks: Sequence[Block]) -> None:
        self.alert_manager = alert_manager
        self.blocks = tuple(blocks)

    def menu_text(self, first_name: str = "") -> str:
        welcome = f"Welcome, {escape_markdown(first_name)}" if first_name else "Welcome"
        return (
            f"{EMOJI['rocket']} *Linux Server Admin Bot*\n\n"
            f"{welcome}\\! Select an option below:"
        )

    def menu_keyboard(self) -> InlineKeyboardMarkup:
        return get_main_menu_keyboard(self.blocks)

    @standard_handler
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            self.menu_text(update.effective_user.first_name),
            parse_mode="MarkdownV2",
            reply_markup=self.menu_keyboard(),
        )

    async def status_text(self) -> str:
        lines = [f"{EMOJI['chart']} *System Status*"]
        summaries = await asyncio.gather(
            *(asyncio.to_thread(block.summary) for block in self.blocks),
            return_exceptions=True,
        )
        for block, summary in zip(self.blocks, summaries, strict=True):
            if isinstance(summary, BaseException):
                logger.warning("Could not read block %s: %s", block.name, summary)
                summary = "Unavailable"
            lines.append(f"{block.icon} *{escape_markdown(block.title)}:* {escape_markdown(summary)}")
        if not self.blocks:
            lines.append("No information blocks enabled\\.")
        return "\n\n".join(lines)

    @standard_handler
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(await self.status_text(), parse_mode="MarkdownV2")

    async def help_text(self) -> str:
        lines = [f"{EMOJI['help']} *Available Commands*", ""]
        for command in CORE_COMMANDS:
            lines.append(f"`/{command}` \\- {escape_markdown(COMMANDS[f'/{command}'])}")
        for block in self.blocks:
            lines.append(f"`/{block.name}` \\- {escape_markdown(block.description)}")
        return "\n".join(lines)

    @standard_handler
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(await self.help_text(), parse_mode="MarkdownV2")

    async def alerts_text(self) -> str:
        lines = [f"{EMOJI['alert']} *Alert Configuration*", ""]
        thresholds = [
            f"• {escape_markdown(block.title)}: {block.alert_threshold}%"
            for block in self.blocks
            if block.alert_threshold is not None
        ]
        if thresholds:
            lines.extend(["*Thresholds*", *thresholds])
            lines.append(f"• Cooldown: {self.alert_manager.cooldown_seconds // 60} minutes")
        else:
            lines.append("No thresholds configured for enabled blocks\\.")

        active_alerts = self.alert_manager.get_active_alerts()
        lines.append("")
        if active_alerts:
            lines.append(f"*Active Alerts:* {len(active_alerts)}")
            for severity, count in self.alert_manager.get_alert_summary().items():
                lines.append(f"• {escape_markdown(severity.title())}: {count}")
            lines.append("")
            for alert in active_alerts[:5]:
                lines.append(f"• {escape_markdown(alert.message)}")
        else:
            lines.append(f"{EMOJI['success']} No active alerts\\.")
        return "\n".join(lines)

    @standard_handler
    async def alerts_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(await self.alerts_text(), parse_mode="MarkdownV2")

    @standard_handler
    async def author_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Handle /author command - show bot author information.

        Args:
            update: Telegram update
            context: Bot context
        """
        message = (
            "👨‍💻 *AUTHOR*\n\n"
            "Arturo Carretero Calvo\n\n"
            "💻 *GitHub:*\n"
            "[github\\.com/ArtCC](https://github.com/ArtCC)\n\n"
            "🚀 Check out my other projects\\!\n\n"
            "✨ *Built with:*\n"
            "GitHub Copilot \\(Claude Opus 4\\.5\\)\n\n"
            "📄 *License:* Apache 2\\.0"
        )
        
        await update.message.reply_text(message, parse_mode="MarkdownV2")