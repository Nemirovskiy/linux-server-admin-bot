"""Call the installed broker's separate system administration entry point."""

import json
import subprocess
import sys

from bot.broker_client import broker_grants
from broker.entry import ADMIN
from broker.policy import POLICY_PATH
from broker.protocol import CONTAINER_ID
from broker.transport import HELPER, SudoTransport


def approve(plan: dict) -> str:
    definitions = plan.get("broker_operations", {})
    if not definitions:
        return "not_requested"
    if plan["source"]["editable"]:
        return "editable"
    if plan["source"]["kind"] in {"directory", "git"}:
        return "prebuilt_wheel_required"
    if not HELPER.is_file() or not ADMIN.is_file():
        return "broker_unavailable"
    if not sys.stdin.isatty():
        return "approval_required"

    selected = _selected_operations(plan, definitions)
    if not selected:
        return "declined"

    payload = json.dumps({"sha256": plan["sha256"], "operations": selected})
    try:
        result = _admin("approve-external", plan["id"], payload)
    except (OSError, subprocess.TimeoutExpired):
        return "approval_failed"
    if result == 2:
        return "declined"
    return "approved" if result == 0 else "approval_failed"


def _selected_operations(plan: dict, definitions: dict) -> dict:
    selected = {}
    for action in sorted(definitions):
        kind = "required" if action in plan["required"] else "optional"
        print(json.dumps({action: definitions[action]}, indent=2), file=sys.stderr)
        try:
            answer = input(f"Request administrator approval for {action} ({kind})? [y/N] ")
        except EOFError:
            return {}
        if answer.strip().lower() in {"y", "yes"}:
            spec = definitions[action]
            if "docker_action" in spec:
                spec = _select_containers(spec)
                if spec is None:
                    continue
            selected[action] = spec
    return selected


def _select_containers(spec: dict) -> dict | None:
    proposed = spec["containers"]
    default = " (Enter to keep the proposed IDs)" if proposed else ""
    try:
        answer = input(f"Full container IDs, separated by spaces{default}: ").split()
    except EOFError:
        return None
    ids = list(dict.fromkeys(answer or proposed))
    if not ids or len(ids) > 32 or any(not CONTAINER_ID.fullmatch(item) for item in ids):
        print("This operation requires one to 32 full lowercase container IDs.", file=sys.stderr)
        return None
    return {**spec, "containers": ids}


def revoke(plugin: str) -> None:
    if not HELPER.is_file():
        if ADMIN.is_file() or POLICY_PATH.parent.is_dir():
            raise ValueError(
                "Restore the broker or revoke the system policy before removing the package"
            )
        return
    grants = broker_grants(SudoTransport())
    if not grants.success:
        raise ValueError(f"Could not verify broker rights for {plugin}: {grants.message}")
    record = grants.data.get(plugin)
    if not isinstance(record, dict) or "sha256" not in record:
        return
    try:
        result = _admin("revoke-external", plugin)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Could not revoke broker rights for {plugin}: {exc}") from exc
    if result:
        raise ValueError(f"Could not revoke broker rights for {plugin} with lsab-broker-admin")


def _admin(operation: str, plugin: str, payload: str | None = None) -> int:
    command = ["/usr/bin/sudo", "--", str(ADMIN), operation, plugin]
    if payload is not None:
        command.extend(("--proposal", payload))
    return subprocess.run(
        command,
        stdout=sys.stderr,
        cwd="/",
        check=False,
        timeout=180,
    ).returncode
