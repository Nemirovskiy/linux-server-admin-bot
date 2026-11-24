"""Network statistics block."""

from bot.api import Block, BlockView, PluginMetadata
from bot.api.presentation import (
    EMOJI,
    escape_markdown,
)


class NetworkBlock(Block):
    metadata = PluginMetadata(
        id="network",
        name="Network",
        description="Network statistics",
        version="1.0.0",
        icon=EMOJI["network"],
    )

    def summary(self) -> str:
        networks = [net for net in self.context.system.get_network_metrics() if net.interface != "lo"]
        return f"{len(networks)} interfaces" if networks else "Unavailable"

    def render(self) -> BlockView:
        networks = [net for net in self.context.system.get_network_metrics() if net.interface != "lo"]
        if not networks:
            return self.view("No network interfaces found\\.")

        sections = []
        for net in networks:
            sections.append(
                f"*{escape_markdown(net.interface)}*\n"
                f"  ↓ RX: {escape_markdown(f'{net.bytes_recv_mb:.2f}')} MB\n"
                f"  ↑ TX: {escape_markdown(f'{net.bytes_sent_mb:.2f}')} MB\n"
                f"  📦 Packets: {net.packets_recv} / {net.packets_sent}\n"
                f"  ⚠️ Errors: {net.errors_in} / {net.errors_out}"
            )
        return self.view("\n\n".join(sections))
