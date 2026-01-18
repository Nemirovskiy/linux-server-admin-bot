"""
Callback query handlers for inline keyboard buttons.
"""
from telegram import Update
from telegram.ext import ContextTypes

from bot.services import AlertManager, DockerManager, SystemMonitor
from bot.utils import (
    chart_generator,
    escape_markdown,
    format_cpu_metrics,
    format_disk_metrics,
    format_memory_metrics,
    format_system_status,
    format_top_processes,
    get_back_to_main_keyboard,
    get_container_detail_keyboard,
    get_container_list_keyboard,
    get_docker_back_keyboard,
    get_docker_menu_keyboard,
    get_main_menu_keyboard,
)
from config import EMOJI, get_logger, settings

logger = get_logger(__name__)


class CallbackHandlers:
    """Handlers for inline keyboard callback queries."""

    def __init__(
        self,
        system_monitor: SystemMonitor,
        alert_manager: AlertManager,
        docker_manager: DockerManager = None,
    ) -> None:
        """
        Initialize callback handlers.

        Args:
            system_monitor: System monitor service
            alert_manager: Alert manager service
            docker_manager: Docker manager service (optional)
        """
        self.system_monitor = system_monitor
        self.alert_manager = alert_manager
        self.docker_manager = docker_manager

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Main callback handler that routes to specific handlers.

        Args:
            update: Telegram update
            context: Bot context
        """
        query = update.callback_query
        await query.answer()

        user = update.effective_user
        if not user or user.id not in settings.allowed_user_ids:
            await query.edit_message_text("🔒 Access denied.")
            return

        data = query.data
        logger.info(f"Callback from user {user.id}: {data}")

        try:
            # Menu navigation
            if data == "menu_main":
                await self._show_main_menu(query)

            # System commands
            elif data == "cmd_status":
                await self._handle_status(query)
            elif data == "cmd_cpu":
                await self._handle_cpu(query, context)
            elif data == "cmd_memory":
                await self._handle_memory(query, context)
            elif data == "cmd_disk":
                await self._handle_disk(query)
            elif data == "cmd_network":
                await self._handle_network(query)
            elif data == "cmd_top":
                await self._handle_top(query, context)
            elif data == "cmd_temp":
                await self._handle_temp(query)
            elif data == "cmd_uptime":
                await self._handle_uptime(query)
            elif data == "cmd_services":
                await self._handle_services(query)
            elif data == "cmd_alerts":
                await self._handle_alerts(query)
            elif data == "cmd_help":
                await self._handle_help(query)

            # Docker commands
            elif data == "cmd_docker":
                await self._handle_docker(query)
            elif data == "docker_list" or data.startswith("docker_list|"):
                page = 0
                if "|" in data:
                    page = int(data.split("|")[1])
                await self._handle_docker_list(query, page)
            elif data.startswith("docker_page|"):
                page = int(data.split("|")[1])
                await self._handle_docker_list(query, page)
            elif data.startswith("docker_detail|"):
                container_id = data.split("|")[1]
                await self._handle_docker_detail(query, container_id)
            elif data == "noop":
                pass  # Do nothing for pagination indicator

            else:
                await query.edit_message_text(f"Unknown action: {data}")

        except Exception as e:
            logger.error(f"Error handling callback {data}: {e}")
            await query.edit_message_text(
                f"❌ Error: {str(e)[:200]}",
                reply_markup=get_back_to_main_keyboard()
            )

    async def _show_main_menu(self, query) -> None:
        """Show the main menu."""
        message = (
            f"{EMOJI['rocket']} *Linux Server Admin Bot*\n\n"
            f"Welcome\\! Select an option below:\n\n"
            f"{EMOJI['chart']} *System Monitoring*\n"
            f"Monitor CPU, Memory, Disk, Network"
        )
        # If coming from a photo message, delete and send new message
        if query.message.photo:
            await query.message.delete()
            await query.message.chat.send_message(
                message,
                parse_mode="MarkdownV2",
                reply_markup=get_main_menu_keyboard()
            )
        else:
            await query.edit_message_text(
                message,
                parse_mode="MarkdownV2",
                reply_markup=get_main_menu_keyboard()
            )

    async def _handle_status(self, query) -> None:
        """Handle system status callback."""
        status = self.system_monitor.get_system_status()
        message = format_system_status(status)
        await query.edit_message_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=get_back_to_main_keyboard()
        )

    async def _handle_cpu(self, query, context) -> None:
        """Handle CPU callback."""
        cpu = self.system_monitor.get_cpu_metrics()
        message = format_cpu_metrics(cpu)
        
        # Generate chart
        chart_buf = chart_generator.generate_cpu_chart(cpu.percent, cpu.per_cpu)
        
        # Send new message with photo (can't edit to photo)
        await query.message.delete()
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=chart_buf,
            caption=message,
            parse_mode="MarkdownV2",
            reply_markup=get_back_to_main_keyboard()
        )

    async def _handle_memory(self, query, context) -> None:
        """Handle memory callback."""
        memory = self.system_monitor.get_memory_metrics()
        message = format_memory_metrics(memory)
        
        # Generate chart
        chart_buf = chart_generator.generate_memory_chart(
            memory.total_gb, memory.used_gb, memory.available_gb, memory.percent
        )
        
        await query.message.delete()
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=chart_buf,
            caption=message,
            parse_mode="MarkdownV2",
            reply_markup=get_back_to_main_keyboard()
        )

    async def _handle_disk(self, query) -> None:
        """Handle disk callback."""
        disks = self.system_monitor.get_disk_metrics()
        message = format_disk_metrics(disks)
        await query.edit_message_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=get_back_to_main_keyboard()
        )

    async def _handle_network(self, query) -> None:
        """Handle network callback."""
        networks = self.system_monitor.get_network_metrics()
        
        if not networks:
            await query.edit_message_text(
                "No network interfaces found.",
                reply_markup=get_back_to_main_keyboard()
            )
            return

        message = f"{EMOJI['network']} *NETWORK INTERFACES*\n\n"

        for net in networks:
            if net.interface == "lo":
                continue
            
            interface_name = escape_markdown(net.interface)
            message += (
                f"*{interface_name}*\n"
                f"  ↓ RX: {escape_markdown(f'{net.bytes_recv_mb:.2f}')} MB\n"
                f"  ↑ TX: {escape_markdown(f'{net.bytes_sent_mb:.2f}')} MB\n"
                f"  📦 Packets: {net.packets_recv} / {net.packets_sent}\n\n"
            )

        await query.edit_message_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=get_back_to_main_keyboard()
        )

    async def _handle_top(self, query, context) -> None:
        """Handle top processes callback."""
        processes = self.system_monitor.get_top_processes(limit=10)
        message = format_top_processes(processes)
        
        # Generate chart
        processes_data = [
            {"name": p.name, "cpu_percent": p.cpu_percent} for p in processes
        ]
        chart_buf = chart_generator.generate_process_chart(processes_data)
        
        await query.message.delete()
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=chart_buf,
            caption=message,
            parse_mode="MarkdownV2",
            reply_markup=get_back_to_main_keyboard()
        )

    async def _handle_alerts(self, query) -> None:
        """Handle alerts callback."""
        active_alerts = self.alert_manager.get_active_alerts()
        
        message = f"{EMOJI['alert']} *ALERT CONFIGURATION*\n\n"
        message += f"*Thresholds*\n"
        message += f"  {EMOJI['cpu']} CPU: *{settings.cpu_alert_threshold}%*\n"
        message += f"  {EMOJI['memory']} Memory: *{settings.memory_alert_threshold}%*\n"
        message += f"  {EMOJI['disk']} Disk: *{settings.disk_alert_threshold}%*\n\n"
        
        if active_alerts:
            message += f"*Active Alerts:* {len(active_alerts)}\n"
            for alert in active_alerts[:5]:
                message += f"🔴 {escape_markdown(alert.message)}\n"
        else:
            message += f"{EMOJI['success']} No active alerts"

        await query.edit_message_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=get_back_to_main_keyboard()
        )

    async def _handle_help(self, query) -> None:
        """Handle help callback."""
        message = f"*{EMOJI['help']} Available Commands*\n\n"
        
        message += "*📊 System Monitoring*\n"
        message += "`/status` \\- System overview\n"
        message += "`/cpu` \\- CPU information\n"
        message += "`/memory` \\- Memory usage\n"
        message += "`/disk` \\- Disk usage\n"
        message += "`/network` \\- Network stats\n"
        message += "`/top` \\- Top processes\n"
        message += "`/temp` \\- System temperature\n"
        message += "`/uptime` \\- System uptime\n"
        message += "`/services` \\- Services status\n\n"
        
        message += "*🔔 Alerts*\n"
        message += "`/alerts` \\- Alert configuration\n\n"
        
        message += "*ℹ️ Info*\n"
        message += "`/author` \\- Bot author\n\n"
        
        message += "_Use the menu buttons for easier navigation\\!_"

        await query.edit_message_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=get_back_to_main_keyboard()
        )

    async def _handle_temp(self, query) -> None:
        """Handle temperature callback."""
        temps = self.system_monitor.get_temperature()

        if not temps:
            message = (
                f"{EMOJI['warning']} No temperature sensors found\\.\n\n"
                f"_This may happen if:_\n"
                f"• Hardware has no sensors\n"
                f"• Drivers are not installed\n"
                f"• Container has no access to /sys"
            )
            await query.edit_message_text(
                message,
                parse_mode="MarkdownV2",
                reply_markup=get_back_to_main_keyboard()
            )
            return

        message = f"{EMOJI['temp']} *SYSTEM TEMPERATURE*\n\n"

        for sensor_type, sensors in temps.items():
            sensor_name = escape_markdown(sensor_type.replace("_", " ").title())
            message += f"*{sensor_name}:*\n"

            for sensor in sensors:
                label = escape_markdown(sensor.label or "Sensor")
                current = sensor.current
                high = sensor.high
                critical = sensor.critical

                if critical and current >= critical:
                    status = "🔴"
                elif high and current >= high:
                    status = "🟡"
                else:
                    status = "🟢"

                temp_str = escape_markdown(f"{current:.1f}°C")
                message += f"  {status} {label}: {temp_str}"

                if high or critical:
                    limits = []
                    if high:
                        limits.append(f"max: {high:.0f}°C")
                    if critical:
                        limits.append(f"crit: {critical:.0f}°C")
                    limits_str = escape_markdown(f" ({', '.join(limits)})")
                    message += limits_str
                message += "\n"

            message += "\n"

        await query.edit_message_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=get_back_to_main_keyboard()
        )

    async def _handle_uptime(self, query) -> None:
        """Handle uptime callback."""
        info = self.system_monitor.get_uptime_info()

        boot_time_str = info["boot_time"].strftime("%d/%m/%Y %H:%M:%S")
        
        message = f"{EMOJI['clock']} *SYSTEM UPTIME*\n\n"
        
        uptime_parts = []
        if info["days"] > 0:
            uptime_parts.append(f"{info['days']} day{'s' if info['days'] != 1 else ''}")
        if info["hours"] > 0:
            uptime_parts.append(f"{info['hours']} hour{'s' if info['hours'] != 1 else ''}")
        uptime_parts.append(f"{info['minutes']} minute{'s' if info['minutes'] != 1 else ''}")
        
        uptime_str = escape_markdown(", ".join(uptime_parts))
        boot_str = escape_markdown(boot_time_str)
        
        message += f"⏱️ *Uptime:* {uptime_str}\n\n"
        message += f"🔄 *Last boot:* {boot_str}\n\n"
        
        message += f"👥 *Logged in users:* {info['users_count']}\n"
        
        if info["users"]:
            for user in info["users"][:5]:
                user_name = escape_markdown(user["name"])
                terminal = escape_markdown(user["terminal"])
                since = escape_markdown(user["started"].strftime("%H:%M"))
                message += f"  • {user_name} \\({terminal}\\) since {since}\n"
        
        await query.edit_message_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=get_back_to_main_keyboard()
        )

    async def _handle_services(self, query) -> None:
        """Handle services callback."""
        services = self.system_monitor.get_services_status()

        if not services:
            message = (
                f"{EMOJI['warning']} No systemd services found\\.\n\n"
                f"_This may happen if:_\n"
                f"• System doesn't use systemd\n"
                f"• No common services installed\n"
                f"• systemctl is not available"
            )
            await query.edit_message_text(
                message,
                parse_mode="MarkdownV2",
                reply_markup=get_back_to_main_keyboard()
            )
            return

        message = f"{EMOJI['services']} *SERVICES STATUS*\n\n"

        running = [s for s in services if s["is_running"]]
        stopped = [s for s in services if not s["is_running"]]

        if running:
            message += f"🟢 *Active \\({len(running)}\\):*\n"
            for svc in running:
                name = escape_markdown(svc["name"])
                sub = escape_markdown(svc["sub_state"])
                message += f"  • {name} \\({sub}\\)\n"
            message += "\n"

        if stopped:
            message += f"🔴 *Inactive \\({len(stopped)}\\):*\n"
            for svc in stopped:
                name = escape_markdown(svc["name"])
                status = escape_markdown(svc["status"])
                message += f"  • {name} \\({status}\\)\n"

        await query.edit_message_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=get_back_to_main_keyboard()
        )

    async def _handle_docker(self, query) -> None:
        """Handle Docker menu callback."""
        if not self.docker_manager:
            await query.edit_message_text(
                f"{EMOJI['docker']} *DOCKER*\n\n"
                f"{EMOJI['error']} Docker manager not initialized\\.",
                parse_mode="MarkdownV2",
                reply_markup=get_back_to_main_keyboard()
            )
            return

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
            await query.edit_message_text(
                message,
                parse_mode="MarkdownV2",
                reply_markup=get_back_to_main_keyboard()
            )
            return

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
        await query.edit_message_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=get_docker_menu_keyboard()
        )

    async def _handle_docker_list(self, query, page: int = 0) -> None:
        """Handle Docker list containers callback with pagination."""
        if not self.docker_manager or not self.docker_manager.is_available():
            await query.edit_message_text(
                f"{EMOJI['error']} Docker is not available\\.",
                parse_mode="MarkdownV2",
                reply_markup=get_back_to_main_keyboard()
            )
            return

        containers, total, total_pages = self.docker_manager.get_containers_page(page=page, per_page=10)

        if total == 0:
            message = (
                f"{EMOJI['container']} *CONTAINERS*\n\n"
                f"No containers found\\."
            )
            await query.edit_message_text(
                message,
                parse_mode="MarkdownV2",
                reply_markup=get_docker_back_keyboard()
            )
            return

        counts = self.docker_manager.get_container_count()
        message = (
            f"{EMOJI['container']} *CONTAINERS* "
            f"\\(🟢 {counts['running']} \\| 🔴 {counts['stopped']} \\| 📦 {total}\\)\n\n"
            f"_Select a container to see details:_"
        )

        keyboard = get_container_list_keyboard(
            containers=containers,
            page=page,
            total_pages=total_pages,
            get_status_emoji=self.docker_manager.get_status_emoji
        )

        await query.edit_message_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=keyboard
        )

    async def _handle_docker_detail(self, query, container_id: str) -> None:
        """Handle Docker container detail view."""
        if not self.docker_manager or not self.docker_manager.is_available():
            await query.edit_message_text(
                f"{EMOJI['error']} Docker is not available\\.",
                parse_mode="MarkdownV2",
                reply_markup=get_back_to_main_keyboard()
            )
            return

        container = self.docker_manager.get_container_by_id(container_id)

        if not container:
            await query.edit_message_text(
                f"{EMOJI['error']} Container not found\\.",
                parse_mode="MarkdownV2",
                reply_markup=get_docker_back_keyboard()
            )
            return

        status_emoji = self.docker_manager.get_status_emoji(container.status)
        name = escape_markdown(container.name)
        image = escape_markdown(container.image[:40])
        status = escape_markdown(container.status)

        message = f"{status_emoji} *{name}*\n\n"
        message += f"*Status:* {status}\n"
        message += f"*Image:* `{image}`\n"

        if container.health:
            health = escape_markdown(container.health)
            message += f"*Health:* {health}\n"

        if container.ip_address:
            ip = escape_markdown(container.ip_address)
            message += f"*IP:* `{ip}`\n"

        if container.ports:
            ports = escape_markdown(container.ports)
            message += f"*Ports:* {ports}\n"

        message += "\n"

        # Resource usage (only for running containers)
        if container.status == 'running':
            if container.cpu_percent is not None:
                cpu = escape_markdown(f"{container.cpu_percent:.1f}%")
                message += f"*CPU:* {cpu}\n"

            if container.memory_mb is not None:
                if container.memory_limit_mb and container.memory_percent:
                    mem = escape_markdown(
                        f"{container.memory_mb:.1f} / {container.memory_limit_mb:.0f} MB ({container.memory_percent:.1f}%)"
                    )
                else:
                    mem = escape_markdown(f"{container.memory_mb:.1f} MB")
                message += f"*Memory:* {mem}\n"

            message += "\n"

        # Timestamps
        if container.created:
            created = escape_markdown(container.created)
            message += f"*Created:* {created}\n"

        if container.started and container.status == 'running':
            started = escape_markdown(container.started)
            message += f"*Started:* {started}\n"

        if container.restart_count > 0:
            message += f"*Restarts:* {container.restart_count}\n"

        await query.edit_message_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=get_container_detail_keyboard(container_id)
        )
