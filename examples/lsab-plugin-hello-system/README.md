# Hello System

Example external plugin for Linux Server Admin Bot. It shows the host name,
OS, local time, and installed versions of the plugin and SDK. It requests
no broker permissions.

Install into the bot's Python environment with `python -m pip install .`.
The environment must already contain a compatible `lsab-api`. Inspect it
with `lsab plugins info hello_system` and enable it with
`lsab plugins enable hello_system`.

Optional settings in `plugins/hello_system.toml` next to `blocks.toml`:

```toml
[settings]
show_hostname = true
show_uptime = false
```

The plugin records its latest setup time in its separate data directory.
