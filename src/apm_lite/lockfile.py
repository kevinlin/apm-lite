"""Lockfile subset (``lockfile_version: '1'``) and upstream-exact content hashes."""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from apm_lite.errors import ApmError

EMPTY_HASH = "sha256:" + hashlib.sha256(b"").hexdigest()
# Upstream ``LockedDependency.to_dict`` order, restricted to the fields apm-lite keeps.
FIELD_ORDER = (
    "repo_url",
    "name",
    "host",
    "port",
    "resolved_commit",
    "resolved_ref",
    "version",
    "depth",
    "resolved_by",
    "package_type",
    "deployed_files",
    "deployed_file_hashes",
    "source",
    "local_path",
    "content_hash",
    "skill_subset",
)
LOCKFILE_VERSION = "1"
_READABLE_VERSIONS = ("1", "2")
_EXCLUDED_DIRS = frozenset({".git", "__pycache__"})
_PIN_MARKER = ".apm-pin"


@dataclass
class LockedDep:
    repo_url: str
    name: str | None = None
    host: str | None = None
    port: int | None = None
    resolved_commit: str | None = None
    resolved_ref: str | None = None
    version: str | None = None
    depth: int = 1
    resolved_by: str | None = None
    package_type: str | None = None
    deployed_files: list[str] = field(default_factory=list)
    deployed_file_hashes: dict[str, str] = field(default_factory=dict)
    source: str | None = None  # "local" for local deps
    local_path: str | None = None
    content_hash: str | None = None
    skill_subset: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        if self.source == "local" and self.local_path:
            return self.local_path
        return f"{self.host}/{self.repo_url}" if self.host else self.repo_url


@dataclass
class Lock:
    deps: dict[str, LockedDep] = field(default_factory=dict)
    local_deployed_files: list[str] = field(default_factory=list)
    local_deployed_file_hashes: dict[str, str] = field(default_factory=dict)

    def all_tracked(self) -> dict[str, tuple[str, str | None]]:
        """Map each deployed path to ``(owner key or ".", recorded hash or None for dirs)``."""
        tracked: dict[str, tuple[str, str | None]] = {}
        for dep in self.deps.values():
            for path in dep.deployed_files:
                tracked[path] = (dep.key, dep.deployed_file_hashes.get(path))
        for path in self.local_deployed_files:
            tracked[path] = (".", self.local_deployed_file_hashes.get(path))
        return tracked


def content_hash(directory: Path) -> str:
    """Port of upstream ``compute_package_hash``: sorted relative paths plus raw bytes."""
    if not directory.is_dir():
        return EMPTY_HASH
    files = []
    for item in directory.rglob("*"):
        if item.is_symlink():
            continue
        rel = item.relative_to(directory)
        if any(part in _EXCLUDED_DIRS for part in rel.parts):
            continue
        if item.is_file():
            if len(rel.parts) == 1 and rel.name == _PIN_MARKER:
                continue
            files.append(rel)
    files.sort(key=lambda p: p.as_posix())
    if not files:
        return EMPTY_HASH
    h = hashlib.sha256()
    for rel in files:
        h.update(rel.as_posix().encode("utf-8"))
        h.update((directory / rel).read_bytes())
    return f"sha256:{h.hexdigest()}"


def file_hash(data: bytes) -> str:
    """Port of upstream ``compute_file_hash``: CRLF folded to LF for UTF-8 text without NUL."""
    canonical = data
    if b"\x00" not in data:
        with contextlib.suppress(UnicodeDecodeError):
            canonical = data.decode("utf-8").replace("\r\n", "\n").encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def dump(lock: Lock, apm_version: str) -> bytes:
    deps = sorted(lock.deps.values(), key=lambda d: (d.depth, d.repo_url, d.key))
    data: dict[str, Any] = {
        "lockfile_version": LOCKFILE_VERSION,
        "apm_version": apm_version,
        "dependencies": [_dep_to_dict(dep) for dep in deps],
    }
    if lock.local_deployed_files:
        data["local_deployed_files"] = sorted(lock.local_deployed_files)
    if lock.local_deployed_file_hashes:
        data["local_deployed_file_hashes"] = dict(sorted(lock.local_deployed_file_hashes.items()))
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False).encode("utf-8")


def _dep_to_dict(dep: LockedDep) -> dict[str, Any]:
    values: dict[str, Any] = {
        **{name: getattr(dep, name) for name in FIELD_ORDER},
        "deployed_files": sorted(dep.deployed_files),
        "deployed_file_hashes": dict(sorted(dep.deployed_file_hashes.items())),
        "skill_subset": sorted(dep.skill_subset),
    }
    return {
        name: values[name]
        for name in FIELD_ORDER
        if values[name] and not (name == "depth" and values[name] == 1)
    }


def read(path: Path) -> Lock | None:
    """Parse ``apm.lock.yaml``; ``None`` when absent, ``ApmError`` when malformed."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise _invalid(path, f"cannot read it ({exc})") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _invalid(path, f"not valid YAML ({exc})") from exc
    if not isinstance(data, dict):
        raise _invalid(path, "the top level must be a mapping")
    version = data.get("lockfile_version", LOCKFILE_VERSION)
    if not isinstance(version, str) or version not in _READABLE_VERSIONS:
        raise _invalid(path, f"unsupported lockfile_version {version!r}")
    raw_deps = data.get("dependencies") or []
    if not isinstance(raw_deps, list):
        raise _invalid(path, "dependencies must be a list")
    lock = Lock(
        local_deployed_files=_str_list(
            data.get("local_deployed_files"), path, "local_deployed_files"
        ),
        local_deployed_file_hashes=_str_map(
            data.get("local_deployed_file_hashes"), path, "local_deployed_file_hashes"
        ),
    )
    for index, raw in enumerate(raw_deps, start=1):
        dep = _dep_from_dict(raw, path, index)
        lock.deps[dep.key] = dep
    return lock


def _invalid(path: Path, problem: str) -> ApmError:
    return ApmError(
        f"invalid lockfile {path}: {problem}",
        hint="delete it and run 'apm install' to regenerate it",
    )


def _dep_from_dict(raw: object, path: Path, index: int) -> LockedDep:
    if not isinstance(raw, dict):
        raise _invalid(path, f"dependency #{index} must be a mapping")
    repo_url = raw.get("repo_url")
    if not isinstance(repo_url, str) or not repo_url.strip():
        raise _invalid(path, f"dependency #{index} has no repo_url")
    depth = raw.get("depth", 1)
    if isinstance(depth, bool) or not isinstance(depth, int):
        raise _invalid(path, f"dependency {repo_url}: depth must be an integer")
    return LockedDep(
        repo_url=repo_url,
        name=_opt_str(raw, "name", path),
        host=_opt_str(raw, "host", path),
        port=_port(raw.get("port")),
        resolved_commit=_opt_str(raw, "resolved_commit", path),
        resolved_ref=_opt_str(raw, "resolved_ref", path),
        version=_opt_str(raw, "version", path),
        depth=depth,
        resolved_by=_opt_str(raw, "resolved_by", path),
        package_type=_opt_str(raw, "package_type", path),
        deployed_files=_str_list(raw.get("deployed_files"), path, "deployed_files"),
        deployed_file_hashes=_str_map(
            raw.get("deployed_file_hashes"), path, "deployed_file_hashes"
        ),
        source=_opt_str(raw, "source", path),
        local_path=_opt_str(raw, "local_path", path),
        content_hash=_opt_str(raw, "content_hash", path),
        skill_subset=_str_list(raw.get("skill_subset"), path, "skill_subset"),
    )


def _opt_str(raw: dict[str, Any], name: str, path: Path) -> str | None:
    value = raw.get(name)
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        raise _invalid(path, f"{name} must be a scalar")
    return str(value)


def _port(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535:
        return value
    return None


def _str_list(value: object, path: Path, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _invalid(path, f"{name} must be a list of strings")
    return list(value)


def _str_map(value: object, path: Path, name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise _invalid(path, f"{name} must map strings to strings")
    return dict(value)
