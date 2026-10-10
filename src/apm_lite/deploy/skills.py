"""Skill planner: package skill folders to per-target skill files (design §6.3)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import frontmatter
import yaml

from apm_lite.fs import has_symlink_between, safe_name
from apm_lite.model import PlannedFile, ResolvedPackage
from apm_lite.targets import Target

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
