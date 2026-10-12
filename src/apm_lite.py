"""apm-lite: agent package manager for Claude Code and Antigravity on internal Git.

Single-file build; run with `python apm_lite.py` or install for the `apm` command.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from types import TracebackType
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import click
import frontmatter
import yaml
from ruamel.yaml import YAML, YAMLError
from ruamel.yaml.comments import CommentedMap, CommentedSeq

__version__ = "0.1.0"


# --- errors ------------------------------------------------------------


class ApmError(Exception):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


# --- model ------------------------------------------------------------


@dataclass(frozen=True)
class DepSpec:
    raw: str | dict  # entry as written in apm.yml
    kind: Literal["remote", "local"]
    key: str  # "host/team/pkg" (remote) or local path as written
    url: str | None = None  # clone URL as written, without "#ref"
    host: str | None = None
    port: int | None = None  # only when non-default
    repo_path: str | None = None  # "team/pkg", ".git" stripped, case kept
    ref: str | None = None
    skills: tuple[str, ...] | None = None  # deduped, sorted
    local_path: str | None = None  # as written
    declared_by: str | None = None  # key of declaring package; None = root


@dataclass
class ResolvedPackage:
    spec: DepSpec
    commit: str | None  # None for local and root
    ref_is_immutable: bool
    depth: int  # 1 for root deps; 0 for the root project
    parent_key: str | None
    parent_repo_url: str | None  # lockfile resolved_by
    source_dir: Path
    package_type: str  # hybrid | claude_skill | skill_bundle | apm_package
    name: str
    version: str | None
    content_hash: str | None
    skills_selected: tuple[str, ...] | None = None  # None = all
    deps: list[DepSpec] = field(default_factory=list)

    @property
    def repo_url(self) -> str:  # lockfile repo_url
        return self.spec.repo_path if self.spec.kind == "remote" else f"_local/{self.name}"

    @property
    def owner(self) -> str:  # lockfile key; "." for the root project
        return "." if self.depth == 0 else self.spec.key

    @property
    def marker(self) -> str:  # hook _apm_source value (design §6.4)
        return self.spec.repo_path if self.spec.kind == "remote" else f"_local/{self.name}"

    @property
    def label(self) -> str:  # <pkg> folder for hook scripts
        if self.depth == 0:
            return self.name
        return (self.spec.repo_path or self.spec.local_path).rstrip("/").split("/")[-1]


@dataclass(frozen=True)
class PlannedFile:
    content: bytes
    mode: int  # 0o644 or 0o755
    owner: str


@dataclass(frozen=True)
class HookEntry:
    event: str
    entry: dict  # native entry, no _apm_source
    marker: str
    owner: str


# --- refs ------------------------------------------------------------
# Ref classification, ported from upstream APM's semver and identity predicates.
#
# Upstream sources: `marketplace/semver.py:34`, `deps/registry/semver.py:15-38` and
# `models/dependency/identity.py:34, 269-282`. apm-lite never resolves semver ranges, so a ref that
# upstream would treat as a range (or reject as a malformed one) is an error here, which keeps the
# two tools in agreement about which refs are plain branch or tag names.

_SEMVER_RE = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_RANGE_OPERATORS = (">=", "<=", ">", "<", "^", "~", "=")
_WILDCARD_RE = re.compile(r"^\d+\.\d+\.[xX*]$")
_RANGE_PREFIX_RE = re.compile(r"^(>=|<=|>|<|\^|~|=)")
_FULL_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")


def is_semver_range(spec: str) -> bool:
    parts = spec.strip().split()
    return bool(parts) and all(_is_range_component(p) for p in parts)


def _is_range_component(c: str) -> bool:
    if not c:
        return False
    if _WILDCARD_RE.match(c):
        return True
    for op in _RANGE_OPERATORS:
        if c.startswith(op):
            return _SEMVER_RE.match(c[len(op) :]) is not None
    return _SEMVER_RE.match(c) is not None


def is_full_sha(ref: str | None) -> bool:
    return ref is not None and _FULL_SHA_RE.fullmatch(ref) is not None


def classify_ref(ref: str | None) -> Literal["none", "sha", "literal"]:
    """Return the kind of `ref`, or raise `ApmError` for a ref apm-lite doesn't accept."""
    if ref is None:
        return "none"
    if "@" in ref:
        raise ApmError(
            f"ref {ref!r} contains '@'",
            hint="upstream APM reads '@' in a ref as an alias; use a ref without it",
        )
    if is_full_sha(ref):
        return "sha"
    if is_semver_range(ref) or _RANGE_PREFIX_RE.match(ref.strip()):
        raise ApmError(
            f"ref {ref!r} looks like a semver version or range, which apm-lite doesn't resolve",
            hint="use the tag name, e.g. 'v1.2.3'",
        )
    return "literal"


# --- targets ------------------------------------------------------------
# The two deployment targets (design §6.1).


@dataclass(frozen=True)
class Target:
    name: str  # "claude" | "antigravity"
    root: str  # ".claude" | ".agents"
    hook_file: str
    sidecar: str
    allowed_events: frozenset[str] | None  # None = any event not in skipped_events
    skipped_events: frozenset[str]
    nested_events: frozenset[str] | None  # None = every event uses the nested matcher form

    @property
    def rules_dir(self) -> str:
        return f"{self.root}/rules"

    @property
    def skills_dir(self) -> str:
        return f"{self.root}/skills"

    @property
    def hooks_dir(self) -> str:
        return f"{self.root}/hooks"

    def supports(self, event: str) -> bool:
        if event in self.skipped_events:
            return False
        return self.allowed_events is None or event in self.allowed_events

    def is_nested(self, event: str) -> bool:
        return self.nested_events is None or event in self.nested_events


_ANTIGRAVITY_EVENTS = frozenset(
    {"PreToolUse", "PostToolUse", "PreInvocation", "PostInvocation", "Stop"}
)

TARGETS: dict[str, Target] = {
    "claude": Target(
        name="claude",
        root=".claude",
        hook_file=".claude/settings.json",
        sidecar=".claude/apm-hooks.json",
        allowed_events=None,
        skipped_events=frozenset({"PreInvocation", "PostInvocation"}),
        nested_events=None,
    ),
    "antigravity": Target(
        name="antigravity",
        root=".agents",
        hook_file=".agents/hooks.json",
        sidecar=".agents/apm-hooks.json",
        allowed_events=_ANTIGRAVITY_EVENTS,
        skipped_events=frozenset(),
        nested_events=frozenset({"PreToolUse", "PostToolUse"}),
    ),
}


# --- fs ------------------------------------------------------------
# Filesystem safety: name checks, containment, atomic writes and the undo log (design §7.1, §7.3).

SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
ALLOWED_ROOTS = (".claude", ".agents", "apm_modules")
ALLOWED_ROOT_FILES = ("apm.yml", "apm.lock.yaml", ".gitignore")


def safe_name(name: str) -> str:
    """Return *name* if it is safe to use as one path component."""
    if not name or not SAFE_NAME_RE.match(name) or name in (".", ".."):
        raise ApmError(
            f"unsafe name {name!r}",
            hint="names may only contain letters, digits, '.', '_' and '-'",
        )
    return name


def has_symlink_between(path: Path, root: Path) -> bool:
    """True if *path* or any of its ancestors below *root* is a symlink (sources and destinations)."""
    path, root = path.absolute(), root.absolute()
    return any(
        part.is_symlink()
        for part in (path, *path.parents)
        if part.is_relative_to(root) and part != root
    )


def check_destination(project: Path, rel: str) -> Path:
    """Validate an allowed project-relative destination without following symlinks."""
    relative = Path(rel)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ApmError(f"unsafe destination {rel!r}: expected a relative path without '..'")
    if relative.parts[0] not in ALLOWED_ROOTS and not (
        len(relative.parts) == 1 and relative.name in ALLOWED_ROOT_FILES
    ):
        raise ApmError(f"destination {rel!r} is outside the allowed project paths")

    project = project.absolute()
    destination = project
    for component in relative.parts:
        destination /= component
        if destination.is_symlink():
            raise ApmError(f"destination {rel!r} passes through a symlink: {destination}")
    if not destination.resolve().is_relative_to(project.resolve()):
        raise ApmError(f"destination {rel!r} resolves outside the project")
    return destination


def atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    """Replace a checked destination using a flushed temporary file beside it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class UndoLog:
    """Keep each path's first state and restore it if the transaction fails."""

    def __init__(self, project: Path) -> None:
        self.project = project.absolute()
        # Missing mode means absent; missing bytes with a mode means a directory.
        self._before: dict[str, tuple[bytes | None, int | None]] = {}

    def _record(self, path: Path) -> None:
        rel = path.relative_to(self.project).as_posix()
        if rel in self._before:
            return
        try:
            previous = path.stat()
        except FileNotFoundError:
            self._before[rel] = (None, None)
            return
        if stat.S_ISREG(previous.st_mode):
            data = path.read_bytes()
        elif stat.S_ISDIR(previous.st_mode):
            data = None
        else:
            raise ApmError(f"destination {rel!r} is not a regular file or directory")
        self._before[rel] = (data, stat.S_IMODE(previous.st_mode))

    def write(self, rel: str, data: bytes, mode: int = 0o644) -> None:
        path = check_destination(self.project, rel)
        if path.is_dir():
            raise ApmError(f"destination {rel!r} is a directory, expected a file")
        missing = []
        parent = path.parent
        while parent != self.project and not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for parent in reversed(missing):
            self._record(parent)
        self._record(path)
        atomic_write(path, data, mode)

    def chmod(self, rel: str, mode: int) -> None:
        path = check_destination(self.project, rel)
        self._record(path)
        path.chmod(mode)

    def delete(self, rel: str) -> None:
        path = check_destination(self.project, rel)
        if path.is_dir():
            raise ApmError(f"destination {rel!r} is a directory, expected a file")
        self._record(path)
        path.unlink(missing_ok=True)

    def rmdir_if_empty(self, rel: str) -> None:
        path = check_destination(self.project, rel)
        self._record(path)
        try:
            path.rmdir()
        except FileNotFoundError:
            return
        except OSError as exc:
            if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                raise

    def rollback(self) -> list[str]:
        failures = []
        for rel, (data, mode) in reversed(self._before.items()):
            try:
                path = check_destination(self.project, rel)
                if mode is None:
                    if path.is_dir():
                        path.rmdir()
                    else:
                        path.unlink(missing_ok=True)
                elif data is None:
                    path.mkdir(parents=True, exist_ok=True)
                    path.chmod(mode)
                else:
                    atomic_write(path, data, mode)
            except BaseException:
                # Restore other paths even when this restoration is interrupted.
                failures.append(rel)
        return failures

    def __enter__(self) -> UndoLog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if exc_type is not None:
            failures = self.rollback()
            if failures:
                raise ApmError(f"rollback failed for: {', '.join(failures)}") from exc
        return False


# --- git ------------------------------------------------------------
# Thin wrapper around the `git` CLI: ref lookup and fetch-by-commit (design §4.1, §8).

_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")
_PEELED = "^{}"


class Git:
    def __init__(self, verbose: bool = False, echo: Callable[[str], None] = print) -> None:
        self.verbose = verbose
        self.echo = echo
        self._refs: dict[str, dict[str, str]] = {}

    def run(self, args: list[str], cwd: Path | None = None) -> str:
        """Run `git <args>`, return stdout, and raise `ApmError` with git's stderr on failure."""
        cmd = f"git {' '.join(args)}"
        if self.verbose:
            self.echo(cmd)
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise ApmError(f"{cmd} failed", hint=proc.stderr.strip() or None)
        return proc.stdout

    def ls_remote(self, url: str) -> dict[str, str]:
        """Return `{ref name: commit}` for *url*; annotated tags map to their peeled commit."""
        if url not in self._refs:
            refs: dict[str, str] = {}
            for line in self.run(["ls-remote", "--", url]).splitlines():
                sha, _, name = line.partition("\t")
                if name.endswith(_PEELED):
                    name = name[: -len(_PEELED)]
                if sha and name:
                    refs[name] = sha
            self._refs[url] = refs
        return self._refs[url]

    def fetch(self, url: str, commit: str, dest: Path) -> None:
        """Put the tree of *commit* from *url* into the new directory *dest*, without `.git`."""
        if dest.exists():
            raise ApmError(f"cannot fetch into existing path {dest}")
        try:
            try:
                self.run(["init", "-q", "--", str(dest)])
                self.run(["fetch", "-q", "--depth", "1", "--", url, commit], cwd=dest)
                self.run(["checkout", "-q", "FETCH_HEAD"], cwd=dest)
            except ApmError:
                shutil.rmtree(dest, ignore_errors=True)
                if self.verbose:
                    self.echo(f"fallback: full clone of {url}")
                self.run(["clone", "-q", "--", url, str(dest)])
                self.run(["checkout", "-q", commit, "--"], cwd=dest)
            shutil.rmtree(dest / ".git")
        except BaseException:
            shutil.rmtree(dest, ignore_errors=True)
            raise


def resolve_ref(refs: dict[str, str], ref: str | None, url: str) -> tuple[str, bool]:
    """Map *ref* to `(commit, immutable)` using the `ls_remote` result for *url*."""
    if ref is None:
        if "HEAD" not in refs:
            raise ApmError(f"{url} has no HEAD", hint="the repository may be empty")
        return refs["HEAD"], False
    if _SHA_RE.fullmatch(ref):
        return ref, True
    tag = f"refs/tags/{ref}"
    if tag + _PEELED in refs:
        return refs[tag + _PEELED], True
    if tag in refs:
        return refs[tag], True
    branch = f"refs/heads/{ref}"
    if branch in refs:
        return refs[branch], False
    raise ApmError(f"ref {ref!r} not found in {url}", hint="check the tag or branch name")


# --- lockfile ------------------------------------------------------------
# Lockfile subset (``lockfile_version: '1'``) and upstream-exact content hashes.

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


def dump_lock(lock: Lock, apm_version: str) -> bytes:
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


# --- manifest ------------------------------------------------------------
# Load, validate and edit `apm.yml`; parse dependency entries into `DepSpec`.
#
# The grammar is the subset of upstream APM's that apm-lite supports (design §3). Editing goes
# through ruamel's round-trip loader so comments and unrelated keys survive a save.

# Upstream `is_valid_fqdn` (utils/github_host.py:1009).
FQDN_RE = re.compile(
    r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)+$"
)

SUPPORTED_FORMS = (
    "https://host/group/repo.git#ref, ssh://git@host/group/repo.git#ref, "
    "git@host:group/repo.git#ref, ./local/path"
)

TARGET_NAMES = ("claude", "antigravity")
DEFAULT_TARGETS = ("claude",)

_KNOWN_KEYS = frozenset(
    {"name", "version", "description", "author", "license", "targets", "dependencies"}
)
_OBJECT_KEYS = frozenset({"git", "path", "ref", "skills"})
_LOCAL_PREFIXES = ("./", "../", "/", "~/")
_DEFAULT_PORTS = (22, 443)
_SEGMENT_RE = re.compile(r"[A-Za-z0-9._-]+")
_SSH_USER_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*")
_SSH_HOST_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_SCP_RE = re.compile(
    r"^(?P<user>[A-Za-z0-9_][A-Za-z0-9_.+-]*)@(?P<host>[^:/\[\]@\s]+):(?P<path>.+)$"
)
_UNSAFE_CHARS_RE = re.compile(r"[\s\x00-\x1f\x7f]")
_PLAIN_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _ignore(message: str) -> None:
    return None


@dataclass
class Manifest:
    path: Path
    name: str
    version: str
    targets: tuple[str, ...]  # default ("claude",)
    deps: list[DepSpec]
    doc: Any  # ruamel CommentedMap, for round-trip saves
    edited: bool = False


# --- dependency entries ------------------------------------------------------------------------


def _reject(
    entry: Any,
    reason: str,
    *,
    declared_by: str | None = None,
    hint: str | None = None,
    forms: bool = True,
) -> ApmError:
    shown = entry if isinstance(entry, str) else json.dumps(entry, default=str)
    where = f" (declared by {declared_by})" if declared_by else ""
    suffix = f". Supported forms: {SUPPORTED_FORMS}" if forms else ""
    return ApmError(f"invalid dependency {shown!r}{where}: {reason}{suffix}", hint)


def _folder_name(local_path: str) -> str:
    return local_path.rstrip("/").rsplit("/", 1)[-1]


def _parse_skills(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("'skills' must be a non-empty list of skill names")
    if not all(isinstance(s, str) and s.strip() for s in value):
        raise ValueError("'skills' entries must be non-empty strings")
    return tuple(sorted({s.strip() for s in value}))


def _repo_path(path: str) -> str:
    if "@" in path:
        raise ValueError("'@' isn't allowed in the repository path (upstream reads it as an alias)")
    path = path.removesuffix(".git")
    segments = path.split("/")
    if len(segments) < 2:
        raise ValueError("the repository path needs at least two segments (group/repo)")
    for segment in segments:
        if segment in (".", "..") or not _SEGMENT_RE.fullmatch(segment):
            raise ValueError(f"repository path segment {segment!r} isn't allowed")
    return "/".join(segments)


def _parse_scheme_url(url: str) -> tuple[str, int | None, str]:
    if "?" in url:
        raise ValueError("a query string isn't allowed")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise ValueError("the URL or its port is malformed") from None
    host = parts.hostname or ""
    if parts.scheme == "https":
        if not FQDN_RE.fullmatch(host):
            raise ValueError(f"host {host!r} isn't a valid fully qualified domain name")
    else:
        user = re.match(r"^ssh://([^@/]+)@", url)
        if user and not _SSH_USER_RE.fullmatch(user.group(1)):
            raise ValueError("the ssh user name isn't allowed")
        if not _SSH_HOST_RE.fullmatch(host):
            raise ValueError(f"host {host!r} isn't allowed")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"port {port} is out of range")
    return host, None if port in _DEFAULT_PORTS else port, _repo_path(parts.path.removeprefix("/"))


def _parse_scp_url(match: re.Match[str]) -> tuple[str, int | None, str]:
    host, path = match.group("host"), match.group("path")
    if not _SSH_HOST_RE.fullmatch(host):
        raise ValueError(f"host {host!r} isn't allowed")
    if path.split("/", 1)[0].isdigit():
        raise ValueError(
            "git@host:path can't carry a port; use ssh://git@host:PORT/group/repo.git instead"
        )
    return host.lower(), None, _repo_path(path)


def _parse_remote_url(url: str) -> tuple[str, int | None, str]:
    lowered = url.lower()
    if lowered.startswith("http://"):
        raise ValueError("'http://' isn't supported; use https://")
    if lowered.startswith(("https://", "ssh://")):
        return _parse_scheme_url(url)
    if "://" in url:
        raise ValueError("only https://, ssh:// and git@host:path URLs are supported")
    scp = _SCP_RE.match(url)
    if scp:
        return _parse_scp_url(scp)
    raise ValueError("'owner/repo' shorthand and scheme-free 'host/path' aren't supported")


def _parse_text(
    text: str,
    raw: Any,
    *,
    ref_override: str | None,
    skills: tuple[str, ...] | None,
    allow_local: bool,
    declared_by: str | None,
    remote_only: bool = False,
) -> DepSpec:
    text = text.strip()
    if not text or _UNSAFE_CHARS_RE.search(text):
        raise _reject(raw, "an empty entry or one with whitespace or control characters")
    if text.startswith(_LOCAL_PREFIXES):
        if remote_only:
            raise _reject(raw, "'git:' needs a repository URL; use 'path:' for a local path")
        if not allow_local:
            raise _reject(
                raw,
                "local paths are only allowed in the root apm.yml",
                declared_by=declared_by,
                hint="declare a remote git URL instead",
            )
        if _folder_name(text) in ("", ".", "..", "~"):
            raise _reject(raw, "a local path must end in a named directory")
        return DepSpec(
            raw=raw, kind="local", key=text, skills=skills, local_path=text, declared_by=declared_by
        )
    url, _, fragment = text.partition("#")
    ref = ref_override if ref_override is not None else (fragment.strip() or None)
    try:
        host, port, repo_path = _parse_remote_url(url)
    except ValueError as e:
        raise _reject(raw, str(e), declared_by=declared_by) from None
    try:
        classify_ref(ref)
    except ApmError as e:
        raise _reject(raw, e.message, declared_by=declared_by, hint=e.hint, forms=False) from None
    return DepSpec(
        raw=raw,
        kind="remote",
        key=f"{host}/{repo_path}",
        url=url,
        host=host,
        port=port,
        repo_path=repo_path,
        ref=ref,
        skills=skills,
        declared_by=declared_by,
    )


def _parse_object(entry: dict, *, allow_local: bool, declared_by: str | None) -> DepSpec:
    unknown = sorted(str(k) for k in entry if k not in _OBJECT_KEYS)
    if unknown:
        raise _reject(entry, f"unsupported key(s) {', '.join(unknown)}", declared_by=declared_by)
    if "git" in entry and "path" in entry:
        raise _reject(
            entry,
            "'git:' with 'path:' (a subdirectory of a repository) isn't supported",
            declared_by=declared_by,
        )
    source_key = "git" if "git" in entry else "path"
    if source_key not in entry:
        raise _reject(entry, "an object entry needs 'git:' or 'path:'", declared_by=declared_by)
    source = entry[source_key]
    if not isinstance(source, str):
        raise _reject(entry, f"'{source_key}:' must be a string", declared_by=declared_by)
    try:
        skills = _parse_skills(entry["skills"]) if "skills" in entry else None
    except ValueError as e:
        raise _reject(entry, str(e), declared_by=declared_by) from None
    ref = entry.get("ref")
    if "ref" in entry and (not isinstance(ref, str) or not ref.strip()):
        raise _reject(entry, "'ref' must be a non-empty string", declared_by=declared_by)
    if source_key == "path":
        if ref is not None:
            raise _reject(entry, "'ref:' only applies to 'git:' entries", declared_by=declared_by)
        if not source.strip().startswith(_LOCAL_PREFIXES):
            raise _reject(
                entry, "'path:' must start with ./, ../, / or ~/", declared_by=declared_by
            )
    return _parse_text(
        source,
        entry,
        ref_override=ref.strip() if ref is not None else None,
        skills=skills,
        allow_local=allow_local,
        declared_by=declared_by,
        remote_only=source_key == "git",
    )


def parse_entry(entry: str | dict, *, allow_local: bool, declared_by: str | None = None) -> DepSpec:
    if isinstance(entry, str):
        return _parse_text(
            entry,
            entry,
            ref_override=None,
            skills=None,
            allow_local=allow_local,
            declared_by=declared_by,
        )
    if isinstance(entry, dict):
        return _parse_object(entry, allow_local=allow_local, declared_by=declared_by)
    raise _reject(entry, "an entry must be a string or an object", declared_by=declared_by)


# --- loading -----------------------------------------------------------------------------------


def _yaml() -> YAML:
    yml = YAML()  # round-trip
    yml.preserve_quotes = True
    yml.indent(mapping=2, sequence=4, offset=2)
    yml.width = 4096  # never wrap long URLs
    return yml


def _read_doc(path: Path, *, is_root: bool) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ApmError(f"{path} not found", hint="run 'apm init'" if is_root else None) from None
    except (OSError, UnicodeDecodeError) as e:
        raise ApmError(f"cannot read {path}: {e}") from None
    try:
        doc = _yaml().load(text)
    except YAMLError as e:
        problem = getattr(e, "problem", None) or str(e)
        raise ApmError(f"{path} isn't valid YAML: {problem}") from None
    if not isinstance(doc, dict):
        raise ApmError(f"{path} must contain a mapping with 'name' and 'version'")
    return doc


def _required_str(doc: dict, key: str, path: Path) -> str:
    value = doc.get(key)
    if isinstance(value, str) and value.strip():
        return str(value)
    if key in doc and value is not None and not isinstance(value, str):
        raise ApmError(
            f"{path}: '{key}' must be a string, got {value!r}",
            hint=f'quote it, e.g. {key}: "{value}"',
        )
    raise ApmError(f"{path}: '{key}' is required and must be a non-empty string")


def _parse_targets(doc: dict, path: Path) -> tuple[str, ...]:
    if "target" in doc:
        raise ApmError(f"{path}: 'target' isn't supported", hint="use 'targets: [claude]' instead")
    if "targets" not in doc:
        return DEFAULT_TARGETS
    value = doc["targets"]
    hint = f"use a list of {' and/or '.join(TARGET_NAMES)}, e.g. targets: [claude]"
    if not isinstance(value, list) or not value:
        raise ApmError(f"{path}: 'targets' must be a non-empty list", hint=hint)
    unknown = [t for t in value if t not in TARGET_NAMES]
    if unknown:
        raise ApmError(f"{path}: unsupported target(s) {unknown!r}", hint=hint)
    return tuple(dict.fromkeys(str(t) for t in value))


def _parse_dependencies(
    doc: dict, path: Path, *, is_root: bool, declared_by: str | None
) -> tuple[list[DepSpec], list[str]]:
    section = doc.get("dependencies")
    if section is None:
        return [], []
    if not isinstance(section, dict):
        raise ApmError(f"{path}: 'dependencies' must be a mapping")
    ignored = [f"dependencies.{k}" for k in section if k != "apm"]
    entries = section.get("apm")
    if entries is None:
        return [], ignored
    if not isinstance(entries, list):
        raise ApmError(f"{path}: 'dependencies.apm' must be a list")
    deps = [parse_entry(e, allow_local=is_root, declared_by=declared_by) for e in entries]
    return deps, ignored


def _check_local_folder_names(deps: list[DepSpec]) -> None:
    seen: dict[str, str] = {}
    for dep in deps:
        if dep.kind != "local":
            continue
        name = _folder_name(dep.key)
        if name in seen:
            raise ApmError(
                f"local dependencies {seen[name]!r} and {dep.key!r} share the folder name {name!r}",
                hint=f"rename one of them; both would be recorded as '_local/{name}'",
            )
        seen[name] = dep.key


def load(
    path: Path,
    *,
    is_root: bool,
    declared_by: str | None = None,
    warn: Callable[[str], None] = _ignore,
) -> Manifest:
    doc = _read_doc(path, is_root=is_root)
    name = _required_str(doc, "name", path)
    version = _required_str(doc, "version", path)
    targets = _parse_targets(doc, path)
    deps, ignored = _parse_dependencies(doc, path, is_root=is_root, declared_by=declared_by)
    if is_root:
        _check_local_folder_names(deps)
    for key in doc:
        if key not in _KNOWN_KEYS:
            warn(f"{path}: ignoring unsupported key '{key}' (kept unchanged)")
    for key in ignored:
        warn(f"{path}: ignoring unsupported key '{key}' (kept unchanged)")
    return Manifest(path=path, name=name, version=version, targets=targets, deps=deps, doc=doc)


# --- editing -----------------------------------------------------------------------------------


def _apm_seq(m: Manifest) -> CommentedSeq:
    """Return the `dependencies.apm` sequence, creating the missing containers."""
    section = m.doc.get("dependencies")
    if section is None:
        section = CommentedMap()
        m.doc["dependencies"] = section
    seq = section.get("apm")
    if seq is None:
        seq = CommentedSeq()
        section["apm"] = seq
    return seq


def _object_entry(spec: DepSpec, skills: tuple[str, ...]) -> CommentedMap:
    entry = CommentedMap()
    if spec.kind == "local":
        entry["path"] = spec.local_path
    else:
        entry["git"] = spec.url
        if spec.ref is not None:
            entry["ref"] = spec.ref
    entry["skills"] = CommentedSeq(skills)
    return entry


def add_entry(m: Manifest, text: str, skills: list[str] | None) -> DepSpec:
    """Append `text` as typed (or merge `skills` into the entry that already declares it)."""
    spec = parse_entry(text, allow_local=True)
    try:
        wanted = _parse_skills(skills) if skills else None
    except ValueError as e:
        raise _reject(text, str(e)) from None
    seq = _apm_seq(m)
    for i, existing in enumerate(m.deps):
        if existing.key != spec.key or existing.ref != spec.ref:
            continue
        # An entry without `skills:` already means "all skills", which covers any subset.
        if wanted is None or existing.skills is None:
            return existing
        merged = tuple(sorted({*existing.skills, *wanted}))
        if merged == existing.skills:
            return existing
        if isinstance(seq[i], dict):
            seq[i]["skills"] = CommentedSeq(merged)
        else:
            seq[i] = _object_entry(existing, merged)
        m.deps[i] = parse_entry(seq[i], allow_local=True)
        m.edited = True
        return m.deps[i]
    seq.fa.set_block_style()
    seq.append(text.strip() if wanted is None else _object_entry(spec, wanted))
    m.deps.append(parse_entry(seq[-1], allow_local=True))
    m.edited = True
    return m.deps[-1]


def _written(spec: DepSpec) -> str:
    """The entry as the user wrote it: the string itself, or an object's `git:` / `path:` value."""
    raw = spec.raw
    return raw if isinstance(raw, str) else str(raw.get("git") or raw.get("path"))


def entry_matches(spec: DepSpec, selector: str) -> bool:
    """True if *selector* names *spec*: as written, by `host/path` key, or by local path."""
    selector = selector.strip()
    return selector in (_written(spec).strip(), spec.key, spec.local_path)


def remove_entry(m: Manifest, selector: str) -> DepSpec:
    """Remove every entry matching `selector` (as written, `host/path` key, or local path)."""
    selector = selector.strip()
    matches = [i for i, spec in enumerate(m.deps) if entry_matches(spec, selector)]
    if not matches:
        declared = ", ".join(spec.key for spec in m.deps) or "none"
        raise ApmError(
            f"no declared dependency matches {selector!r}",
            hint=f"declared dependencies: {declared}",
        )
    removed = m.deps[matches[0]]
    seq = m.doc["dependencies"]["apm"]
    for i in reversed(matches):
        del seq[i]
        del m.deps[i]
    if not seq:
        seq.fa.set_flow_style()  # a block-style empty sequence doesn't dump as valid YAML
    m.edited = True
    return removed


def dump_manifest(m: Manifest) -> bytes:
    buf = io.BytesIO()
    _yaml().dump(m.doc, buf)
    return buf.getvalue()


def _is_plain_string(name: str) -> bool:
    if not _PLAIN_NAME_RE.fullmatch(name):
        return False
    # Both YAML 1.1 (PyYAML, upstream's reader) and 1.2 (ruamel) must read it back as a string.
    return yaml.safe_load(name) == name and YAML(typ="safe").load(name) == name


def init_text(name: str) -> str:
    shown = name if _is_plain_string(name) else json.dumps(name, ensure_ascii=False)
    return f'name: {shown}\nversion: "0.1.0"\ntargets:\n  - claude\ndependencies:\n  apm: []\n'


# --- packages ------------------------------------------------------------
# Remote package placement and package shape detection (design §4).


@dataclass(frozen=True)
class PackageInfo:
    package_type: str
    name: str
    version: str | None
    deps: list[DepSpec]


def install_dir(project: Path, repo_path: str) -> Path:
    """Return the checked cache destination, preserving repository path case."""
    for segment in repo_path.split("/"):
        safe_name(segment)
    return check_destination(project, f"apm_modules/{repo_path}")


def check_overlaps(paths: dict[str, str]) -> None:
    """Reject equal or nested cache destinations, naming both packages."""
    for (left_key, left_path), (right_key, right_path) in combinations(paths.items(), 2):
        left, right = left_path.casefold(), right_path.casefold()
        if left == right or left.startswith(right + "/") or right.startswith(left + "/"):
            raise ApmError(
                f"overlapping package destinations: {left_key} ({left_path}) "
                f"and {right_key} ({right_path})"
            )


def place(
    project: Path, spec: DepSpec, commit: str, locked: LockedDep | None, git: Git
) -> tuple[Path, str]:
    """Reuse a verified locked tree, or fetch and verify before replacing the cache."""
    if spec.kind != "remote" or spec.repo_path is None or spec.url is None:
        raise ApmError(f"cannot place {spec.key}: local packages are read in place")
    destination = install_dir(project, spec.repo_path)
    replay = locked is not None and commit == locked.resolved_commit
    try:
        if replay and destination.is_dir():
            cached_hash = content_hash(destination)
            if cached_hash == locked.content_hash:
                return destination, cached_hash

        temporary_rel = f"apm_modules/.tmp/{uuid4().hex}"
        temporary = check_destination(project, temporary_rel)
        temporary.parent.mkdir(parents=True, exist_ok=True)
        try:
            git.fetch(spec.url, commit, temporary)
            temporary = check_destination(project, temporary_rel)
            fetched_hash = content_hash(temporary)
            if replay and fetched_hash != locked.content_hash:
                raise ApmError(
                    f"content hash mismatch for {spec.key} at {commit}: tamper or force-push",
                    hint="verify the package source and lockfile before installing again",
                )
            destination = install_dir(project, spec.repo_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_dir():
                shutil.rmtree(destination)
            elif destination.exists():
                destination.unlink()
            temporary.rename(destination)
            return destination, fetched_hash
        finally:
            if temporary.exists():
                shutil.rmtree(check_destination(project, temporary_rel))
    except OSError as exc:
        raise ApmError(f"cannot place {spec.key}: {exc}") from exc


def inspect(source_dir: Path, spec: DepSpec, *, warn: Callable[[str], None]) -> PackageInfo:
    """Classify in upstream order and validate a requested collection subset."""
    manifest_path = source_dir / "apm.yml"
    manifest = (
        load(manifest_path, is_root=False, declared_by=spec.key, warn=warn)
        if manifest_path.is_file()
        else None
    )
    skills = {path.parent.name for path in source_dir.glob("skills/*/SKILL.md") if path.is_file()}
    if (source_dir / "SKILL.md").is_file():
        package_type = "hybrid" if manifest is not None else "claude_skill"
    elif skills:
        package_type = "skill_bundle"
    elif manifest is not None and (source_dir / ".apm").is_dir():
        package_type = "apm_package"
    else:
        raise ApmError(f"{spec.key}: not an apm package or skill")

    if spec.skills is not None:
        if package_type != "skill_bundle":
            raise ApmError(f"{spec.key}: 'skills' requires a skill collection")
        for name in spec.skills:
            safe_name(name)
            if name not in skills:
                raise ApmError(f"{spec.key}: missing skill {name!r}")

    name = (spec.repo_path or spec.local_path or source_dir.name).rstrip("/").rsplit("/", 1)[-1]
    return PackageInfo(
        package_type=package_type,
        name=manifest.name if manifest is not None else name,
        version=manifest.version if manifest is not None else None,
        deps=manifest.deps if manifest is not None else [],
    )


# --- rules ------------------------------------------------------------
# Rule planner: `.apm/instructions/*.instructions.md` to per-target rule files (design §6.2).

_SUFFIX = ".instructions.md"
_RULE_MODE = 0o644


def _split_top_level(text: str) -> list[str]:
    """Split on commas that are outside `{...}` and `[...]`."""
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def split_apply_to(value: object) -> list[str]:
    """Turn an `applyTo` frontmatter value into a list of globs."""
    if isinstance(value, list):
        parts = [item for item in value if isinstance(item, str)]
    elif isinstance(value, str):
        parts = _split_top_level(value)
    else:
        return []
    stripped = (part.strip() for part in parts)
    return [part for part in stripped if part]


def render_rule(text: str, target: str) -> str:
    """Render one rule source for *target*; the body is kept, other frontmatter is dropped."""
    if target not in ("claude", "antigravity"):
        raise ValueError(f"unknown target {target!r}")
    post = frontmatter.loads(text.replace("\r\n", "\n"))
    body = post.content.lstrip("\n")
    globs = split_apply_to(post.metadata.get("applyTo"))
    if target == "claude":
        if globs:
            quoted = [json.dumps(g, ensure_ascii=True) for g in globs]
            head = "---\npaths:\n" + "".join(f"  - {q}\n" for q in quoted) + "---\n\n"
        else:
            head = ""
    elif globs:
        head = "---\ntrigger: glob\nglobs: " + json.dumps(",".join(globs)) + "\n---\n\n"
    else:
        head = "---\ntrigger: always_on\n---\n\n"
    return (head + body).rstrip("\n") + "\n"


def plan_rules(pkg: ResolvedPackage, target: Target) -> dict[str, PlannedFile]:
    """Rule files *pkg* deploys for *target*: relative path to planned file."""
    if pkg.depth != 0 and pkg.package_type != "apm_package":
        return {}
    src_dir = pkg.source_dir / ".apm" / "instructions"
    if has_symlink_between(src_dir, pkg.source_dir) or not src_dir.is_dir():
        return {}
    planned: dict[str, PlannedFile] = {}
    for path in sorted(src_dir.iterdir()):
        if not path.name.endswith(_SUFFIX) or path.is_symlink() or not path.is_file():
            continue
        name = safe_name(path.name[: -len(_SUFFIX)])
        try:
            text = path.read_bytes().decode("utf-8-sig")
            content = render_rule(text, target.name)
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise ApmError(
                f"cannot read rule {path.name} from {pkg.owner}: {exc}",
                hint="fix the file's frontmatter (it must be valid YAML in UTF-8)",
            ) from exc
        planned[f"{target.rules_dir}/{name}.md"] = PlannedFile(
            content=content.encode("utf-8"), mode=_RULE_MODE, owner=pkg.owner
        )
    return planned


# --- skills ------------------------------------------------------------
# Skill planner: package skill folders to per-target skill files (design §6.3).

_SKIP_NAMES = frozenset({".git", ".apm", "__pycache__"})
_SKILL_NAME_RE = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"
_MAX_NAME_LEN = 64


def to_hyphen_case(name: str) -> str:
    """Convert a package name to hyphen-case for Claude Skills spec.

    Ported from upstream `integration/skill_integrator.py:99`.
    """
    # Extract just the repo name if it's owner/repo format
    if "/" in name:
        name = name.split("/")[-1]

    # Replace underscores and spaces with hyphens
    result = name.replace("_", "-").replace(" ", "-")

    # Insert hyphens before uppercase letters (camelCase to hyphen-case)
    result = re.sub(r"([a-z])([A-Z])", r"\1-\2", result)

    # Convert to lowercase and remove any invalid characters
    result = re.sub(r"[^a-z0-9-]", "", result.lower())

    # Remove consecutive hyphens
    result = re.sub(r"-+", "-", result)

    # Remove leading/trailing hyphens
    result = result.strip("-")

    # Truncate to 64 chars (Claude Skills spec limit)
    return result[:_MAX_NAME_LEN]


def validate_skill_name(name: str) -> bool:
    """True if *name* follows the agentskills.io naming rules."""
    return (
        0 < len(name) <= _MAX_NAME_LEN
        and "--" not in name
        and re.fullmatch(_SKILL_NAME_RE, name) is not None
    )


def _sources(pkg: ResolvedPackage) -> list[tuple[str, Path]]:
    """(skill name, source folder) pairs *pkg* contributes, in a stable order."""
    if pkg.depth != 0 and pkg.package_type in ("claude_skill", "hybrid"):
        return [(safe_name(to_hyphen_case(pkg.label)), pkg.source_dir)]
    if pkg.depth != 0 and pkg.package_type == "skill_bundle":
        base = pkg.source_dir / "skills"
        selected = pkg.skills_selected
    elif pkg.depth == 0 or pkg.package_type == "apm_package":
        base = pkg.source_dir / ".apm" / "skills"
        selected = None
    else:
        return []
    if selected is None:
        if has_symlink_between(base, pkg.source_dir) or not base.is_dir():
            return []
        names = sorted(p.name for p in base.iterdir() if (p / "SKILL.md").is_file())
    else:
        names = list(selected)
    found = []
    for name in names:
        src = base / safe_name(name)
        if not has_symlink_between(src, pkg.source_dir) and src.is_dir():
            found.append((name, src))
    return found


def _copy_plan(src: Path, dest: str, owner: str) -> dict[str, PlannedFile]:
    """Plan a copy of *src* under *dest*: no symlinks, no `.git`, `.apm` or `__pycache__`."""
    planned: dict[str, PlannedFile] = {}
    for dirpath, dirnames, filenames in os.walk(src):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames if d not in _SKIP_NAMES and not (here / d).is_symlink()
        )
        for fname in sorted(filenames):
            path = here / fname
            if fname in _SKIP_NAMES or path.is_symlink() or not path.is_file():
                continue
            mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            rel = path.relative_to(src).as_posix()
            planned[f"{dest}/{rel}"] = PlannedFile(
                content=path.read_bytes(), mode=mode, owner=owner
            )
    return planned


def _description_problem(skill_md: Path) -> str | None:
    """Why *skill_md* lacks a description, or None if it has the key (R12)."""
    try:
        meta = frontmatter.loads(skill_md.read_bytes().decode("utf-8-sig")).metadata
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return "SKILL.md is missing or its frontmatter can't be read"
    if "description" in meta:
        return None
    return "SKILL.md has no `description` in its frontmatter"


def plan_skills(
    pkg: ResolvedPackage, target: Target
) -> tuple[dict[str, PlannedFile], dict[str, str], list[str]]:
    """Skill files *pkg* deploys for *target*: (files, skill roots to owner, warnings)."""
    files: dict[str, PlannedFile] = {}
    roots: dict[str, str] = {}
    warnings: list[str] = []
    who = pkg.name if pkg.depth == 0 else pkg.owner
    for name, src in _sources(pkg):
        if not validate_skill_name(name):
            warnings.append(
                f"skill '{name}' from {who}: not a valid skill name "
                "(lowercase letters, digits and single hyphens, up to 64 characters)"
            )
        problem = _description_problem(src / "SKILL.md")
        if problem:
            warnings.append(f"skill '{name}' from {who}: {problem}")
        dest = f"{target.skills_dir}/{name}"
        roots[dest] = pkg.owner
        files.update(_copy_plan(src, dest, pkg.owner))
    return files, roots, warnings


# --- hooks ------------------------------------------------------------
# Plan hook bundles and merge native entries using ownership sidecars (design §6.4).

_RELATIVE_SCRIPT = r"(?<![.$])(\.[\\/][^\s\"']+)"
_PLUGIN_SCRIPT = r"\$\{(?:CLAUDE_PLUGIN_ROOT|PLUGIN_ROOT)\}[\\/][^\s\"']+"
_SCRIPT_TOKEN = re.compile(
    rf"(?P<quote>[\"']?)(?P<path>{_PLUGIN_SCRIPT}|{_RELATIVE_SCRIPT})(?P=quote)"
)
_SCRIPT_HINT = "move the script, and anything it needs, into .apm/hooks/"
# A `../x` token would be left unrewritten and resolve against the harness cwd at runtime.
_PARENT_SCRIPT = re.compile(r"(?:^|[\s\"'=])\.\.[\\/]")
# Upstream routes `hooks-<token>.json` to that harness only (integration/hook_file_routing.py),
# so files for harnesses apm-lite doesn't support are skipped rather than deployed everywhere.
_TARGET_TOKENS = (
    "claude",
    "antigravity",
    "copilot",
    "vscode",
    "cursor",
    "codex",
    "gemini",
    "windsurf",
    "kiro",
)
_NODE_CONFIG_FILES = frozenset({"package.json", "package-lock.json"})


def read_json(path: Path) -> dict:
    """Read a JSON object, treating only a missing file as an empty object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ApmError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ApmError(f"invalid JSON {path}: expected an object")
    return data


def _script_relative_path(
    token: str, bases: tuple[Path, ...], hooks_root: Path, package_root: Path
) -> str:
    for base in bases:
        path = base / token
        if not path.exists() and not path.is_symlink():
            continue
        try:
            resolved = path.resolve()
            rel = resolved.relative_to(hooks_root.resolve()).as_posix()
        except (OSError, RuntimeError, ValueError) as exc:
            raise ApmError(f"hook script {path} is outside .apm/hooks/", _SCRIPT_HINT) from exc
        if has_symlink_between(path, package_root) or not path.is_file():
            raise ApmError(f"hook script {path} must be a regular non-symlink file", _SCRIPT_HINT)
        if any(char in rel for char in '$`"'):
            raise ApmError(f"unsafe hook script path {rel!r}", _SCRIPT_HINT)
        return rel
    raise ApmError(f"hook script {token!r} does not exist in .apm/hooks/", _SCRIPT_HINT)


def rewrite_command(
    command: str,
    *,
    target: str,
    label: str,
    hook_file_dir: Path,
    hooks_root: Path,
    package_root: Path,
) -> str:
    """Rewrite supported script tokens to their copies, preserving shell quoting."""
    label = safe_name(label)
    if _PARENT_SCRIPT.search(command):
        raise ApmError(
            f"hook command {command!r} references a script outside .apm/hooks/ ('../')",
            _SCRIPT_HINT,
        )

    def replace(match: re.Match[str]) -> str:
        token = match["path"]
        if token.startswith("${"):
            relative = token.split("}", 1)[1][1:]
            bases = (package_root,)
        else:
            relative = token[2:]
            bases = (hook_file_dir, package_root)
        rel = _script_relative_path(relative.replace("\\", "/"), bases, hooks_root, package_root)
        if target == "claude":
            return f'"${{CLAUDE_PROJECT_DIR}}/.claude/hooks/{label}/{rel}"'
        quote = match["quote"]
        return f"{quote}hooks/{label}/{rel}{quote}"

    return _SCRIPT_TOKEN.sub(replace, command)


def _event_map(value: object, source: str) -> dict[str, list[dict]]:
    if not isinstance(value, dict):
        raise ApmError(f"invalid hooks in {source}: expected an event object")
    for event, entries in value.items():
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            raise ApmError(f"invalid hook event {event!r} in {source}: expected a list of objects")
    return value


def _handlers(
    raw: dict,
    source: Path,
    pkg: ResolvedPackage,
    target: Target,
    hooks_root: Path,
    warnings: list[str],
) -> list[dict]:
    handlers = raw.get("hooks", [raw])
    if not isinstance(handlers, list) or any(not isinstance(h, dict) for h in handlers):
        raise ApmError(f"invalid handlers in {source}: expected a list of objects")
    rendered = []
    for handler in handlers:
        if "command" not in handler:
            warnings.append(f"hook in {source} has no command; skipped for {target.name}")
            continue
        if handler.get("type", "command") != "command" or not isinstance(handler["command"], str):
            raise ApmError(f"invalid command handler in {source}")
        entry = {
            "type": "command",
            "command": rewrite_command(
                handler["command"],
                target=target.name,
                label=pkg.label,
                hook_file_dir=source.parent,
                hooks_root=hooks_root,
                package_root=pkg.source_dir,
            ),
        }
        if "timeout" in handler:
            if type(handler["timeout"]) is not int:
                raise ApmError(f"invalid timeout in {source}: expected an integer")
            entry["timeout"] = handler["timeout"]
        rendered.append(entry)
    return rendered


def plan_hooks(
    pkg: ResolvedPackage,
    target: Target,
) -> tuple[list[HookEntry], dict[str, PlannedFile], list[str]]:
    """Select hook sources, render native entries and plan their self-contained bundle."""
    if pkg.depth != 0 and pkg.package_type != "apm_package":
        return [], {}, []  # hooks, like rules, come only from APM packages and the project (R8)
    hooks_root = pkg.source_dir / ".apm" / "hooks"
    if not hooks_root.is_dir() or has_symlink_between(hooks_root, pkg.source_dir):
        return [], {}, []
    label = safe_name(pkg.label)
    routed = {hooks_root / f"hooks-{token}.json" for token in _TARGET_TOKENS}
    override = hooks_root / f"hooks-{target.name}.json"
    generic = [
        p
        for p in sorted(hooks_root.glob("*.json"))
        if p not in routed and p.name not in _NODE_CONFIG_FILES
    ]
    sources = [override] if override.exists() else generic
    hook_definitions = {*generic, *routed}  # never copied into the bundle
    entries: list[HookEntry] = []
    warnings: list[str] = []
    for source in sources:
        if source.is_symlink():
            raise ApmError(f"hook source {source} must not be a symlink")
        doc = read_json(source)
        for event, declarations in _event_map(doc.get("hooks", doc), str(source)).items():
            if not target.supports(event):
                warnings.append(
                    f"hook event {event!r} in {source} is unsupported by {target.name}; skipped"
                )
                continue
            for raw in declarations:
                matcher = raw.get("matcher")
                if matcher is not None and not isinstance(matcher, str):
                    raise ApmError(f"invalid matcher in {source}: expected a string")
                handlers = _handlers(raw, source, pkg, target, hooks_root, warnings)
                if not handlers:
                    continue
                native = (
                    [{"matcher": matcher or "*", "hooks": handlers}]
                    if target.is_nested(event)
                    else handlers
                )
                entries.extend(HookEntry(event, entry, pkg.marker, pkg.owner) for entry in native)

    files: dict[str, PlannedFile] = {}
    for dirpath, dirnames, filenames in os.walk(hooks_root):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if not (here / d).is_symlink())
        for name in sorted(filenames):
            path = here / name
            if path in hook_definitions or path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(hooks_root).as_posix()
            files[f"{target.hooks_dir}/{label}/{rel}"] = PlannedFile(
                path.read_bytes(), stat.S_IMODE(path.stat().st_mode), pkg.owner
            )
    return entries, files, warnings


def _owned_events(sidecar: dict, target: Target) -> dict[str, list[dict]]:
    owned = {}
    for event, entries in _event_map(sidecar, target.sidecar).items():
        if any(not isinstance(e.get("_apm_source"), str) for e in entries):
            raise ApmError(f"invalid ownership marker in {target.sidecar}")
        owned[event] = [
            {key: value for key, value in entry.items() if key != "_apm_source"}
            for entry in entries
        ]
    return owned


def merge(
    native: dict,
    sidecar: dict,
    planned: list[HookEntry],
    target: Target,
    configured: bool,
) -> tuple[dict, dict | None]:
    """Strip previously owned entries and append the configured plan without mutating inputs."""
    owned = _owned_events(sidecar, target)
    result = dict(native)
    key = "hooks" if target.name == "claude" else "apm"
    events = _event_map(native.get(key, {}), target.hook_file)
    if target.name == "antigravity":
        if key in native and not sidecar:
            raise ApmError(
                f"unowned apm container in {target.hook_file}",
                "move your hooks under another hook name",
            )
        if key in native and events != owned:
            raise ApmError(
                f"edited apm container in {target.hook_file}",
                "move your edits under another hook name",
            )
        remaining: dict[str, list[dict]] = {}
    else:
        remaining = {}
        for event, entries in events.items():
            counts = Counter(json.dumps(e, sort_keys=True) for e in owned.get(event, []))
            kept = []
            for entry in entries:
                identity = json.dumps(entry, sort_keys=True)
                if counts[identity]:
                    counts[identity] -= 1
                else:
                    kept.append(entry)
            remaining[event] = kept

    new_sidecar: dict[str, list[dict]] = {}
    if configured:
        for hook in planned:
            remaining.setdefault(hook.event, []).append(hook.entry)
            new_sidecar.setdefault(hook.event, []).append(dict(hook.entry, _apm_source=hook.marker))
    remaining = {event: entries for event, entries in remaining.items() if entries}
    if remaining:
        result[key] = remaining
    else:
        result.pop(key, None)
    return result, new_sidecar or None


# --- resolver ------------------------------------------------------------
# Breadth-first dependency resolution, lock pins and frozen drift checks.

Mode = Literal["install", "update", "frozen"]


@dataclass
class _Request:
    spec: DepSpec
    chain: tuple[str, ...]
    commit: str
    immutable: bool | None  # A pinned literal needs no lookup until a conflict is possible.
    refs: dict[str, str] | None = None

    @property
    def label(self) -> str:
        return " -> ".join(("root", *self.chain, self.spec.key)) + f"#{self.spec.ref or 'HEAD'}"


def _frozen_error(reason: str) -> ApmError:
    return ApmError(f"--frozen: {reason}", hint="run 'apm install' to update apm.lock.yaml")


def _check_frozen_request(spec: DepSpec, locked: LockedDep | None, *, winner: bool) -> None:
    if locked is None:
        raise _frozen_error(f"remote request {spec.key} has no lockfile entry")
    if not is_full_sha(locked.resolved_commit):
        raise _frozen_error(f"remote request {spec.key} has no full 40-hex commit")
    if is_full_sha(spec.ref) and spec.ref.lower() != locked.resolved_commit.lower():
        raise _frozen_error(f"SHA request for {spec.key} differs from the locked commit")
    if winner and spec.ref != locked.resolved_ref:
        raise _frozen_error(
            f"winning request's declared ref for {spec.key} differs from resolved_ref"
        )


def _remote_request(
    spec: DepSpec,
    chain: tuple[str, ...],
    locked: LockedDep | None,
    *,
    updating: bool,
    frozen: bool,
    git: Git,
) -> _Request:
    if is_full_sha(spec.ref):
        return _Request(spec, chain, spec.ref, True)
    pinned = (
        locked is not None
        and is_full_sha(locked.resolved_commit)
        and not updating
        and (spec.ref == locked.resolved_ref or (frozen and spec.ref is None))
    )
    if pinned:
        return _Request(spec, chain, locked.resolved_commit, None if spec.ref else False)
    refs = git.ls_remote(spec.url)
    commit, immutable = resolve_ref(refs, spec.ref, spec.url)
    return _Request(spec, chain, commit, immutable, refs)


def _check_immutable(
    request: _Request, previous: list[_Request], git: Git, *, frozen: bool
) -> None:
    for other in previous:
        if (
            request.commit.lower() == other.commit.lower()
            or request.immutable is False
            or other.immutable is False
        ):
            continue
        for candidate in (request, other):
            if candidate.immutable is None:
                refs = request.refs if request.refs is not None else other.refs
                if refs is None:
                    refs = git.ls_remote(candidate.spec.url)
                candidate.refs = refs
                tag = f"refs/tags/{candidate.spec.ref}"
                candidate.immutable = (
                    tag in refs
                    or tag + "^{}" in refs
                    or f"refs/heads/{candidate.spec.ref}" not in refs
                )
        if request.immutable and other.immutable:
            message = (
                f"conflicting immutable refs for {request.spec.key}: "
                f"{other.label} ({other.commit}) and {request.label} ({request.commit})"
            )
            raise _frozen_error(message) if frozen else ApmError(message)


def _local_sources(project: Path, deps: list[DepSpec]) -> dict[str, Path]:
    sources: dict[str, Path] = {}
    names: dict[str, str] = {}
    for spec in deps:
        if spec.kind != "local":
            continue
        path = Path(spec.local_path).expanduser()
        source = (project / path).resolve()
        for key, previous in sources.items():
            if source == previous:
                raise ApmError(
                    f"local dependencies {key!r} and {spec.key!r} resolve to the same directory"
                )
        if path.name in names:
            raise ApmError(
                f"local dependencies {names[path.name]!r} and {spec.key!r} "
                f"share the folder name {path.name!r}"
            )
        sources[spec.key] = source
        names[path.name] = spec.key
    return sources


def _check_paths(paths: dict[str, str], deps: list[DepSpec]) -> None:
    for spec in deps:
        if spec.kind == "remote":
            paths.setdefault(spec.key, spec.repo_path)
    check_overlaps(paths)


def _check_frozen_graph(lock: Lock, seen: dict[str, ResolvedPackage]) -> None:
    for key, locked in lock.deps.items():
        if locked.source != "local" and key not in seen:
            raise _frozen_error(f"remote lockfile entry {key} is no longer reached")
    for key, pkg in seen.items():
        locked = lock.deps.get(key)
        subset = tuple(sorted(set(locked.skill_subset))) if locked and locked.skill_subset else None
        if pkg.skills_selected != subset:
            raise _frozen_error(f"skill subset for {key} differs from the locked skill_subset")


def resolve(
    project: Path,
    manifest: Manifest,
    lock: Lock | None,
    *,
    mode: Mode,
    update_keys: set[str] | None,
    git: Git,
    warn: Callable[[str], None],
) -> list[ResolvedPackage]:
    """Resolve root dependencies and their children, preserving breadth-first winners."""
    if mode == "frozen" and lock is None:
        raise _frozen_error("the lockfile is missing")
    local_sources = _local_sources(project, manifest.deps)
    paths: dict[str, str] = {}
    _check_paths(paths, manifest.deps)
    queue = deque((spec, 1, ()) for spec in manifest.deps)
    seen: dict[str, ResolvedPackage] = {}
    requests: dict[str, list[_Request]] = {}
    while queue:
        spec, depth, chain = queue.popleft()
        if depth > 50:
            raise ApmError(f"dependency depth exceeds 50: {' -> '.join((*chain, spec.key))}")
        locked = lock.deps.get(spec.key) if lock else None
        request = None
        if spec.kind == "remote":
            if mode == "frozen":
                _check_frozen_request(spec, locked, winner=spec.key not in seen)
            updating = mode == "update" and (update_keys is None or spec.key in update_keys)
            request = _remote_request(
                spec, chain, locked, updating=updating, frozen=mode == "frozen", git=git
            )
            previous = requests.setdefault(spec.key, [])
            _check_immutable(request, previous, git, frozen=mode == "frozen")
            previous.append(request)
        if spec.key in seen:
            pkg = seen[spec.key]
            if spec.skills is not None:
                inspect(pkg.source_dir, spec, warn=warn)
            if pkg.skills_selected is None or spec.skills is None:
                pkg.skills_selected = None
            else:
                pkg.skills_selected = tuple(sorted(set(pkg.skills_selected) | set(spec.skills)))
            if spec.ref != pkg.spec.ref:
                warn(f"{spec.key}: keeping {requests[spec.key][0].label}; ignoring {request.label}")
            continue
        if spec.kind == "local":
            source, digest = local_sources[spec.key], None
        else:
            source, digest = place(project, spec, request.commit, locked, git)
        info = inspect(source, spec, warn=warn)
        parent_key = chain[-1] if chain else None
        pkg = ResolvedPackage(
            spec=spec,
            commit=request.commit if request else None,
            ref_is_immutable=request.immutable is not False if request else False,
            depth=depth,
            parent_key=parent_key,
            parent_repo_url=seen[parent_key].repo_url if parent_key else None,
            source_dir=source,
            package_type=info.package_type,
            name=info.name,
            version=info.version,
            content_hash=digest,
            skills_selected=spec.skills,
            deps=info.deps,
        )
        seen[spec.key] = pkg
        _check_paths(paths, pkg.deps)
        queue.extend((child, depth + 1, (*chain, spec.key)) for child in pkg.deps)
    if mode == "frozen":
        _check_frozen_graph(lock, seen)
    return list(seen.values())


# --- reconcile ------------------------------------------------------------
# Deploy a complete plan, preserve edits and commit metadata inside an undo log.


@dataclass
class Result:
    lock: Lock
    written: list[str]
    deleted: list[str]
    kept_edited: list[str]
    warnings: list[str]


def root_package(project: Path, manifest: Manifest) -> ResolvedPackage:
    return ResolvedPackage(
        spec=DepSpec(raw=".", kind="local", key=".", local_path="."),
        commit=None,
        ref_is_immutable=False,
        depth=0,
        parent_key=None,
        parent_repo_url=None,
        source_dir=project,
        package_type="apm_package",
        name=manifest.name,
        version=manifest.version,
        content_hash=None,
    )


def classify(abs_path: Path, planned: PlannedFile, recorded: str | None) -> str:
    if not abs_path.exists():
        return "write"
    if not abs_path.is_file():
        raise ApmError(f"destination {abs_path} is not a regular file")
    current = abs_path.read_bytes()
    if current == planned.content:
        executable = bool(abs_path.stat().st_mode & 0o111)
        return "noop" if executable == bool(planned.mode & 0o111) else "chmod"
    if recorded is None:
        return "conflict"
    return "write" if file_hash(current) == recorded else "keep-edited"


def _claim(
    claims: dict[str, tuple[str, str, bool]], rel: str, owner: str, *, directory: bool = False
) -> None:
    folded = rel.casefold()
    for previous, previous_owner, previous_dir in claims.values():
        other = previous.casefold()
        overlap = folded == other
        if folded.startswith(other + "/"):
            overlap = not previous_dir or owner != previous_owner
        elif other.startswith(folded + "/"):
            overlap = not directory or owner != previous_owner
        if overlap:
            raise ApmError(
                f"overlapping deployment paths: {previous_owner} ({previous}) and {owner} ({rel})"
            )
    claims[folded] = (rel, owner, directory)


def _new_lock(
    packages: list[ResolvedPackage],
    files: dict[str, PlannedFile],
    roots: dict[str, str],
    hashes: dict[str, str],
) -> Lock:
    lock = Lock()
    owners = {rel: planned.owner for rel, planned in files.items()} | roots
    for pkg in packages:
        paths = sorted(rel for rel, owner in owners.items() if owner == pkg.owner)
        recorded = {rel: hashes[rel] for rel in paths if rel in hashes}
        if pkg.depth == 0:
            lock.local_deployed_files = paths
            lock.local_deployed_file_hashes = recorded
            continue
        lock.deps[pkg.owner] = LockedDep(
            repo_url=pkg.repo_url,
            name=pkg.name,
            host=pkg.spec.host,
            port=pkg.spec.port,
            resolved_commit=pkg.commit,
            resolved_ref=pkg.spec.ref,
            version=pkg.version,
            depth=pkg.depth,
            resolved_by=pkg.parent_repo_url,
            package_type=pkg.package_type,
            deployed_files=paths,
            deployed_file_hashes=recorded,
            source="local" if pkg.spec.kind == "local" else None,
            local_path=pkg.spec.local_path,
            content_hash=pkg.content_hash,
            skill_subset=sorted(pkg.skills_selected) if pkg.skills_selected is not None else [],
        )
    return lock


def _cleanup_plan(
    project: Path, tracked: dict[str, tuple[str, str | None]], planned: set[str]
) -> tuple[list[str], list[str], list[str]]:
    paths = {rel: check_destination(project, rel) for rel in tracked if rel not in planned}
    roots = {rel for rel, path in paths.items() if path.is_dir()}
    directories = set(roots)
    deleted, kept = [], []
    for rel, path in paths.items():
        recorded = tracked[rel][1]
        if path.is_dir():
            if recorded is not None:
                raise ApmError(f"destination {rel} is a directory, expected a file")
        elif path.exists():
            if not path.is_file():
                raise ApmError(f"destination {rel} is not a regular file")
            if recorded is not None and file_hash(path.read_bytes()) == recorded:
                deleted.append(rel)
            else:
                kept.append(rel)
        parents = list(Path(rel).parents)[:-1]
        for index, parent in enumerate(parents):
            if parent.as_posix() in roots:
                directories.update(p.as_posix() for p in parents[:index])
                break
    for rel in directories:
        check_destination(project, rel)
    return sorted(deleted), sorted(directories, key=lambda p: (p.count("/"), p), reverse=True), kept


def _cache_paths(project: Path, old: Lock | None, packages: list[ResolvedPackage]) -> list[str]:
    active = {
        f"apm_modules/{pkg.spec.repo_path}".casefold()
        for pkg in packages
        if pkg.spec.kind == "remote"
    }
    keys = {pkg.owner for pkg in packages}
    stale = []
    for key, dep in old.deps.items() if old else []:
        if dep.source == "local" or key in keys:
            continue
        rel = f"apm_modules/{dep.repo_url}"
        check_destination(project, rel)
        folded = rel.casefold()
        if not any(
            folded == live or folded.startswith(live + "/") or live.startswith(folded + "/")
            for live in active
        ):
            stale.append(rel)
    check_destination(project, "apm_modules/.tmp")
    return stale


def _clean_cache(project: Path, stale: list[str]) -> None:
    for rel in [*stale, "apm_modules/.tmp"]:
        path = check_destination(project, rel)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    root = check_destination(project, "apm_modules")
    if not root.exists():
        return
    for here, _, _ in os.walk(root, topdown=False, followlinks=False):
        path = check_destination(project, Path(here).relative_to(project).as_posix())
        try:
            path.rmdir()
        except OSError as exc:
            if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                raise


def _write_changed(log: UndoLog, rel: str, data: bytes, written: list[str]) -> None:
    path = check_destination(log.project, rel)
    if path.exists() and path.read_bytes() == data:
        return
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    log.write(rel, data, mode)
    written.append(rel)


def reconcile(
    project: Path,
    manifest: Manifest,
    *,
    mode: Mode = "install",
    update_keys: set[str] | None = None,
    git: Git,
    warn: Callable[[str], None],
) -> Result:
    project = project.absolute()
    warnings: list[str] = []

    def notice(message: str) -> None:
        warnings.append(message)
        warn(message)

    old = read(check_destination(project, "apm.lock.yaml"))
    try:
        packages = resolve(
            project, manifest, old, mode=mode, update_keys=update_keys, git=git, warn=notice
        )
    except BaseException:
        _clean_cache(project, [])
        raise
    packages.append(root_package(project, manifest))
    files: dict[str, PlannedFile] = {}
    roots: dict[str, str] = {}
    claims: dict[str, tuple[str, str, bool]] = {}
    hooks: dict[str, list[HookEntry]] = {name: [] for name in TARGETS}
    for pkg in packages:
        for name in manifest.targets:
            target = TARGETS[name]
            skill_files, skill_roots, skill_warnings = plan_skills(pkg, target)
            entries, scripts, hook_warnings = plan_hooks(pkg, target)
            for message in [*skill_warnings, *hook_warnings]:
                notice(message)
            for rel, owner in skill_roots.items():
                _claim(claims, rel, owner, directory=True)
                roots[rel] = owner
            for batch in (plan_rules(pkg, target), skill_files, scripts):
                for rel, planned_file in batch.items():
                    _claim(claims, rel, planned_file.owner)
                    files[rel] = planned_file
            hooks[name].extend(entries)

    hook_files: dict[str, bytes] = {}
    hook_deletes: list[str] = []
    for name, target in TARGETS.items():
        native_path = check_destination(project, target.hook_file)
        sidecar_path = check_destination(project, target.sidecar)
        old_native, old_sidecar = read_json(native_path), read_json(sidecar_path)
        native, sidecar = merge(
            old_native, old_sidecar, hooks[name], target, configured=name in manifest.targets
        )
        # Rewrite a hook file only when its content changes, so a user's own formatting survives.
        if native != old_native:
            hook_files[target.hook_file] = (json.dumps(native, indent=2) + "\n").encode()
        if sidecar is not None and sidecar != old_sidecar:
            hook_files[target.sidecar] = (json.dumps(sidecar, indent=2) + "\n").encode()
        elif sidecar is None and sidecar_path.exists():
            hook_deletes.append(target.sidecar)

    tracked = old.all_tracked() if old else {}
    actions: dict[str, str] = {}
    hashes: dict[str, str] = {}
    kept: list[str] = []
    for rel in roots:
        path = check_destination(project, rel)
        if path.exists() and not path.is_dir():
            raise ApmError(f"destination {rel} is not a directory")
    for rel, planned_file in files.items():
        recorded = tracked.get(rel, ("", None))[1]
        action = classify(check_destination(project, rel), planned_file, recorded)
        if action == "conflict":
            raise ApmError(
                f"{rel} is not managed by apm and differs from the planned file",
                hint="move or delete it before installing",
            )
        actions[rel] = action
        hashes[rel] = recorded if action == "keep-edited" else file_hash(planned_file.content)
        if action == "keep-edited":
            kept.append(rel)
    obsolete, directories, cleanup_kept = _cleanup_plan(project, tracked, set(files) | set(roots))
    kept.extend(cleanup_kept)
    for rel in sorted(kept):
        notice(f"kept {rel}: edited since install (delete it to get the package version)")
    stale = _cache_paths(project, old, packages)
    ignore_path = check_destination(project, ".gitignore")
    ignore = ignore_path.read_bytes() if ignore_path.exists() else b""
    if b"apm_modules/" not in ignore.splitlines():
        ignore += b"\n# APM dependencies\napm_modules/\n"
    check_destination(project, "apm.yml")
    manifest_bytes = dump_manifest(manifest) if manifest.edited else None
    lock = _new_lock(packages, files, roots, hashes)
    lock_bytes = dump_lock(lock, __version__)
    written: list[str] = []
    deleted: list[str] = []
    with UndoLog(project) as log:
        for rel, planned_file in files.items():
            if actions[rel] == "write":
                log.write(rel, planned_file.content, planned_file.mode)
                written.append(rel)
            elif actions[rel] == "chmod":
                log.chmod(rel, planned_file.mode)
                written.append(rel)
        for rel, content in hook_files.items():
            _write_changed(log, rel, content, written)
        for rel in [*hook_deletes, *obsolete]:
            log.delete(rel)
            deleted.append(rel)
        for rel in directories:
            path = project / rel
            existed = path.exists()
            log.rmdir_if_empty(rel)
            if existed and not path.exists():
                deleted.append(rel)
        _clean_cache(project, stale)
        _write_changed(log, ".gitignore", ignore, written)
        if manifest_bytes is not None:
            _write_changed(log, "apm.yml", manifest_bytes, written)
        _write_changed(log, "apm.lock.yaml", lock_bytes, written)
    return Result(lock, sorted(written), sorted(deleted), sorted(kept), warnings)


# --- cli ------------------------------------------------------------
# The `apm` command line (design §8).

MANIFEST = "apm.yml"
LOCKFILE = "apm.lock.yaml"


class _Group(click.Group):
    """Turn `ApmError` into `error:` / `hint:` lines on stderr and exit status 1."""

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except ApmError as exc:
            click.echo(f"error: {exc.message}", err=True)
            if exc.hint:
                click.echo(f"hint: {exc.hint}", err=True)
            ctx.exit(1)


def _warn(message: str) -> None:
    click.echo(f"warning: {message}", err=True)


def _git(ctx: click.Context) -> Git:
    return Git(verbose=ctx.obj["verbose"], echo=lambda line: click.echo(line, err=True))


def _load(project: Path) -> Manifest:
    return load(project / MANIFEST, is_root=True, warn=_warn)


def _report(ctx: click.Context, result: Result) -> None:
    # reconcile() already warned (through _warn) about kept edits and skipped content.
    if ctx.obj["verbose"]:
        for path in result.written:
            click.echo(f"wrote {path}")
        for path in result.deleted:
            click.echo(f"deleted {path}")
    click.echo(
        f"{len(result.lock.deps)} package(s) locked; "
        f"{len(result.written)} file(s) written, {len(result.deleted)} deleted"
    )


def _select_key(m: Manifest, project: Path, selector: str) -> str:
    """Map a user selector to a dependency key: a declared entry, or any locked package."""
    for spec in m.deps:
        if entry_matches(spec, selector):
            return spec.key
    lock = read(project / LOCKFILE)
    if lock and selector in lock.deps:
        return selector
    raise ApmError(
        f"no dependency matches {selector!r}",
        hint="use the entry as written in apm.yml, or its host/path key from 'apm deps list'",
    )


@click.group(cls=_Group)
@click.version_option(__version__, prog_name="apm-lite")
@click.option("-v", "--verbose", is_flag=True, help="Show git commands and file changes.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Install rules, skills and hooks from internal Git into Claude Code and Antigravity."""
    ctx.obj = {"verbose": verbose}


@cli.command()
def init() -> None:
    """Create a starter apm.yml in the current directory."""
    project = Path.cwd()
    path = check_destination(project, MANIFEST)
    if path.exists():
        raise ApmError(f"{MANIFEST} already exists")
    atomic_write(path, init_text(project.name).encode())
    click.echo(f"created {MANIFEST}")


@cli.command()
@click.argument("dep", required=False)
@click.option("--skill", "skills", multiple=True, help="Install only this skill (repeatable).")
@click.option("--frozen", is_flag=True, help="Install exactly apm.lock.yaml; fail on drift.")
@click.pass_context
def install(ctx: click.Context, dep: str | None, skills: tuple[str, ...], frozen: bool) -> None:
    """Install every dependency, or add DEP to apm.yml and install."""
    if frozen and dep:
        raise click.UsageError("--frozen can't be combined with a new dependency")
    if skills and not dep:
        raise click.UsageError("--skill needs a DEP")
    project = Path.cwd()
    m = _load(project)
    if dep:
        add_entry(m, dep, list(skills) or None)
    mode = "frozen" if frozen else "install"
    _report(ctx, reconcile(project, m, mode=mode, git=_git(ctx), warn=_warn))


@cli.command()
@click.argument("dep")
@click.pass_context
def uninstall(ctx: click.Context, dep: str) -> None:
    """Remove DEP from apm.yml and delete what it deployed."""
    project = Path.cwd()
    m = _load(project)
    remove_entry(m, dep)
    _report(ctx, reconcile(project, m, mode="install", git=_git(ctx), warn=_warn))


@cli.command()
@click.argument("dep", required=False)
@click.pass_context
def update(ctx: click.Context, dep: str | None) -> None:
    """Re-resolve refs for every dependency, or only DEP, and install."""
    project = Path.cwd()
    m = _load(project)
    keys = {_select_key(m, project, dep)} if dep else None
    result = reconcile(project, m, mode="update", update_keys=keys, git=_git(ctx), warn=_warn)
    _report(ctx, result)


@cli.group()
def deps() -> None:
    """Show installed dependencies from apm.lock.yaml."""


def _read_lock() -> Lock:
    lock = read(Path.cwd() / LOCKFILE)
    if lock is None:
        raise ApmError(f"no {LOCKFILE} here", hint="run 'apm install' first")
    return lock


def _line(dep: LockedDep) -> str:
    ref = dep.resolved_ref or ("local" if dep.source == "local" else "HEAD")
    commit = (dep.resolved_commit or "")[:7]
    return f"{dep.name or dep.repo_url}  {dep.key}  {ref}  {commit}".rstrip()


@deps.command("list")
def deps_list() -> None:
    """One line per locked package: name, source, ref, commit."""
    lock = _read_lock()
    for key in sorted(lock.deps):
        click.echo(_line(lock.deps[key]))


@deps.command("tree")
def deps_tree() -> None:
    """Locked packages as a tree, children under the package that declared them."""
    lock = _read_lock()
    children: dict[str | None, list[LockedDep]] = {}
    for dep in sorted(lock.deps.values(), key=lambda d: d.key):
        children.setdefault(dep.resolved_by if dep.depth > 1 else None, []).append(dep)

    def walk(parent: str | None, indent: int, seen: frozenset[str]) -> None:
        for dep in children.get(parent, []):
            click.echo("  " * indent + _line(dep))
            if dep.repo_url not in seen:
                walk(dep.repo_url, indent + 1, seen | {dep.repo_url})

    walk(None, 0, frozenset())


def main() -> None:
    cli(prog_name="apm")


if __name__ == "__main__":
    main()
