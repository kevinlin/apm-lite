"""The five deployment targets (design §6.1). Deploy code reads these records, not the names."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

_NO_MAP: Mapping = MappingProxyType({})


@dataclass(frozen=True)
class Target:
    name: str  # the value used in `targets:`
    root: str  # ".claude", ".agents", ...
    hook_file: str
    sidecar: str
    script_prefix: str  # replaces a hook script token's folder: f"{script_prefix}/<pkg>/<rel>"
    allowed_events: frozenset[str] | None = None  # native names; None = any not skipped
    skipped_events: frozenset[str] = frozenset()
    nested_events: frozenset[str] | None = None  # None = every event uses {matcher, hooks}
    skills_root: str | None = None  # parent of skills/; None = root
    rule_ext: str | None = ".md"  # None = the harness has no per-file rules
    hook_key: str = "hooks"  # top-level key of the hook file that holds the events
    owned_container: bool = False  # hook_key belongs to apm-lite alone (Antigravity's `apm`)
    hook_defaults: Mapping[str, object] = field(default=_NO_MAP)  # set when entries are added
    event_map: Mapping[str, str] = field(default=_NO_MAP)  # source event -> native event
    default_matcher: str | None = "*"  # matcher written when a nested source entry has none
    timeout_ms: bool = False  # the harness reads timeouts in milliseconds
    force_quote: bool = False  # always double-quote a rewritten script token

    @property
    def rules_dir(self) -> str:
        return f"{self.root}/rules"

    @property
    def skills_dir(self) -> str:
        return f"{self.skills_root or self.root}/skills"

    @property
    def hooks_dir(self) -> str:
        return f"{self.root}/hooks"

    def native_event(self, event: str) -> str:
        return self.event_map.get(event, event)

    def supports(self, event: str) -> bool:
        """Whether the harness has *event* (a native name, see `native_event`)."""
        if event in self.skipped_events:
            return False
        return self.allowed_events is None or event in self.allowed_events

    def is_nested(self, event: str) -> bool:
        return self.nested_events is None or event in self.nested_events


_INVOCATION_EVENTS = frozenset({"PreInvocation", "PostInvocation"})  # Antigravity only
_ANTIGRAVITY_EVENTS = frozenset({"PreToolUse", "PostToolUse", "Stop"}) | _INVOCATION_EVENTS
# Upstream integration/hook_native_formats.py CURSOR_NATIVE_EVENTS.
_CURSOR_EVENTS = frozenset(
    {
        "sessionStart",
        "sessionEnd",
        "preToolUse",
        "postToolUse",
        "postToolUseFailure",
        "subagentStart",
        "subagentStop",
        "beforeShellExecution",
        "afterShellExecution",
        "beforeMCPExecution",
        "afterMCPExecution",
        "beforeReadFile",
        "afterFileEdit",
        "beforeSubmitPrompt",
        "preCompact",
        "stop",
        "afterAgentResponse",
        "afterAgentThought",
        "beforeTabFileRead",
        "afterTabFileEdit",
        "workspaceOpen",
    }
)
# Codex rejects a hooks.json it can't parse, so only its own events are written
# (codex-rs/protocol/src/hooks.rs HookEventsToml).
_CODEX_EVENTS = frozenset(
    {
        "PreModelRequest",
        "PostModelRequest",
        "PreToolUse",
        "PermissionRequest",
        "PostToolUse",
        "PreCompact",
        "PostCompact",
        "SessionStart",
        "SessionEnd",
        "UserPromptSubmit",
        "SubagentStart",
        "SubagentStop",
        "Stop",
        "Interrupt",
    }
)

TARGETS: dict[str, Target] = {
    "claude": Target(
        name="claude",
        root=".claude",
        hook_file=".claude/settings.json",
        sidecar=".claude/apm-hooks.json",
        script_prefix="${CLAUDE_PROJECT_DIR}/.claude/hooks",
        skipped_events=_INVOCATION_EVENTS,
        force_quote=True,
    ),
    "antigravity": Target(
        name="antigravity",
        root=".agents",
        hook_file=".agents/hooks.json",
        sidecar=".agents/apm-hooks.json",
        script_prefix="hooks",  # agy runs hooks with .agents/ as the working directory
        allowed_events=_ANTIGRAVITY_EVENTS,
        nested_events=frozenset({"PreToolUse", "PostToolUse"}),
        hook_key="apm",
        owned_container=True,
    ),
    "cursor": Target(
        name="cursor",
        root=".cursor",
        hook_file=".cursor/hooks.json",
        sidecar=".cursor/apm-hooks.json",
        script_prefix=".cursor/hooks",  # Cursor runs project hooks from the project root
        allowed_events=_CURSOR_EVENTS,
        nested_events=frozenset(),
        skills_root=".agents",
        rule_ext=".mdc",
        hook_defaults=MappingProxyType({"version": 1}),
        event_map=MappingProxyType(
            {
                "PreToolUse": "preToolUse",
                "PostToolUse": "postToolUse",
                "UserPromptSubmit": "beforeSubmitPrompt",
                "Stop": "stop",
                "SubagentStop": "subagentStop",
                "SessionStart": "sessionStart",
                "SessionEnd": "sessionEnd",
                "PreCompact": "preCompact",
            }
        ),
        default_matcher=None,
    ),
    "codex": Target(
        name="codex",
        root=".codex",
        hook_file=".codex/hooks.json",
        sidecar=".codex/apm-hooks.json",
        # Codex runs hooks from the session cwd, so resolve from the git root (Codex hooks docs).
        script_prefix="$(git rev-parse --show-toplevel)/.codex/hooks",
        allowed_events=_CODEX_EVENTS,
        skills_root=".agents",
        rule_ext=None,
        default_matcher=None,
        force_quote=True,
    ),
    "gemini": Target(
        name="gemini",
        root=".gemini",
        hook_file=".gemini/settings.json",
        sidecar=".gemini/apm-hooks.json",
        script_prefix=".gemini/hooks",
        skipped_events=_INVOCATION_EVENTS,
        skills_root=".agents",
        rule_ext=None,
        event_map=MappingProxyType(
            {
                "PreToolUse": "BeforeTool",
                "PostToolUse": "AfterTool",
                "Stop": "SessionEnd",
                "preToolUse": "BeforeTool",
                "postToolUse": "AfterTool",
            }
        ),
        default_matcher=None,
        timeout_ms=True,
    ),
}
