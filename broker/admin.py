"""Root-only updates to the installed broker policy."""

import argparse
import fcntl
import json
import os
from dataclasses import replace
from tempfile import NamedTemporaryFile

from broker.actions import ACTIONS, OWNERS
from broker.policy import POLICY_PATH, Grant, Policy, load_policy
from broker.protocol import CONTAINER_ID, BrokerError
from broker.security import trusted


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
    return ("\n".join(lines) + "\n").encode("ascii")


def administer(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="lsab-broker --admin")
    parser.add_argument("operation", choices=("grant", "revoke", "check-policy"))
    parser.add_argument("plugin", nargs="?")
    parser.add_argument("action", nargs="?")
    parser.add_argument("--container", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if args.operation == "check-policy":
            load_policy(POLICY_PATH, OWNERS)
            print("Broker policy is valid.")
            return 0
        _update(args)
    except (BrokerError, OSError, ValueError) as exc:
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
    trusted(POLICY_PATH.parent, directory=True)
    lock_name = POLICY_PATH.with_name(".permissions.lock")
    fd = os.open(lock_name, os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
    with os.fdopen(fd, "rb+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        policy = load_policy(POLICY_PATH, OWNERS)
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
        new = replace(policy, plugins={**policy.plugins, args.plugin: grant})
        raw = _format(new)
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
