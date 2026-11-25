"""Root-only updates to the installed broker policy."""

import argparse
import fcntl
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import replace
from tempfile import NamedTemporaryFile

from broker.actions import ACTIONS, OWNERS
from broker.executor import run_command
from broker.operations import check_executable
from broker.policy import MAX_POLICY, POLICY_PATH, Grant, Policy, external_grant, load_policy
from broker.protocol import CONTAINER_ID, IDENTIFIER, BrokerError, json_loads
from broker.security import trusted
from broker.worker import audit


def _format(policy: Policy) -> bytes:
    lines = [
        "[policy]",
        "version = 1",
        'default = "deny"',
        f"caller_uids = {json.dumps(sorted(policy.callers))}",
    ]
    for name, grant in sorted(policy.plugins.items()):
        lines.extend(
            (
                "",
                f"[plugins.{name}]",
                f"enabled = {str(grant.enabled).lower()}",
                f"allow = {json.dumps(sorted(grant.allow))}",
                f"containers = {json.dumps(sorted(grant.containers))}",
            )
        )
    if policy.external:
        lines.extend(("", "[external]"))
        for name, record in sorted(policy.external.items()):
            lines.append(f"{name} = {json.dumps(json.dumps(record, sort_keys=True))}")
    return ("\n".join(lines) + "\n").encode("ascii")


def administer(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="lsab-broker --admin")
    parser.add_argument(
        "operation",
        choices=("grant", "revoke", "check-policy", "approve-external", "revoke-external"),
    )
    parser.add_argument("plugin", nargs="?")
    parser.add_argument("action", nargs="?")
    parser.add_argument("--container", action="append", default=[])
    parser.add_argument("--proposal")
    args = parser.parse_args(argv)
    try:
        if args.proposal is not None and args.operation != "approve-external":
            raise ValueError("--proposal is for external approval only")
        if args.operation == "check-policy":
            load_policy(POLICY_PATH, OWNERS)
            print("Broker policy is valid.")
            return 0
        if args.operation in {"approve-external", "revoke-external"}:
            if not _update_external(args):
                print("External broker approval declined.")
                return 2
        else:
            _update(args)
    except (BrokerError, OSError, ValueError, RecursionError) as exc:
        parser.exit(1, f"Broker policy update failed: {exc}\n")
    print("Broker policy updated.")
    return 0


def _update(args: argparse.Namespace) -> None:
    action = ACTIONS.get(args.action)
    if action is None or action.plugin != args.plugin:
        raise ValueError("Select a registered action for the given plugin")
    if args.container and (args.operation != "grant" or args.plugin != "docker"):
        raise ValueError("--container is available for Docker grants only")
    if any(not CONTAINER_ID.fullmatch(item) for item in args.container):
        raise ValueError("Provide full lowercase hexadecimal container IDs")
    with _locked_policy() as policy:
        grant = policy.plugins.get(args.plugin, Grant(False, frozenset()))
        if args.operation == "grant":
            containers = grant.containers.union(args.container)
            if action.mutates and not containers:
                raise ValueError(
                    "Docker writes require at least one explicitly allowed --container"
                )
            grant = Grant(True, grant.allow | {args.action}, containers)
        else:
            grant = replace(grant, allow=grant.allow - {args.action})
        _save(replace(policy, plugins={**policy.plugins, args.plugin: grant}))


def _update_external(args: argparse.Namespace) -> bool:
    plugin = args.plugin
    if not plugin or not IDENTIFIER.fullmatch(plugin) or args.action or args.container:
        raise ValueError("Select one external plugin ID")
    record = None
    if args.operation == "approve-external":
        if args.proposal is None or len(args.proposal) > MAX_POLICY:
            raise ValueError("Supply the bounded JSON approval proposal")
        raw = args.proposal.encode("utf-8")
        if len(raw) > MAX_POLICY:
            raise ValueError("Approval proposal is too large")
        record = external_grant(plugin, json_loads(raw))
        if not record["operations"]:
            raise ValueError("Select at least one operation")
        for spec in record["operations"].values():
            check_executable(spec)
        print(f"Approve external broker operations for {plugin}?")
        print(json.dumps(record, indent=2, sort_keys=True), flush=True)
        _show_docker_targets(record)
        with open("/dev/tty", encoding="utf-8") as terminal:
            print(
                "Type 'approve' to update the system policy: ", end="", file=sys.stderr, flush=True
            )
            if terminal.readline(64).strip() != "approve":
                return False
    with _locked_policy() as policy:
        records = policy.external.copy()
        if record is None:
            records.pop(plugin, None)
        else:
            records[plugin] = record
        _save(replace(policy, external=records))
    audit(
        {
            "operation": args.operation,
            "plugin_id": plugin,
            "actions": sorted(record["operations"]) if record else [],
            "artifact": record["sha256"] if record else None,
            "result": "OK",
        }
    )
    return True


def _show_docker_targets(record: dict) -> None:
    ids = {
        container
        for spec in record["operations"].values()
        for container in spec.get("containers", [])
    }
    if not ids:
        return
    action = ACTIONS["docker.containers.read"]
    containers = action.output(run_command(action.argv, action.timeout))
    names = {row["id"]: row["name"] for row in containers if row["id"] in ids}
    if ids - names.keys():
        raise ValueError("An approved Docker target is missing; enter current full container IDs")
    print("Docker targets " + json.dumps(names, sort_keys=True), flush=True)


@contextmanager
def _locked_policy():
    trusted(POLICY_PATH.parent, directory=True)
    lock_name = POLICY_PATH.with_name(".permissions.lock")
    fd = os.open(lock_name, os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
    with os.fdopen(fd, "rb+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield load_policy(POLICY_PATH, OWNERS)


def _save(policy: Policy) -> None:
    raw = _format(policy)
    if len(raw) > MAX_POLICY:
        raise ValueError("Broker policy is too large")
    Policy.parse(raw, OWNERS)
    _replace(raw)


def _replace(raw: bytes) -> None:
    with NamedTemporaryFile(dir=POLICY_PATH.parent, delete=False) as stream:
        try:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(stream.name, POLICY_PATH)
        finally:
            if os.path.exists(stream.name):
                os.unlink(stream.name)
