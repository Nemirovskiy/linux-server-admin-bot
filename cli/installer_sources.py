"""Resolve a trusted plugin source into a versioned, inspectable wheel."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tomllib
from configparser import ConfigParser
from configparser import Error as ConfigParserError
from dataclasses import asdict, dataclass
from email import message_from_bytes
from importlib.metadata import EntryPoint
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

from bot.api import PLUGIN_API_VERSION
from bot.commands import CORE_COMMANDS
from bot.plugins.models import DistributionInfo
from bot.plugins.sources.entrypoints import _check_requirements, _descriptor
from bot.plugins.validator import PluginValidator
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

MAX_WHEEL = 256 * 1024**2


def command(args: list[str], *, input_text: str | None = None) -> str:
    try:
        result = subprocess.run(
            args,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"} if args[0] == "git" else None,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"{Path(args[0]).name} timed out") from exc
    if result.returncode:
        detail = result.stderr.strip().splitlines()
        message = detail[-1][:500] if detail else f"exit {result.returncode}"
        raise ValueError(f"{Path(args[0]).name}: {message}")
    return result.stdout


def pip(python: Path, *args: str) -> str:
    return command(
        [
            str(python),
            "-m",
            "pip",
            "--disable-pip-version-check",
            "--no-input",
            "--no-cache-dir",
            "--require-virtualenv",
            *args,
        ]
    )


def sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


@dataclass(frozen=True)
class Source:
    kind: str
    value: str
    ref: str | None = None
    commit: str | None = None
    editable: bool = False

    @property
    def builds_code(self) -> bool:
        return self.kind in {"git", "directory"}


def resolve(value: str, ref: str | None = None, editable: bool = False) -> Source:
    path = Path(value).expanduser()
    if path.is_dir():
        if ref or not (path / "pyproject.toml").is_file():
            raise ValueError("Use a local directory with pyproject.toml, without --ref")
        return Source("directory", str(path.resolve()), editable=editable)
    if path.is_file():
        if ref or editable or path.suffix != ".whl":
            raise ValueError("A local artifact must be a wheel, without --ref or --editable")
        return Source("wheel", str(path.resolve()))

    url = urlsplit(value.removeprefix("git+"))
    if url.scheme:
        return _resolve_url(value, url, ref, editable)

    if ref or editable:
        raise ValueError("--ref is for Git and --editable is for local directories")
    request = Requirement(value)
    if request.url or request.extras or request.marker:
        raise ValueError("Specify a package name and optional version from the package index")
    return Source("index", str(request))


def _resolve_url(value, url, ref, editable) -> Source:
    if editable or url.username or url.password or url.query or url.fragment:
        raise ValueError("URLs must not contain credentials, query arguments or fragments")
    if url.scheme == "https" and url.path.endswith(".whl") and not ref:
        return Source("release", value)
    github = url.scheme == "https" and url.hostname == "github.com"
    local_git = value.startswith("git+file://") and url.scheme == "file" and not url.netloc
    if not (github or local_git):
        raise ValueError("Use a GitHub repository, HTTPS wheel, or local git+file URL")
    if github and (url.port is not None or not re.fullmatch(r"/[\w.-]+/[\w.-]+/?", url.path)):
        raise ValueError("Expected a GitHub owner/repository URL")
    if local_git and not Path(unquote(url.path)).is_dir():
        raise ValueError("The local Git repository does not exist")
    if ref is None or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", ref) or ".." in ref:
        raise ValueError("Git sources require --ref with a tag, branch or commit")
    return Source("git", url.geturl(), ref)


def newest_git_ref(source: Source, *, pre: bool = False) -> str | None:
    tags = command(["git", "ls-remote", "--tags", "--refs", "--", source.value])
    versions = []
    for line in tags.splitlines():
        _, _, qualified = line.partition("\t")
        ref = qualified.removeprefix("refs/tags/")
        if not qualified.startswith("refs/tags/"):
            continue
        try:
            version = Version(ref.removeprefix("v"))
        except InvalidVersion:
            continue
        if pre or not (version.is_prerelease or version.is_devrelease):
            versions.append((version, ref))
    return max(versions)[1] if versions else source.ref


def fetch(python: Path, source: Source, work: Path, *, pre: bool = False) -> tuple[Path, Source]:
    output = work / "wheels"
    output.mkdir()
    if source.kind == "wheel":
        path = Path(source.value)
        if path.stat().st_size > MAX_WHEEL:
            raise ValueError("Wheel exceeds the size limit")
        shutil.copyfile(path, output / path.name)
    elif source.builds_code:
        path = Path(source.value)
        if source.kind == "git":
            path = work / "git"
            command(["git", "init", "--quiet", str(path)])
            command(
                [
                    "git",
                    "-C",
                    str(path),
                    "fetch",
                    "--quiet",
                    "--depth=1",
                    "--",
                    source.value,
                    source.commit or source.ref or "",
                ]
            )
            commit = command(["git", "-C", str(path), "rev-parse", "FETCH_HEAD"]).strip()
            if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
                raise ValueError("Could not resolve a Git commit")
            command(["git", "-C", str(path), "checkout", "--quiet", "--detach", commit])
            source = Source("git", source.value, source.ref, commit)
        pip(python, "wheel", "--no-deps", "--wheel-dir", str(output), str(path))
    else:
        pip(
            python,
            "download",
            "--only-binary=:all:",
            "--no-deps",
            "--dest",
            str(output),
            *(("--pre",) if pre else ()),
            source.value,
        )
    wheels = list(output.glob("*.whl"))
    if len(wheels) != 1 or wheels[0].stat().st_size > MAX_WHEEL:
        raise ValueError("Expected one wheel within the size limit")
    return wheels[0], source


def inspect_wheel(wheel: Path, source: Source) -> dict:
    try:
        dist_name, version, _, _ = parse_wheel_filename(wheel.name)
        with ZipFile(wheel) as archive:
            names = _wheel_members(archive)
            metadata, entry_file = _metadata_files(archive)
            entries = ConfigParser(interpolation=None)
            entries.optionxform = str
            entries.read_string(_read(archive, entry_file).decode())
            ((plugin_id, entry),) = entries.items("lsab.plugins")
            module, separator, _ = entry.partition(":")
            if not separator or not all(part.isidentifier() for part in module.split(".")):
                raise ValueError("Invalid lsab.plugins entry point")
            module_path = PurePosixPath(*module.split("."))
            if not {str(module_path.with_suffix(".py")), str(module_path / "__init__.py")} & names:
                raise ValueError("Wheel does not contain the advertised plugin module")
            root = PurePosixPath(module.split(".")[0])
            manifest = tomllib.loads(_read(archive, str(root / "plugin.toml")).decode())
        if (
            canonicalize_name(metadata["Name"]) != dist_name
            or Version(metadata["Version"]) != version
        ):
            raise ValueError("Wheel filename and distribution metadata disagree")
        dependencies = tuple(metadata.get_all("Requires-Dist", []))
        info = DistributionInfo(
            str(dist_name), metadata["Version"], metadata.get("Requires-Python"), dependencies
        )
        _check_requirements(info, pending_dependencies=True)
        descriptor = _descriptor(
            manifest, EntryPoint(plugin_id, entry, "lsab.plugins"), info, Path(module)
        )
        PluginValidator(CORE_COMMANDS).validate(descriptor)
        required = descriptor.required_permissions
        optional = descriptor.optional_permissions
        schema = descriptor.data_schema_version
        return {
            "id": plugin_id,
            "distribution": str(dist_name),
            "version": metadata["Version"],
            "source": asdict(source),
            "wheel": wheel.name,
            "sha256": sha256(wheel),
            "required": list(required),
            "optional": list(optional),
            "broker_operations": descriptor.broker_operations,
            "schema": schema,
            "dependencies": metadata.get_all("Requires-Dist", []),
            "api": PLUGIN_API_VERSION,
        }
    except (BadZipFile, ConfigParserError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid plugin wheel {wheel.name}: {exc}") from exc


def _wheel_members(archive: ZipFile) -> set[str]:
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise ValueError("Duplicate archive member")
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ValueError("Unsafe wheel member path")
        parts = path.parts
        if (
            parts
            and parts[0].endswith(".data")
            and len(parts) > 2
            and parts[1] in {"purelib", "platlib"}
        ):
            parts = parts[2:]
        if parts and parts[0].partition(".")[0] in {
            "bot",
            "cli",
            "lsab_api",
            "config",
            "broker",
        }:
            raise ValueError("Wheel overlaps a protected application package")
    if sum(item.file_size for item in archive.infolist()) > MAX_WHEEL:
        raise ValueError("Unpacked wheel exceeds the size limit")
    return set(names)


def _metadata_files(archive: ZipFile):
    (metadata,) = (name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
    entry = str(PurePosixPath(metadata).with_name("entry_points.txt"))
    return message_from_bytes(_read(archive, metadata)), entry


def _read(archive: ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > 65536:
        raise ValueError(f"Metadata is too large: {name}")
    return archive.read(info)
