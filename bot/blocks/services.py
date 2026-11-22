"""System services block."""

from bot.blocks.base import Block, BlockView
from bot.utils import escape_markdown
from config import EMOJI


class ServicesBlock(Block):
    name = "services"
    title = "Services"
    icon = EMOJI["services"]
    description = "System services"
    enabled_by_default = True

    def summary(self) -> str:
        services = self.monitor.get_services_status()
        if not services:
            return "Unavailable"
        running = sum(service["is_running"] for service in services)
        return f"{running} active / {len(services)} checked"

    def render(self) -> BlockView:
        services = self.monitor.get_services_status()
        if not services:
            return self.view(
                f"{EMOJI['warning']} No systemd services found\\.\n\n"
                f"_This may happen if:_\n"
                f"• System doesn't use systemd\n"
                f"• No common services installed\n"
                f"• systemctl is not available"
            )

        message = ""

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

        return self.view(message)
