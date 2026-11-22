"""CPU information block."""

from bot.blocks.base import Block, BlockView
from bot.models import Alert, CPUMetrics
from bot.utils import chart_generator, escape_markdown
from bot.utils.formatters import _create_progress_bar, _get_status_indicator
from config import EMOJI


class CpuBlock(Block):
    name = "cpu"
    title = "CPU"
    icon = EMOJI["cpu"]
    description = "CPU information"
    enabled_by_default = True

    sends_alerts = True

    @property
    def alert_threshold(self) -> int:
        return self.alerts.cpu_threshold

    def check_alert(self) -> Alert | None:
        return self.alerts.check_cpu_alert(self.monitor.get_cpu_metrics(interval=0.5))

    def summary(self) -> str:
        return f"{self.monitor.get_cpu_metrics(interval=0.5).percent:.1f}%"

    def render(self) -> BlockView:
        metrics = self.monitor.get_cpu_metrics()
        return self.view(
            format_cpu_metrics(metrics),
            photo=chart_generator.generate_cpu_chart(metrics.percent, metrics.per_cpu),
            chart_caption=f"{EMOJI['chart']} CPU Usage Chart",
        )


def format_cpu_metrics(metrics: CPUMetrics) -> str:
    """
    Format CPU metrics for Telegram message.

    Args:
        metrics: CPU metrics

    Returns:
        Formatted message in MarkdownV2
    """
    status_indicator = _get_status_indicator(metrics.percent)
    overall_bar = _create_progress_bar(metrics.percent, 100, 12)

    message = ""
    message += "*Overall Usage*\n"
    message += f"{escape_markdown(overall_bar)} {escape_markdown(f'{metrics.percent:.1f}%')} {status_indicator}\n\n"
    message += f"*Cores:* {escape_markdown(str(metrics.count))}\n"

    if metrics.frequency_current:
        message += f"*Frequency:* {escape_markdown(f'{metrics.frequency_current:.0f} MHz')}\n"

    if metrics.load_avg:
        load_str = (
            f"{metrics.load_avg[0]:.2f}, {metrics.load_avg[1]:.2f}, {metrics.load_avg[2]:.2f}"
        )
        message += f"*Load Average:* {escape_markdown(load_str)}\n"

    message += "\n*Per\\-Core Usage*\n\n"

    for i, usage in enumerate(metrics.per_cpu):
        bar = _create_progress_bar(usage, 100, length=8)
        indicator = _get_status_indicator(usage)
        message += (
            f"CPU{i}: {escape_markdown(bar)} {escape_markdown(f'{usage:.1f}%')} {indicator}\n"
        )

    return message
