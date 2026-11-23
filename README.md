# 🤖 Linux Server Admin Bot

<p align="left">
  <img src="https://github.com/ArtCC/linux-server-admin-bot/blob/main/assets/linux-server-admin-bot.png" alt="Linux Server Admin Bot" width="175"/>
</p>

[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)](Dockerfile)

Telegram bot for monitoring Ubuntu servers. Monitor system resources and receive automated alerts - all from Telegram.

## ✨ Features

### 📊 System Monitoring
- **CPU**: Real-time usage, per-core breakdown, frequency, load average
- **Memory**: RAM and swap usage with detailed breakdown
- **Disk**: Root partition space usage
- **Network**: Interface statistics, traffic monitoring
- **Processes**: Top CPU-consuming processes

### 🐳 Docker Management
- Paginated container list (10 per page)
- Container detail view with CPU, memory, IP, ports
- **Start, Stop, Restart** containers directly from Telegram
- Container status with health indicators
- Graceful detection when Docker is not available

### 📈 Visualizations
- Beautiful matplotlib charts for all metrics
- CPU usage per core
- Memory distribution pie charts
- Disk usage visualization
- Process resource graphs

### 🔔 Automated Alerts
- Configurable thresholds for CPU, RAM, and disk
- Automatic notifications when limits exceeded
- Smart cooldown to prevent spam
- Alert severity levels (info, warning, critical)

### 🔒 Security
- User whitelist authentication
- Rate limiting per user
- Comprehensive logging
- Host PID namespace with restricted capabilities (read-only process access)

## 🚀 Quick Start

### Prerequisites
- Ubuntu server (18.04+)
- Docker and Docker Compose
- Telegram Bot Token ([create one](https://t.me/botfather))
- Your Telegram User ID ([get it](https://t.me/userinfobot))

### Installation

1. **Clone the repository**
```bash
git clone https://github.com/artcc/linux-server-admin-bot.git
cd linux-server-admin-bot
```

2. **Configure environment**
```bash
cp .env.example .env
nano .env
```

Edit `.env` with your values:
```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_ALLOWED_USER_IDS=123456789,987654321

CPU_ALERT_THRESHOLD=80
MEMORY_ALERT_THRESHOLD=80
DISK_ALERT_THRESHOLD=90
```

3. **Create logs directory with correct permissions**
```bash
mkdir -p logs data/config
sudo chown -R 1000:1000 logs data/config
sudo chmod -R 775 logs
```

4. **Build and run with Docker Compose**
```bash
docker-compose up -d
```

5. **Check logs**
```bash
docker-compose logs -f
```

6. **Start chatting with your bot on Telegram!**

## 📖 Usage

### 🎨 Interactive Menu

The bot features a **beautiful interactive menu** with inline keyboard buttons for easy navigation:

- **Main Menu**: Open enabled information blocks with one tap
- **System Monitoring**: CPU, Memory, Disk, Network, Processes
- **Quick Navigation**: Back buttons and menu shortcuts

Just type `/start` to see the interactive menu!

### Available Commands

#### Basic
- `/start` - Welcome message with interactive menu
- `/help` - Show all available commands
- `/status` - Overall system status summary

#### System Monitoring
- `/cpu` - Detailed CPU information with chart
- `/memory` - Memory usage with visualization
- `/disk` - Disk space usage
- `/top` - Top processes by CPU usage
- `/network` - Network interface statistics
- `/temp` - System temperature sensors
- `/uptime` - System uptime and logged users
- `/services` - Systemd services status

#### Docker
- `/docker` - Docker container management menu

#### Alerts
- `/alerts` - View alert configuration and active alerts

#### Info
- `/author` - Bot author information

## 🏗️ Architecture

```
linux-server-admin-bot/
├── bot/
│   ├── blocks/               # One automatically discovered module per block
│   │   ├── base.py           # Shared command, rendering and navigation
│   │   └── docker.py         # Docker screens, nested menus and actions
│   ├── handlers/             # Core commands and callback routing
│   │   ├── basic.py          # Start, status, help and alerts
│   │   └── callbacks.py      # Inline keyboard callbacks
│   ├── services/             # Shared services
│   │   ├── system_monitor.py # psutil wrapper
│   │   └── alert_manager.py  # Alert system
│   ├── monitors/             # Background tasks
│   │   └── health_monitor.py # Periodic health checks
│   ├── models/               # Data models
│   │   └── metrics.py        # Dataclasses for metrics
│   └── utils/                # Utilities
│       ├── decorators.py     # Auth, rate limiting, logging
│       ├── formatters.py     # Message formatting
│       ├── keyboards.py      # Inline keyboard layouts
│       └── charts.py         # Chart generation
├── config/                   # Configuration
│   ├── settings.py           # Pydantic settings
│   ├── constants.py          # Constants and enums
│   └── logger.py             # Logging setup
├── tests/                    # Unit tests
├── docs/                     # Documentation
├── main.py                   # Application entry point
├── Dockerfile                # Multi-stage Docker build
├── docker-compose.yml        # Docker Compose config
└── requirements.txt          # Python dependencies
```

### Design Principles
- **Separation of Concerns**: Clear separation between handlers, services, and utilities
- **Type Safety**: Full type hints and Pydantic validation
- **Testability**: Dependency injection and comprehensive tests
- **Security**: Authorization, rate limiting, and audit logging
- **Observability**: Structured logging and error handling
- **Scalability**: Async/await patterns for concurrent operations

## 🔧 Configuration

### Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token | ✅ Yes | - |
| `TELEGRAM_ALLOWED_USER_IDS` | Comma-separated user IDs | ✅ Yes | - |
| `CPU_ALERT_THRESHOLD` | CPU usage alert threshold (%) | ❌ No | 80 |
| `MEMORY_ALERT_THRESHOLD` | Memory usage alert threshold (%) | ❌ No | 80 |
| `DISK_ALERT_THRESHOLD` | Disk usage alert threshold (%) | ❌ No | 90 |
| `ALERT_CHECK_INTERVAL` | Alert check interval (seconds) | ❌ No | 300 |
| `ALERT_COOLDOWN` | Alert cooldown period (seconds) | ❌ No | 600 |
| `RATE_LIMIT_CALLS` | Max calls per period | ❌ No | 10 |
| `RATE_LIMIT_PERIOD` | Rate limit period (seconds) | ❌ No | 60 |
| `LOG_LEVEL` | Logging level | ❌ No | INFO |

### Information blocks

The bot reads a local, Git-ignored `blocks.toml`. On first startup it copies
the tracked `blocks.toml.example`. On later starts it appends newly discovered
blocks with `false`, preserving existing values, comments and ordering.
Edit the local `[blocks]` table over SSH and restart to apply your changes.
Before pulling the release that removes the tracked file, save a copy of
your existing `blocks.toml` outside the checkout, then restore it to the
native or Compose location below.

**Native.** The local file defaults to `blocks.toml` beside `main.py`. An existing
file at that location is kept. To use another location, set `BLOCKS_CONFIG` or
pass `--blocks-config /path/to/blocks.toml` both for validation and startup.

```bash
python main.py --check-blocks
```

**Docker Compose.** The local file is `./data/config/blocks.toml` on the host.
Prepare the directory before starting the container. If upgrading from the
previous root-level file, copy it before the first startup:

```bash
mkdir -p data/config
if [ -f blocks.toml ] && [ ! -e data/config/blocks.toml ]; then
  cp blocks.toml data/config/blocks.toml
fi
sudo chown -R 1000:1000 data/config
docker compose run --rm --no-deps linux-server-admin-bot python main.py --check-blocks
docker compose up -d --force-recreate linux-server-admin-bot
```

The host directory persists across container and image replacement. Use the same
Compose files for validation and startup when enabling Docker management.
When validation reports a missing service or a permission problem, the check
returns a nonzero exit code. During ordinary startup the bot can still serve
other blocks, and the affected block shows its diagnostic.

### Docker management

The base Compose service runs without the Docker socket. To enable container
management, set `docker = true` in `blocks.toml`, set `DOCKER_SOCKET_GID` to the
socket's group ID if needed, and include the additional Compose file:

```bash
export DOCKER_SOCKET_GID="$(stat -c '%g' /var/run/docker.sock)"
docker compose -f docker-compose.yml -f docker-compose.docker.yml up -d --force-recreate
```

To disable management and remove access to the socket, set `docker = false` and
recreate the service using just the base Compose file.

### CrowdSec

Set `crowdsec = true` in the local `blocks.toml`, validate and restart. Open CrowdSec
from `/start` or use `/crowdsec` for the main screen. The details button is
shown when alerts exist in the last 24 hours. The IP total counts distinct
addresses with a ban decision in those alerts, including expired decisions.
LAPI availability and registered bouncers are shown separately from the totals.

CrowdSec parses its configured log sources. The bot reads the resulting alerts
and checks for new detections on the normal `ALERT_CHECK_INTERVAL`. It groups
new detections into one notification per check and starts counting from the
bot startup time. Historical alerts remain visible in the summary.

Run `cscli alerts list --since 24h --limit 0 -o json` as the account that runs
the bot to verify access. The bot calls `cscli` directly without an interactive
sudo prompt. Set `CROWDSEC_CSCLI` in the process environment to use a different
executable. In a container, that executable and the required CrowdSec
configuration and data access must be available inside the container.

### Adding a block

Place a Python file in [`bot/blocks/`](bot/blocks/) with one class that inherits
from `Block`. The bot discovers it when it starts. Use a unique command name
and add its switch to `blocks.toml`. New blocks are disabled until
explicitly enabled in the local file. The order of entries in
the config determines their order in the menu and status.

For example, save this as `bot/blocks/example.py`:

```python
from bot.blocks import Block


class ExampleBlock(Block):
    name = "example"
    title = "Example"
    icon = "📌"
    description = "Example status"

    def summary(self) -> str:
        return "Available"
```

Set `example = true` under `[blocks]`, validate the configuration and restart.
The base class supplies the Telegram command, main menu button, default detail
screen and back button. Override `render()` to add details, a chart or a
keyboard. For nested buttons, declare their callback names in `extra_actions`
and handle them in `submenu()`. Prefix nested callback names with the block
name and an underscore, for example `example_details`. The Docker block
shows this pattern with a container list, pagination and container actions.

For scheduled alerts, set `sends_alerts = True` and implement `check_alert()`.
Block files are imported during config validation and startup. Keep remote
connections and other runtime work inside methods. Only install block files
from sources you trust.

## 🎨 Bot Avatar

You can use the official bot avatar for your own instance:

<p align="left">
  <img src="https://github.com/ArtCC/linux-server-admin-bot/blob/main/assets/linux-server-admin-bot.png" alt="Bot Avatar" width="200"/>
</p>

**Download**: [linux-server-admin-bot.png](https://github.com/ArtCC/linux-server-admin-bot/blob/main/assets/linux-server-admin-bot.png)

To set this image as your bot's profile picture:
1. Download the image from the link above
2. Open [@BotFather](https://t.me/botfather) on Telegram
3. Send `/setuserpic`
4. Select your bot
5. Upload the downloaded image

## 🧪 Development

### Local Setup

1. **Create virtual environment**
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows
```

2. **Install dependencies**
```bash
pip install -r requirements.txt
```

3. **Run locally**
```bash
python main.py
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=bot --cov-report=html

# Run specific test file
pytest tests/test_system_monitor.py
```

### Code Quality

```bash
# Format code
black .

# Lint code
ruff check .

# Type checking
mypy bot/ config/
```

## 🐛 Troubleshooting

### Bot doesn't respond
- Check logs: `docker-compose logs -f`
- Verify bot token is correct
- Ensure your user ID is in `TELEGRAM_ALLOWED_USER_IDS`

### High memory usage
- Adjust resource limits in `docker-compose.yml`
- Increase alert check interval
- Reduce chart DPI in settings

### Temperature sensors not working
- Install `lm-sensors`: `sudo apt install lm-sensors`
- Run sensor detection: `sudo sensors-detect`

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 🙏 Acknowledgments

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) - Telegram Bot framework
- [psutil](https://github.com/giampaolo/psutil) - System monitoring
- [docker-py](https://github.com/docker/docker-py) - Docker SDK for Python
- [matplotlib](https://matplotlib.org/) - Visualization library

## 📧 Support

For support, open an issue on GitHub.

## 📝 License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

---

Made with ❤️ for system administrators

<p align="left">
  <sub>100% built with GitHub Copilot (Claude Opus 4.5)</sub><br>
  <sub>Arturo Carretero Calvo — 2026</sub>
</p>