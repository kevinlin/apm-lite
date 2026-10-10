# apm-lite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. In this repo, tasks are delegated through agent-handoff (see "Delegation map").

**Goal:** Build `src/apm_lite/`, a ~3k-line rewrite of APM that installs rules, skills and hooks from internal Git and root-local paths into Claude Code and Antigravity, with upstream-compatible `apm.yml` and `apm.lock.yaml`, then cut the repo over to it.

**Architecture:** Pure deploy planners (rules, skills, hooks) feed one `reconcile` step that classifies every file, applies writes inside an in-memory undo log, cleans up, and commits metadata last. A breadth-first resolver pins commits from the lockfile and fetches with plain `git`. Upstream `src/apm_cli/` stays in the tree as a read-only reference until Task 13.

**Tech Stack:** Python 3.10+, click, PyYAML, ruamel.yaml, python-frontmatter, pytest, ruff, uv, git.

**Spec:** [requirements](2026-10-10-apm-lite-requirements.md) (R1–R38, N1–N9) and [design](2026-10-10-apm-lite-design.md) (§1–§13). Every task's requirements include the spec sections it cites; read them before starting.

## Global Constraints

- Python `>=3.10`; macOS and Linux only; `git` on PATH (N5).
- Runtime deps: click, PyYAML, ruamel.yaml, python-frontmatter. Nothing else (N7). Dev deps: pytest, ruff.
- No HTTP client, no update check, no telemetry. The only network access is `git` subprocesses (N1).
- Read no tokens or credential env vars. Run git with `GIT_TERMINAL_PROMPT=0` (N2).
- Write only through `fs.check_destination`, under `.claude/`, `.agents/`, `apm_modules/`, plus root `apm.yml`, `apm.lock.yaml`, `.gitignore` (R30).
- Lockfile: `lockfile_version: '1'` (string), key order per design §5.1, no `generated_at`, no `deployments`.
- Tests are offline and need no tokens (N8). Until Task 13, new tests live in `tests_lite/` and run with `uv run pytest tests_lite`.
- Don't modify anything under `src/apm_cli/` or `tests/` before Task 13.
- Output through `click.echo`; errors as `error: …` / `hint: …` on stderr, exit 1.
- Code style: ruff (line length 100), type hints, small focused modules.
- The GitHub repo is public. Don't commit internal host names, mirror URLs or credentials. `uv.lock` is untracked because it records index URLs, and each developer resolves through their own configured index (`UV_DEFAULT_INDEX` or `~/.config/uv/uv.toml`, e.g. a JFrog Artifactory PyPI remote).

## Review Focus

1. **CRLF package sources:** a rule authored on Windows (`\r\n`) must parse its frontmatter and deploy with the same text as an LF source. Test in Task 6.
2. **Existing `.claude/settings.json` with user content** (`permissions`, user hooks, key order): only apm entries change, and everything else round-trips exactly. Test in Task 7.
3. **Odd repo and skill names** (`Team/My_Skills`, uppercase skill folders): the `apm_modules` path keeps case, the single-skill name is hyphen-cased, and the collection skill gets a warning, not a crash. Tests in Tasks 6 and 8.
4. **`apm install` with no `apm.yml`:** clear error with the hint `run 'apm init'`, exit 1, nothing written. Test in Task 11.
5. **Git failure** (unknown host, auth denied, missing ref): the error shows the git command and stderr, exit 1, and the project tree is unchanged. Test in Task 11.

---

## Delegation map

Identities come from `.handoff/config.toml`:
- `fast_worker`: cursor, claude-sonnet-5-5-xhigh, allow-all.
- `deep_reasoner`: codex, gpt-6.1-sol, xhigh.
- `driver`: this Claude Code session, inline.

Routing rule:
- `deep_reasoner`: code where a wrong premise silently loses data or breaks compatibility (fs safety, hooks ownership, cache reuse, resolution, reconcile).
- `fast_worker`: spec-complete modules with exact tests.
- `driver`: integration, compatibility verification and final acceptance.

| Task | Identity | Depends | Acceptance (must pass) |
|---|---|---|---|
| 0 Antigravity format spike | driver | – (needs `agy` login) | spike report recorded in design §11 |
| 1 Scaffold, model, fixtures | fast_worker | – | `uv run pytest tests_lite/test_fixtures.py -q` |
| 2 `fs.py` | deep_reasoner | 1 | `uv run pytest tests_lite/test_fs.py -q` |
| 3 `refs.py`, `manifest.py` | fast_worker | 1 | `uv run pytest tests_lite/test_refs.py tests_lite/test_manifest.py -q` |
| 4 `lockfile.py` + hash golden | fast_worker | 1 | `uv run pytest tests_lite/test_lockfile.py -q` |
| 5 `git.py` | fast_worker | 1 | `uv run pytest tests_lite/test_git.py -q` |
| 6 `targets.py`, rules, skills | fast_worker | 1 | `uv run pytest tests_lite/test_rules.py tests_lite/test_skills.py -q` |
| 7 `deploy/hooks.py` | deep_reasoner | 1 | `uv run pytest tests_lite/test_hooks.py -q` |
| 8 `packages.py` | deep_reasoner | 2,3,4,5 | `uv run pytest tests_lite/test_packages.py -q` |
| 9 `resolver.py` | deep_reasoner | 8 | `uv run pytest tests_lite/test_resolver.py -q` |
| 10 `reconcile.py` | deep_reasoner | 2,4,6,7,9 | `uv run pytest tests_lite/test_reconcile.py -q` |
| 11 `cli.py` + CLI flows | driver | 10 | `uv run pytest tests_lite -q` |
| 12 Upstream compatibility fixtures | driver | 11 | `uv run python tests_lite/compat/verify_upstream.py` exits 0; `uv run pytest tests_lite/test_compat.py -q` |
| 13 Cut-over | fast_worker | 12 | `uv run pytest -q` (new `tests/`), `uv run ruff check src tests`, `git ls-files src/apm_cli` empty |
| 14 Docs | fast_worker | 13 | files exist per N9; `grep -rn "microsoft.github.io\|aka.ms\|brew install" README.md docs/*.md` empty |
| 15 Final acceptance | driver | 14 | full suite, ruff, line count, whole-branch review |

Parallel waves: **A** = 0 and 1 (0 runs whenever `agy` is logged in; it must finish before Tasks 6 and 7 are accepted). **B** = 2, 3, 4, 5, 6, 7 in parallel (disjoint files). **C** = 8. **D** = 9. **E** = 10. **F** = 11 → 12 → 13 → 14 → 15.

Delegation rules for every job:
- Use the agent-handoff Delegation Packet. Constraints for every task: only touch the task's listed files; don't commit; run only your task's test files (other Wave B modules may be mid-change).
- The driver reviews each diff against the task and the cited spec sections, runs the acceptance command, and commits per task.
- The fix-round cap is `implementation_max_rounds = 5`.
- The user waived the plan spec-review and adversarial gates for this plan.

## File structure

```
src/apm_lite/
  __init__.py        __version__
  errors.py          ApmError
  model.py           DepSpec, ResolvedPackage, PlannedFile, HookEntry
  fs.py              safe_name, check_destination, atomic_write, UndoLog
  refs.py            classify, is_full_sha, semver predicates (ported)
  manifest.py        Manifest, load, parse_entry, add_entry, remove_entry, dump, init_text
  lockfile.py        LockedDep, Lock, read, dump, content_hash, file_hash
  git.py             Git (ls_remote cache, fetch with fallback), resolve_ref
  packages.py        install_dir, check_overlaps, place, inspect
  resolver.py        resolve
  targets.py         Target, TARGETS
  deploy/__init__.py
  deploy/rules.py    split_apply_to, render_rule, plan_rules
  deploy/skills.py   to_hyphen_case, validate_skill_name, plan_skills
  deploy/hooks.py    read_json, plan_hooks, rewrite_command, merge
  reconcile.py       root_package, plan, classify, reconcile
  cli.py             click app
tests_lite/          (renamed to tests/ in Task 13)
  conftest.py        git_server, project, run_cli fixtures
  test_*.py          one per module, plus test_cli_flows.py, test_compat.py
  fixtures/          hash_golden.json, compat/
  compat/verify_upstream.py
```

---

### Task 0: Antigravity format spike

**Identity:** driver (inline) · **Depends:** `agy` logged in (run `agy` once interactively) · **Spec:** design §6.2, §6.4, §11

This is throwaway work in the scratchpad, not product code. It answers two questions before Tasks 6 and 7 are accepted:
- Does `agy` 1.3.3 load `.agents/rules/*.md` with `trigger: always_on`, and with `trigger: glob` plus `globs` as one comma-separated string?
- Does it run `.agents/hooks.json` commands written as relative paths, from the project root?

- [ ] **Step 1:** Build the spike project (already scripted in the session scratchpad as `agy-spike/`):
  - `.agents/rules/always.md` (`trigger: always_on`, secret word `BANANA-ALWAYS`);
  - `.agents/rules/py.md` (`trigger: glob`, `globs: "**/*.py,**/*.pyi"`, codeword `PYTHON-GLOB`);
  - `.agents/skills/spike-skill/SKILL.md` (answer `CHERRY-SKILL`);
  - `.agents/hooks.json` with an `apm` container: `PreInvocation` flat, `PreToolUse` nested, pointing at relative `.agents/hooks/spike/*.sh` scripts that append `pwd` to `hook-log.txt`;
  - `src/app.py`.
- [ ] **Step 2:** In that dir, run: `agy -p "1) secret word per your rules? 2) list loaded workspace rules (with trigger) and skills 3) read src/app.py, then give the Python codeword 4) spike skill answer?" --dangerously-skip-permissions --print-timeout 300s`.
- [ ] **Step 3:** Record the pass or fail of each check, plus `hook-log.txt`, in design §11. If a check fails, change design §6.2 or §6.4 (and the Task 6/7 tests) before those tasks are accepted. If `agy` ignores `trigger: always_on` but loads frontmatter-less rules, revert to upstream's form, which has no frontmatter. If it rejects the string `globs`, use upstream's YAML list.

---

### Task 1: Scaffold, model, test fixtures

**Identity:** fast_worker · **Depends:** – · **Spec:** design §2, §9 (fake server)

**Files:**
- Modify: `pyproject.toml`: in `[tool.setuptools.packages.find]`, change `include` to `["apm_cli*", "apm_lite*"]`. Change nothing else.
- Modify: `.gitignore` (add `uv.lock`); untrack `uv.lock` (Step 2).
- Create: `src/apm_lite/__init__.py`, `src/apm_lite/errors.py`, `src/apm_lite/model.py`, `src/apm_lite/deploy/__init__.py`
- Create: `tests_lite/__init__.py` (empty), `tests_lite/conftest.py`, `tests_lite/test_fixtures.py`

**Interfaces (Produces):**

```python
# src/apm_lite/__init__.py
__version__ = "0.1.0"

# src/apm_lite/errors.py
class ApmError(Exception):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

# src/apm_lite/model.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

@dataclass(frozen=True)
class DepSpec:
    raw: str | dict                 # entry as written in apm.yml
    kind: Literal["remote", "local"]
    key: str                        # "host/team/pkg" (remote) or local path as written
    url: str | None = None          # clone URL as written, without "#ref"
    host: str | None = None
    port: int | None = None         # only when non-default
    repo_path: str | None = None    # "team/pkg", ".git" stripped, case kept
    ref: str | None = None
    skills: tuple[str, ...] | None = None   # deduped, sorted
    local_path: str | None = None   # as written
    declared_by: str | None = None  # key of declaring package; None = root

@dataclass
class ResolvedPackage:
    spec: DepSpec
    commit: str | None              # None for local and root
    ref_is_immutable: bool
    depth: int                      # 1 for root deps; 0 for the root project
    parent_key: str | None
    parent_repo_url: str | None     # lockfile resolved_by
    source_dir: Path
    package_type: str               # hybrid | claude_skill | skill_bundle | apm_package
    name: str
    version: str | None
    content_hash: str | None
    skills_selected: tuple[str, ...] | None = None   # None = all
    deps: list[DepSpec] = field(default_factory=list)

    @property
    def repo_url(self) -> str:      # lockfile repo_url
        return self.spec.repo_path if self.spec.kind == "remote" else f"_local/{self.name}"

    @property
    def owner(self) -> str:         # lockfile key; "." for the root project
        return "." if self.depth == 0 else self.spec.key

    @property
    def marker(self) -> str:        # hook _apm_source value (design §6.4)
        return self.spec.repo_path if self.spec.kind == "remote" else f"_local/{self.name}"

    @property
    def label(self) -> str:         # <pkg> folder for hook scripts
        if self.depth == 0:
            return self.name
        return (self.spec.repo_path or self.spec.local_path).rstrip("/").split("/")[-1]

@dataclass(frozen=True)
class PlannedFile:
    content: bytes
    mode: int                       # 0o644 or 0o755
    owner: str

@dataclass(frozen=True)
class HookEntry:
    event: str
    entry: dict                     # native entry, no _apm_source
    marker: str
    owner: str
```

**Fixture contract** (`tests_lite/conftest.py`):
- `git_env(tmp_path, monkeypatch)`: sets `GIT_CONFIG_GLOBAL` to `tmp_path/"gitconfig"`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_TERMINAL_PROMPT=0`, author and committer name/email env vars.
- `git_server(tmp_path, git_env) -> GitServer`, with:
  - `GitServer.repo(path: str) -> ServerRepo`: creates a bare repo at `tmp_path/"server"/path + ".git"`, plus a work clone. Writes `url."file://<tmp>/server/".insteadOf` for both `https://git.test/` and `ssh://git@git.test/` into the gitconfig.
  - `ServerRepo.commit(files: dict[str, str | bytes], message="c", exec_files=()) -> str`: replaces the whole tree, commits, pushes, and returns the SHA.
  - `ServerRepo.tag(name, sha=None, *, annotated=False, force=False)` and `ServerRepo.branch(name, sha=None)`: push (`force` re-points an existing tag).
  - Commits use fixed `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` so SHAs are deterministic (Task 12 relies on it).
  - `ServerRepo.https`, `ServerRepo.ssh`, `ServerRepo.scp`: give `https://git.test/<path>.git`, `ssh://git@git.test/<path>.git` and `git@git.test:<path>.git`.
  - `ServerRepo.set_config(key, value)`: git config on the bare repo.
  - The scp form needs its own `insteadOf` line: `url."file://<tmp>/server/".insteadOf "git@git.test:"`.
- `project(tmp_path) -> Path`: an empty `tmp_path/"proj"` dir.

- [ ] **Step 1: Write the failing fixture test**

```python
# tests_lite/test_fixtures.py
import subprocess

def test_git_server_serves_https_ssh_and_scp_urls(git_server):
    repo = git_server.repo("team/pkg")
    sha = repo.commit({"apm.yml": "name: pkg\nversion: '1.0.0'\n"})
    repo.tag("v1.0.0")
    for url in (repo.https, repo.ssh, repo.scp):
        out = subprocess.run(["git", "ls-remote", url], capture_output=True, text=True, check=True)
        assert sha in out.stdout
        assert "refs/tags/v1.0.0" in out.stdout

def test_model_owner_and_marker(tmp_path):
    from apm_lite.model import DepSpec, ResolvedPackage
    spec = DepSpec(raw="x", kind="remote", key="git.test/team/pkg", repo_path="team/pkg")
    pkg = ResolvedPackage(spec=spec, commit="a" * 40, ref_is_immutable=True, depth=1,
                          parent_key=None, parent_repo_url=None, source_dir=tmp_path,
                          package_type="apm_package", name="pkg", version="1.0.0",
                          content_hash=None)
    assert (pkg.owner, pkg.marker, pkg.label, pkg.repo_url) == (
        "git.test/team/pkg", "team/pkg", "pkg", "team/pkg")
```

- [ ] **Step 2:** Stop tracking `uv.lock`. Upstream's copy pins every package to `packagefeedproxy.microsoft.io`, and any regenerated copy would record the developer's own index URL in a public repo. Run `git rm --cached uv.lock`, add a `uv.lock` line to `.gitignore`, then `rm uv.lock`. Run `uv sync --extra dev`, which resolves through the configured index (pypi.org by default). Then run `uv run pytest tests_lite/test_fixtures.py -q`. Expected: FAIL (fixture or module missing).
- [ ] **Step 3:** Implement the files above to the contract.
- [ ] **Step 4:** `uv run pytest tests_lite/test_fixtures.py -q`. Expected: 2 passed.
- [ ] **Step 5:** Driver commits: `feat(lite): scaffold apm_lite package, model and git test server`.

---

### Task 2: `fs.py`: containment, atomic writes, undo log

**Identity:** deep_reasoner · **Depends:** 1 · **Spec:** R30, R37; design §7.1 (undo log), §7.3

**Files:** Create `src/apm_lite/fs.py`, `tests_lite/test_fs.py`

**Interfaces (Produces):**

```python
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
ALLOWED_ROOTS = (".claude", ".agents", "apm_modules")
ALLOWED_ROOT_FILES = ("apm.yml", "apm.lock.yaml", ".gitignore")

def safe_name(name: str) -> str: ...            # ApmError unless SAFE_NAME_RE and not "." / ".."
def check_destination(project: Path, rel: str) -> Path: ...  # returns absolute path; ApmError per §7.3
def atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None: ...
    # temp ".<name>.<random>.tmp" in path.parent, fsync, chmod, os.replace; mkdir parents
class UndoLog:
    def __init__(self, project: Path) -> None: ...
    def write(self, rel: str, data: bytes, mode: int = 0o644) -> None: ...  # check, record, atomic_write
    def chmod(self, rel: str, mode: int) -> None: ...
    def delete(self, rel: str) -> None: ...      # file only; records bytes+mode first
    def rmdir_if_empty(self, rel: str) -> None: ...
    def rollback(self) -> list[str]: ...         # reverse order; returns rels that failed
    def __enter__(self) -> "UndoLog": ...
    def __exit__(self, exc_type, exc, tb) -> bool: ...
        # on any BaseException: rollback(); if failures, raise ApmError listing them
        # chained from the original; else return False (re-raise original)
```

Record only the first prior state per path. A directory created by `write` is recorded too, so rollback removes it if empty.

- [ ] **Step 1: Write failing tests**

```python
# tests_lite/test_fs.py
import os, pytest
from apm_lite.errors import ApmError
from apm_lite.fs import UndoLog, atomic_write, check_destination, safe_name

@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a b", "x$y"])
def test_safe_name_rejects(bad):
    with pytest.raises(ApmError):
        safe_name(bad)

@pytest.mark.parametrize("rel", ["src/x", "../x", ".claude/../../x", "/etc/passwd", "apm.yml.bak"])
def test_check_destination_rejects_outside_allowlist(project, rel):
    with pytest.raises(ApmError):
        check_destination(project, rel)

def test_check_destination_rejects_symlinked_root(project, tmp_path):
    outside = tmp_path / "outside"; outside.mkdir()
    (project / ".claude").symlink_to(outside)
    with pytest.raises(ApmError):
        check_destination(project, ".claude/rules/x.md")

def test_check_destination_accepts(project):
    assert check_destination(project, ".claude/rules/x.md") == project / ".claude/rules/x.md"
    assert check_destination(project, "apm.lock.yaml") == project / "apm.lock.yaml"

def test_atomic_write_sets_mode_and_leaves_no_temp(project):
    p = project / ".claude/hooks/pkg/x.sh"
    atomic_write(p, b"#!/bin/sh\n", 0o755)
    assert p.read_bytes() == b"#!/bin/sh\n" and os.stat(p).st_mode & 0o111
    assert [f.name for f in p.parent.iterdir()] == ["x.sh"]

def test_undo_log_restores_everything_on_error(project):
    (project / ".claude").mkdir()
    (project / ".claude/a.md").write_bytes(b"old")
    (project / ".claude/gone.md").write_bytes(b"keep me")
    os.chmod(project / ".claude/gone.md", 0o755)
    with pytest.raises(RuntimeError):
        with UndoLog(project) as log:
            log.write(".claude/a.md", b"new")
            log.write(".claude/new/b.md", b"b")
            log.delete(".claude/gone.md")
            raise RuntimeError("boom")
    assert (project / ".claude/a.md").read_bytes() == b"old"
    assert not (project / ".claude/new").exists()
    assert (project / ".claude/gone.md").read_bytes() == b"keep me"
    assert os.stat(project / ".claude/gone.md").st_mode & 0o777 == 0o755

def test_undo_log_rolls_back_keyboard_interrupt(project):
    with pytest.raises(KeyboardInterrupt):
        with UndoLog(project) as log:
            log.write("apm.yml", b"x")
            raise KeyboardInterrupt
    assert not (project / "apm.yml").exists()

def test_undo_log_keeps_changes_on_success(project):
    with UndoLog(project) as log:
        log.write(".agents/rules/r.md", b"r")
    assert (project / ".agents/rules/r.md").read_bytes() == b"r"
```

- [ ] **Step 2:** `uv run pytest tests_lite/test_fs.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement `fs.py` to the interface. Symlink check: walk each existing component from `project` down to the destination and reject any `is_symlink()`. Then require `realpath` to stay inside `realpath(project)`.
- [ ] **Step 4:** Re-run. Expected: all pass.
- [ ] **Step 5:** Driver commits: `feat(lite): add fs containment, atomic writes and undo log`.

---

### Task 3: `refs.py` and `manifest.py`

**Identity:** fast_worker · **Depends:** 1 · **Spec:** R1–R6, R10, R33, R34; design §3

**Files:** Create `src/apm_lite/refs.py`, `src/apm_lite/manifest.py`, `tests_lite/test_refs.py`, `tests_lite/test_manifest.py`

**Interfaces (Produces):**

```python
# refs.py: port these verbatim from upstream (marketplace/semver.py:34, deps/registry/semver.py:15-38,
# models/dependency/identity.py:34, :269-282)
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
                        r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$")
_RANGE_OPERATORS = (">=", "<=", ">", "<", "^", "~", "=")
_WILDCARD_RE = re.compile(r"^\d+\.\d+\.[xX*]$")
_RANGE_PREFIX_RE = re.compile(r"^(>=|<=|>|<|\^|~|=)")
def is_semver_range(spec: str) -> bool:
    parts = spec.strip().split()
    return bool(parts) and all(_is_range_component(p) for p in parts)
def _is_range_component(c: str) -> bool:
    if not c: return False
    if _WILDCARD_RE.match(c): return True
    for op in _RANGE_OPERATORS:
        if c.startswith(op):
            return _SEMVER_RE.match(c[len(op):]) is not None
    return _SEMVER_RE.match(c) is not None
def is_full_sha(ref: str | None) -> bool: ...    # re.fullmatch(r"[0-9a-fA-F]{40}")
def classify(ref: str | None) -> Literal["none", "sha", "literal"]:
    # ApmError(hint="use the tag name, e.g. 'v1.2.3'") if is_semver_range(ref)
    # or _RANGE_PREFIX_RE.match(ref.strip()); ApmError if "@" in ref

# manifest.py
FQDN_RE = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?"
                     r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)+$")   # upstream is_valid_fqdn
@dataclass
class Manifest:
    path: Path
    name: str
    version: str
    targets: tuple[str, ...]        # default ("claude",)
    deps: list[DepSpec]
    doc: Any                        # ruamel CommentedMap, for round-trip saves
    edited: bool = False
def load(path: Path, *, is_root: bool, declared_by: str | None = None,
         warn: Callable[[str], None] = lambda m: None) -> Manifest: ...
def parse_entry(entry: str | dict, *, allow_local: bool, declared_by: str | None = None) -> DepSpec: ...
def add_entry(m: Manifest, text: str, skills: list[str] | None) -> DepSpec: ...   # sets m.edited
def remove_entry(m: Manifest, selector: str) -> DepSpec: ...   # match raw / key / local path; sets m.edited
def dump(m: Manifest) -> bytes: ...             # ruamel round-trip, comments kept
def init_text(name: str) -> str: ...
    # 'name: <name>\nversion: "0.1.0"\ntargets:\n  - claude\ndependencies:\n  apm: []\n'
```

Parsing rules (design §3.2): the `key` is `f"{host}/{repo_path}"`, and `port` is kept only when it isn't 22 or 443. Error messages must name the supported forms: `https://host/group/repo.git#ref`, `ssh://git@host/group/repo.git#ref`, `git@host:group/repo.git#ref`, `./local/path`.

- [ ] **Step 1: Write failing tests**

```python
# tests_lite/test_refs.py
import pytest
from apm_lite.errors import ApmError
from apm_lite.refs import classify

@pytest.mark.parametrize("ref,kind", [(None, "none"), ("main", "literal"), ("v1.2.3", "literal"),
    ("release/2024-q1", "literal"), ("a" * 40, "sha"), ("1a2b" * 10, "sha")])
def test_classify_ok(ref, kind):
    assert classify(ref) == kind

@pytest.mark.parametrize("ref", ["1.2.3", "^1.2.0", "~2.1.0", ">=1.0.0 <2.0.0", "1.2.x", ">=banana",
                                 "release@candidate"])
def test_classify_rejects_semver_like_and_at(ref):
    with pytest.raises(ApmError):
        classify(ref)
```

```python
# tests_lite/test_manifest.py
import pytest
from apm_lite.errors import ApmError
from apm_lite.manifest import add_entry, dump, init_text, load, parse_entry, remove_entry

@pytest.mark.parametrize("entry,host,port,path,ref,key", [
    ("https://git.corp.example/team/pkg.git#v1.0", "git.corp.example", None, "team/pkg", "v1.0",
     "git.corp.example/team/pkg"),
    ("ssh://git@git.corp.example:7999/team/pkg.git#main", "git.corp.example", 7999, "team/pkg",
     "main", "git.corp.example/team/pkg"),
    ("ssh://git@git.corp.example:22/team/pkg", "git.corp.example", None, "team/pkg", None,
     "git.corp.example/team/pkg"),
    ("git@git.corp.example:group/sub/Pkg.git", "git.corp.example", None, "group/sub/Pkg", None,
     "git.corp.example/group/sub/Pkg"),
])
def test_parse_remote(entry, host, port, path, ref, key):
    d = parse_entry(entry, allow_local=False)
    assert (d.kind, d.host, d.port, d.repo_path, d.ref, d.key) == ("remote", host, port, path, ref, key)
    assert d.url == entry.split("#")[0]

def test_parse_object_ref_overrides_and_skills_sorted():
    d = parse_entry({"git": "https://git.corp.example/t/s.git#v1", "ref": "v2",
                     "skills": ["b", "a", "b"]}, allow_local=False)
    assert (d.ref, d.skills) == ("v2", ("a", "b"))

@pytest.mark.parametrize("bad", [
    "team/pkg", "git.corp.example/team/pkg", "http://git.corp.example/t/p.git",
    "https://gitserver/t/p.git", "https://git..corp/t/p.git", "https://git_corp.example/t/p.git",
    "https://git.corp.example/pkg.git", "https://git.corp.example/t/p@x.git",
    "ssh://git@git.corp.example/t/p.git#release@candidate", "git@git.corp.example:7999/t/p.git",
    "https://git.corp.example/t/../p.git", "https://git.corp.example/t/p.git#1.2.3",
    {"git": "https://git.corp.example/t/p.git", "path": "sub"},
    {"git": "https://git.corp.example/t/p.git", "alias": "x"},
    {"git": "https://git.corp.example/t/p.git", "version": "1"},
    {"git": "https://git.corp.example/t/p.git", "skills": []},
])
def test_parse_rejects(bad):
    with pytest.raises(ApmError):
        parse_entry(bad, allow_local=True)

def test_local_only_when_allowed():
    assert parse_entry("./packages/x", allow_local=True).key == "./packages/x"
    with pytest.raises(ApmError):
        parse_entry("./packages/x", allow_local=False)

def _write(p, text):
    p.write_text(text); return p

def test_load_defaults_and_warnings(project):
    warnings = []
    m = load(_write(project / "apm.yml", "name: p\nversion: '1.0'\nscripts: {a: b}\n"
             "dependencies:\n  mcp: []\n"), is_root=True, warn=warnings.append)
    assert m.targets == ("claude",) and m.deps == []
    assert len(warnings) == 2

@pytest.mark.parametrize("text", ["version: '1'\n", "name: p\nversion: 1.0\n", "name: ' '\nversion: '1'\n",
    "name: p\nversion: '1'\ntargets: []\n", "name: p\nversion: '1'\ntargets: [cursor]\n",
    "name: p\nversion: '1'\ntarget: claude\n"])
def test_load_rejects(project, text):
    with pytest.raises(ApmError):
        load(_write(project / "apm.yml", text), is_root=True)

def test_dependency_manifest_rejects_local(project):
    with pytest.raises(ApmError):
        load(_write(project / "apm.yml", "name: p\nversion: '1'\ndependencies:\n  apm: [./x]\n"),
             is_root=False, declared_by="git.test/t/p")

def test_root_local_duplicate_folder_name_rejected(project):
    with pytest.raises(ApmError):
        load(_write(project / "apm.yml", "name: p\nversion: '1'\ndependencies:\n"
                    "  apm: [./a/base, ./b/base]\n"), is_root=True)

def test_add_and_remove_keep_comments_and_transport(project):
    m = load(_write(project / "apm.yml", init_text("p").replace("apm: []", "apm: []  # deps")),
             is_root=True)
    add_entry(m, "git@git.corp.example:team/pkg.git#v1", None)
    add_entry(m, "https://git.corp.example/team/skills.git#v2", ["b"])
    add_entry(m, "https://git.corp.example/team/skills.git#v2", ["a"])
    text = dump(m).decode()
    assert "git@git.corp.example:team/pkg.git#v1" in text
    assert "skills:\n" in text and "- a\n" in text and "- b\n" in text
    remove_entry(m, "git.corp.example/team/pkg")
    assert "team/pkg" not in dump(m).decode()
    with pytest.raises(ApmError):
        remove_entry(m, "nope")
```

- [ ] **Step 2:** Run both test files. Expected: FAIL.
- [ ] **Step 3:** Implement. Use `ruamel.yaml.YAML()` (round-trip) for load and dump. Validate `name`/`version` with `isinstance(v, str) and v.strip()`. Add entries by appending to the `dependencies.apm` sequence, creating it if absent.
- [ ] **Step 4:** Re-run. Expected: all pass.
- [ ] **Step 5:** Driver commits: `feat(lite): add ref classification and manifest parsing`.

---

### Task 4: `lockfile.py` and hashes

**Identity:** fast_worker · **Depends:** 1 · **Spec:** R17–R19; design §5

**Files:** Create `src/apm_lite/lockfile.py`, `tests_lite/test_lockfile.py`, `tests_lite/fixtures/make_hash_golden.py`, `tests_lite/fixtures/hash_golden.json`

**Interfaces (Produces):**

```python
EMPTY_HASH = "sha256:" + hashlib.sha256(b"").hexdigest()
FIELD_ORDER = ("repo_url", "name", "host", "port", "resolved_commit", "resolved_ref", "version",
               "depth", "resolved_by", "package_type", "deployed_files", "deployed_file_hashes",
               "source", "local_path", "content_hash", "skill_subset")   # upstream to_dict order
@dataclass
class LockedDep:
    repo_url: str
    name: str | None = None
    host: str | None = None
    port: int | None = None
    resolved_commit: str | None = None
    resolved_ref: str | None = None
    version: str | None = None
    depth: int = 1
    resolved_by: str | None = None
    package_type: str | None = None
    deployed_files: list[str] = field(default_factory=list)
    deployed_file_hashes: dict[str, str] = field(default_factory=dict)
    source: str | None = None       # "local" for local deps
    local_path: str | None = None
    content_hash: str | None = None
    skill_subset: list[str] = field(default_factory=list)
    @property
    def key(self) -> str: ...       # local_path if source == "local" else f"{host}/{repo_url}"
@dataclass
class Lock:
    deps: dict[str, LockedDep] = field(default_factory=dict)
    local_deployed_files: list[str] = field(default_factory=list)
    local_deployed_file_hashes: dict[str, str] = field(default_factory=dict)
    def all_tracked(self) -> dict[str, tuple[str, str | None]]: ...
        # deployed path -> (owner key or ".", recorded hash or None for dirs)
def read(path: Path) -> Lock | None: ...       # None if missing; ApmError on bad version/repo_url
def dump(lock: Lock, apm_version: str) -> bytes: ...
def content_hash(directory: Path) -> str: ...  # port of upstream compute_package_hash
def file_hash(data: bytes) -> str: ...         # port of upstream compute_file_hash on bytes
```

`dump`:
- Emit `lockfile_version: '1'`, `apm_version`, then `dependencies` sorted by `(depth, repo_url)`, then `local_deployed_files` and `local_deployed_file_hashes` (both only if non-empty).
- Each dependency dict follows `FIELD_ORDER`, omitting falsy values and `depth == 1`. `deployed_files` and `skill_subset` are sorted; `deployed_file_hashes` is sorted by key.
- Use `yaml.safe_dump(data, sort_keys=False, default_flow_style=False)`.

Hash ports (upstream `utils/content_hash.py:25-153`):

```python
def content_hash(directory: Path) -> str:
    if not directory.is_dir():
        return EMPTY_HASH
    files = []
    for item in directory.rglob("*"):
        if item.is_symlink():
            continue
        rel = item.relative_to(directory)
        if any(part in {".git", "__pycache__"} for part in rel.parts):
            continue
        if item.is_file():
            if len(rel.parts) == 1 and rel.name == ".apm-pin":
                continue
            files.append(rel)
    files.sort(key=lambda p: p.as_posix())
    if not files:
        return EMPTY_HASH
    h = hashlib.sha256()
    for rel in files:
        h.update(rel.as_posix().encode("utf-8"))
        h.update((directory / rel).read_bytes())
    return f"sha256:{h.hexdigest()}"

def file_hash(data: bytes) -> str:
    canonical = data
    if b"\x00" not in data:
        try:
            canonical = data.decode("utf-8").replace("\r\n", "\n").encode("utf-8")
        except UnicodeDecodeError:
            pass
    return "sha256:" + hashlib.sha256(canonical).hexdigest()
```

Golden generator (`tests_lite/fixtures/make_hash_golden.py`, run once now while upstream exists):
- Build three fixture trees under a temp dir: a nested tree with `.git/x`, `__pycache__/y.pyc`, a symlink and a root `.apm-pin`; an empty dir; and a tree with a CRLF file plus a binary file containing `\x00`.
- Call `apm_cli.utils.content_hash.compute_package_hash` and `compute_file_hash` on them.
- Write `hash_golden.json` as `{"trees": {name: {"files": {rel: content-as-latin1}, "hash": ...}}, "files": {name: {"bytes_latin1": ..., "hash": ...}}}`.

- [ ] **Step 1: Generate the golden file:** `uv run python tests_lite/fixtures/make_hash_golden.py`. Commit the JSON with the task.
- [ ] **Step 2: Write failing tests**

```python
# tests_lite/test_lockfile.py
import json, pytest
from pathlib import Path
from apm_lite.errors import ApmError
from apm_lite.lockfile import Lock, LockedDep, content_hash, dump, file_hash, read

GOLDEN = json.loads((Path(__file__).parent / "fixtures/hash_golden.json").read_text())

def test_content_hash_matches_upstream(tmp_path):
    for name, tree in GOLDEN["trees"].items():
        root = tmp_path / name; root.mkdir()
        for rel, content in tree["files"].items():
            p = root / rel; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content.encode("latin1"))
        assert content_hash(root) == tree["hash"], name

def test_file_hash_matches_upstream():
    for name, f in GOLDEN["files"].items():
        assert file_hash(f["bytes_latin1"].encode("latin1")) == f["hash"], name

def _lock():
    return Lock(deps={
        "git.test/team/base": LockedDep(repo_url="team/base", name="base", host="git.test",
            resolved_commit="a" * 40, resolved_ref="v1", version="1.0.0", package_type="apm_package",
            deployed_files=[".claude/rules/b.md"], deployed_file_hashes={".claude/rules/b.md": "sha256:1"},
            content_hash="sha256:2"),
        "git.test/team/child": LockedDep(repo_url="team/child", host="git.test", port=7999,
            resolved_commit="b" * 40, depth=2, resolved_by="team/base", package_type="skill_bundle",
            skill_subset=["z", "a"]),
        "./packages/x": LockedDep(repo_url="_local/x", name="x", package_type="claude_skill",
            source="local", local_path="./packages/x")},
        local_deployed_files=[".claude/rules/own.md"],
        local_deployed_file_hashes={".claude/rules/own.md": "sha256:3"})

def test_dump_shape_and_order():
    text = dump(_lock(), "0.1.0").decode()
    assert text.startswith("lockfile_version: '1'\napm_version: 0.1.0\ndependencies:\n- repo_url: _local/x\n")
    assert "generated_at" not in text and "deployments" not in text and "depth: 1" not in text
    base = text.index("repo_url: team/base"); child = text.index("repo_url: team/child")
    assert base < child
    block = text[base:child]
    order = [k for k in ("name:", "host:", "resolved_commit:", "resolved_ref:", "version:",
                         "package_type:", "deployed_files:", "deployed_file_hashes:", "content_hash:")]
    assert [block.index(k) for k in order] == sorted(block.index(k) for k in order)
    assert "skill_subset:\n  - a\n  - z\n" in text or "skill_subset:\n- a\n- z\n" in text

def test_round_trip(tmp_path):
    p = tmp_path / "apm.lock.yaml"; p.write_bytes(dump(_lock(), "0.1.0"))
    lock = read(p)
    assert set(lock.deps) == {"git.test/team/base", "git.test/team/child", "./packages/x"}
    assert lock.deps["git.test/team/child"].depth == 2
    assert lock.all_tracked()[".claude/rules/own.md"] == (".", "sha256:3")

@pytest.mark.parametrize("text", ["lockfile_version: 1\n", "lockfile_version: '3'\n",
                                  "lockfile_version: '1'\ndependencies:\n- name: x\n", "- a\n"])
def test_read_rejects(tmp_path, text):
    p = tmp_path / "apm.lock.yaml"; p.write_text(text)
    with pytest.raises(ApmError):
        read(p)

def test_read_ignores_unknown_keys(tmp_path):
    p = tmp_path / "apm.lock.yaml"
    p.write_text("lockfile_version: '1'\ndeployments: []\ndependencies:\n- repo_url: t/p\n  host: h.x\n  is_dev: true\n")
    assert "h.x/t/p" in read(p).deps
```

- [ ] **Step 3:** Run. Expected: FAIL. Implement. Run again. Expected: all pass.
- [ ] **Step 4:** Driver commits: `feat(lite): add lockfile subset and upstream-exact hashes`.

---

### Task 5: `git.py`

**Identity:** fast_worker · **Depends:** 1 · **Spec:** N1, N2; design §4.1 (ls_remote, fetch, fallback), §8 (git failures)

**Files:** Create `src/apm_lite/git.py`, `tests_lite/test_git.py`

**Interfaces (Produces):**

```python
class Git:
    def __init__(self, verbose: bool = False, echo: Callable[[str], None] = print) -> None: ...
    def ls_remote(self, url: str) -> dict[str, str]: ...
        # cached per url; {"HEAD": sha, "refs/heads/main": sha, "refs/tags/v1": peeled sha}
    def fetch(self, url: str, commit: str, dest: Path) -> None: ...
        # dest must not exist; shallow fetch by SHA into dest; on failure rmtree(dest) and
        # retry with "git clone <url> dest" + "git checkout <commit>"; then rmtree(dest/".git")
    def run(self, args: list[str], cwd: Path | None = None) -> str: ...
        # env adds GIT_TERMINAL_PROMPT=0; ApmError(f"git {' '.join(args)} failed", hint=stderr)
def resolve_ref(refs: dict[str, str], ref: str | None, url: str) -> tuple[str, bool]: ...
    # (commit, immutable): None -> (refs["HEAD"], False); 40-hex -> (ref, True);
    # refs/tags/<ref> (peeled "^{}" wins) -> True; refs/heads/<ref> -> False; else ApmError
```

In verbose mode, echo every git command as `git <args>`, and echo `fallback: full clone of <url>` when the fallback runs.

- [ ] **Step 1: Write failing tests**

```python
# tests_lite/test_git.py
import pytest
from apm_lite.errors import ApmError
from apm_lite.git import Git, resolve_ref

def test_ls_remote_and_resolve(git_server):
    r = git_server.repo("team/pkg")
    c1 = r.commit({"a.txt": "1"}); r.tag("v1")
    c2 = r.commit({"a.txt": "2"})
    refs = Git().ls_remote(r.https)
    assert resolve_ref(refs, "v1", r.https) == (c1, True)
    assert resolve_ref(refs, "main", r.https) == (c2, False)
    assert resolve_ref(refs, None, r.https) == (c2, False)
    assert resolve_ref(refs, c1, r.https) == (c1, True)
    with pytest.raises(ApmError):
        resolve_ref(refs, "nope", r.https)

def test_annotated_tag_peeled(git_server):
    r = git_server.repo("team/ann")
    c = r.commit({"a": "1"}); r.tag("v2", annotated=True)
    assert resolve_ref(Git().ls_remote(r.ssh), "v2", r.ssh) == (c, True)

def test_fetch_checks_out_commit_without_dot_git(git_server, tmp_path):
    r = git_server.repo("team/pkg")
    c1 = r.commit({"a.txt": "1"}); r.commit({"a.txt": "2"})
    dest = tmp_path / "out"
    Git().fetch(r.scp, c1, dest)
    assert (dest / "a.txt").read_text() == "1" and not (dest / ".git").exists()

def test_fetch_falls_back_to_clone_for_unadvertised_commit(git_server, tmp_path):
    r = git_server.repo("team/old")
    c1 = r.commit({"a.txt": "1"}); r.commit({"a.txt": "2"})
    r.set_config("uploadpack.allowReachableSHA1InWant", "false")
    r.set_config("uploadpack.allowAnySHA1InWant", "false")
    lines = []
    Git(verbose=True, echo=lines.append).fetch(r.https, c1, tmp_path / "out")
    assert (tmp_path / "out/a.txt").read_text() == "1"
    assert any(line.startswith("fallback: full clone") for line in lines)

def test_git_failure_carries_stderr(git_env):
    with pytest.raises(ApmError) as e:
        Git().ls_remote("https://git.test/missing/repo.git")
    assert "git ls-remote" in e.value.message and e.value.hint
```

- [ ] **Step 2:** Run. Expected: FAIL. Implement. Run again. Expected: all pass.
- [ ] **Step 3:** Driver commits: `feat(lite): add git wrapper with SHA fetch and clone fallback`.

---

### Task 6: `targets.py`, rules and skills planners

**Identity:** fast_worker · **Depends:** 1 · **Spec:** R12, R20, R21, R24; design §6.1–§6.3

**Files:** Create `src/apm_lite/targets.py`, `src/apm_lite/deploy/rules.py`, `src/apm_lite/deploy/skills.py`, `tests_lite/test_rules.py`, `tests_lite/test_skills.py`

**Interfaces (Produces):**

```python
# targets.py
@dataclass(frozen=True)
class Target:
    name: str                 # "claude" | "antigravity"
    root: str                 # ".claude" | ".agents"
    hook_file: str            # ".claude/settings.json" | ".agents/hooks.json"
    sidecar: str              # ".claude/apm-hooks.json" | ".agents/apm-hooks.json"
    allowed_events: frozenset[str] | None   # None = any except skipped_events
    skipped_events: frozenset[str]
    nested_events: frozenset[str] | None    # None = all nested
    @property
    def rules_dir(self) -> str: return f"{self.root}/rules"
    @property
    def skills_dir(self) -> str: return f"{self.root}/skills"
    @property
    def hooks_dir(self) -> str: return f"{self.root}/hooks"
TARGETS: dict[str, Target]   # values per design §6.1

# deploy/rules.py
def split_apply_to(value: object) -> list[str]: ...
    # list -> str items stripped; str -> split on commas outside {...} and [...]; strip; drop empty
def render_rule(text: str, target: str) -> str: ...   # design §6.2 table, exactly
def plan_rules(pkg: ResolvedPackage, target: Target) -> dict[str, PlannedFile]: ...
    # only for package_type == "apm_package" or the root project; rel path -> PlannedFile(mode 0o644)

# deploy/skills.py
def to_hyphen_case(name: str) -> str: ...        # verbatim port, upstream skill_integrator.py:99
def validate_skill_name(name: str) -> bool: ...  # r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", len<=64, no "--"
def plan_skills(pkg: ResolvedPackage, target: Target
               ) -> tuple[dict[str, PlannedFile], dict[str, str], list[str]]: ...
    # (files, skill_roots rel -> owner, warnings); modes: 0o755 if source has any x bit else 0o644
```

`render_rule` rules:
- Parse with `frontmatter.loads` after replacing `\r\n` with `\n`.
- Body: `post.content.lstrip("\n")`.
- Globs are JSON-quoted with `json.dumps(g, ensure_ascii=True)`.
- Claude, with globs: `"---\npaths:\n" + "".join(f"  - {q}\n" for q in quoted) + "---\n\n" + body`.
- Claude, without globs: `body`.
- Antigravity, with globs: `"---\ntrigger: glob\nglobs: " + json.dumps(",".join(globs)) + "\n---\n\n" + body`.
- Antigravity, without globs: `"---\ntrigger: always_on\n---\n\n" + body`.
- Ensure the output ends with exactly one `\n`.

`plan_skills` sources (design §6.3):
- `claude_skill` / `hybrid`: the whole `source_dir` → `<skills>/<to_hyphen_case(label)>/`.
- `skill_bundle`: `skills/<n>/` for `n` in `skills_selected`, or all.
- `apm_package` and the root: `.apm/skills/<n>/`.
- Skip `.git`, `.apm`, `__pycache__` and symlinks.
- `safe_name(n)` errors propagate. A name failing `validate_skill_name` gets a warning.
- A `SKILL.md` without a `description` frontmatter key gets a warning.
- `skill_roots` maps `<skills>/<n>` to the owner.

- [ ] **Step 1: Write failing tests**

```python
# tests_lite/test_rules.py
import pytest
from apm_lite.deploy.rules import render_rule, split_apply_to

SRC = '---\napplyTo: "**/*.py,**/*.{ts,tsx}"\ndescription: d\n---\n\n# Style\nUse black.\n'

def test_split_apply_to():
    assert split_apply_to("**/*.py, **/*.{ts,tsx},,") == ["**/*.py", "**/*.{ts,tsx}"]
    assert split_apply_to(["a/**", " b "]) == ["a/**", "b"]
    assert split_apply_to(None) == []

def test_claude_scoped():
    assert render_rule(SRC, "claude") == ('---\npaths:\n  - "**/*.py"\n  - "**/*.{ts,tsx}"\n---\n\n'
                                          "# Style\nUse black.\n")

def test_antigravity_scoped():
    assert render_rule(SRC, "antigravity") == ('---\ntrigger: glob\nglobs: "**/*.py,**/*.{ts,tsx}"\n'
                                               "---\n\n# Style\nUse black.\n")

def test_unscoped_both():
    src = "---\ndescription: d\n---\nAlways.\n"
    assert render_rule(src, "claude") == "Always.\n"
    assert render_rule(src, "antigravity") == "---\ntrigger: always_on\n---\n\nAlways.\n"

def test_crlf_source_same_as_lf():          # Review Focus 1
    assert render_rule(SRC.replace("\n", "\r\n"), "claude") == render_rule(SRC, "claude")
```

```python
# tests_lite/test_skills.py
import os
from apm_lite.deploy.skills import plan_skills, to_hyphen_case
from apm_lite.model import DepSpec, ResolvedPackage
from apm_lite.targets import TARGETS

def _pkg(tmp_path, ptype, selected=None, path="team/My_Skills"):
    spec = DepSpec(raw="x", kind="remote", key=f"git.test/{path}", repo_path=path)
    return ResolvedPackage(spec=spec, commit="a" * 40, ref_is_immutable=True, depth=1, parent_key=None,
        parent_repo_url=None, source_dir=tmp_path, package_type=ptype, name="n", version=None,
        content_hash=None, skills_selected=selected)

def _w(p, text="x", exe=False):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
    if exe: os.chmod(p, 0o755)

def test_hyphen_case():
    assert to_hyphen_case("My_Skills") == "my-skills" and to_hyphen_case("myRepo Name") == "my-repo-name"

def test_single_skill_whole_dir_normalised_name(tmp_path):   # Review Focus 3
    _w(tmp_path / "SKILL.md", "---\nname: s\ndescription: d\n---\nx")
    _w(tmp_path / "scripts/run.sh", exe=True); _w(tmp_path / ".git/HEAD"); _w(tmp_path / ".apm/x")
    files, roots, warnings = plan_skills(_pkg(tmp_path, "claude_skill"), TARGETS["claude"])
    assert set(files) == {".claude/skills/my-skills/SKILL.md", ".claude/skills/my-skills/scripts/run.sh"}
    assert files[".claude/skills/my-skills/scripts/run.sh"].mode == 0o755
    assert roots == {".claude/skills/my-skills": "git.test/team/My_Skills"} and warnings == []

def test_collection_subset_and_warnings(tmp_path):
    _w(tmp_path / "skills/a/SKILL.md", "---\nname: a\n---\nno description")
    _w(tmp_path / "skills/Bad_Name/SKILL.md", "---\ndescription: d\n---\n")
    _w(tmp_path / "skills/c/SKILL.md", "---\ndescription: d\n---\n")
    files, roots, warnings = plan_skills(_pkg(tmp_path, "skill_bundle", ("a", "Bad_Name")),
                                         TARGETS["antigravity"])
    assert set(roots) == {".agents/skills/a", ".agents/skills/Bad_Name"}
    assert len(warnings) == 2
```

- [ ] **Step 2:** Run. Expected: FAIL. Implement. Run again. Expected: all pass.
- [ ] **Step 3:** Driver commits: `feat(lite): add targets and rule/skill planners`.

---

### Task 7: `deploy/hooks.py`

**Identity:** deep_reasoner · **Depends:** 1 · **Spec:** R22–R26, R28; design §6.4

**Files:** Create `src/apm_lite/deploy/hooks.py`, `tests_lite/test_hooks.py`

**Interfaces (Produces):**

```python
def read_json(path: Path) -> dict: ...    # {} if missing; ApmError naming path if malformed / not a dict
def rewrite_command(command: str, *, target: str, label: str, hook_file_dir: Path,
                    hooks_root: Path, package_root: Path) -> str: ...
    # resolves ./x and ${CLAUDE_PLUGIN_ROOT}/x, ${PLUGIN_ROOT}/x (relative-script regex from
    # upstream hook_command_paths.py: r"(?<![.$])(\.[\\/][^\s\"']+)"); scripts must be regular
    # non-symlink files inside hooks_root (= package_root/.apm/hooks); claude ->
    # "\"${CLAUDE_PROJECT_DIR}/.claude/hooks/<label>/<rel>\"" (single quotes replaced, double kept);
    # antigravity -> ".agents/hooks/<label>/<rel>"; ApmError on $, ` or " in rel
def plan_hooks(pkg: ResolvedPackage, target: Target
              ) -> tuple[list[HookEntry], dict[str, PlannedFile], list[str]]: ...
    # (entries, script files, warnings); sources per design §6.4 selection; skipped events warn
def merge(native: dict, sidecar: dict, planned: list[HookEntry], target: Target,
          configured: bool) -> tuple[dict, dict | None]: ...
    # strip then (if configured) add; returns (new native, new sidecar or None to delete);
    # ApmError for unowned or edited "apm" container (antigravity)
```

Native shapes and ownership: design §6.4, exactly.
- Claude: nested `{"matcher": m or "*", "hooks": [handler...]}`.
- Antigravity: nested for `PreToolUse`/`PostToolUse`, flat handlers for the other allowed events.
- A handler is `{"type": "command", "command": ..., "timeout": t}`, with `timeout` only when given in the source.
- Comparison key for the multiset strip: `json.dumps(entry, sort_keys=True)`.
- `merge` must not reorder or touch keys other than `hooks` (Claude) or `apm` (Antigravity). Use plain `dict`; Python preserves insertion order.

- [ ] **Step 1: Write failing tests**

```python
# tests_lite/test_hooks.py
import json, os, pytest
from apm_lite.deploy.hooks import merge, plan_hooks, read_json, rewrite_command
from apm_lite.errors import ApmError
from apm_lite.model import DepSpec, HookEntry, ResolvedPackage
from apm_lite.targets import TARGETS

def _pkg(root):
    spec = DepSpec(raw="x", kind="remote", key="git.test/team/pkg", repo_path="team/pkg")
    return ResolvedPackage(spec=spec, commit="a" * 40, ref_is_immutable=True, depth=1, parent_key=None,
        parent_repo_url=None, source_dir=root, package_type="apm_package", name="pkg", version=None,
        content_hash=None)

def _hooks(root, name, doc, scripts=("check.sh",)):
    d = root / ".apm/hooks"; d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(doc))
    for s in scripts:
        (d / s).write_text("#!/bin/sh\n"); os.chmod(d / s, 0o755)
    return d

@pytest.mark.parametrize("cmd,expected", [
    ("./check.sh", '"${CLAUDE_PROJECT_DIR}/.claude/hooks/pkg/check.sh"'),
    ('bash "./check.sh" --x', 'bash "${CLAUDE_PROJECT_DIR}/.claude/hooks/pkg/check.sh" --x'),
    ("bash './check.sh'", 'bash "${CLAUDE_PROJECT_DIR}/.claude/hooks/pkg/check.sh"'),
    ("${CLAUDE_PLUGIN_ROOT}/.apm/hooks/check.sh", '"${CLAUDE_PROJECT_DIR}/.claude/hooks/pkg/check.sh"'),
    ("echo hi", "echo hi"),
])
def test_rewrite_claude(tmp_path, cmd, expected):
    d = _hooks(tmp_path, "h.json", {})
    assert rewrite_command(cmd, target="claude", label="pkg", hook_file_dir=d, hooks_root=d,
                           package_root=tmp_path) == expected

def test_rewrite_antigravity_relative(tmp_path):
    d = _hooks(tmp_path, "h.json", {})
    assert rewrite_command("./check.sh", target="antigravity", label="pkg", hook_file_dir=d,
                           hooks_root=d, package_root=tmp_path) == ".agents/hooks/pkg/check.sh"

def test_script_outside_hooks_rejected(tmp_path):
    d = _hooks(tmp_path, "h.json", {}); (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/x.sh").write_text("x")
    with pytest.raises(ApmError):
        rewrite_command("${CLAUDE_PLUGIN_ROOT}/scripts/x.sh", target="claude", label="pkg",
                        hook_file_dir=d, hooks_root=d, package_root=tmp_path)

def test_plan_selects_target_file_and_skips_events(tmp_path):
    _hooks(tmp_path, "hooks.json", {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "./check.sh", "timeout": 5}]}],
        "SessionStart": [{"command": "./check.sh"}], "PreInvocation": [{"command": "./check.sh"}]}})
    _hooks(tmp_path, "hooks-antigravity.json", {"PreToolUse": [{"matcher": "run_command", "hooks": [
        {"command": "./check.sh"}]}], "Stop": [{"command": "./check.sh"}]})
    c, c_files, c_warn = plan_hooks(_pkg(tmp_path), TARGETS["claude"])
    assert [e.event for e in c] == ["PreToolUse", "SessionStart"] and len(c_warn) == 1
    assert c[0].entry == {"matcher": "Bash", "hooks": [{"type": "command", "command":
        '"${CLAUDE_PROJECT_DIR}/.claude/hooks/pkg/check.sh"', "timeout": 5}]}
    assert c[1].entry["matcher"] == "*"
    assert c_files[".claude/hooks/pkg/check.sh"].mode == 0o755
    assert ".claude/hooks/pkg/hooks.json" not in c_files
    a, _, _ = plan_hooks(_pkg(tmp_path), TARGETS["antigravity"])
    assert [e.event for e in a] == ["PreToolUse", "Stop"]
    assert a[1].entry == {"type": "command", "command": ".agents/hooks/pkg/check.sh"}

def _entry(cmd, event="PreToolUse"):
    return HookEntry(event=event, entry={"matcher": "*", "hooks": [{"type": "command", "command": cmd}]},
                     marker="team/pkg", owner="git.test/team/pkg")

def test_claude_merge_keeps_user_content_and_duplicates():          # Review Focus 2
    user = {"matcher": "*", "hooks": [{"type": "command", "command": "a"}]}
    native = {"permissions": {"allow": ["Bash"]}, "hooks": {"PreToolUse": [user, dict(user)]},
              "model": "x"}
    sidecar = {"PreToolUse": [dict(user, _apm_source="team/pkg")]}
    new_native, new_sidecar = merge(native, sidecar, [_entry("b")], TARGETS["claude"], True)
    assert list(new_native) == ["permissions", "hooks", "model"]
    assert new_native["hooks"]["PreToolUse"] == [user, _entry("b").entry]
    assert new_sidecar == {"PreToolUse": [dict(_entry("b").entry, _apm_source="team/pkg")]}

def test_retired_target_strips_and_deletes_sidecar():
    e = _entry("b").entry
    native, sidecar = merge({"hooks": {"PreToolUse": [e]}}, {"PreToolUse": [dict(e, _apm_source="t")]},
                            [], TARGETS["claude"], False)
    assert native == {} and sidecar is None

def test_antigravity_container_ownership():
    t = TARGETS["antigravity"]
    with pytest.raises(ApmError):
        merge({"apm": {"Stop": []}}, {}, [], t, True)
    owned = {"type": "command", "command": "x"}
    with pytest.raises(ApmError):
        merge({"apm": {"Stop": [{"command": "edited"}]}}, {"Stop": [dict(owned, _apm_source="t")]}, [], t, True)
    new, side = merge({"mine": {"Stop": []}, "apm": {"Stop": [owned]}},
                      {"Stop": [dict(owned, _apm_source="t")]},
                      [HookEntry("Stop", {"type": "command", "command": "y"}, "t", "o")], t, True)
    assert new == {"mine": {"Stop": []}, "apm": {"Stop": [{"type": "command", "command": "y"}]}}

def test_read_json_malformed(tmp_path):
    (tmp_path / "s.json").write_text("{nope")
    with pytest.raises(ApmError):
        read_json(tmp_path / "s.json")
```

When `merge` leaves `hooks` empty for Claude, it drops the `hooks` key.

- [ ] **Step 2:** Run. Expected: FAIL. Implement. Run again. Expected: all pass.
- [ ] **Step 3:** Driver commits: `feat(lite): add hook planning, rewrite and ownership-safe merge`.

---

### Task 8: `packages.py`

**Identity:** deep_reasoner · **Depends:** 2, 3, 4, 5 · **Spec:** R7–R10, R18; design §4.1, §4.2, §4.4

**Files:** Create `src/apm_lite/packages.py`, `tests_lite/test_packages.py`

**Interfaces (Produces):**

```python
@dataclass(frozen=True)
class PackageInfo:
    package_type: str        # hybrid | claude_skill | skill_bundle | apm_package
    name: str
    version: str | None
    deps: list[DepSpec]
def install_dir(project: Path, repo_path: str) -> Path: ...   # project/"apm_modules"/repo_path
def check_overlaps(paths: dict[str, str]) -> None: ...
    # key -> repo_path; ApmError if two equal case-insensitively or one is an ancestor of another
def place(project: Path, spec: DepSpec, commit: str, locked: LockedDep | None, git: Git) -> tuple[Path, str]: ...
    # (source_dir, content_hash). Reuse when commit == locked.resolved_commit and the dir's
    # content_hash == locked.content_hash. Otherwise fetch into apm_modules/.tmp/<random>, hash, and
    # if commit == locked commit and hash != locked hash -> ApmError("content hash mismatch ...
    # tamper or force-push", hint=...). Then swap into place. All paths via fs.check_destination.
def inspect(source_dir: Path, spec: DepSpec, *, warn) -> PackageInfo: ...
    # design §4.2 table; loads apm.yml with manifest.load(is_root=False, declared_by=spec.key)
    # when present; validates skills: (missing name or wrong shape -> ApmError)
```

- [ ] **Step 1: Write failing tests**

```python
# tests_lite/test_packages.py
import pytest
from apm_lite.errors import ApmError
from apm_lite.git import Git
from apm_lite.lockfile import LockedDep, content_hash
from apm_lite.manifest import parse_entry
from apm_lite.packages import check_overlaps, inspect, place

APM = "name: base\nversion: '1.0.0'\n"

def test_place_fetches_then_reuses(git_server, project):
    r = git_server.repo("Team/Base"); c = r.commit({"apm.yml": APM, ".apm/instructions/a.instructions.md": "x"})
    spec = parse_entry(r.https, allow_local=False)
    src, h = place(project, spec, c, None, Git())
    assert src == project / "apm_modules/Team/Base" and h == content_hash(src)
    locked = LockedDep(repo_url="Team/Base", host="git.test", resolved_commit=c, content_hash=h)
    lines = []
    place(project, spec, c, locked, Git(verbose=True, echo=lines.append))
    assert lines == []                                  # reused, no git call

def test_place_refetches_modified_cache_and_detects_tamper(git_server, project):
    r = git_server.repo("team/base"); c = r.commit({"apm.yml": APM})
    spec = parse_entry(r.https, allow_local=False)
    src, h = place(project, spec, c, None, Git())
    (src / "apm.yml").write_text("hacked")
    locked = LockedDep(repo_url="team/base", host="git.test", resolved_commit=c, content_hash=h)
    src, h2 = place(project, spec, c, locked, Git())
    assert h2 == h and (src / "apm.yml").read_text() == APM
    bad = LockedDep(repo_url="team/base", host="git.test", resolved_commit=c, content_hash="sha256:0")
    with pytest.raises(ApmError, match="content hash"):
        place(project, spec, c, bad, Git())

def test_new_commit_records_new_hash(git_server, project):
    r = git_server.repo("team/base"); c1 = r.commit({"apm.yml": APM}); c2 = r.commit({"apm.yml": APM + "#2\n"})
    spec = parse_entry(r.https, allow_local=False)
    _, h1 = place(project, spec, c1, None, Git())
    locked = LockedDep(repo_url="team/base", host="git.test", resolved_commit=c1, content_hash=h1)
    _, h2 = place(project, spec, c2, locked, Git())
    assert h2 != h1

def test_overlaps():
    with pytest.raises(ApmError):
        check_overlaps({"a": "team/x", "b": "team/x/sub"})
    with pytest.raises(ApmError):
        check_overlaps({"a": "Team/X", "b": "team/x"})
    check_overlaps({"a": "team/x", "b": "team/xy"})

@pytest.mark.parametrize("files,ptype", [
    ({"SKILL.md": "s", "apm.yml": APM}, "hybrid"), ({"SKILL.md": "s"}, "claude_skill"),
    ({"skills/a/SKILL.md": "s"}, "skill_bundle"), ({"apm.yml": APM, ".apm/skills/a/SKILL.md": "s"}, "apm_package")])
def test_inspect_shapes(tmp_path, files, ptype):
    for rel, text in files.items():
        p = tmp_path / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
    spec = parse_entry("https://git.test/team/x.git", allow_local=False)
    assert inspect(tmp_path, spec, warn=lambda m: None).package_type == ptype

def test_inspect_rejects(tmp_path):
    spec = parse_entry("https://git.test/team/x.git", allow_local=False)
    with pytest.raises(ApmError, match="not an apm package"):
        inspect(tmp_path, spec, warn=lambda m: None)
    (tmp_path / "SKILL.md").write_text("s")
    sub = parse_entry({"git": "https://git.test/team/x.git", "skills": ["a"]}, allow_local=False)
    with pytest.raises(ApmError):
        inspect(tmp_path, sub, warn=lambda m: None)
```

- [ ] **Step 2:** Run. Expected: FAIL. Implement. Run again. Expected: all pass.
- [ ] **Step 3:** Driver commits: `feat(lite): add package placement, reuse and shape detection`.

---

### Task 9: `resolver.py`

**Identity:** deep_reasoner · **Depends:** 8 · **Spec:** R9, R13–R16, R19; design §4.3, §4.4

**Files:** Create `src/apm_lite/resolver.py`, `tests_lite/test_resolver.py`

**Interfaces (Produces):**

```python
Mode = Literal["install", "update", "frozen"]
def resolve(project: Path, manifest: Manifest, lock: Lock | None, *, mode: Mode,
            update_keys: set[str] | None, git: Git, warn: Callable[[str], None]
           ) -> list[ResolvedPackage]: ...
    # breadth-first order; local root deps resolved in place (commit None); calls
    # packages.check_overlaps before any place(); returns packages with skills_selected merged
```

Behaviour: design §4.3, all bullets.
- `update_keys=None` with `mode="update"` means all keys.
- Frozen errors use one message prefix: `--frozen: ` plus the R19 reason, with the hint `run 'apm install' to update apm.lock.yaml`.

- [ ] **Step 1: Write failing tests**

```python
# tests_lite/test_resolver.py
import pytest
from apm_lite.errors import ApmError
from apm_lite.git import Git
from apm_lite.lockfile import Lock, LockedDep
from apm_lite.manifest import load
from apm_lite.resolver import resolve

def _apm(name, deps=()):
    lines = "".join(f"    - {d}\n" for d in deps)
    return f"name: {name}\nversion: '1.0.0'\ndependencies:\n  apm:\n{lines}" if deps else \
           f"name: {name}\nversion: '1.0.0'\n"

def _root(project, deps):
    (project / "apm.yml").write_text(_apm("root", deps))
    return load(project / "apm.yml", is_root=True)

def _res(project, m, lock=None, mode="install", keys=None, warn=None):
    return resolve(project, m, lock, mode=mode, update_keys=keys, git=Git(),
                   warn=(warn or (lambda s: None)))

def _lock_of(pkgs):
    return Lock(deps={p.spec.key: LockedDep(repo_url=p.repo_url, host=p.spec.host,
        resolved_commit=p.commit, resolved_ref=p.spec.ref, depth=p.depth, content_hash=p.content_hash)
        for p in pkgs if p.spec.kind == "remote"})

def test_transitive_bfs_and_shallowest_wins(git_server, project):
    leaf = git_server.repo("t/leaf"); l1 = leaf.commit({"SKILL.md": "s"}); leaf.branch("dev")
    l2 = leaf.commit({"SKILL.md": "s2"})
    base = git_server.repo("t/base"); base.commit({"apm.yml": _apm("base", [leaf.https + "#dev"]), ".apm/x": ""})
    warnings = []
    pkgs = _res(project, _root(project, [base.ssh, leaf.scp]), warn=warnings.append)
    assert [p.spec.key for p in pkgs] == ["git.test/t/base", "git.test/t/leaf"]
    assert pkgs[1].commit == l2 and pkgs[1].depth == 1 and warnings

def test_immutable_conflict_errors(git_server, project):
    leaf = git_server.repo("t/leaf"); leaf.commit({"SKILL.md": "1"}); leaf.tag("v1")
    leaf.commit({"SKILL.md": "2"}); leaf.tag("v2")
    base = git_server.repo("t/base"); base.commit({"apm.yml": _apm("base", [leaf.https + "#v2"]), ".apm/x": ""})
    with pytest.raises(ApmError, match="t/leaf"):
        _res(project, _root(project, [leaf.https + "#v1", base.https]))

def test_pins_reused_and_moved_tag_no_false_conflict(git_server, project):
    leaf = git_server.repo("t/leaf"); c1 = leaf.commit({"SKILL.md": "1"}); leaf.tag("v1")
    m = _root(project, [leaf.https + "#v1", leaf.https + "#" + c1])
    pkgs = _res(project, m)
    leaf.commit({"SKILL.md": "2"}); leaf.tag("v1", force=True)      # tag moved upstream
    again = _res(project, m, lock=_lock_of(pkgs))
    assert again[0].commit == c1

def test_update_moves_branch_and_frozen_checks(git_server, project):
    leaf = git_server.repo("t/leaf"); c1 = leaf.commit({"SKILL.md": "1"})
    m = _root(project, [leaf.https + "#main"])
    lock = _lock_of(_res(project, m))
    c2 = leaf.commit({"SKILL.md": "2"})
    assert _res(project, m, lock=lock)[0].commit == c1
    assert _res(project, m, lock=lock, mode="update")[0].commit == c2
    assert _res(project, m, lock=lock, mode="frozen")[0].commit == c1
    with pytest.raises(ApmError, match="--frozen"):
        _res(project, _root(project, [leaf.https + "#v9"]), lock=lock, mode="frozen")
    with pytest.raises(ApmError, match="--frozen"):
        _res(project, _root(project, []), lock=lock, mode="frozen")     # stale entry
    with pytest.raises(ApmError, match="--frozen"):
        _res(project, m, lock=None, mode="frozen")

def test_subset_union_and_cycle(git_server, project):
    col = git_server.repo("t/col"); col.commit({"skills/a/SKILL.md": "a", "skills/b/SKILL.md": "b"})
    base = git_server.repo("t/base")
    base.commit({"apm.yml": _apm("base", ["{git: " + col.https + ", skills: [b]}", "https://git.test/t/base.git"]),
                 ".apm/x": ""})
    pkgs = _res(project, _root(project, ["{git: " + col.https + ", skills: [a]}", base.https]))
    col_pkg = next(p for p in pkgs if p.spec.key == "git.test/t/col")
    assert col_pkg.skills_selected == ("a", "b") and len(pkgs) == 2

def test_local_root_dep_with_remote_child(git_server, project):
    leaf = git_server.repo("t/leaf"); leaf.commit({"SKILL.md": "s"})
    local = project / "packages/x"; local.mkdir(parents=True)
    (local / "apm.yml").write_text(_apm("x", [leaf.https])); (local / ".apm").mkdir()
    pkgs = _res(project, _root(project, ["./packages/x"]))
    assert [p.spec.kind for p in pkgs] == ["local", "remote"] and pkgs[0].commit is None
    assert pkgs[1].parent_repo_url == "_local/x"
```

- [ ] **Step 2:** Run. Expected: FAIL. Implement. Run again. Expected: all pass.
- [ ] **Step 3:** Driver commits: `feat(lite): add breadth-first resolver with pins, conflicts and frozen checks`.

---

### Task 10: `reconcile.py`

**Identity:** deep_reasoner · **Depends:** 2, 4, 6, 7, 9 · **Spec:** R11, R25–R31, R37, R38; design §7

**Files:** Create `src/apm_lite/reconcile.py`, `tests_lite/test_reconcile.py`

**Interfaces (Produces):**

```python
@dataclass
class Result:
    lock: Lock
    written: list[str]
    deleted: list[str]
    kept_edited: list[str]
    warnings: list[str]
def root_package(project: Path, manifest: Manifest) -> ResolvedPackage: ...
    # depth 0, package_type "apm_package", spec kind "local", key ".", name manifest.name
def classify(abs_path: Path, planned: PlannedFile, recorded: str | None) -> str: ...
    # "noop" | "chmod" | "write" | "keep-edited" | "conflict"   (design §7.2 table)
def reconcile(project: Path, manifest: Manifest, *, mode: Mode = "install",
              update_keys: set[str] | None = None, git: Git, warn: Callable[[str], None]) -> Result: ...
    # steps 1-7 of design §7.1; steps 4-7 inside fs.UndoLog; returns the committed Lock
```

Details:
- Old lock: `lockfile.read(project/"apm.lock.yaml")`.
- New lock entries:
  - Each package's `deployed_files`: planned files plus skill roots for that owner, sorted. Its `deployed_file_hashes` come from the planned bytes, or from the old recorded hash for `keep-edited`.
  - Root output goes to `local_deployed_files` and `local_deployed_file_hashes`.
  - `skill_subset`: `sorted(skills_selected)` when not `None`.
- Commit: `UndoLog.write("apm.yml", manifest.dump())` only if `manifest.edited`; the lockfile only if its bytes differ.
- `.gitignore`: append the block only if no line equals `apm_modules/`.

- [ ] **Step 1: Write failing tests**

```python
# tests_lite/test_reconcile.py
import json, os, pytest
from apm_lite import reconcile as rc
from apm_lite.errors import ApmError
from apm_lite.git import Git
from apm_lite.manifest import load
from apm_lite.model import PlannedFile

RULE = "---\napplyTo: '**/*.py'\n---\nUse black.\n"
HOOKS = json.dumps({"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "./c.sh"}]}]})

def _pkg_repo(git_server, path="t/base", extra=None):
    r = git_server.repo(path)
    files = {"apm.yml": f"name: {path.split('/')[-1]}\nversion: '1.0.0'\n",
             ".apm/instructions/style.instructions.md": RULE, ".apm/hooks/hooks.json": HOOKS,
             ".apm/hooks/c.sh": "#!/bin/sh\n", **(extra or {})}
    c = r.commit(files, exec_files=(".apm/hooks/c.sh",))
    return r, c

def _proj(project, deps, targets="[claude]"):
    body = "".join(f"    - {d}\n" for d in deps)
    (project / "apm.yml").write_text(f"name: proj\nversion: '0.1.0'\ntargets: {targets}\n"
                                     f"dependencies:\n  apm:\n{body}" if deps else
                                     f"name: proj\nversion: '0.1.0'\ntargets: {targets}\n")
    return load(project / "apm.yml", is_root=True)

def _run(project, m, **kw):
    return rc.reconcile(project, m, git=Git(), warn=lambda s: None, **kw)

def _snapshot(project):
    out = {}
    for p in sorted(project.rglob("*")):
        rel = p.relative_to(project).as_posix()
        if rel.startswith("apm_modules") or not p.is_file():
            continue
        out[rel] = (p.read_bytes(), os.stat(p).st_mode & 0o777)
    return out

def test_install_deploys_and_reinstall_is_noop(git_server, project):
    r, c = _pkg_repo(git_server)
    m = _proj(project, [r.https])
    res = _run(project, m)
    assert (project / ".claude/rules/style.md").read_text().startswith("---\npaths:")
    assert os.stat(project / ".claude/hooks/base/c.sh").st_mode & 0o111
    settings = json.loads((project / ".claude/settings.json").read_text())
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert "apm_modules/" in (project / ".gitignore").read_text()
    before = {p: os.stat(project / p).st_mtime_ns for p in _snapshot(project)}
    res2 = _run(project, load(project / "apm.yml", is_root=True))
    assert res2.written == [] and res2.deleted == []
    assert {p: os.stat(project / p).st_mtime_ns for p in _snapshot(project)} == before

def test_classify_table(tmp_path):
    p = tmp_path / "f"; pf = PlannedFile(b"new", 0o644, "o")
    assert rc.classify(p, pf, None) == "write"
    p.write_bytes(b"new"); assert rc.classify(p, pf, None) == "noop"
    os.chmod(p, 0o755); assert rc.classify(p, pf, None) == "chmod"
    os.chmod(p, 0o644); p.write_bytes(b"old")
    from apm_lite.lockfile import file_hash
    assert rc.classify(p, pf, file_hash(b"old")) == "write"
    assert rc.classify(p, pf, file_hash(b"orig")) == "keep-edited"
    assert rc.classify(p, pf, None) == "conflict"

def test_user_edit_kept_on_reinstall_and_cleanup(git_server, project):
    r, _ = _pkg_repo(git_server)
    m = _proj(project, [r.https]); _run(project, m)
    rule = project / ".claude/rules/style.md"; rule.write_text("mine")
    res = _run(project, load(project / "apm.yml", is_root=True))
    assert rule.read_text() == "mine" and res.kept_edited == [".claude/rules/style.md"]
    _run(project, _proj(project, []))
    assert rule.read_text() == "mine"
    assert not (project / ".claude/hooks/base/c.sh").exists()

def test_untracked_conflict_and_package_collision(git_server, project):
    r, _ = _pkg_repo(git_server)
    (project / ".claude/rules").mkdir(parents=True)
    (project / ".claude/rules/style.md").write_text("hand written")
    with pytest.raises(ApmError, match="style.md"):
        _run(project, _proj(project, [r.https]))
    (project / ".claude/rules/style.md").unlink()
    r2, _ = _pkg_repo(git_server, "t/other")
    with pytest.raises(ApmError, match="t/other|t/base"):
        _run(project, _proj(project, [r.https, r2.https]))

def test_retire_antigravity(git_server, project):
    r, _ = _pkg_repo(git_server)
    (project / ".agents").mkdir()
    (project / ".agents/hooks.json").write_text(json.dumps({"mine": {"Stop": []}}))
    _run(project, _proj(project, [r.https], "[claude, antigravity]"))
    assert (project / ".agents/rules/style.md").exists()
    assert "apm" in json.loads((project / ".agents/hooks.json").read_text())
    _run(project, _proj(project, [r.https], "[claude]"))
    assert not (project / ".agents/rules/style.md").exists()
    assert json.loads((project / ".agents/hooks.json").read_text()) == {"mine": {"Stop": []}}
    assert not (project / ".agents/apm-hooks.json").exists()

@pytest.mark.parametrize("fail_at", ["apply", "hook_sidecar", "lockfile", "interrupt_cleanup"])
def test_fault_injection_restores_tree(git_server, project, monkeypatch, fail_at):
    r, _ = _pkg_repo(git_server)
    _run(project, _proj(project, [r.https]))
    r.commit({"apm.yml": "name: base\nversion: '2.0.0'\n",              # style rule dropped -> cleanup deletes
              ".apm/instructions/other.instructions.md": "new",          # new file -> apply writes
              ".apm/hooks/hooks.json": HOOKS.replace("Bash", "Edit"),     # hooks change -> sidecar rewritten
              ".apm/hooks/c.sh": "#!/bin/sh\n"}, exec_files=(".apm/hooks/c.sh",))
    snap = _snapshot(project)
    real_write = rc.UndoLog.write
    real_delete = rc.UndoLog.delete
    def write(self, rel, data, mode=0o644):
        if (fail_at == "apply" and rel.endswith("other.md")) or \
           (fail_at == "hook_sidecar" and rel.endswith("apm-hooks.json")) or \
           (fail_at == "lockfile" and rel == "apm.lock.yaml"):
            raise OSError("injected")
        return real_write(self, rel, data, mode)
    def delete(self, rel):
        if fail_at == "interrupt_cleanup":
            raise KeyboardInterrupt
        return real_delete(self, rel)
    monkeypatch.setattr(rc.UndoLog, "write", write)
    monkeypatch.setattr(rc.UndoLog, "delete", delete)
    with pytest.raises((OSError, KeyboardInterrupt)):
        _run(project, load(project / "apm.yml", is_root=True), mode="update")
    assert _snapshot(project) == snap
    monkeypatch.undo()
    _run(project, load(project / "apm.yml", is_root=True), mode="update")
    assert (project / ".claude/rules/other.md").exists()
```

`reconcile.py` must import `UndoLog` into its namespace (`from apm_lite.fs import UndoLog`) so the monkeypatch hits it. If the `hook_sidecar` case writes the sidecar before the native file, the test still passes, because it only asserts restoration.

- [ ] **Step 2:** Run. Expected: FAIL. Implement. Run again. Expected: all pass.
- [ ] **Step 3:** Driver commits: `feat(lite): add reconcile with edit-safe classification and undo log`.

---

### Task 11: `cli.py` and CLI flows

**Identity:** driver (inline) · **Depends:** 10 · **Spec:** R32–R36, Review Focus 4–5; design §8

**Files:** Create `src/apm_lite/cli.py`, `src/apm_lite/__main__.py` (`from apm_lite.cli import main; main()`), `tests_lite/test_cli_flows.py`

**Interfaces (Produces):** `main()`, a click group `cli` with `--version`, `-v/--verbose`, `init`, `install [DEP] [--skill NAME]... [--frozen]`, `uninstall DEP`, `update [DEP]`, and `deps list` / `deps tree`. All `ApmError` handling sits in one place: `main()` wraps `cli(standalone_mode=False)`, and click usage errors stay with click.

- [ ] **Step 1: Write failing flow tests** (CliRunner, `catch_exceptions=False`, with `os.chdir(project)` via `monkeypatch.chdir`):

```python
# tests_lite/test_cli_flows.py
from click.testing import CliRunner
from apm_lite.cli import cli

def run(*args):
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)   # click 8.2: stderr separate

def test_install_without_manifest(project, monkeypatch):           # Review Focus 4
    monkeypatch.chdir(project)
    res = run("install")
    assert res.exit_code == 1 and "apm init" in res.stderr and list(project.iterdir()) == []

def test_init_install_uninstall_flow(git_server, project, monkeypatch):
    monkeypatch.chdir(project)
    r = git_server.repo("t/skills"); r.commit({"skills/a/SKILL.md": "---\ndescription: d\n---\n",
                                                "skills/b/SKILL.md": "---\ndescription: d\n---\n"})
    assert run("init").exit_code == 0 and run("init").exit_code == 1
    assert run("install", r.scp, "--skill", "a").exit_code == 0
    assert (project / ".claude/skills/a/SKILL.md").exists() and not (project / ".claude/skills/b").exists()
    assert r.scp in (project / "apm.yml").read_text()
    out = run("deps", "list").stdout
    assert "git.test/t/skills" in out
    assert run("install", "--frozen").exit_code == 0
    assert run("uninstall", "git.test/t/skills").exit_code == 0
    assert not (project / ".claude/skills/a").exists()

def test_git_failure_leaves_tree(git_server, project, monkeypatch):  # Review Focus 5
    monkeypatch.chdir(project); run("init")
    before = (project / "apm.yml").read_bytes()
    res = run("install", "https://git.test/t/missing.git")
    assert res.exit_code == 1 and "git" in res.stderr
    assert (project / "apm.yml").read_bytes() == before and not (project / "apm.lock.yaml").exists()

def test_frozen_rejects_with_dep_and_drift(git_server, project, monkeypatch):
    monkeypatch.chdir(project); run("init")
    assert run("install", "--frozen", "https://git.test/t/x.git").exit_code != 0
    assert run("install", "--frozen").exit_code == 1    # no lockfile

def test_update_and_tree(git_server, project, monkeypatch):
    monkeypatch.chdir(project); run("init")
    leaf = git_server.repo("t/leaf"); leaf.commit({"SKILL.md": "---\ndescription: d\n---\n1"})
    base = git_server.repo("t/base")
    base.commit({"apm.yml": f"name: base\nversion: '1'\ndependencies:\n  apm:\n    - {leaf.https}\n", ".apm/x": ""})
    assert run("install", base.https).exit_code == 0
    leaf.commit({"SKILL.md": "---\ndescription: d\n---\n2"})
    assert run("update").exit_code == 0
    assert (project / ".claude/skills/leaf/SKILL.md").read_text().endswith("2")
    tree = run("deps", "tree").stdout
    assert tree.index("t/base") < tree.index("t/leaf")
```

- [ ] **Step 2:** Run. Expected: FAIL. Implement `cli.py`. Run `uv run pytest tests_lite -q`. Expected: the whole suite passes.
- [ ] **Step 3:** Commit: `feat(lite): add CLI commands and end-to-end flow tests`.

---

### Task 12: Upstream compatibility fixtures (N4)

**Identity:** driver (inline) · **Depends:** 11 · **Spec:** N4; design §9 (upstream compatibility)

**Files:** Create `tests_lite/compat/verify_upstream.py`, `tests_lite/fixtures/compat/` (`apm.yml`, `apm.lock.yaml`, `expected.json`), `tests_lite/test_compat.py`

- [ ] **Step 1: Generate the fixture project.** Make `tests_lite/compat/make_fixture.py` build the project with the `git_server` helpers and run apm-lite against it. Inputs:
  - every entry form: https with tag, ssh with port, scp, object with `skills`, root local;
  - a transitive dependency.
  Copy the resulting `apm.yml` and `apm.lock.yaml` into `tests_lite/fixtures/compat/`. Write `expected.json` with, per entry: apm-lite's ref, identity key and `apm_modules` path; and per deployed file: owner and hash.
- [ ] **Step 2: Verify with upstream.** `tests_lite/compat/verify_upstream.py` imports `apm_cli` and checks:
  - `APMPackage.from_apm_yml` on the fixture manifest;
  - for each entry, `DependencyReference.parse` (string) or `parse_from_dict` (dict) gives a `reference` equal to the expected ref, an alias of `None`, `get_unique_key()` equal to the expected key, and an install path (`models/dependency/materialization.py`) equal to the expected path;
  - `LockFile.read` succeeds, and the deployment ledger rebuilt from it (`core/deployment_ledger.py`) assigns each deployed file the expected owner.
  Exit 0 on success. Run `uv run python tests_lite/compat/verify_upstream.py`, and commit its output as `tests_lite/fixtures/compat/upstream_verified.txt`.
- [ ] **Step 3: Write `test_compat.py`:** regenerate the fixture project in `tmp_path` with apm-lite and assert that `apm.yml` and `apm.lock.yaml` match the committed fixtures byte for byte. Mask nothing: commits are deterministic, because the fixture sets `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`.
- [ ] **Step 4: Optional round trip:** add `test_upstream_round_trip`, skipped unless `APM_UPSTREAM_BIN` is set. Assertions per design §9.
- [ ] **Step 5:** `uv run pytest tests_lite/test_compat.py -q` passes. Commit: `test(lite): verify apm.yml and lockfile fixtures against upstream readers`.

---

### Task 13: Cut-over

**Identity:** fast_worker · **Depends:** 12 · **Spec:** N6, N7, N9; design §10

**Files:**
- Delete everything in design §10 "Deleted". Remove `tests_lite/compat/verify_upstream.py` and `make_hash_golden.py` too, since they import `apm_cli`. Keep their committed outputs.
- `git mv tests_lite tests` (after deleting the old `tests/`). Update imports and paths inside the tests (`tests_lite` → `tests`).
- Rewrite `pyproject.toml`:
  - `name = "apm-lite"`, `version = "0.1.0"`, `description = "Agent package manager for Claude Code and Antigravity on internal Git"`;
  - `dependencies = ["click>=8.0.0", "PyYAML>=6.0", "ruamel.yaml>=0.18.0", "python-frontmatter>=1.0.0"]`;
  - `[project.optional-dependencies] dev = ["pytest>=7.0", "ruff>=0.11"]`;
  - `[project.scripts] apm = "apm_lite.cli:main"`;
  - packages `include = ["apm_lite*"]`;
  - drop the `build` extra, `[tool.mutmut]`, pytest markers and `addopts`, and the `apm_cli` package-data;
  - keep `[tool.ruff]` with line-length 100 and target py310;
  - `[tool.pytest.ini_options] testpaths = ["tests"]`.
- `.gitignore`: remove the `AGENTS.md`, `PRD*.md`, `docs/superpowers/` and `.github/extensions/` lines, plus the other upstream-only entries. Keep the Python, venv, `.handoff/` and `apm_modules/` entries.
- Don't create or commit a `uv.lock` (Task 1 untracked it).
- `git add AGENTS.md`.

- [ ] **Step 1:** Run `uv sync --extra dev && uv run pytest -q`. Expected: every test from `tests_lite` passes under `tests/`.
- [ ] **Step 2:** Run `uv run ruff check src tests` (expected clean) and `git ls-files src/apm_cli docs/src scripts .github` (expected empty).
- [ ] **Step 3:** Run `uv tool install --force . && apm --version`. Expected: `0.1.0`.
- [ ] **Step 4:** Driver reviews the deletion list against design §10, then commits: `chore: cut over to apm-lite and remove upstream APM`.

---

### Task 14: Docs

**Identity:** fast_worker (driver runs the declawed pass) · **Depends:** 13 · **Spec:** N9; design §10

**Files:** Rewrite `README.md`, `CONTRIBUTING.md`, `PRINCIPLES.md`, `CHANGELOG.md`. Create `docs/reference.md` and `docs/authoring-packages.md`. Update `AGENTS.md` (drop the `src/apm_cli` line).

Content checklist:
- **README:** one-paragraph purpose; install (`git clone <internal mirror>`, `uv tool install .`); note that the `apm` command clashes with upstream APM on PATH; quickstart (`apm init`, `apm install https://…`, commit `apm.yml` + `apm.lock.yaml`); command table; link to the two docs.
- **reference.md:** the `apm.yml` fields and rejected forms (R1–R6); the dependency-forms table from design §3.2; the lockfile example from design §5.1; the target paths table from design §6.1; command flags; `--frozen` drift cases (R19); cleanup and edit rules (R25, R29).
- **authoring-packages.md:** the three package shapes; rule frontmatter (`applyTo`) and both target outputs; skill layout and naming; hooks (source grammar, `hooks-claude.json` / `hooks-antigravity.json`, scripts inside `.apm/hooks/`, the event support table); releasing by tag; a "test your package locally" section using a root local path.
- **CONTRIBUTING:** `uv sync --extra dev`, `uv run pytest`, `uv run ruff check src tests`; the module map from design §2. Explain that `uv.lock` is untracked, and show how to point uv at an internal PyPI mirror. Use a placeholder host such as `https://artifactory.example.com/api/pypi/pypi-remote/simple`, never a real one.
- **PRINCIPLES:** three principles (no invented frontmatter, portability, reliability over magic), two to three lines each.
- **CHANGELOG:** `## 0.1.0 (2026-10-10)`, first release of apm-lite, forked from APM v0.33.0.

- [ ] **Step 1:** Write the docs. Every path and field must match the code; spot-check them with `grep`.
- [ ] **Step 2:** Run `grep -rn "microsoft.github.io\|aka.ms\|brew install\|curl -sSL" README.md docs/*.md CONTRIBUTING.md`. Expected: empty.
- [ ] **Step 3:** Driver runs the declawed skill on each doc and fixes the findings.
- [ ] **Step 4:** Commit: `docs: add apm-lite README, reference and authoring guide`.

---

### Task 15: Final acceptance

**Identity:** driver (inline) · **Depends:** 14

- [ ] `uv run pytest -q` passes; `uv run ruff check src tests` is clean.
- [ ] Size target: `find src/apm_lite -name '*.py' | xargs grep -cv '^\s*$' | awk -F: '{s+=$2} END {print s}'` reports about 3000. Report the number; it isn't a gate.
- [ ] Network check: `grep -rn "requests\|urllib\|http.client\|socket" src/apm_lite` is empty (N1).
- [ ] Whole-branch review against the requirements, R1–R38 and N1–N9. Record any gaps as follow-up tasks.
- [ ] Antigravity end-to-end (driver, with `agy` logged in): run the built `apm install` in a sample project with `targets: [antigravity]`. Repeat Task 0's `agy -p` probe against the real output, and confirm the rules, skill and hooks load and that the hook log shows the project root as the working directory.
- [ ] Write the agent-handoff session receipt.
