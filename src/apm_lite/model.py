from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class DepSpec:
    raw: str | dict  # entry as written in apm.yml
    kind: Literal["remote", "local"]
    key: str  # "host/team/pkg" (remote) or local path as written
    url: str | None = None  # clone URL as written, without "#ref"
    host: str | None = None
    port: int | None = None  # only when non-default
    repo_path: str | None = None  # "team/pkg", ".git" stripped, case kept
    ref: str | None = None
    skills: tuple[str, ...] | None = None  # deduped, sorted
    local_path: str | None = None  # as written
    declared_by: str | None = None  # key of declaring package; None = root


@dataclass
class ResolvedPackage:
    spec: DepSpec
    commit: str | None  # None for local and root
    ref_is_immutable: bool
    depth: int  # 1 for root deps; 0 for the root project
    parent_key: str | None
    parent_repo_url: str | None  # lockfile resolved_by
    source_dir: Path
    package_type: str  # hybrid | claude_skill | skill_bundle | apm_package
    name: str
    version: str | None
    content_hash: str | None
    skills_selected: tuple[str, ...] | None = None  # None = all
    deps: list[DepSpec] = field(default_factory=list)

    @property
    def repo_url(self) -> str:  # lockfile repo_url
        return self.spec.repo_path if self.spec.kind == "remote" else f"_local/{self.name}"

    @property
    def owner(self) -> str:  # lockfile key; "." for the root project
        return "." if self.depth == 0 else self.spec.key

    @property
    def marker(self) -> str:  # hook _apm_source value (design §6.4)
        return self.spec.repo_path if self.spec.kind == "remote" else f"_local/{self.name}"

    @property
    def label(self) -> str:  # <pkg> folder for hook scripts
        if self.depth == 0:
            return self.name
        return (self.spec.repo_path or self.spec.local_path).rstrip("/").split("/")[-1]


@dataclass(frozen=True)
class PlannedFile:
    content: bytes
    mode: int  # 0o644 or 0o755
    owner: str


@dataclass(frozen=True)
class HookEntry:
    event: str
    entry: dict  # native entry, no _apm_source
    marker: str
    owner: str
