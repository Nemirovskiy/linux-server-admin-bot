"""System uptime block."""

from bot.blocks.base import Block, BlockView
from bot.utils import escape_markdown, format_duration
from config import EMOJI


class UptimeBlock(Block):
    name = "uptime"
    title = "Uptime"
    icon = EMOJI["clock"]
    description = "System uptime"
    enabled_by_default = True

    def summary(self) -> str:
        return format_duration(self.monitor.get_uptime_info()["uptime_seconds"])

    def render(self) -> BlockView:
        info = self.monitor.get_uptime_info()

        boot_time_str = info["boot_time"].strftime("%d/%m/%Y %H:%M:%S")

        message = ""

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

        return self.view(message)
