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
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Optional

from telegram import BotCommand
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from bot.api import Block, NullBroker, PluginContext
from bot.blocks import Block as LegacyBlock
from bot.blocks import discover_blocks, select_blocks
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
        self._shutdown = AsyncExitStack()

    async def initialize(self, check_only: bool = False) -> None:
        providers = discover_blocks(CORE_COMMANDS)
        try:
            enabled = select_blocks(self.config_path, providers, Path(__file__).with_name("blocks.toml.example"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"{self.config_path}: {exc}") from exc

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
        broker = NullBroker()
        blocks = []
        for name in enabled:
            provider = providers[name]
            context = PluginContext(name, {}, get_logger(provider.__module__), monitor, alerts, broker)
            block = provider._from_context(context) if issubclass(provider, LegacyBlock) else provider(context)
            blocks.append(block)
        self.blocks = tuple(blocks)
        if check_only:
            print(f"Enabled blocks: {', '.join(enabled) or 'none'}")
            issues = []
            for block in self.blocks:
                issue = block.dependency_issue()
                if issue:
                    issues.append(f"{block.metadata.id}: {issue}")
            if issues:
                raise ValueError("\n".join(issues))
            return

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
            self.app.add_handler(CommandHandler(block.metadata.id, block.command))
        self.app.add_handler(CallbackQueryHandler(callbacks.handle_callback))

        checks = [block.poll_alert for block in self.blocks if block.metadata.sends_alerts]
        if checks:
            self.health_monitor = HealthMonitor(checks, self.app)

        logger.info("Enabled blocks: %s", ", ".join(enabled) or "none")

    async def start(self) -> None:
        """Start the bot application."""
        if not self.app:
            raise RuntimeError("Application not initialized")

        logger.info("Starting bot application...")

        async with AsyncExitStack() as shutdown:
            await self.app.initialize()
            shutdown.push_async_callback(self.app.shutdown)
            await self.app.start()
            shutdown.push_async_callback(self.app.stop)

            for block in self.blocks:
                shutdown.push_async_callback(block.stop)
                await block.setup()
            for block in self.blocks:
                await block.start()

            await self._set_bot_commands()
            await self.app.updater.start_polling(drop_pending_updates=True)
            shutdown.push_async_callback(self.app.updater.stop)
            if self.health_monitor:
                self.health_monitor.start()
                shutdown.callback(self.health_monitor.stop)

            self._shutdown = shutdown.pop_all()

        logger.info("Bot is running! Press Ctrl+C to stop.")

    async def _set_bot_commands(self) -> None:
        """Set the bot commands menu visible in Telegram."""
        commands = [BotCommand(name, description) for name, description in CORE_COMMANDS.items()]
        commands.extend(BotCommand(block.metadata.id, block.metadata.description) for block in self.blocks)

        await self.app.bot.set_my_commands(commands)
        logger.info("Bot commands menu configured")

    async def stop(self) -> None:
        """Stop the bot application gracefully."""
        logger.info("Stopping bot application...")

        await self._shutdown.aclose()
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

        try:
            await stop_event.wait()
        finally:
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
