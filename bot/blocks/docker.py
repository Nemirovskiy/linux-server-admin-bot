"""Docker information, nested container screens, and management actions."""

import asyncio
from dataclasses import dataclass
from typing import List, Optional

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.blocks.base import Block, BlockView
from bot.services import AlertManager, SystemMonitor
from bot.utils import escape_markdown, get_back_to_main_keyboard
from config import EMOJI, get_logger

logger = get_logger(__name__)

# Try to import docker, handle if not installed
try:
    import docker
    from docker.errors import DockerException

    DOCKER_SDK_AVAILABLE = True
except ImportError:
    DOCKER_SDK_AVAILABLE = False
    docker = None
    DockerException = Exception


class DockerBlock(Block):
    name = "docker"
    title = "Docker"
    icon = EMOJI["docker"]
    description = "Docker containers"
    extra_actions = (
        "docker_list",
        "docker_page",
        "docker_detail",
        "docker_start",
        "docker_stop",
        "docker_restart",
    )

    def __init__(self, monitor: SystemMonitor, alerts: AlertManager) -> None:
        super().__init__(monitor, alerts)
        self.docker_manager = DockerManager()

    def dependency_issue(self) -> str | None:
        if self.docker_manager.is_available():
            return None
        error = self.docker_manager.get_error_message()
        if error == "Docker SDK not installed":
            return "Install the Docker SDK from requirements.txt."
        return "Start Docker and grant the bot access to the Docker socket."

    def summary(self) -> str:
        issue = self.dependency_issue()
        if issue:
            return issue
        counts = self.docker_manager.get_container_count()
        return f"{counts['running']} running / {counts['total']} total"

    def render(self) -> BlockView:
        if not self.docker_manager.is_available():
            error = self.docker_manager.get_error_message() or "Unknown error"
            return self.view(
                f"{EMOJI['error']} Docker is not available on this server\\.\n\n"
                f"_Possible reasons:_\n"
                f"• Docker is not installed\n"
                f"• Docker daemon is not running\n"
                f"• No permission to access Docker socket\n\n"
                f"_Error:_ {escape_markdown(error[:100])}"
            )

        counts = self.docker_manager.get_container_count()
        return self.view(
            f"{EMOJI['success']} Docker is available\n\n"
            f"*Containers:*\n"
            f"  🟢 Running: {counts['running']}\n"
            f"  🔴 Stopped: {counts['stopped']}\n"
            f"  📦 Total: {counts['total']}",
            keyboard=get_docker_menu_keyboard(),
        )

    async def submenu(self, query: CallbackQuery, action: str, value: str) -> None:
        if action in {"docker_list", "docker_page"}:
            view = await asyncio.to_thread(self._container_list, int(value) if value else 0)
            await self.show_view(query, view)
        elif action == "docker_detail":
            view = await asyncio.to_thread(self._container_detail, value)
            await self.show_view(query, view)
        else:
            await self._handle_docker_action(query, value, action.removeprefix("docker_"))

    def _container_list(self, page: int = 0) -> BlockView:
        """Handle Docker list containers callback with pagination."""
        if not self.docker_manager.is_available():
            return self.view(f"{EMOJI['error']} Docker is not available\\.")

        containers, total, total_pages = self.docker_manager.get_containers_page(
            page=page, per_page=10
        )

        if total == 0:
            message = f"{EMOJI['container']} *CONTAINERS*\n\n" f"No containers found\\."
            return self.view(message, keyboard=get_docker_back_keyboard())

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
            get_status_emoji=self.docker_manager.get_status_emoji,
        )

        return self.view(message, keyboard=keyboard)

    def _container_detail(self, container_id: str) -> BlockView:
        """Handle Docker container detail view."""
        if not self.docker_manager.is_available():
            return self.view(f"{EMOJI['error']} Docker is not available\\.")

        container = self.docker_manager.get_container_by_id(container_id)

        if not container:
            return self.view(
                f"{EMOJI['error']} Container not found\\.", keyboard=get_docker_back_keyboard()
            )

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
        if container.status == "running":
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

        if container.started and container.status == "running":
            started = escape_markdown(container.started)
            message += f"*Started:* {started}\n"

        if container.restart_count > 0:
            message += f"*Restarts:* {container.restart_count}\n"

        return self.view(
            message, keyboard=get_container_detail_keyboard(container_id, container.status)
        )

    async def _handle_docker_action(
        self, query: CallbackQuery, container_id: str, action: str
    ) -> None:
        """Handle Docker container actions (start/stop/restart)."""
        if not self.docker_manager.is_available():
            await query.edit_message_text(
                f"{EMOJI['error']} Docker is not available\\.",
                parse_mode="MarkdownV2",
                reply_markup=get_back_to_main_keyboard(),
            )
            return

        # Show processing message
        action_emoji = {"start": "▶️", "stop": "⏹️", "restart": "🔄"}
        action_text = {"start": "Starting", "stop": "Stopping", "restart": "Restarting"}

        await query.edit_message_text(
            f"{action_emoji.get(action, '⏳')} {action_text.get(action, 'Processing')} container\\.\\.\\.",
            parse_mode="MarkdownV2",
        )

        operations = {
            "start": self.docker_manager.start_container,
            "stop": self.docker_manager.stop_container,
            "restart": self.docker_manager.restart_container,
        }
        success, message = await asyncio.to_thread(operations[action], container_id)

        if success:
            result_emoji = EMOJI["success"]
        else:
            result_emoji = EMOJI["error"]

        # Show result briefly, then refresh detail view
        escaped_message = escape_markdown(message)
        await query.edit_message_text(f"{result_emoji} {escaped_message}", parse_mode="MarkdownV2")

        # Wait a moment then show updated container detail
        await asyncio.sleep(1.5)
        await self.show_view(query, await asyncio.to_thread(self._container_detail, container_id))


@dataclass
class ContainerInfo:
    """Container information."""

    id: str
    short_id: str
    name: str
    image: str
    status: str
    health: Optional[str] = None
    ports: Optional[str] = None


@dataclass
class ContainerDetail:
    """Detailed container information."""

    id: str
    short_id: str
    name: str
    image: str
    status: str
    health: Optional[str] = None
    ports: Optional[str] = None
    created: Optional[str] = None
    started: Optional[str] = None
    ip_address: Optional[str] = None
    cpu_percent: Optional[float] = None
    memory_mb: Optional[float] = None
    memory_limit_mb: Optional[float] = None
    memory_percent: Optional[float] = None
    restart_count: int = 0
    command: Optional[str] = None
    environment: Optional[List[str]] = None


class DockerManager:
    """Manager for Docker operations."""

    def __init__(self) -> None:
        """Initialize DockerManager."""
        self._client = None
        self._available: Optional[bool] = None
        self._error_message: Optional[str] = None

    def is_available(self) -> bool:
        """
        Check if Docker is available on the system.

        Returns:
            True if Docker is available and accessible
        """
        if self._available:
            return self._available

        if not DOCKER_SDK_AVAILABLE:
            self._available = False
            self._error_message = "Docker SDK not installed"
            logger.warning("Docker SDK (docker-py) is not installed")
            return False

        try:
            self._client = docker.from_env(timeout=5)
            # Test connection by pinging
            self._client.ping()
            self._available = True
            logger.info("Docker is available and connected")
            return True
        except DockerException as e:
            self._available = False
            self._error_message = str(e)
            logger.warning(f"Docker is not available: {e}")
            return False
        except Exception as e:
            self._available = False
            self._error_message = str(e)
            logger.warning(f"Error connecting to Docker: {e}")
            return False

    def get_error_message(self) -> Optional[str]:
        """Get the error message if Docker is not available."""
        return self._error_message

    def get_client(self):
        """Get Docker client (creates if needed)."""
        if self._client is None and self.is_available():
            pass  # is_available() already creates the client
        return self._client

    def list_containers(self, all_containers: bool = True) -> List[ContainerInfo]:
        """
        List Docker containers.

        Args:
            all_containers: Include stopped containers if True

        Returns:
            List of ContainerInfo objects
        """
        if not self.is_available():
            return []

        try:
            containers = self._client.containers.list(all=all_containers)
            result = []

            for container in containers:
                # Get health status if available
                health = None
                if hasattr(container, "health") and container.health:
                    health = container.health
                elif "Health" in container.attrs.get("State", {}):
                    health = container.attrs["State"]["Health"].get("Status")

                # Get port mappings
                ports = self._format_ports(container.ports)

                # Get image name
                image_tags = container.image.tags
                image = image_tags[0] if image_tags else container.image.short_id

                result.append(
                    ContainerInfo(
                        id=container.id,
                        short_id=container.short_id,
                        name=container.name,
                        image=image,
                        status=container.status,
                        health=health,
                        ports=ports,
                    )
                )

            # Sort: running first, then by name
            status_order = {
                "running": 0,
                "restarting": 1,
                "paused": 2,
                "created": 3,
                "exited": 4,
                "dead": 5,
            }
            result.sort(key=lambda c: (status_order.get(c.status, 6), c.name.lower()))

            return result

        except Exception as e:
            logger.error(f"Error listing containers: {e}")
            return []

    def _format_ports(self, ports: dict) -> Optional[str]:
        """Format port mappings to readable string."""
        if not ports:
            return None

        port_list = []
        for container_port, host_bindings in ports.items():
            if host_bindings:
                for binding in host_bindings:
                    host_port = binding.get("HostPort", "")
                    if host_port:
                        port_list.append(f"{host_port}→{container_port}")
            else:
                port_list.append(container_port)

        return ", ".join(port_list[:3]) if port_list else None  # Limit to 3 ports

    def get_container_count(self) -> dict:
        """
        Get container count summary.

        Returns:
            Dictionary with total, running, stopped counts
        """
        containers = self.list_containers(all_containers=True)

        running = sum(1 for c in containers if c.status == "running")
        stopped = sum(1 for c in containers if c.status in ["exited", "dead"])
        other = len(containers) - running - stopped

        return {
            "total": len(containers),
            "running": running,
            "stopped": stopped,
            "other": other,
        }

    def get_status_emoji(self, status: str) -> str:
        """Get emoji for container status."""
        status_emojis = {
            "running": "🟢",
            "restarting": "🔄",
            "paused": "⏸️",
            "created": "📦",
            "exited": "🔴",
            "dead": "💀",
        }
        return status_emojis.get(status, "❓")

    def get_container_by_id(self, container_id: str) -> Optional[ContainerDetail]:
        """
        Get detailed information about a specific container.

        Args:
            container_id: Container ID (short or full)

        Returns:
            ContainerDetail object or None if not found
        """
        if not self.is_available():
            return None

        try:
            container = self._client.containers.get(container_id)
            attrs = container.attrs
            config = attrs.get("Config", {})
            state = attrs.get("State", {})
            network_settings = attrs.get("NetworkSettings", {})

            # Get health status
            health = None
            if "Health" in state:
                health = state["Health"].get("Status")

            # Get port mappings
            ports = self._format_ports(container.ports)

            # Get image name
            image_tags = container.image.tags
            image = image_tags[0] if image_tags else container.image.short_id

            # Get IP address
            ip_address = None
            networks = network_settings.get("Networks", {})
            for net_name, net_info in networks.items():
                if net_info.get("IPAddress"):
                    ip_address = net_info["IPAddress"]
                    break

            # Get timestamps
            created = (
                attrs.get("Created", "")[:19].replace("T", " ") if attrs.get("Created") else None
            )
            started = (
                state.get("StartedAt", "")[:19].replace("T", " ")
                if state.get("StartedAt")
                else None
            )

            # Get restart count
            restart_count = attrs.get("RestartCount", 0)

            # Get command
            cmd = config.get("Cmd")
            command = " ".join(cmd) if cmd else None

            # Get CPU and memory stats if running
            cpu_percent = None
            memory_mb = None
            memory_limit_mb = None
            memory_percent = None

            if container.status == "running":
                try:
                    stats = container.stats(stream=False)

                    # CPU calculation
                    cpu_stats = stats.get("cpu_stats", {})
                    precpu_stats = stats.get("precpu_stats", {})

                    cpu_delta = cpu_stats.get("cpu_usage", {}).get(
                        "total_usage", 0
                    ) - precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
                    system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get(
                        "system_cpu_usage", 0
                    )
                    online_cpus = cpu_stats.get("online_cpus", 1)

                    if system_delta > 0 and cpu_delta > 0:
                        cpu_percent = round((cpu_delta / system_delta) * online_cpus * 100, 2)

                    # Memory calculation
                    memory_stats = stats.get("memory_stats", {})
                    usage = memory_stats.get("usage", 0)
                    limit = memory_stats.get("limit", 0)

                    if usage > 0:
                        memory_mb = round(usage / (1024 * 1024), 2)
                    if limit > 0:
                        memory_limit_mb = round(limit / (1024 * 1024), 2)
                        if usage > 0:
                            memory_percent = round((usage / limit) * 100, 2)
                except Exception as e:
                    logger.debug(f"Could not get stats for container {container.name}: {e}")

            return ContainerDetail(
                id=container.id,
                short_id=container.short_id,
                name=container.name,
                image=image,
                status=container.status,
                health=health,
                ports=ports,
                created=created,
                started=started,
                ip_address=ip_address,
                cpu_percent=cpu_percent,
                memory_mb=memory_mb,
                memory_limit_mb=memory_limit_mb,
                memory_percent=memory_percent,
                restart_count=restart_count,
                command=command,
            )

        except Exception as e:
            logger.error(f"Error getting container {container_id}: {e}")
            return None

    def get_containers_page(self, page: int = 0, per_page: int = 10) -> tuple:
        """
        Get a page of containers.

        Args:
            page: Page number (0-indexed)
            per_page: Number of containers per page

        Returns:
            Tuple of (containers_list, total_count, total_pages)
        """
        all_containers = self.list_containers(all_containers=True)
        total = len(all_containers)
        total_pages = (total + per_page - 1) // per_page if total > 0 else 1

        start = page * per_page
        end = start + per_page

        return all_containers[start:end], total, total_pages

    def start_container(self, container_id: str) -> tuple[bool, str]:
        """
        Start a container.

        Args:
            container_id: Container ID (short or full)

        Returns:
            Tuple of (success, message)
        """
        if not self.is_available():
            return False, "Docker is not available"

        try:
            container = self._client.containers.get(container_id)

            if container.status == "running":
                return False, f"Container '{container.name}' is already running"

            container.start()
            logger.info(f"Container {container.name} started successfully")
            return True, f"Container '{container.name}' started successfully"

        except Exception as e:
            logger.error(f"Error starting container {container_id}: {e}")
            return False, f"Error starting container: {str(e)[:100]}"

    def stop_container(self, container_id: str, timeout: int = 10) -> tuple[bool, str]:
        """
        Stop a container.

        Args:
            container_id: Container ID (short or full)
            timeout: Timeout in seconds before killing

        Returns:
            Tuple of (success, message)
        """
        if not self.is_available():
            return False, "Docker is not available"

        try:
            container = self._client.containers.get(container_id)

            if container.status != "running":
                return False, f"Container '{container.name}' is not running"

            container.stop(timeout=timeout)
            logger.info(f"Container {container.name} stopped successfully")
            return True, f"Container '{container.name}' stopped successfully"

        except Exception as e:
            logger.error(f"Error stopping container {container_id}: {e}")
            return False, f"Error stopping container: {str(e)[:100]}"

    def restart_container(self, container_id: str, timeout: int = 10) -> tuple[bool, str]:
        """
        Restart a container.

        Args:
            container_id: Container ID (short or full)
            timeout: Timeout in seconds before killing

        Returns:
            Tuple of (success, message)
        """
        if not self.is_available():
            return False, "Docker is not available"

        try:
            container = self._client.containers.get(container_id)
            container.restart(timeout=timeout)
            logger.info(f"Container {container.name} restarted successfully")
            return True, f"Container '{container.name}' restarted successfully"

        except Exception as e:
            logger.error(f"Error restarting container {container_id}: {e}")
            return False, f"Error restarting container: {str(e)[:100]}"


def get_docker_menu_keyboard() -> InlineKeyboardMarkup:
    """Get the Docker submenu keyboard."""
    keyboard = [
        [
            InlineKeyboardButton(
                f"{EMOJI['container']} List Containers", callback_data="docker_list"
            )
        ],
        [InlineKeyboardButton(f"{EMOJI['back']} Back to Menu", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_docker_back_keyboard() -> InlineKeyboardMarkup:
    """Get keyboard to go back to Docker menu."""
    keyboard = [
        [InlineKeyboardButton(f"{EMOJI['back']} Back to Docker", callback_data="cmd_docker")],
        [InlineKeyboardButton(f"{EMOJI['home']} Main Menu", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_container_list_keyboard(
    containers: list[ContainerInfo], page: int, total_pages: int, get_status_emoji
) -> InlineKeyboardMarkup:
    """
    Get keyboard with paginated container list.

    Args:
        containers: List of ContainerInfo objects for current page
        page: Current page number (0-indexed)
        total_pages: Total number of pages
        get_status_emoji: Function to get emoji for status
    """
    keyboard = []

    # Add container buttons (2 per row for better UX)
    row = []
    for container in containers:
        emoji = get_status_emoji(container.status)
        # Truncate name if too long
        name = container.name[:20] + "..." if len(container.name) > 20 else container.name
        row.append(
            InlineKeyboardButton(
                f"{emoji} {name}", callback_data=f"docker_detail|{container.short_id}"
            )
        )
        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:  # Add remaining buttons
        keyboard.append(row)

    # Add pagination controls
    pagination_row = []
    if page > 0:
        pagination_row.append(
            InlineKeyboardButton("◀️ Prev", callback_data=f"docker_page|{page - 1}")
        )

    pagination_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))

    if page < total_pages - 1:
        pagination_row.append(
            InlineKeyboardButton("Next ▶️", callback_data=f"docker_page|{page + 1}")
        )

    keyboard.append(pagination_row)

    # Add back button
    keyboard.append(
        [InlineKeyboardButton(f"{EMOJI['back']} Back to Docker", callback_data="cmd_docker")]
    )

    return InlineKeyboardMarkup(keyboard)


def get_container_detail_keyboard(
    container_id: str, status: str = "running"
) -> InlineKeyboardMarkup:
    """Get keyboard for container detail view with actions."""
    keyboard = []

    # Action buttons based on container status
    action_row = []
    if status == "running":
        action_row.append(
            InlineKeyboardButton("⏹️ Stop", callback_data=f"docker_stop|{container_id}")
        )
        action_row.append(
            InlineKeyboardButton("🔄 Restart", callback_data=f"docker_restart|{container_id}")
        )
    else:
        action_row.append(
            InlineKeyboardButton("▶️ Start", callback_data=f"docker_start|{container_id}")
        )

    if action_row:
        keyboard.append(action_row)

    # Refresh button
    keyboard.append(
        [
            InlineKeyboardButton(
                f"{EMOJI['refresh']} Refresh", callback_data=f"docker_detail|{container_id}"
            )
        ]
    )

    # Navigation buttons
    keyboard.append(
        [InlineKeyboardButton(f"{EMOJI['back']} Back to List", callback_data="docker_list|0")]
    )
    keyboard.append(
        [
            InlineKeyboardButton(f"{EMOJI['docker']} Docker Menu", callback_data="cmd_docker"),
            InlineKeyboardButton(f"{EMOJI['home']} Main Menu", callback_data="menu_main"),
        ]
    )

    return InlineKeyboardMarkup(keyboard)
