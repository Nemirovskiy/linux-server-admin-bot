"""
Main application entry point.

Copyright 2026 Linux Server Admin Bot Contributors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
# =============================================================================
# CRITICAL: Environment setup MUST happen before ANY other imports
# =============================================================================
import os

# Configure psutil to use host's /proc and /sys
_host_proc = os.environ.get("HOST_PROC_PATH", "/host/proc")
_host_sys = os.environ.get("HOST_SYS_PATH", "/host/sys")

if os.path.exists(_host_proc):
    os.environ["PSUTIL_PROCFS_PATH"] = _host_proc
    print(f"[ENV] Set PSUTIL_PROCFS_PATH={_host_proc}")

if os.path.exists(_host_sys):
    os.environ["PSUTIL_SYSFS_PATH"] = _host_sys
    print(f"[ENV] Set PSUTIL_SYSFS_PATH={_host_sys}")

# =============================================================================
# Now safe to import everything else
# =============================================================================
import argparse
import asyncio
import signal
import sys
from pathlib import Path
from typing import Optional

from telegram import BotCommand
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from bot.blocks import Block, discover_blocks, select_blocks
from bot.handlers import BasicHandlers, CallbackHandlers
from bot.handlers.basic import CORE_COMMANDS
from bot.monitors import HealthMonitor
from bot.services import AlertManager, SystemMonitor
from config import get_logger, settings, setup_logging

logger = get_logger(__name__)


class BotApplication:
    """Main bot application."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.app: Optional[Application] = None
        self.blocks: tuple[Block, ...] = ()
        self.health_monitor: Optional[HealthMonitor] = None

    async def initialize(self, check_only: bool = False) -> None:
        providers = discover_blocks(CORE_COMMANDS)
        defaults = {name: provider.enabled_by_default for name, provider in providers.items()}
        try:
            enabled = select_blocks(self.config_path, defaults)
        except (OSError, ValueError) as exc:
            raise ValueError(f"{self.config_path}: {exc}") from exc

        if check_only:
            print(f"Enabled blocks: {', '.join(enabled) or 'none'}")
            return

        monitor = SystemMonitor(
            proc_path=settings.host_proc_path,
            sys_path=settings.host_sys_path,
        )
        alerts = AlertManager(
            cpu_threshold=settings.cpu_alert_threshold,
            memory_threshold=settings.memory_alert_threshold,
            disk_threshold=settings.disk_alert_threshold,
            cooldown_seconds=settings.alert_cooldown,
        )
        self.blocks = tuple(providers[name](monitor, alerts) for name in enabled)
        self.app = Application.builder().token(settings.telegram_bot_token).build()
        basic = BasicHandlers(alerts, self.blocks)
        disabled_actions = {
            action
            for name, provider in providers.items() if name not in enabled
            for action in (f"cmd_{name}", *provider.extra_actions)
        }
        callbacks = CallbackHandlers(basic, disabled_actions)

        for command in CORE_COMMANDS:
            self.app.add_handler(CommandHandler(command, getattr(basic, f"{command}_command")))
        for block in self.blocks:
            self.app.add_handler(CommandHandler(block.name, block.command))
        self.app.add_handler(CallbackQueryHandler(callbacks.handle_callback))

        checks = [block.check_alert for block in self.blocks if block.sends_alerts]
        if checks:
            self.health_monitor = HealthMonitor(checks, self.app)

        logger.info("Enabled blocks: %s", ", ".join(enabled) or "none")

    async def start(self) -> None:
        """Start the bot application."""
        if not self.app:
            raise RuntimeError("Application not initialized")

        logger.info("Starting bot application...")

        await self.app.initialize()
        await self.app.start()
        await self._set_bot_commands()
        await self.app.updater.start_polling(drop_pending_updates=True)

        if self.health_monitor:
            self.health_monitor.start()

        logger.info("Bot is running! Press Ctrl+C to stop.")

    async def _set_bot_commands(self) -> None:
        """Set the bot commands menu visible in Telegram."""
        commands = [BotCommand(name, description) for name, description in CORE_COMMANDS.items()]
        commands.extend(BotCommand(block.name, block.description) for block in self.blocks)

        await self.app.bot.set_my_commands(commands)
        logger.info("Bot commands menu configured")

    async def stop(self) -> None:
        """Stop the bot application gracefully."""
        logger.info("Stopping bot application...")

        # Stop health monitor
        if self.health_monitor:
            self.health_monitor.stop()

        # Stop bot
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

        logger.info("Bot application stopped")

    async def run(self) -> None:
        """Run the bot application."""
        await self.initialize()
        await self.start()

        # Wait for termination signal
        stop_event = asyncio.Event()

        def signal_handler(sig: int, frame: any) -> None:
            """Handle termination signals."""
            logger.info(f"Received signal {sig}, shutting down...")
            stop_event.set()

        # Register signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Wait for stop event
        await stop_event.wait()

        # Cleanup
        await self.stop()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--blocks-config", type=Path,
        default=Path(os.environ.get("BLOCKS_CONFIG", Path(__file__).with_name("blocks.toml"))),
        help="path to the TOML block configuration",
    )
    parser.add_argument("--check-blocks", action="store_true", help="validate the block config and exit")
    args = parser.parse_args()
    bot_app = BotApplication(args.blocks_config)

    if args.check_blocks:
        try:
            await bot_app.initialize(check_only=True)
        except ValueError as exc:
            parser.exit(1, f"{exc}\n")
        return

    # Setup logging
    setup_logging()

    # Ensure required directories exist
    settings.ensure_directories()

    logger.info("=" * 60)
    logger.info("Linux Server Admin Bot starting...")
    logger.info(f"Allowed users: {settings.allowed_user_ids}")
    logger.info("=" * 60)

    try:
        await bot_app.run()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Bot application terminated")


if __name__ == "__main__":
    asyncio.run(main())
