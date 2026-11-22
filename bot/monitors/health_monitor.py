"""
Background monitoring and alert scheduler.
"""
from collections.abc import Callable, Sequence
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from telegram.ext import Application

from bot.models import Alert
from bot.utils import escape_markdown
from config import EMOJI, get_logger, settings

logger = get_logger(__name__)


class HealthMonitor:
    """Background health monitoring service."""

    def __init__(
        self,
        checks: Sequence[Callable[[], Alert | None]],
        bot_app: Application,
    ) -> None:
        self.checks = tuple(checks)
        self.bot_app = bot_app
        self.scheduler: Optional[AsyncIOScheduler] = None
        self._alert_chat_ids: set[int] = set()

    def register_alert_chat(self, chat_id: int) -> None:
        """
        Register a chat to receive alerts.

        Args:
            chat_id: Telegram chat ID
        """
        self._alert_chat_ids.add(chat_id)
        logger.info(f"Registered alert chat: {chat_id}")

    def unregister_alert_chat(self, chat_id: int) -> None:
        """
        Unregister a chat from receiving alerts.

        Args:
            chat_id: Telegram chat ID
        """
        self._alert_chat_ids.discard(chat_id)
        logger.info(f"Unregistered alert chat: {chat_id}")

    async def check_system_health(self) -> None:
        """Check system health and trigger alerts if needed."""
        for check in self.checks:
            try:
                alert = check()
                if alert:
                    await self._send_alert_to_chats(alert)
            except Exception:
                logger.exception("Error checking an enabled block for alerts")

    async def _send_alert_to_chats(self, alert: Alert) -> None:
        """
        Send alert to all registered chats.

        Args:
            alert: Alert object
        """
        if not self._alert_chat_ids:
            logger.warning("No chats registered for alerts")
            return

        # Format alert message
        severity_emoji = {
            "info": EMOJI["info"],
            "warning": EMOJI["warning"],
            "critical": EMOJI["error"],
        }
        
        emoji = severity_emoji.get(alert.severity, EMOJI["warning"])
        
        message = (
            f"{emoji} *ALERT: {escape_markdown(alert.title)}*\n\n"
            f"{escape_markdown(alert.message)}\n\n"
            f"Threshold: {escape_markdown(f'{alert.threshold:.1f}%')}\n"
            f"Current: {escape_markdown(f'{alert.metric_value:.1f}%')}\n"
            f"Severity: {escape_markdown(alert.severity.upper())}"
        )

        # Send to all registered chats
        for chat_id in self._alert_chat_ids:
            try:
                await self.bot_app.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode="MarkdownV2",
                )
                logger.info(f"Alert sent to chat {chat_id}: {alert.title}")
            except Exception as e:
                logger.error(f"Failed to send alert to chat {chat_id}: {e}")

    def start(self) -> None:
        """Start the health monitoring scheduler."""
        if self.scheduler and self.scheduler.running:
            logger.warning("Health monitor already running")
            return

        self.scheduler = AsyncIOScheduler()
        
        # Schedule health check
        self.scheduler.add_job(
            self.check_system_health,
            trigger=IntervalTrigger(seconds=settings.alert_check_interval),
            id="health_check",
            name="System Health Check",
            replace_existing=True,
        )

        self.scheduler.start()
        logger.info(f"Health monitor started (interval: {settings.alert_check_interval}s)")

        # Register all allowed users for alerts
        for user_id in settings.allowed_user_ids:
            self.register_alert_chat(user_id)

    def stop(self) -> None:
        """Stop the health monitoring scheduler."""
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Health monitor stopped")
        else:
            logger.warning("Health monitor not running")
