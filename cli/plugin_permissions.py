"""Call the installed broker's separate system administration entry point."""

import json
import subprocess
import sys

from bot.broker_client import broker_grants
from broker.entry import ADMIN
from broker.permission_text import describe_operation
from broker.policy import POLICY_PATH
from broker.protocol import CONTAINER_ID
from broker.transport import HELPER, SudoTransport

_APPROVAL_MESSAGES = {
    "approval_required": "Broker permissions require separate administrator approval.",
    "broker_unavailable": "The system broker is unavailable.",
    "editable": "Broker permissions require an installation from a prebuilt wheel.",
    "prebuilt_wheel_required": "Broker permissions require an installation from a prebuilt wheel.",
    "approval_failed": "Administrator approval could not be completed.",
}


def approve(plan: dict) -> str:
    definitions = plan.get("broker_operations", {})
    if not definitions:
        return "not_requested"
    status = _approval_blocker(plan)
    selected = {}
    if status is None:
        print(
            f"\n{plan['id']} is installed and disabled. Choose which broker permissions to request.",
            file=sys.stderr,
        )
        selected = _selected_operations(plan, definitions)
        status = _request_approval(plan, selected) if selected else "declined"
    _report_approval(plan, selected, status)
    return status


def _approval_blocker(plan: dict) -> str | None:
    if plan["source"]["editable"]:
        return "editable"
    if plan["source"]["kind"] in {"directory", "git"}:
        return "prebuilt_wheel_required"
    if not HELPER.is_file() or not ADMIN.is_file():
        return "broker_unavailable"
    if not sys.stdin.isatty():
        return "approval_required"
    return None


def _report_approval(plan: dict, selected: dict, status: str) -> None:
    message = _APPROVAL_MESSAGES.get(status)
    if message:
        print(message, file=sys.stderr)
    elif selected and status == "declined":
        print("The administrator did not approve the selected permissions.", file=sys.stderr)

    granted = set(selected) if status == "approved" else set()
    skipped = plan["broker_operations"].keys() - granted
    if granted:
        print(f"Broker permissions granted: {', '.join(sorted(granted))}.", file=sys.stderr)
    if skipped:
        print(f"Broker permissions not granted: {', '.join(sorted(skipped))}.", file=sys.stderr)
    missing = skipped.intersection(plan["required"])
    if missing:
        print(f"The plugin cannot load without: {', '.join(sorted(missing))}.", file=sys.stderr)


def _request_approval(plan: dict, selected: dict) -> str:
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
        spec = definitions[action]
        required = action in plan["required"]
        kind = "Required" if required else "Optional"
        print(f"\n{kind} broker permission: {action}", file=sys.stderr)
        print("\n".join(describe_operation(spec)), file=sys.stderr)
        if required:
            print("Without this permission, the plugin cannot load.", file=sys.stderr)
        else:
            print("Without this permission, the operation will be unavailable.", file=sys.stderr)
        try:
            answer = input("Request administrator approval for this operation? [y/N] ")
        except EOFError:
            return {}
        if answer.strip().lower() in {"y", "yes"}:
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
