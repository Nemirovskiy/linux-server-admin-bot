"""Read installed external plugin metadata without importing plugin packages."""

from __future__ import annotations

import json
import platform
import re
import sys
import tomllib
from collections.abc import Callable, Iterable, Iterator, Sequence
from importlib.metadata import (
    Distribution,
    EntryPoint,
    PackageNotFoundError,
    distributions,
    version,
)
from logging import getLogger
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from bot.api import PLUGIN_API_VERSION, PluginMetadata
from bot.plugins.models import DiscoveryFailure, DistributionInfo, PluginDescriptor
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

GROUP = "lsab.plugins"
MAX_MANIFEST_BYTES = 64 * 1024
_API_DIST = "lsab-api"
logger = getLogger(__name__)
_ENTRY = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", re.ASCII)
_PERMISSION = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,3}")


def _entries() -> Iterator[EntryPoint]:
    seen = set()
    for distribution in distributions():
        try:
            entries = distribution.entry_points.select(group=GROUP)
            if not entries:
                continue
            # lib/lib64 and other site-packages aliases can expose one wheel twice.
            key = (
                Path(distribution.locate_file("")).resolve(),
                canonicalize_name(distribution.metadata.get("Name") or ""),
                distribution.version,
            )
            if key not in seen:
                seen.add(key)
                yield from entries
        except (OSError, ValueError, AttributeError):
            logger.warning("Skipping unreadable entry point metadata in an installed distribution")


class EntryPointPluginSource:
    def __init__(self, entries: Callable[[], Iterable[EntryPoint]] = _entries) -> None:
        self._entries = entries

    def discover(self) -> Iterator[PluginDescriptor | DiscoveryFailure]:
        for entry in self._entries():
            yield self._read(entry)

    def _read(self, entry: EntryPoint) -> PluginDescriptor | DiscoveryFailure:
        distribution = None
        try:
            if entry.dist is None:
                raise ValueError("Entry point has no installed distribution")
            distribution = _distribution(entry.dist)
            if not _ENTRY.fullmatch(entry.value):
                raise ValueError("Expected a module:Class entry point without extras")
            module = PurePosixPath(*entry.module.split("."))
            root_package = PurePosixPath(module.parts[0])
            records = {PurePosixPath(path.as_posix()): path for path in entry.dist.files or ()}
            manifest = _locate(entry.dist, records, (root_package / "plugin.toml",))
            module_file = _locate(
                entry.dist, records, (module.with_suffix(".py"), module / "__init__.py")
            )
            with manifest.open("rb") as stream:
                raw = stream.read(MAX_MANIFEST_BYTES + 1)
            if len(raw) > MAX_MANIFEST_BYTES:
                raise ValueError("Plugin manifest exceeds the size limit")
            data = tomllib.loads(raw.decode("utf-8"))
            _check_requirements(distribution)
            return _descriptor(data, entry, distribution, module_file)
        except Exception as exc:
            return DiscoveryFailure(
                entry.name, "external", str(exc) or type(exc).__name__, distribution
            )


def _locate(dist: Distribution, files: dict, choices: Sequence[PurePosixPath]) -> Path:
    for key in choices:
        record = files.get(key)
        if record is not None:
            return Path(dist.locate_file(record)).resolve(strict=True)
    raise ValueError(f"Installed distribution does not record {choices[0]}")


def _origin(dist: Distribution) -> str | None:
    try:
        data = json.loads(dist.read_text("direct_url.json") or "{}")
        url = urlsplit(data["url"])
        if url.scheme not in {"file", "http", "https", "ssh", "git"}:
            return None
        # Do not echo URL credentials, query parameters or fragments.
        return urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], url.path, "", ""))
    except (KeyError, TypeError, ValueError, OSError):
        return None


def _distribution(dist: Distribution) -> DistributionInfo:
    metadata = dist.metadata
    name = metadata.get("Name")
    installed_version = dist.version
    if not name or not installed_version:
        raise ValueError("Distribution name or version is missing")
    Version(installed_version)
    return DistributionInfo(
        name=name,
        version=installed_version,
        requires_python=metadata.get("Requires-Python"),
        requires_dist=tuple(dist.requires or ()),
        author=metadata.get("Author") or metadata.get("Author-email"),
        license=metadata.get("License-Expression") or metadata.get("License"),
        origin=_origin(dist),
    )


def _check_requirements(distribution: DistributionInfo) -> None:
    python_version = platform.python_version()
    if distribution.requires_python and not SpecifierSet(distribution.requires_python).contains(
        python_version, prereleases=True
    ):
        raise ValueError(
            f"Requires Python {distribution.requires_python}; running {python_version}"
        )

    api_declared = False
    for value in distribution.requires_dist:
        requirement = Requirement(value)
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        if canonicalize_name(requirement.name) == _API_DIST:
            if requirement.url or not requirement.specifier:
                raise ValueError("Declare a version requirement for lsab-api")
            api_declared = True
            installed = _sdk_version()
        else:
            try:
                installed = version(requirement.name)
            except PackageNotFoundError as exc:
                raise ValueError(f"Missing Python dependency: {requirement.name}") from exc
        if not requirement.specifier.contains(installed, prereleases=True):
            raise ValueError(
                f"Requires {requirement.name}{requirement.specifier}; installed {installed}"
            )
    if not api_declared:
        raise ValueError("Distribution must declare a dependency on lsab-api")


def _sdk_version() -> str:
    try:
        installed = version(_API_DIST)
    except PackageNotFoundError as exc:
        raise ValueError("The standalone lsab-api distribution is not installed") from exc
    if Version(installed) != Version(PLUGIN_API_VERSION) or Version(installed) != Version("1.1.0"):
        raise ValueError(f"The core requires lsab-api==1.1.0; installed {installed}")
    return installed


def _permissions(data: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not isinstance(data, dict) or set(data) - {"required", "optional"}:
        raise ValueError("Expected a permissions table with required and optional arrays")
    values = (data.get("required", []), data.get("optional", []))
    for items in values:
        if not isinstance(items, list) or any(
            not isinstance(item, str) or len(item) > 96 or not _PERMISSION.fullmatch(item)
            for item in items
        ):
            raise ValueError("Invalid permission declaration")
    required, optional = (tuple(items) for items in values)
    if len(set(required + optional)) != len(required + optional):
        raise ValueError("Duplicate permission declaration")
    return required, optional


def _descriptor(
    data: dict, entry: EntryPoint, distribution: DistributionInfo, module_file: Path
) -> PluginDescriptor:
    if set(data) - {"plugin", "permissions", "compatibility"} or not isinstance(
        data.get("plugin"), dict
    ):
        raise ValueError("Expected a [plugin] manifest table")
    config = data["plugin"].copy()
    if entry.name != config.get("id"):
        raise ValueError("Entry point name must match the manifest plugin ID")
    if config.pop("default_enabled", False) is not False:
        raise ValueError("External plugins must default to disabled")
    if config.pop("version", distribution.version) != distribution.version:
        raise ValueError("Manifest version must match the distribution")
    extras = config.pop("extra_actions", [])
    if not isinstance(extras, list) or any(not isinstance(action, str) for action in extras):
        raise ValueError("extra_actions must be an array of strings")
    compatibility = data.get("compatibility", {})
    if not isinstance(compatibility, dict) or set(compatibility) - {"platforms"}:
        raise ValueError("Invalid compatibility table")
    platforms = compatibility.get("platforms", ["linux"])
    if not isinstance(platforms, list) or any(not isinstance(item, str) for item in platforms):
        raise ValueError("platforms must be an array of strings")
    if sys.platform not in platforms:
        raise ValueError(f"Unsupported platform: {sys.platform}")
    required, optional = _permissions(data.get("permissions", {}))
    return PluginDescriptor(
        metadata=PluginMetadata(
            **config, version=distribution.version, permissions=required + optional
        ),
        source="external",
        entry_point=entry.value,
        extra_actions=tuple(extras),
        distribution=distribution,
        required_permissions=required,
        optional_permissions=optional,
        entry=entry,
        module_file=module_file,
    )
