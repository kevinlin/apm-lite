"""Rule planner: `.apm/instructions/*.instructions.md` to per-target rule files (design §6.2)."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import frontmatter
import yaml

from apm_lite.errors import ApmError
from apm_lite.fs import has_symlink_between, safe_name
from apm_lite.model import PlannedFile, ResolvedPackage
from apm_lite.targets import TARGETS, Target

_SUFFIX = ".instructions.md"
_RULE_MODE = 0o644
_RULE_TARGETS = ("claude", "antigravity", "cursor")


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


def _check_cursor_glob(glob: str) -> None:
    """Refuse a glob Cursor's bare `globs` scalar can't carry (design §6.2)."""
    if any(unicodedata.category(ch) == "Cc" for ch in glob):
        raise ApmError(
            f"glob {glob!r} contains a control character",
            hint="remove it from the rule's applyTo",
        )
    if len(_split_top_level(glob)) > 1:
        raise ApmError(
            f"glob {glob!r} contains a comma outside {{}} and []",
            hint="list each glob as its own applyTo item, or put the comma inside {a,b}",
        )


def _cursor_head(globs: list[str], description: object) -> str:
    lines = []
    if isinstance(description, str) and description:
        lines.append(f"description: {json.dumps(description, ensure_ascii=True)}")
    if globs:
        for glob in globs:
            _check_cursor_glob(glob)
        lines += ["globs: " + ", ".join(globs), "alwaysApply: false"]
    else:
        lines.append("alwaysApply: true")
    return "---\n" + "\n".join(lines) + "\n---\n\n"


def render_rule(text: str, target: str) -> str:
    """Render one rule source for *target*; the body is kept, other frontmatter is dropped."""
    if target not in _RULE_TARGETS:
        if target in TARGETS:
            raise ValueError(f"target {target!r} has no rule format")
        raise ValueError(f"unknown target {target!r}")
    post = frontmatter.loads(text.replace("\r\n", "\n"))
    body = post.content.lstrip("\n")
    globs = split_apply_to(post.metadata.get("applyTo"))
    if target == "cursor":
        head = _cursor_head(globs, post.metadata.get("description"))
    elif target == "claude":
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


def _rule_sources(pkg: ResolvedPackage) -> Iterator[Path]:
    """The rule source files *pkg* contributes, in name order (shared by `plan_rules`, `has_rules`)."""
    if pkg.depth != 0 and pkg.package_type != "apm_package":
        return
    src_dir = pkg.source_dir / ".apm" / "instructions"
    if has_symlink_between(src_dir, pkg.source_dir) or not src_dir.is_dir():
        return
    for path in sorted(src_dir.iterdir()):
        if path.name.endswith(_SUFFIX) and not path.is_symlink() and path.is_file():
            yield path


def has_rules(pkg: ResolvedPackage) -> bool:
    """Whether *pkg* has at least one rule source. Names and content are not validated here."""
    return next(_rule_sources(pkg), None) is not None


def plan_rules(pkg: ResolvedPackage, target: Target) -> dict[str, PlannedFile]:
    """Rule files *pkg* deploys for *target*: relative path to planned file."""
    if target.rule_ext is None:
        return {}
    planned: dict[str, PlannedFile] = {}
    for path in _rule_sources(pkg):
        name = safe_name(path.name[: -len(_SUFFIX)])
        try:
            text = path.read_bytes().decode("utf-8-sig")
            content = render_rule(text, target.name)
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise ApmError(
                f"cannot read rule {path.name} from {pkg.owner}: {exc}",
                hint="fix the file's frontmatter (it must be valid YAML in UTF-8)",
            ) from exc
        except ApmError as exc:
            raise ApmError(
                f"cannot render rule {path.name} from {pkg.owner} for {target.name}: {exc.message}",
                hint=exc.hint,
            ) from exc
        planned[f"{target.rules_dir}/{name}{target.rule_ext}"] = PlannedFile(
            content=content.encode("utf-8"), mode=_RULE_MODE, owner=pkg.owner
        )
    return planned
