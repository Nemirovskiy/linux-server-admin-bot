"""The lsab command."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from bot import __version__
from bot.api import PLUGIN_API_VERSION
from bot.plugins.config import PluginConfig
from bot.runtime_status import read_status
from cli.management import PluginManagement
from cli.output import emit, table
from cli.services import ServiceError, service_controller

_SOURCE_ROOT = Path(__file__).resolve().parent.parent


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="lsab", description="Manage Linux Server Admin Bot")
    root.add_argument("--config", type=Path, help="blocks.toml path (or BLOCKS_CONFIG)")
    root.add_argument("--json", action="store_true", help="JSON output for automation")
    root.add_argument(
        "--verbose", action="store_true", help="show informational diagnostics on stderr"
    )
    root.add_argument("--no-color", action="store_true", help="plain text output (the default)")
    root.add_argument("--service", choices=("auto", "systemd", "docker", "manual"), default="auto")
    root.add_argument("--project-dir", type=Path, help="Compose project or native checkout path")
    root.add_argument(
        "--compose-file",
        type=Path,
        action="append",
        default=[],
        help="repeat for Compose overrides",
    )
    commands = root.add_subparsers(dest="command", required=True)
    plugins = commands.add_parser("plugins", help="view and manage built-in blocks")
    actions = plugins.add_subparsers(dest="action", required=True)
    for name, help_text in (
        ("list", "list known blocks"),
        ("check", "check enabled blocks"),
        ("configure", "edit switches interactively"),
        ("apply", "verify configuration, restart and wait for readiness"),
    ):
        actions.add_parser(name, help=help_text)
    for name, help_text in (
        ("info", "show a block manifest"),
        ("doctor", "check one block, even if disabled"),
    ):
        actions.add_parser(name, help=help_text).add_argument("id")
    for name in ("enable", "disable"):
        change = actions.add_parser(name, help=f"{name} one or more blocks")
        change.add_argument("ids", nargs="+")
        modes = change.add_mutually_exclusive_group()
        modes.add_argument("--dry-run", action="store_true", help="preview without writing")
        modes.add_argument("--apply", action="store_true", help="validate and restart after saving")
    rollback = actions.add_parser("rollback", help="restore a previous verified configuration")
    rollback.add_argument("--apply", action="store_true", help="restart after restoring")
    for name, help_text in (
        ("status", "show service, desired and runtime state"),
        ("start", "start the service"),
        ("stop", "stop the service"),
        ("restart", "restart the service"),
        ("version", "show bot and Plugin API versions"),
        ("doctor", "diagnose configured blocks and show service state"),
    ):
        commands.add_parser(name, help=help_text)
    logs = commands.add_parser("logs", help="read recent service logs")
    logs.add_argument("--follow", action="store_true")
    return root


def _locations(args: argparse.Namespace) -> tuple[Path, PluginConfig]:
    source_checkout = (_SOURCE_ROOT / "blocks.toml.example").is_file()
    project = (args.project_dir or (_SOURCE_ROOT if source_checkout else Path.cwd())).resolve()
    default = project / ("data/config/blocks.toml" if args.service == "docker" else "blocks.toml")
    path = args.config or Path(os.environ.get("BLOCKS_CONFIG", default))
    template = project / "blocks.toml.example"
    return project, PluginConfig(path, template if template.is_file() else None)


def _plugin_table(plugins: list[dict], *, runtime: bool = False) -> str:
    headers = ("ID", "SOURCE", "CONFIG")
    rows = [
        (row["id"], row["source"], "enabled" if row["enabled"] else "disabled") for row in plugins
    ]
    if runtime:
        return table(
            (*headers, "EXECUTION"),
            ((*row, info["execution"]) for row, info in zip(rows, plugins, strict=True)),
        )
    return table(headers, rows)


def _diagnostic_text(rows: list[dict]) -> str:
    return (
        table(
            ("ID", "RESULT", "DETAIL"),
            (
                (
                    row["id"],
                    "OK" if row["ok"] else "FAILED",
                    row["error"] or row["dependency_issue"] or "",
                )
                for row in rows
            ),
        )
        if rows
        else "No enabled blocks."
    )


def _interactive(management: PluginManagement, args: argparse.Namespace, project: Path) -> int:
    if args.json or not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("plugins configure requires an interactive terminal and plain text output")
    from cli.tui import open_configure

    snapshot = management.snapshot()
    manager = management.manager(snapshot.text)
    plugins = [management.describe(record) for record in manager.list_plugins()]
    updates = open_configure(plugins)
    if not updates:
        print("No changes saved.")
        return 0
    management.change(updates, expected=snapshot)
    print("Configuration saved.")
    controller = service_controller(args.service, project, args.compose_file)
    if controller.kind != "manual" and input(
        "Apply changes and restart the bot? [y/N] "
    ).lower() in {"y", "yes"}:
        management.apply(controller)
        print("Service is ready with the new configuration.")
    return 0


def _plugins(args: argparse.Namespace, management: PluginManagement, project: Path) -> int:
    output_json = args.json
    match args.action:
        case "list":
            rows = management.plugins()
            emit(rows, as_json=output_json, text=_plugin_table(rows))
        case "info":
            runtime = read_status(management.directory)
            row = management.describe(management.manager().get_plugin(args.id), runtime)
            fields = row["metadata"] or {"id": row["id"]}
            details = {
                **fields,
                "source": row["source"],
                "enabled": row["enabled"],
                "execution": row["execution"],
                "entry_point": row["entry_point"],
                "error": row["error"],
            }
            emit(
                row,
                as_json=output_json,
                text="\n".join(
                    f"{key}: {value}" for key, value in details.items() if value is not None
                ),
            )
        case "check" | "doctor":
            rows = management.diagnose(args.id if args.action == "doctor" else None)
            emit(rows, as_json=output_json, text=_diagnostic_text(rows))
            return int(not all(row["ok"] for row in rows))
        case "enable" | "disable":
            controller = (
                service_controller(args.service, project, args.compose_file) if args.apply else None
            )
            result = management.change(
                dict.fromkeys(args.ids, args.action == "enable"),
                dry_run=args.dry_run,
                controller=controller,
            )
            action = "Would " + args.action if args.dry_run else args.action.capitalize() + "d"
            message = f"{action} {', '.join(result['switches'])}."
            if result["applied"]:
                message += " Service is ready."
            emit(result, as_json=output_json, text=message)
        case "apply":
            controller = service_controller(args.service, project, args.compose_file)
            emit(
                management.apply(controller),
                as_json=output_json,
                text="Service is ready with the new configuration.",
            )
        case "rollback":
            controller = (
                service_controller(args.service, project, args.compose_file) if args.apply else None
            )
            result = management.rollback(controller)
            message = "Previous configuration restored."
            if result["applied"]:
                message += " Service is ready."
            emit(result, as_json=output_json, text=message)
        case "configure":
            return _interactive(management, args, project)
    return 0


def _run(args: argparse.Namespace) -> int:
    if args.command == "version":
        data = {"version": __version__, "plugin_api_version": PLUGIN_API_VERSION}
        emit(data, as_json=args.json, text=f"lsab {__version__} (Plugin API {PLUGIN_API_VERSION})")
        return 0
    project, config = _locations(args)
    management = PluginManagement(config)
    if args.command == "plugins":
        return _plugins(args, management, project)

    controller = service_controller(args.service, project, args.compose_file)
    if args.command == "status":
        data = management.status(controller)
        runtime = data["runtime"]
        running = f"ready (PID {runtime['pid']})" if runtime else "no fresh readiness report"
        service = data["service"]
        pending = "\nConfiguration has pending changes." if data["pending_restart"] else ""
        text = f"Service: {service['backend']} / {service['state']}\nBot: {running}{pending}\n\n"
        emit(data, as_json=args.json, text=text + _plugin_table(data["plugins"], runtime=True))
    elif args.command == "doctor":
        rows = management.diagnose()
        service = controller.status()
        emit(
            {"service": service, "plugins": rows},
            as_json=args.json,
            text=f"Service: {service['backend']} / {service['state']}\n\n" + _diagnostic_text(rows),
        )
        return int(not all(row["ok"] for row in rows))
    elif args.command == "logs":
        if args.follow and args.json:
            raise ValueError("--follow requires plain text output")
        output = controller.logs(args.follow)
        emit({"logs": output}, as_json=args.json, text=output.rstrip())
    else:
        operation = {
            "start": controller.start,
            "stop": controller.stop,
            "restart": controller.restart,
        }[args.command]
        operation()
        emit(
            {"operation": args.command, "backend": controller.kind},
            as_json=args.json,
            text=f"Service {args.command} requested.",
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s: %(message)s"
    )
    try:
        return _run(args)
    except KeyError as exc:
        error = f"Unknown block: {exc.args[0]}"
    except (OSError, ValueError, ServiceError, EOFError) as exc:
        error = str(exc)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    if args.json:
        emit({"error": error}, as_json=True)
    else:
        print(f"lsab: {error}", file=sys.stderr)
    return 1
