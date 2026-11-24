"""Docker information, nested container screens, and management actions."""

import asyncio
import base64
import re
from dataclasses import dataclass

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.api import Block, BlockView, PluginMetadata
from bot.api.presentation import (
    EMOJI,
    escape_markdown,
    get_back_to_main_keyboard,
)


class DockerBlock(Block):
    metadata = PluginMetadata(
        id="docker",
        name="Docker",
        description="Docker containers",
        version="1.0.0",
        icon=EMOJI["docker"],
        permissions=(
            "docker.containers.read",
            "docker.containers.inspect",
            "docker.containers.stats",
            "docker.containers.start",
            "docker.containers.stop",
            "docker.containers.restart",
        ),
    )
    extra_actions = (
        "docker_list",
        "docker_page",
        "docker_detail",
        "docker_start",
        "docker_stop",
        "docker_restart",
    )

    def dependency_issue(self) -> str | None:
        result = self.execute_privileged_sync("docker.containers.read")
        return None if result.success else result.message

    def summary(self) -> str:
        result = self.execute_privileged_sync("docker.containers.read")
        if not result.success:
            return result.message
        running, _ = _counts(result.data)
        return f"{running} running / {len(result.data)} total"

    def render(self) -> BlockView:
        result = self.execute_privileged_sync("docker.containers.read")
        if not result.success:
            return self._unavailable(result.message)

        running, stopped = _counts(result.data)
        return self.view(
            f"{EMOJI['success']} Docker is available\n\n"
            f"*Containers:*\n"
            f"  🟢 Running: {running}\n"
            f"  🔴 Stopped: {stopped}\n"
            f"  📦 Total: {len(result.data)}",
            keyboard=get_docker_menu_keyboard(),
        )

    def _unavailable(self, message: str) -> BlockView:
        return self.view(f"{EMOJI['error']} {escape_markdown(message)}")

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
        result = self.execute_privileged_sync("docker.containers.read")
        if not result.success:
            return self._unavailable(result.message)
        all_containers = result.data
        total = len(all_containers)
        if total == 0:
            return self.view("No containers found\\.", keyboard=get_docker_back_keyboard())

        total_pages = (total + 9) // 10
        page = min(max(page, 0), total_pages - 1)
        containers = [ContainerInfo(**row) for row in all_containers[page * 10 : (page + 1) * 10]]
        running, stopped = _counts(all_containers)
        message = (
            f"{EMOJI['container']} *CONTAINERS* "
            f"\\(🟢 {running} \\| 🔴 {stopped} \\| 📦 {total}\\)\n\n"
            f"_Select a container to see details:_"
        )

        keyboard = get_container_list_keyboard(
            containers=containers,
            page=page,
            total_pages=total_pages,
            get_status_emoji=_status_emoji,
        )

        return self.view(message, keyboard=keyboard)

    def _container_detail(self, container_id: str) -> BlockView:
        """Handle Docker container detail view."""
        try:
            full_id = _decode_id(container_id)
        except ValueError:
            return self._unavailable("This button is outdated. Reopen the container list.")
        result = self.execute_privileged_sync("docker.containers.inspect", container_id=full_id)
        if not result.success:
            return self._unavailable(result.message)
        container = ContainerDetail(**result.data)
        if container.status == "running":
            stats = self.execute_privileged_sync("docker.containers.stats", container_id=full_id)
            if stats.success:
                container = ContainerDetail(**result.data, **stats.data)

        status_emoji = _status_emoji(container.status)
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
        try:
            full_id = _decode_id(container_id)
        except ValueError:
            await self.show_view(
                query, self._unavailable("This button is outdated. Reopen the container list.")
            )
            return

        # Show processing message
        action_emoji = {"start": "▶️", "stop": "⏹️", "restart": "🔄"}
        action_text = {"start": "Starting", "stop": "Stopping", "restart": "Restarting"}

        await query.edit_message_text(
            f"{action_emoji.get(action, '⏳')} {action_text.get(action, 'Processing')} container\\.\\.\\.",
            parse_mode="MarkdownV2",
        )

        result = await self.execute_privileged(f"docker.containers.{action}", container_id=full_id)
        if not result.success:
            await self.show_view(query, self._unavailable(result.message))
            return
        escaped_message = escape_markdown(result.message)
        await query.edit_message_text(
            f"{EMOJI['success']} {escaped_message}", parse_mode="MarkdownV2"
        )

        # Wait a moment then show updated container detail
        await asyncio.sleep(1.5)
        await self.show_view(query, await asyncio.to_thread(self._container_detail, container_id))


def _encode_id(container_id: str) -> str:
    return base64.urlsafe_b64encode(bytes.fromhex(container_id)).decode("ascii").rstrip("=")


def _decode_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise ValueError("Invalid container button ID")
    container_id = base64.urlsafe_b64decode(value + "=").hex()
    if _encode_id(container_id) != value:
        raise ValueError("Non-canonical container button ID")
    return container_id


def _counts(containers: list[dict]) -> tuple[int, int]:
    return (
        sum(row["status"] == "running" for row in containers),
        sum(row["status"] in {"exited", "dead"} for row in containers),
    )


def _status_emoji(status: str) -> str:
    return {
        "running": "🟢",
        "restarting": "🔄",
        "paused": "⏸️",
        "created": "📦",
        "exited": "🔴",
        "dead": "💀",
    }.get(status, "❓")


@dataclass
class ContainerInfo:
    id: str
    name: str
    image: str
    status: str
    ports: str = ""


@dataclass
class ContainerDetail(ContainerInfo):
    health: str = ""
    created: str = ""
    started: str = ""
    ip_address: str = ""
    cpu_percent: float | None = None
    memory_mb: float | None = None
    memory_limit_mb: float | None = None
    memory_percent: float | None = None
    restart_count: int = 0


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
                f"{emoji} {name}", callback_data=f"docker_detail|{_encode_id(container.id)}"
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
