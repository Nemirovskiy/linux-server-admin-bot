"""Disk usage block."""

from bot.blocks.base import Block, BlockView
from bot.models import Alert, DiskMetrics
from bot.utils import escape_markdown
from bot.utils.formatters import _create_progress_bar, _get_status_indicator
from config import EMOJI


class DiskBlock(Block):
    name = "disk"
    title = "Disk"
    icon = EMOJI["disk"]
    description = "Disk usage"

    sends_alerts = True

    @property
    def alert_threshold(self) -> int:
        return self.alerts.disk_threshold

    def check_alert(self) -> Alert | None:
        return self.alerts.check_disk_alert(self.monitor.get_disk_metrics())

    def summary(self) -> str:
        return f"{self.monitor.get_disk_metrics().percent:.1f}%"

    def render(self) -> BlockView:
        return self.view(format_all_disk_metrics(self.monitor.get_all_disk_metrics()))


def _format_disk_size(size_bytes: int) -> str:
    """Format disk size to appropriate unit (GB or TB)."""
    gb = size_bytes / (1024**3)
    if gb >= 1000:
        return f"{size_bytes / (1024**4):.2f} TB"
    return f"{gb:.2f} GB"


def format_all_disk_metrics(disks: list[DiskMetrics]) -> str:
    """
    Format multiple disk metrics for Telegram message.

    Args:
        disks: List of DiskMetrics

    Returns:
        Formatted message in MarkdownV2
    """
    if not disks:
        return "No disks found\\."

    message = ""

    for disk in disks:
        status_indicator = _get_status_indicator(disk.percent, 75, 90)
        bar = _create_progress_bar(disk.percent, 100, length=10)

        # Format mount point with filesystem type
        mount = escape_markdown(disk.mount_point)
        fstype = escape_markdown(disk.fstype) if disk.fstype else ""

        # Use TB for large disks, GB for smaller
        total_str = _format_disk_size(disk.total)
        used_str = _format_disk_size(disk.used)
        free_str = _format_disk_size(disk.free)

        message += f"*{mount}*"
        if fstype:
            message += f" \\({fstype}\\)"
        message += "\n"
        message += (
            f"{escape_markdown(bar)} {escape_markdown(f'{disk.percent:.1f}%')} {status_indicator}\n"
        )
        message += f"Used: {escape_markdown(used_str)} / {escape_markdown(total_str)}\n"
        message += f"Free: {escape_markdown(free_str)}\n\n"

    return message
