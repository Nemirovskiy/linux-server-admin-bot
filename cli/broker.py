"""Broker diagnostics and read-only permission inspection."""

import sys
from dataclasses import asdict

from bot.api.broker import BrokerResult
from bot.broker_client import BrokerClient
from broker.protocol import VERSION
from broker.transport import SudoTransport
from cli.output import emit, table

_CONTROL = frozenset({"broker.status", "broker.actions", "broker.grants"})


def _request(action: str) -> BrokerResult:
    return BrokerClient("broker", SudoTransport(), _CONTROL).execute_sync(action, {})


def _failure(result: BrokerResult, *, as_json: bool) -> int:
    emit(asdict(result), as_json=as_json, text=f"{result.code}: {result.message}")
    return 1


def broker_command(args) -> int:
    if args.action == "version":
        emit({"protocol_version": VERSION}, as_json=args.json, text=f"Broker protocol v{VERSION}")
        return 0
    action = "broker.status" if args.action == "status" else "broker.actions"
    result = _request(action)
    if not result.success:
        return _failure(result, as_json=args.json)
    if action == "broker.status":
        emit(
            result.data,
            as_json=args.json,
            text=f"Broker available (protocol v{result.data['protocol_version']}).",
        )
        return 0
    rows = result.data
    if args.action == "info":
        rows = [row for row in rows if row["id"] == args.id]
        if not rows:
            raise ValueError(f"Unknown broker action: {args.id}")
    text = table(
        ("ACTION", "PLUGIN", "PARAMETER", "CHANGE", "EXECUTABLE"),
        (
            (
                row["id"],
                row["plugin"],
                row["parameter"] or "none",
                row["mutates"],
                "available" if row["executable_available"] else "missing",
            )
            for row in rows
        ),
    )
    emit(rows, as_json=args.json, text=text)
    return int(args.action == "doctor" and not all(row["executable_available"] for row in rows))


def permissions_command(args, management) -> int:
    manager = management.manager()
    records = (manager.get_plugin(args.id),) if args.id else manager.list_plugins()
    grants = {}
    if args.action != "requested":
        result = _request("broker.grants")
        if not result.success:
            return _failure(result, as_json=args.json)
        grants = result.data
    rows = []
    for record in records:
        requested = set(record.metadata.permissions) if record.metadata else set()
        granted = set(grants.get(record.plugin_id, {}).get("allow", ()))
        row = {"id": record.plugin_id, "requested": sorted(requested)}
        if args.action != "requested":
            row |= {"granted": sorted(granted), "missing": sorted(requested - granted)}
        rows.append(row)
    if args.action == "browse":
        if args.json or not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError(
                "permissions browse requires an interactive terminal and plain text output"
            )
        from cli.tui import open_terminal

        if rows:
            open_terminal(lambda screen: _browse(screen, rows))
        return 0
    columns = ("requested", "granted", "missing") if args.action == "list" else (args.action,)
    emit(
        rows,
        as_json=args.json,
        text=table(
            ("PLUGIN", *(item.upper() for item in columns)),
            ((row["id"], *(", ".join(row[column]) or "–" for column in columns)) for row in rows),
        ),
    )
    return 0


def _browse(screen, rows: list[dict]) -> None:
    selected = 0
    scroll = 0
    while True:
        row = rows[selected]
        screen.clear()
        height, _ = screen.size()
        visible = max(1, height - 3)
        screen.line(0, "Broker permissions | A/D select plugin | Up/Down scroll | Q close")
        screen.line(1, f"{row['id']} ({selected + 1}/{len(rows)})", True)
        items = [
            f"{'Granted' if action in row['granted'] else 'Missing':7} {action}"
            for action in row["requested"]
        ] or ["No permissions requested."]
        scroll = min(scroll, max(0, len(items) - visible))
        for index, text in enumerate(items[scroll : scroll + visible], start=2):
            screen.line(index, text)
        screen.refresh()
        key = screen.key().lower()
        if key in {"q", "escape"}:
            return
        if key in {"a", "d"}:
            selected = (selected + (1 if key == "d" else -1)) % len(rows)
            scroll = 0
        elif key == "down":
            scroll += 1
        elif key == "up":
            scroll = max(0, scroll - 1)
