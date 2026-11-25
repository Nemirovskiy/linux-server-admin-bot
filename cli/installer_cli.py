"""Installer commands and interactive operation selection."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from broker.permission_text import describe_operation
from cli.installer import Installer
from cli.installer_sources import Source, resolve
from cli.output import emit, table
from cli.plugin_permissions import approve
from packaging.version import Version

COMMANDS = {
    "install",
    "update",
    "outdated",
    "remove",
    "history",
    "environment",
    "cache",
    "installer",
}


def add_commands(actions: argparse._SubParsersAction) -> None:
    install = actions.add_parser(
        "install", help="install an external plugin in a replacement environment"
    )
    install.add_argument("source")
    install.add_argument("--editable", action="store_true")
    install.add_argument("--ref")
    install.add_argument("--sha256")
    _confirmation(install, preview=True)
    update = actions.add_parser("update", help="update an installed external plugin")
    update.add_argument("id", nargs="?")
    update.add_argument("--all", action="store_true")
    update.add_argument("--version")
    update.add_argument("--source")
    update.add_argument("--ref")
    update.add_argument("--pre", action="store_true")
    update.add_argument("--sha256")
    update.add_argument("--allow-migration", action="store_true")
    _confirmation(update, preview=True)
    outdated = actions.add_parser("outdated", help="check for newer releases")
    outdated.add_argument("--pre", action="store_true")
    _confirmation(outdated)
    remove = actions.add_parser(
        "remove", help="remove a package while keeping its files and settings"
    )
    remove.add_argument("id")
    remove.add_argument("--purge", action="store_true")
    _confirmation(remove, preview=True)
    environment = actions.add_parser("environment", help="inspect or recover managed environments")
    environment.add_argument("operation", choices=("info", "check", "lock", "recover", "export"))
    environment.add_argument(
        "path", nargs="?", type=Path, help="destination for a Docker wheel bundle"
    )
    cache = actions.add_parser("cache", help="inspect or clean the package cache")
    cache.add_argument("operation", choices=("info", "clean"))
    actions.add_parser("installer", help="open the interactive installer menu")
    actions.add_parser("history", help="list package transactions")


def _confirmation(parser: argparse.ArgumentParser, *, preview: bool = False) -> None:
    parser.add_argument("--yes", action="store_true", help="confirm trusted package operations")
    if preview:
        parser.add_argument(
            "--dry-run", action="store_true", help="show the plan without switching"
        )


def _confirm(args, prompt: str) -> None:
    if args.yes:
        return
    if args.json or not sys.stdin.isatty():
        raise ValueError(f"{prompt} Use --yes to confirm non-interactively.")
    if input(prompt + " [y/N] ").strip().lower() not in {"y", "yes"}:
        raise ValueError("Operation cancelled")


def _output(args, data, *, human: bool = True) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False) if human and not args.json else ""
    emit(data, as_json=args.json, text=text)


def _installation_prompt(plan: dict) -> str:
    plugin, version = plan["id"], plan["version"]
    if plan["operation"] == "update":
        return f"Update {plugin} from {plan['previous_version']} to {version}?"
    return f"Install {plugin} {version}?"


def _show_installation_plan(plan: dict, *, revoke_previous: bool, preview: bool) -> None:
    stream = sys.stdout if preview else sys.stderr
    if preview:
        print("Planned change: " + _installation_prompt(plan).removesuffix("?") + ".", file=stream)
        print(f"Wheel SHA-256: {plan['sha256']}", file=stream)
    source = plan["source"]
    print(f"Source ({source['kind']}): {source['value']!r}", file=stream)
    if source["commit"]:
        print(f"Git revision: {source['ref']} ({source['commit']})", file=stream)
    if plan["schema_changed"]:
        print("Data schema changes. Applying this update requires --allow-migration.", file=stream)
    if plan["packages"]:
        packages = ", ".join(f"{item['name']} {item['version']}" for item in plan["packages"])
        print(f"Packages to install or replace: {packages}.", file=stream)
    if plan["new_permissions"]:
        print(f"New permissions declared: {', '.join(plan['new_permissions'])}.", file=stream)
    operations = plan["broker_operations"]
    if revoke_previous:
        print("The update will revoke the previous version's broker permissions.", file=stream)
    if plan["operation"] == "install" or operations or revoke_previous:
        print("The plugin will be disabled after this change.", file=stream)
    if operations:
        _show_broker_plan(plan, preview=preview)


def _show_broker_plan(plan: dict, *, preview: bool) -> None:
    stream = sys.stdout if preview else sys.stderr
    print("Broker permissions require separate administrator approval.", file=stream)
    source = plan["source"]
    if source["editable"] or source["kind"] in {"directory", "git"}:
        print("Granting broker permissions requires installation from a prebuilt wheel.", file=stream)
    elif not preview and sys.stdin.isatty():
        print(
            "If the system broker is available, you can choose which permissions to request "
            "after installation.",
            file=stream,
        )
    if preview:
        for action, spec in sorted(plan["broker_operations"].items()):
            kind = "Required" if action in plan["required"] else "Optional"
            print(f"\n{kind} broker permission: {action}", file=stream)
            print("\n".join(describe_operation(spec)), file=stream)


def _plan(
    args, installer: Installer, source: Source, operation: str, key: str | None = None
) -> dict:
    installer._guard()
    if source.builds_code:
        _confirm(args, "Building from source may execute package build code. Trust this source?")
    with tempfile.TemporaryDirectory(prefix="lsab-install-") as directory:
        plan, wheel, inventory = installer.prepare(
            source,
            Path(directory),
            operation=operation,
            plugin_id=key,
            expected_hash=getattr(args, "sha256", None),
            pre=getattr(args, "pre", False),
        )
        if (
            operation == "update"
            and Version(plan["version"]) < Version(plan["previous_version"])
            and not (args.version or args.ref or args.source)
        ):
            raise ValueError(
                "Automatic update would downgrade the plugin; select a version explicitly"
            )
        revoke_previous = False
        if not args.json:
            if operation == "update":
                previous = installer._records(Path(plan["base"])).get(plan["id"], {})
                revoke_previous = bool(previous.get("broker_operations"))
            _show_installation_plan(plan, revoke_previous=revoke_previous, preview=args.dry_run)
        if args.dry_run:
            return plan
        _confirm(args, _installation_prompt(plan))
        options = {"allow_migration": getattr(args, "allow_migration", False)}
        if plan.get("broker_operations") and not args.json:
            options["approve_permissions"] = approve
        result = installer.apply(plan, wheel, inventory, **options)
        if not args.json:
            verb = "Installed" if operation == "install" else "Updated"
            disabled = operation == "install" or plan["broker_operations"] or revoke_previous
            suffix = " The plugin is disabled." if disabled else ""
            print(f"{verb} {plan['id']} {plan['version']}.{suffix}")
        return result


def rollback(args, installer: Installer) -> int:
    _confirm(args, "Restore the previous package environment or version?")
    if args.id is None:
        _output(args, installer.rollback())
        return 0
    old = installer.prior_artifact(args.id)
    path = installer.cache / old["sha256"] / old["wheel"]
    if not path.is_file():
        raise ValueError("Previous wheel is no longer cached")
    source = resolve(str(path))
    with tempfile.TemporaryDirectory(prefix="lsab-rollback-") as directory:
        installed = installer._installed(installer._inventory(installer.active))
        operation = "rollback" if args.id in installed else "install"
        plan, wheel, inventory = installer.prepare(
            source,
            Path(directory),
            operation=operation,
            plugin_id=args.id,
            expected_hash=old["sha256"],
        )
        plan["source"] = {**old["source"], "editable": False}
        if plan["schema_changed"]:
            raise ValueError(
                "Data schema differs; restore the documented backup before rolling back"
            )
        _output(args, installer.apply(plan, wheel, inventory))
    return 0


def run(args, installer: Installer) -> int:
    match args.action:
        case "install":
            result = _plan(
                args, installer, resolve(args.source, args.ref, args.editable), "install"
            )
        case "update":
            if (
                bool(args.id) == args.all
                or args.all
                and (args.version or args.source or args.ref or args.sha256)
            ):
                raise ValueError(
                    "Select one plugin or --all; source and version options require one plugin"
                )
            ids = (
                [args.id]
                if args.id
                else sorted(installer._installed(installer._inventory(installer.active)))
            )
            result = []
            for key in ids:
                source = (
                    resolve(args.source, args.ref)
                    if args.source
                    else installer.source_for(key, args.version, args.ref, pre=args.pre)
                )
                result.append(_plan(args, installer, source, "update", key))
        case "outdated":
            result = _outdated(args, installer)
        case "remove":
            plan = installer.remove(args.id, dry_run=True, purge=args.purge)
            if not args.dry_run:
                print(json.dumps(plan, ensure_ascii=False, indent=2), file=sys.stderr)
                _confirm(
                    args,
                    (
                        "Remove the plugin and purge its settings and data?"
                        if args.purge
                        else "Remove the plugin?"
                    ),
                )
            result = plan if args.dry_run else installer.remove(args.id, purge=args.purge)
        case "history":
            result = installer.history()
        case "cache":
            result = (
                installer.clean_cache() if args.operation == "clean" else installer.cache_info()
            )
        case "environment":
            if args.operation == "export":
                if args.path is None:
                    raise ValueError("Provide a destination directory for the export")
                result = installer.export(args.path)
            elif args.path is not None:
                raise ValueError("A destination is only accepted for environment export")
            elif args.operation == "recover":
                result = installer.recover()
            elif args.operation == "lock":
                result = installer.lockfile()
            else:
                result = installer.environment(check=args.operation == "check")
        case "installer":
            return _interactive(args, installer)
        case _:
            raise ValueError(f"Unknown installer action: {args.action}")
    _output(args, result, human=args.action not in {"install", "update"})
    return 0


def _outdated(args, installer: Installer) -> list[dict]:
    installer._guard()
    rows = []
    for key, old in sorted(installer._installed(installer._inventory(installer.active)).items()):
        row = {"id": key, "installed": old["version"]}
        try:
            source = installer.source_for(key, pre=args.pre)
            if source.builds_code and not args.yes:
                raise ValueError("Source build requires --yes")
            with tempfile.TemporaryDirectory(prefix="lsab-outdated-") as directory:
                plan, _, _ = installer.prepare(
                    source, Path(directory), operation="update", plugin_id=key, pre=args.pre
                )
            row.update(
                available=plan["version"],
                outdated=Version(plan["version"]) > Version(old["version"]),
            )
        except ValueError as exc:
            row["issue"] = str(exc)
        rows.append(row)
    return rows


def _interactive(args, installer: Installer) -> int:
    if args.json or not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("The installer menu requires an interactive terminal")
    from cli.tui import open_terminal

    choice = open_terminal(_installer_menu)
    args.yes = False
    if choice == "Install":
        raw = input("Package name, wheel, directory, or GitHub URL: ").strip()
        ref = input("Git revision (empty for other sources): ").strip() or None
        args.dry_run = False
        _plan(args, installer, resolve(raw, ref), "install")
    elif choice == "Check updates":
        args.pre = False
        _output(args, _outdated(args, installer))
    elif choice == "History":
        _output(args, installer.history())
    elif choice == "Rollback":
        args.id = None
        return rollback(args, installer)
    elif choice == "Installed plugins":
        rows = installer._installed(installer._inventory(installer.active)).values()
        print(
            table(
                ("ID", "DISTRIBUTION", "VERSION"),
                ((row["id"], row["distribution"], row["version"]) for row in rows),
            )
        )
    return 0


def _installer_menu(screen):
    options = ("Installed plugins", "Install", "Check updates", "History", "Rollback")
    active = 0
    while True:
        screen.clear()
        screen.line(0, "Plugin Installer | Up/Down, Enter or Space, Q close")
        for index, name in enumerate(options):
            screen.line(index + 2, name, index == active)
        screen.refresh()
        key = screen.key().lower()
        if key in {"q", "escape"}:
            return None
        if key in {"up", "down"}:
            active = (active + (1 if key == "down" else -1)) % len(options)
        elif key in {"\r", "\n", " "}:
            return options[active]
