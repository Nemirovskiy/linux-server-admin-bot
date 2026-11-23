"""
Text formatting utilities for Telegram messages.
"""

from bot.models import (
    DiskMetrics,
    SystemStatus,
)
from config.constants import EMOJI


def escape_markdown(text: str) -> str:
    """
    Escape special characters for Telegram MarkdownV2.

    Args:
        text: Text to escape

    Returns:
        Escaped text
    """
    text = text.replace("\\", "\\\\")
    special_chars = ["_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"]
    for char in special_chars:
        text = text.replace(char, f"\\{char}")
    return text


def format_bytes(bytes_value: int) -> str:
    """
    Format bytes to human-readable string.

    Args:
        bytes_value: Number of bytes

    Returns:
        Formatted string (e.g., "1.5 GB")
    """
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f} PB"


def format_duration(seconds: float) -> str:
    """
    Format duration in seconds to human-readable string.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string (e.g., "2d 5h 30m")
    """
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or not parts:
        parts.append(f"{minutes}m")
    
    return " ".join(parts)


def format_system_status(status: SystemStatus) -> str:
    """
    Format system status for Telegram message.

    Args:
        status: System status

    Returns:
        Formatted message in MarkdownV2
    """
    message = (
        f"📊 *SYSTEM STATUS*\n\n"
        f"{EMOJI['cpu']} *CPU*\n"
        f"{format_usage_line(status.cpu.percent)}\n"
        f"└ Cores: {escape_markdown(str(status.cpu.count))}\n\n"
        f"{EMOJI['memory']} *Memory*\n"
        f"{format_usage_line(status.memory.percent)}\n"
        f"└ {escape_markdown(f'{status.memory.used_gb:.1f}GB')} / {escape_markdown(f'{status.memory.total_gb:.1f}GB')}\n\n"
        f"{EMOJI['disk']} *Disk*\n"
        f"{format_usage_line(status.disk.percent, 75, 90)}\n"
        f"└ {escape_markdown(f'{status.disk.used_gb:.1f}GB')} / {escape_markdown(f'{status.disk.total_gb:.1f}GB')}\n\n"
        f"{EMOJI['clock']} *Uptime:* {escape_markdown(status.uptime_formatted)}\n"
    )
    
    return message


def format_usage_line(
    percent: float, warning_threshold: int = 60, critical_threshold: int = 80
) -> str:
    """Format a compact MarkdownV2 progress bar, percentage, and status indicator."""
    bar = _create_progress_bar(percent, 100)
    indicator = _get_status_indicator(percent, warning_threshold, critical_threshold)
    return f"{escape_markdown(f'{bar} {percent:.1f}%')} {indicator}"


def format_disk_metrics(metrics: DiskMetrics) -> str:
    """
    Format disk metrics for Telegram message.

    Args:
        metrics: Disk metrics

    Returns:
        Formatted message in MarkdownV2
    """
    status_indicator = _get_status_indicator(metrics.percent, 75, 90)
    bar = _create_progress_bar(metrics.percent, 100, length=12)
    
    message = "💿 *DISK INFORMATION*\n\n"
    message += f"*Mount Point:* {escape_markdown(metrics.mount_point)}\n\n"
    message += "*Usage*\n"
    message += f"{escape_markdown(bar)} {escape_markdown(f'{metrics.percent:.1f}%')} {status_indicator}\n\n"
    message += f"*Total:* {escape_markdown(f'{metrics.total_gb:.2f} GB')}\n"
    message += f"*Used:* {escape_markdown(f'{metrics.used_gb:.2f} GB')}\n"
    message += f"*Free:* {escape_markdown(f'{metrics.free_gb:.2f} GB')}\n"
    
    return message


def _create_progress_bar(value: float, max_value: float, length: int = 10) -> str:
    """
    Create a text-based progress bar.

    Args:
        value: Current value
        max_value: Maximum value
        length: Bar length in characters

    Returns:
        Progress bar string
    """
    filled = int((value / max_value) * length)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}]"


def _get_status_indicator(percent: float, warning_threshold: int = 60, critical_threshold: int = 80) -> str:
    """
    Get a colored status indicator based on percentage.

    Args:
        percent: Current percentage value
        warning_threshold: Threshold for warning (yellow)
        critical_threshold: Threshold for critical (red)

    Returns:
        Status emoji (🟢, 🟡, or 🔴)
    """
    if percent >= critical_threshold:
        return "🔴"
    elif percent >= warning_threshold:
        return "🟡"
    else:
        return "🟢"
