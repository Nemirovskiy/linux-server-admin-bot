"""Example external plugin written against the standalone SDK."""

import platform
from datetime import datetime
from importlib.metadata import version

from lsab_api import PLUGIN_API_VERSION, Block, BlockView, PluginMetadata, escape_markdown


class HelloSystemBlock(Block):
    metadata = PluginMetadata(
        id="hello_system", name="Hello System", description="Server name, OS, and local time",
        version=version("lsab-plugin-hello-system"), icon="🧩", api_version="1.1",
    )

    def summary(self) -> str:
        if self.context.config.get("show_hostname", True):
            return platform.node() or platform.system()
        return platform.system()

    async def setup(self) -> None:
        if self.context.data_dir is not None:
            marker = self.context.data_dir / "last-start.txt"
            marker.write_text(datetime.now().astimezone().isoformat() + "\n", encoding="utf-8")

    def render(self) -> BlockView:
        fields = []
        if self.context.config.get("show_hostname", True):
            fields.append(("Host", platform.node()))
        fields.extend((
            ("OS", platform.platform()),
            ("Local time", datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")),
            ("Plugin", self.metadata.version),
            ("API", PLUGIN_API_VERSION),
        ))
        if self.context.config.get("show_uptime", False):
            uptime = self.context.system.get_uptime_info()
            fields.append(("Uptime", f"{uptime['days']}d {uptime['hours']}h {uptime['minutes']}m"))
        return self.view("\n".join(f"*{name}:* {escape_markdown(value)}" for name, value in fields))
