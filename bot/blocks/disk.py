"""Disk usage block."""

from bot.api import Alert, Block, BlockView, DiskMetrics, PluginMetadata
from bot.api.presentation import (
    EMOJI,
    create_progress_bar,
    escape_markdown,
    format_usage_line,
    get_status_indicator,
)


class DiskBlock(Block):
    metadata = PluginMetadata(
        id="disk",
        name="Disk",
        description="Disk usage",
        version="1.0.0",
        icon=EMOJI["disk"],
        sends_alerts=True,
    )

    @property
    def alert_threshold(self) -> int:
        return self.context.alerts.disk_threshold

    def check_alert(self) -> Alert | None:
        return self.context.alerts.check_disk_alert(self.context.system.get_disk_metrics())

    def summary(self) -> str:
        return f"{self.context.system.get_disk_metrics().percent:.1f}%"

    def status_section(self) -> str:
        metrics = self.context.system.get_disk_metrics()
        size = escape_markdown(f"{metrics.used_gb:.1f}GB / {metrics.total_gb:.1f}GB")
        return f"{self.metadata.icon} *{self.metadata.name}*\n{format_usage_line(metrics.percent, 75, 90)}\n└ {size}"

    def render(self) -> BlockView:
        return self.view(format_all_disk_metrics(self.context.system.get_all_disk_metrics()))


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
        status_indicator = get_status_indicator(disk.percent, 75, 90)
        bar = create_progress_bar(disk.percent, 100, length=10)

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
