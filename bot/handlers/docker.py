"""
Docker command handlers.
"""
from telegram import Update
from telegram.ext import ContextTypes

from bot.services import DockerManager
from bot.utils import escape_markdown, get_back_to_main_keyboard, standard_handler
from config import EMOJI, get_logger

logger = get_logger(__name__)


class DockerHandlers:
    """Handlers for Docker management commands."""

    def __init__(self, docker_manager: DockerManager) -> None:
        """
        Initialize Docker handlers.

        Args:
            docker_manager: Docker manager service
        """
        self.docker_manager = docker_manager

    @standard_handler
    async def docker_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Handle /docker command - show Docker menu or unavailable message.

        Args:
            update: Telegram update
            context: Bot context
        """
        from bot.utils.keyboards import get_docker_menu_keyboard

        try:
            if not self.docker_manager.is_available():
                error_msg = self.docker_manager.get_error_message() or "Unknown error"
                message = (
                    f"{EMOJI['docker']} *DOCKER*\n\n"
                    f"{EMOJI['error']} Docker is not available on this server\\.\n\n"
                    f"_Possible reasons:_\n"
                    f"• Docker is not installed\n"
                    f"• Docker daemon is not running\n"
                    f"• No permission to access Docker socket\n\n"
                    f"_Error:_ `{escape_markdown(error_msg[:100])}`"
                )
                await update.message.reply_text(
                    message,
                    parse_mode="MarkdownV2",
                    reply_markup=get_back_to_main_keyboard(),
                )
                return

            # Docker is available, show menu
            counts = self.docker_manager.get_container_count()
            message = (
                f"{EMOJI['docker']} *DOCKER*\n\n"
                f"{EMOJI['success']} Docker is available\n\n"
                f"*Containers:*\n"
                f"  🟢 Running: {counts['running']}\n"
                f"  🔴 Stopped: {counts['stopped']}\n"
                f"  📦 Total: {counts['total']}\n\n"
                f"_Select an option below:_"
            )
            await update.message.reply_text(
                message,
                parse_mode="MarkdownV2",
                reply_markup=get_docker_menu_keyboard(),
            )

        except Exception as e:
            logger.error(f"Error in docker_command: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")
