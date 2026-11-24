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

from bot.api import Block
from bot.commands import CORE_COMMANDS
from bot.handlers import BasicHandlers, CallbackHandlers
from bot.monitors import HealthMonitor
from bot.plugins.config import PluginConfig
from bot.plugins.context import default_context_factory
from bot.plugins.manager import PluginManager
from bot.plugins.models import PluginState
from bot.plugins.sources import BuiltinPluginSource
from bot.runtime_status import RuntimeStatus
from bot.services import AlertManager
from config import get_logger, settings, setup_logging

logger = get_logger(__name__)


class BotApplication:
    """Main bot application."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.app: Optional[Application] = None
        self.plugin_manager: PluginManager | None = None
        self.alerts: AlertManager | None = None
        self.health_monitor: Optional[HealthMonitor] = None
        self._shutdown = AsyncExitStack()

    @property
    def blocks(self) -> tuple[Block, ...]:
        if self.plugin_manager is None:
            return ()
        return tuple(
            record.instance
            for record in self.plugin_manager.startup_records()
            if record.instance is not None
        )

    async def initialize(self, check_only: bool = False) -> None:
        if not check_only:
            settings.require_telegram()
        factory = default_context_factory()
        self.alerts = factory.alerts
        self.plugin_manager = PluginManager(
            sources=(BuiltinPluginSource(),),
            config=PluginConfig(self.config_path, Path(__file__).with_name("blocks.toml.example")),
            context_factory=factory,
        )
        try:
            self.plugin_manager.discover()
        except (OSError, ValueError) as exc:
            raise ValueError(f"{self.config_path}: {exc}") from exc
        if check_only:
            await self.plugin_manager.load_enabled()
            await self.plugin_manager.check_enabled()
            records = self.plugin_manager.startup_records()
            print(f"Enabled blocks: {', '.join(record.plugin_id for record in records) or 'none'}")
            issues = [
                f"{record.plugin_id}: {record.error or record.dependency_issue}"
                for record in records if record.error or record.dependency_issue
            ]
            if issues:
                raise ValueError("\n".join(issues))
            return

        self.app = Application.builder().token(settings.telegram_bot_token).build()

    def _register_handlers(self) -> None:
        manager = self.plugin_manager
        blocks = manager.running_blocks()
        basic = BasicHandlers(self.alerts, blocks)
        disabled_actions = set()
        failed_actions = set()
        for record in manager.list_plugins():
            declared = record.descriptor.actions if record.descriptor else (f"cmd_{record.plugin_id}",)
            actions = {action for action in declared if isinstance(action, str)}
            if not record.enabled:
                disabled_actions.update(actions)
            elif record.state is PluginState.FAILED:
                failed_actions.update(actions)
        callbacks = CallbackHandlers(basic, disabled_actions, failed_actions)

        for command in CORE_COMMANDS:
            self.app.add_handler(CommandHandler(command, getattr(basic, f"{command}_command")))
        for block in blocks:
            self.app.add_handler(CommandHandler(block.metadata.id, block.command))
        self.app.add_handler(CallbackQueryHandler(callbacks.handle_callback))

        checks = manager.alert_checks()
        self.health_monitor = HealthMonitor(checks, self.app) if checks else None
        logger.info("Running blocks: %s", ", ".join(block.metadata.id for block in blocks) or "none")

    async def start(self) -> None:
        """Start the bot application."""
        if self.app is None or self.plugin_manager is None:
            raise RuntimeError("Application not initialized")

        logger.info("Starting bot application...")

        async with AsyncExitStack() as shutdown:
            await self.app.initialize()
            shutdown.push_async_callback(self.app.shutdown)
            await self.app.start()
            shutdown.push_async_callback(self.app.stop)

            shutdown.push_async_callback(self.plugin_manager.stop_all)
            await self.plugin_manager.load_enabled()
            await self.plugin_manager.setup_all()
            await self.plugin_manager.start_all()
            await self.plugin_manager.check_enabled()

            self._register_handlers()
            await self._set_bot_commands()
            await self.app.updater.start_polling(drop_pending_updates=True)
            shutdown.push_async_callback(self.app.updater.stop)
            if self.health_monitor:
                shutdown.push_async_callback(self.health_monitor.stop)
                self.health_monitor.start()

            self._shutdown = shutdown.pop_all()

        logger.info("Bot is running! Press Ctrl+C to stop.")

    async def _set_bot_commands(self) -> None:
        """Set the bot commands menu visible in Telegram."""
        commands = [BotCommand(name, description) for name, description in CORE_COMMANDS.items()]
        commands.extend(BotCommand(block.metadata.id, block.metadata.description) for block in self.plugin_manager.running_blocks())

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

        assert self.plugin_manager is not None
        status = RuntimeStatus(self.config_path, self.plugin_manager)
        try:
            status.start()
            await stop_event.wait()
        finally:
            try:
                await status.close()
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
