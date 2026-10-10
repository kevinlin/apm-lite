"""Deploy a complete plan, preserve edits and commit metadata inside an undo log."""

from __future__ import annotations

import errno
import json
import os
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from apm_lite import __version__, lockfile
from apm_lite.deploy.hooks import merge, plan_hooks, read_json
from apm_lite.deploy.rules import plan_rules
from apm_lite.deploy.skills import plan_skills
from apm_lite.errors import ApmError
from apm_lite.fs import UndoLog, check_destination
from apm_lite.git import Git
from apm_lite.lockfile import Lock, LockedDep, file_hash
from apm_lite.manifest import Manifest
from apm_lite.manifest import dump as dump_manifest
from apm_lite.model import DepSpec, HookEntry, PlannedFile, ResolvedPackage
from apm_lite.resolver import Mode, resolve
from apm_lite.targets import TARGETS


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

    old = lockfile.read(check_destination(project, "apm.lock.yaml"))
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
    lock_bytes = lockfile.dump(lock, __version__)
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
