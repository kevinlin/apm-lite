"""Load, validate and edit `apm.yml`; parse dependency entries into `DepSpec`.

The grammar is the subset of upstream APM's that apm-lite supports (design §3). Editing goes
through ruamel's round-trip loader so comments and unrelated keys survive a save.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml as pyyaml
from ruamel.yaml import YAML, YAMLError
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from apm_lite.errors import ApmError
from apm_lite.model import DepSpec
from apm_lite.refs import classify

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
        classify(ref)
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


def remove_entry(m: Manifest, selector: str) -> DepSpec:
    """Remove every entry matching `selector` (as written, `host/path` key, or local path)."""
    selector = selector.strip()
    matches = [
        i
        for i, spec in enumerate(m.deps)
        if selector in (_written(spec).strip(), spec.key, spec.local_path)
    ]
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


def dump(m: Manifest) -> bytes:
    buf = io.BytesIO()
    _yaml().dump(m.doc, buf)
    return buf.getvalue()


def _is_plain_string(name: str) -> bool:
    if not _PLAIN_NAME_RE.fullmatch(name):
        return False
    # Both YAML 1.1 (PyYAML, upstream's reader) and 1.2 (ruamel) must read it back as a string.
    return pyyaml.safe_load(name) == name and YAML(typ="safe").load(name) == name


def init_text(name: str) -> str:
    shown = name if _is_plain_string(name) else json.dumps(name, ensure_ascii=False)
    return f'name: {shown}\nversion: "0.1.0"\ntargets:\n  - claude\ndependencies:\n  apm: []\n'
