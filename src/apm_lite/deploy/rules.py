"""Rule planner: `.apm/instructions/*.instructions.md` to per-target rule files (design §6.2)."""

from __future__ import annotations

import json

import frontmatter
import yaml

from apm_lite.errors import ApmError
from apm_lite.fs import safe_name
from apm_lite.model import PlannedFile, ResolvedPackage
from apm_lite.targets import Target

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
    if src_dir.is_symlink() or not src_dir.is_dir():
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
