"""Breadth-first dependency resolution, lock pins and frozen drift checks."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from apm_lite import packages
from apm_lite.errors import ApmError
from apm_lite.git import Git, resolve_ref
from apm_lite.lockfile import Lock, LockedDep
from apm_lite.manifest import Manifest
from apm_lite.model import DepSpec, ResolvedPackage
from apm_lite.refs import is_full_sha

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
    packages.check_overlaps(paths)


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
                packages.inspect(pkg.source_dir, spec, warn=warn)
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
            source, digest = packages.place(project, spec, request.commit, locked, git)
        info = packages.inspect(source, spec, warn=warn)
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
