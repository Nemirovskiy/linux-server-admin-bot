"""CPU information block."""

from bot.api import Alert, Block, BlockView, CPUMetrics, PluginMetadata
from bot.api.presentation import (
    EMOJI,
    chart_generator,
    create_progress_bar,
    escape_markdown,
    format_usage_line,
    get_status_indicator,
)


class CpuBlock(Block):
    metadata = PluginMetadata(
        id="cpu",
        name="CPU",
        description="CPU information",
        version="1.0.0",
        icon=EMOJI["cpu"],
        sends_alerts=True,
    )

    @property
    def alert_threshold(self) -> int:
        return self.context.alerts.cpu_threshold

    def check_alert(self) -> Alert | None:
        return self.context.alerts.check_cpu_alert(self.context.system.get_cpu_metrics(interval=0.5))

    def summary(self) -> str:
        return f"{self.context.system.get_cpu_metrics(interval=0.5).percent:.1f}%"

    def status_section(self) -> str:
        metrics = self.context.system.get_cpu_metrics(interval=0.5)
        return (
            f"{self.metadata.icon} *{self.metadata.name}*\n"
            f"{format_usage_line(metrics.percent)}\n"
            f"└ Cores: {escape_markdown(str(metrics.count))}"
        )

    def render(self) -> BlockView:
        metrics = self.context.system.get_cpu_metrics()
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
    status_indicator = get_status_indicator(metrics.percent)
    overall_bar = create_progress_bar(metrics.percent, 100, 12)

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
        bar = create_progress_bar(usage, 100, length=8)
        indicator = get_status_indicator(usage)
        message += (
            f"CPU{i}: {escape_markdown(bar)} {escape_markdown(f'{usage:.1f}%')} {indicator}\n"
        )

    return message
