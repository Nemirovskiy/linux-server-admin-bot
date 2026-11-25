"""Terminal descriptions of validated external broker operations."""

_DOCKER_ACTIONS = {
    "docker.containers.read": "List the selected Docker containers",
    "docker.containers.inspect": "Inspect the selected Docker containers",
    "docker.containers.stats": "Read resource usage for the selected Docker containers",
    "docker.containers.start": "Start the selected Docker containers",
    "docker.containers.stop": "Stop the selected Docker containers",
    "docker.containers.restart": "Restart the selected Docker containers",
}


def describe_operation(spec: dict, container_names: dict[str, str] | None = None) -> list[str]:
    if "docker_action" in spec:
        action = spec["docker_action"]
        lines = [f"{_DOCKER_ACTIONS[action]} ({action})."]
        if not spec["containers"]:
            return [*lines, "Full container IDs must be selected before approval."]
        if container_names is None:
            lines.append("Proposed container IDs. You can keep or replace them:")
        else:
            lines.append("Access is limited to these container IDs:")
        for container in spec["containers"]:
            name = container_names.get(container) if container_names else None
            lines.append(f"  {name!r} ({container})" if name else f"  {container}")
        return lines

    command = " ".join(spec["argv"])
    lines = ["The broker runs this command with system privileges:", f"  {command}"]
    for parameter, bounds in sorted(spec["parameters"].items()):
        lines.append(f"Allowed values for {parameter}: {bounds['min']} to {bounds['max']}.")
    return lines
