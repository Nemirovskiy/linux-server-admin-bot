"""Memory usage block."""

from bot.blocks.base import Block, BlockView
from bot.models import Alert, MemoryMetrics
from bot.utils import chart_generator, escape_markdown
from bot.utils.formatters import _create_progress_bar, _get_status_indicator
from config import EMOJI


class MemoryBlock(Block):
    name = "memory"
    title = "Memory"
    icon = EMOJI["memory"]
    description = "Memory usage"

    sends_alerts = True

    @property
    def alert_threshold(self) -> int:
        return self.alerts.memory_threshold

    def check_alert(self) -> Alert | None:
        return self.alerts.check_memory_alert(self.monitor.get_memory_metrics())

    def summary(self) -> str:
        return f"{self.monitor.get_memory_metrics().percent:.1f}%"

    def render(self) -> BlockView:
        metrics = self.monitor.get_memory_metrics()
        photo = chart_generator.generate_memory_chart(
            metrics.total_gb, metrics.used_gb, metrics.available_gb, metrics.percent
        )
        return self.view(
            format_memory_metrics(metrics),
            photo=photo,
            chart_caption=f"{EMOJI['chart']} Memory Usage Chart",
        )


def format_memory_metrics(metrics: MemoryMetrics) -> str:
    """
    Format memory metrics for Telegram message.

    Args:
        metrics: Memory metrics

    Returns:
        Formatted message in MarkdownV2
    """
    status_indicator = _get_status_indicator(metrics.percent)
    bar = _create_progress_bar(metrics.percent, 100, length=12)

    message = ""
    message += "*Usage*\n"
    message += f"{escape_markdown(bar)} {escape_markdown(f'{metrics.percent:.1f}%')} {status_indicator}\n\n"
    message += f"*Total:* {escape_markdown(f'{metrics.total_gb:.2f} GB')}\n"
    message += f"*Used:* {escape_markdown(f'{metrics.used_gb:.2f} GB')}\n"
    message += f"*Available:* {escape_markdown(f'{metrics.available_gb:.2f} GB')}\n"

    if metrics.swap_total > 0:
        swap_indicator = _get_status_indicator(metrics.swap_percent)
        swap_bar = _create_progress_bar(metrics.swap_percent, 100, length=12)
        message += "\n*Swap*\n"
        message += f"{escape_markdown(swap_bar)} {escape_markdown(f'{metrics.swap_percent:.1f}%')} {swap_indicator}\n"
        message += f"└ {escape_markdown(f'{metrics.swap_used / (1024**3):.2f} GB')} / {escape_markdown(f'{metrics.swap_total / (1024**3):.2f} GB')}\n"

    return message
