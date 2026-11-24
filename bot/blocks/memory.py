"""Memory usage block."""

from bot.api import Alert, Block, BlockView, MemoryMetrics, PluginMetadata
from bot.api.presentation import (
    EMOJI,
    chart_generator,
    create_progress_bar,
    escape_markdown,
    format_usage_line,
    get_status_indicator,
)


class MemoryBlock(Block):
    metadata = PluginMetadata(
        id="memory",
        name="Memory",
        description="Memory usage",
        version="1.0.0",
        icon=EMOJI["memory"],
        sends_alerts=True,
    )

    @property
    def alert_threshold(self) -> int:
        return self.context.alerts.memory_threshold

    def check_alert(self) -> Alert | None:
        return self.context.alerts.check_memory_alert(self.context.system.get_memory_metrics())

    def summary(self) -> str:
        return f"{self.context.system.get_memory_metrics().percent:.1f}%"

    def status_section(self) -> str:
        metrics = self.context.system.get_memory_metrics()
        size = escape_markdown(f"{metrics.used_gb:.1f}GB / {metrics.total_gb:.1f}GB")
        return f"{self.metadata.icon} *{self.metadata.name}*\n{format_usage_line(metrics.percent)}\n└ {size}"

    def render(self) -> BlockView:
        metrics = self.context.system.get_memory_metrics()
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
    status_indicator = get_status_indicator(metrics.percent)
    bar = create_progress_bar(metrics.percent, 100, length=12)

    message = ""
    message += "*Usage*\n"
    message += f"{escape_markdown(bar)} {escape_markdown(f'{metrics.percent:.1f}%')} {status_indicator}\n\n"
    message += f"*Total:* {escape_markdown(f'{metrics.total_gb:.2f} GB')}\n"
    message += f"*Used:* {escape_markdown(f'{metrics.used_gb:.2f} GB')}\n"
    message += f"*Available:* {escape_markdown(f'{metrics.available_gb:.2f} GB')}\n"

    if metrics.swap_total > 0:
        swap_indicator = get_status_indicator(metrics.swap_percent)
        swap_bar = create_progress_bar(metrics.swap_percent, 100, length=12)
        message += "\n*Swap*\n"
        message += f"{escape_markdown(swap_bar)} {escape_markdown(f'{metrics.swap_percent:.1f}%')} {swap_indicator}\n"
        message += f"└ {escape_markdown(f'{metrics.swap_used / (1024**3):.2f} GB')} / {escape_markdown(f'{metrics.swap_total / (1024**3):.2f} GB')}\n"

    return message
