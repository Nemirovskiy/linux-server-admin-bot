"""Collect environment metadata or validate enabled plugins without Telegram polling."""

import json
import sys
from importlib.metadata import distributions
from pathlib import Path

from bot.plugins.config import PluginConfig
from bot.runtime_status import config_digest
from cli.management import PluginManagement
from packaging.utils import canonicalize_name


def main() -> None:
    request = json.load(sys.stdin)
    management = PluginManagement(
        PluginConfig(
            Path(request["config"]), Path(request["template"]) if request["template"] else None
        )
    )
    manager = management.manager(request.get("configuration"))
    catalog = [
        {
            "id": record.plugin_id,
            "source": record.source,
            "state": record.state.name.lower(),
            "error": record.error,
            "distribution": record.distribution.name if record.distribution else None,
            "version": record.distribution.version if record.distribution else None,
            "origin": record.distribution.origin if record.distribution else None,
            "required": list(record.descriptor.required_permissions) if record.descriptor else [],
            "optional": list(record.descriptor.optional_permissions) if record.descriptor else [],
            "schema": record.descriptor.data_schema_version if record.descriptor else 1,
        }
        for record in (*manager.list_plugins(), *manager.conflicts)
    ]
    data = {
        "catalog": catalog,
        "defaults": manager.defaults,
        "packages": {
            canonicalize_name(dist.metadata["Name"]): dist.version
            for dist in distributions()
            if dist.metadata.get("Name") and dist.version
        },
        "digest": config_digest(manager.config_text),
        "enabled": [record.plugin_id for record in manager.startup_records()],
    }
    if request.get("check"):
        data["diagnostics"] = management.diagnose(configuration=manager.config_text)
    Path(request["output"]).write_text(json.dumps(data), encoding="utf-8")


if __name__ == "__main__":
    main()
