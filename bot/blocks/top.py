"""Top processes block."""

from bot.blocks.base import Block, BlockView
from bot.models import ProcessInfo
from bot.utils import chart_generator, escape_markdown
from bot.utils.formatters import _get_status_indicator
from config import EMOJI
from config.constants import MAX_PROCESS_COUNT


class TopBlock(Block):
    name = "top"
    title = "Processes"
    icon = EMOJI["process"]
    description = "Top processes"
    enabled_by_default = True

    def summary(self) -> str:
        processes = self.monitor.get_top_processes(limit=1)
        if not processes:
            return "No processes found"
        process = processes[0]
        return f"{process.name} ({process.cpu_percent:.1f}% CPU)"

    def render(self) -> BlockView:
        processes = self.monitor.get_top_processes(limit=MAX_PROCESS_COUNT)
        if not processes:
            return self.view("No processes found\\.")
        photo = chart_generator.generate_process_chart(
            [{"name": p.name, "cpu_percent": p.cpu_percent} for p in processes]
        )
        return self.view(
            format_top_processes(processes),
            photo=photo,
            chart_caption=f"{EMOJI['chart']} Top Processes",
        )


def format_top_processes(processes: list[ProcessInfo]) -> str:
    """
    Format top processes for Telegram message.

    Args:
        processes: List of process information

    Returns:
        Formatted message in MarkdownV2
    """
    message = ""

    for i, proc in enumerate(processes, 1):
        cpu_indicator = _get_status_indicator(proc.cpu_percent)
        message += (
            f"*{i}\\. {escape_markdown(proc.name[:25])}*\n"
            f"   {cpu_indicator} CPU: {escape_markdown(f'{proc.cpu_percent:.1f}%')} │ "
            f"MEM: {escape_markdown(f'{proc.memory_mb:.0f}MB')} │ "
            f"PID: {escape_markdown(str(proc.pid))}\n"
        )

    return message
