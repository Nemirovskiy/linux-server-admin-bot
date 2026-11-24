"""System temperature block."""

from bot.api import Block, BlockView, PluginMetadata
from bot.api.presentation import (
    EMOJI,
    escape_markdown,
)


class TempBlock(Block):
    metadata = PluginMetadata(
        id="temp",
        name="Temperature",
        description="System temperature",
        version="1.0.0",
        icon=EMOJI["temp"],
    )

    def summary(self) -> str:
        temperatures = self.context.system.get_temperature() or {}
        readings = [sensor.current for sensors in temperatures.values() for sensor in sensors]
        return f"{max(readings):.1f}°C (highest)" if readings else "Unavailable"

    def render(self) -> BlockView:
        temps = self.context.system.get_temperature()
        if not temps:
            return self.view(
                f"{EMOJI['warning']} No temperature sensors found\\.\n\n"
                f"_This may happen if:_\n"
                f"• Hardware has no sensors\n"
                f"• Drivers are not installed\n"
                f"• Container has no access to /sys"
            )

        message = ""

        for sensor_type, sensors in temps.items():
            sensor_name = escape_markdown(sensor_type.replace("_", " ").title())
            message += f"*{sensor_name}:*\n"

            for sensor in sensors:
                label = escape_markdown(sensor.label or "Sensor")
                current = sensor.current
                high = sensor.high
                critical = sensor.critical

                if critical and current >= critical:
                    status = "🔴"
                elif high and current >= high:
                    status = "🟡"
                else:
                    status = "🟢"

                temp_str = escape_markdown(f"{current:.1f}°C")
                message += f"  {status} {label}: {temp_str}"

                if high or critical:
                    limits = []
                    if high:
                        limits.append(f"max: {high:.0f}°C")
                    if critical:
                        limits.append(f"crit: {critical:.0f}°C")
                    limits_str = escape_markdown(f" ({', '.join(limits)})")
                    message += limits_str
                message += "\n"

            message += "\n"

        return self.view(message)
