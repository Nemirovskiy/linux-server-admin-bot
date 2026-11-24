"""System uptime block."""

from bot.api import Block, BlockView, PluginMetadata
from bot.api.presentation import (
    EMOJI,
    escape_markdown,
    format_duration,
)


class UptimeBlock(Block):
    metadata = PluginMetadata(
        id="uptime",
        name="Uptime",
        description="System uptime",
        version="1.0.0",
        icon=EMOJI["clock"],
    )

    def summary(self) -> str:
        return format_duration(self.context.system.get_uptime_info()["uptime_seconds"])

    def render(self) -> BlockView:
        info = self.context.system.get_uptime_info()

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
