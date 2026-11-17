"""Plan hook bundles and merge native entries using ownership sidecars (design §6.4)."""

from __future__ import annotations

import json
import os
import re
import stat
from collections import Counter
from pathlib import Path

from apm_lite.errors import ApmError
from apm_lite.fs import has_symlink_between, safe_name
from apm_lite.model import HookEntry, PlannedFile, ResolvedPackage
from apm_lite.targets import Target

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
        hint = None
        if (
            isinstance(exc, ValueError)
            and path.parent.name == ".gemini"
            and path.name == "settings.json"
        ):
            hint = "apm can't keep comments in this file: remove them, or keep this package's hooks out of gemini"
        raise ApmError(f"cannot read JSON {path}: {exc}", hint) from exc
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
    target: Target,
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
        quote = '"' if target.force_quote else match["quote"]
        return f"{quote}{target.script_prefix}/{label}/{rel}{quote}"

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
                target=target,
                label=pkg.label,
                hook_file_dir=source.parent,
                hooks_root=hooks_root,
                package_root=pkg.source_dir,
            ),
        }
        if "timeout" in handler:
            timeout = handler["timeout"]
            if type(timeout) is not int or timeout <= 0:
                raise ApmError(f"invalid timeout in {source}: expected a positive integer")
            entry["timeout"] = timeout * 1000 if target.timeout_ms else timeout
        rendered.append(entry)
    return rendered


def _cursor_matcher(matcher: str | None, event: str, source: Path, *, foreign: bool) -> str | None:
    """Translate only tool alternatives with an exact Cursor equivalent."""
    if not foreign or matcher in (None, "", "*", ".*"):
        return matcher
    mapping = {
        "Bash": "Shell",
        "Read": "Read",
        "Edit": "Write",
        "Write": "Write",
        "Grep": "Grep",
        "Task": "Task",
        "WebFetch": "WebFetch",
        "WebSearch": "WebSearch",
    }
    names = matcher.split("|")
    if (
        event not in {"preToolUse", "postToolUse"}
        or any(name not in mapping for name in names)
        or (set(names) & {"Edit", "Write"} and not {"Edit", "Write"} <= set(names))
    ):
        raise ApmError(
            f"matcher {matcher!r} in {source} has no exact Cursor equivalent",
            "ship hooks-cursor.json with Cursor's own events and matchers",
        )
    return "|".join(dict.fromkeys(mapping[name] for name in names))


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
    cursor = target.name == "cursor"
    for source in sources:
        if source.is_symlink():
            raise ApmError(f"hook source {source} must not be a symlink")
        doc = read_json(source)
        for source_event, declarations in _event_map(doc.get("hooks", doc), str(source)).items():
            event = target.native_event(source_event)
            if not target.supports(event):
                warnings.append(
                    f"hook event {source_event!r} in {source} is unsupported by {target.name}; skipped"
                )
                continue
            for raw in declarations:
                matcher = raw.get("matcher")
                if matcher is not None and not isinstance(matcher, str):
                    raise ApmError(f"invalid matcher in {source}: expected a string")
                handlers = _handlers(raw, source, pkg, target, hooks_root, warnings)
                if not handlers:
                    continue
                if target.is_nested(event):
                    if target.default_matcher is not None:
                        matcher = matcher or target.default_matcher
                    outer = {} if matcher is None else {"matcher": matcher}
                    outer["hooks"] = handlers
                    native = [outer]
                else:
                    native = handlers
                    if cursor:
                        matcher = _cursor_matcher(
                            matcher, event, source, foreign=source_event != event
                        )
                        for handler in native:
                            if matcher is not None:
                                handler["matcher"] = matcher
                            if source_event in {"Stop", "SubagentStop"}:
                                handler["loop_limit"] = None
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
    key = target.hook_key
    events = _event_map(native.get(key, {}), target.hook_file)
    if target.owned_container:
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
            previous = owned.get(event, [])
            # Our entries are appended. Prefer that suffix so equal user entries keep their order.
            if previous and json.dumps(entries[-len(previous) :], sort_keys=True) == json.dumps(
                previous, sort_keys=True
            ):
                remaining[event] = entries[: -len(previous)]
                continue
            counts = Counter(json.dumps(e, sort_keys=True) for e in previous)
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
    if configured and planned:
        for key, value in target.hook_defaults.items():
            result.setdefault(key, value)
    return result, new_sidecar or None
