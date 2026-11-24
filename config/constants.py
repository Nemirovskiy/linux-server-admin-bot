"""
Application constants and enums.
"""
from enum import Enum


from lsab_api.metrics import AlertType as AlertType


class ChartType(str, Enum):
    """Types of charts that can be generated."""

    LINE = "line"
    BAR = "bar"
    PIE = "pie"
    HORIZONTAL_BAR = "hbar"


# Emoji constants for better UX
EMOJI = {
    "cpu": "🖥️",
    "memory": "💾",
    "disk": "💿",
    "network": "🌐",
    "warning": "⚠️",
    "error": "❌",
    "success": "✅",
    "info": "ℹ️",
    "chart": "📊",
    "fire": "🔥",
    "check": "✔️",
    "cross": "✖️",
    "arrow_up": "⬆️",
    "arrow_down": "⬇️",
    "clock": "🕐",
    "lock": "🔒",
    "rocket": "🚀",
    "server": "🖥️",
    "back": "⬅️",
    "home": "🏠",
    "help": "❓",
    "alert": "🔔",
    "process": "⚙️",
    "settings": "⚙️",
    "refresh": "🔄",
    "temp": "🌡️",
    "services": "🔧",
    "author": "👨‍💻",
    "docker": "🐳",
    "container": "📦",
}

# Command descriptions for bot menu
COMMANDS = {
    "/start": "Start bot and show welcome message",
    "/help": "Show help and available commands",
    "/status": "Overall system status (CPU, RAM, Disk)",
    "/cpu": "Detailed CPU information with chart",
    "/memory": "Detailed RAM memory information",
    "/disk": "Disk usage information",
    "/top": "Top processes by CPU usage",
    "/alerts": "View alert configuration",
    "/network": "Network information",
    "/temp": "CPU and system temperature",
    "/uptime": "Server uptime",
    "/services": "Systemd services status",
    "/docker": "Docker containers management",
    "/author": "Bot author information",
}

# System metrics refresh rates (seconds)
REFRESH_RATES = {
    "cpu": 1,
    "memory": 2,
    "disk": 5,
    "network": 2,
}

# Chart colors palette
CHART_COLORS = {
    "primary": "#3498db",
    "success": "#2ecc71",
    "warning": "#f39c12",
    "danger": "#e74c3c",
    "info": "#1abc9c",
    "cpu": "#3498db",
    "memory": "#9b59b6",
    "disk": "#e67e22",
    "network": "#1abc9c",
}

# Format strings
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M:%S"

# Limits
MAX_LOG_LINES = 100
MAX_PROCESS_COUNT = 10
