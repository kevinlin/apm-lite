"""Remote package placement and package shape detection (design §4)."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from uuid import uuid4

from apm_lite.errors import ApmError
from apm_lite.fs import check_destination, safe_name
from apm_lite.git import Git
from apm_lite.lockfile import LockedDep, content_hash
from apm_lite.manifest import load
from apm_lite.model import DepSpec


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
