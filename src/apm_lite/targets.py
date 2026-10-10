"""The two deployment targets (design §6.1)."""

from __future__ import annotations

from dataclasses import dataclass


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
