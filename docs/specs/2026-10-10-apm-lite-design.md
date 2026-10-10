# apm-lite: design

- Date: 2026-10-10
- Status: draft, pending review
- Implements: [requirements](2026-10-10-apm-lite-requirements.md) (R1–R38, N1–N9)
- Upstream reference: APM v0.33.0, commit `280b8a7b` on this repo's `main`. File references like `deps/lockfile.py:351` are relative to upstream `src/apm_cli/` at that commit.

## 1. Approach

Rewrite a small core in a new package, `src/apm_lite/`, with upstream as the reference. Port specific behaviour (dependency URL parsing, rule frontmatter transforms, hook merge format, lockfile writer, content hash) by reading upstream and writing the minimum equivalent. Don't vendor upstream modules: they import marketplace, policy, auth and host backends.

Rejected alternatives:

- **Prune in place:** the remaining code would still be shaped for 10 targets, many hosts and two scopes. It has three target resolvers, a hook IR layer, and a Claude MCP adapter that subclasses Copilot's. We'd likely keep 40–60k lines, plus 608k lines of tests to triage.
- **Vendor upstream modules into a new skeleton:** the modules pull half the tree back in through imports.

The new package is built next to `src/apm_cli/` so upstream stays readable during the port. The old tree is deleted once the new one passes its tests (§10).

## 2. Architecture

```
cli ──> manifest.load(apm.yml) ──> resolver.resolve(manifest, old_lock, mode)
                                        │  uses git.py, packages.py
                                        v
                               [ResolvedPackage, ...]
                                        │
                     reconcile.plan(packages, targets) ──> Plan
                                        │  uses deploy/rules, skills, hooks
                                        v
                     reconcile.apply(plan, old_lock) ──> files on disk
                                        │
                          lockfile.save + manifest.save
```

| Module | Responsibility | Depends on |
|---|---|---|
| `cli.py` | Click commands and flags. Catches `ApmError`, prints `error:` / `hint:`, exits 1 | everything below |
| `errors.py` | `ApmError(message, hint=None)` | — |
| `manifest.py` | Load and validate `apm.yml`; parse dependency entries into `DepSpec`; add and remove entries with ruamel round-trip (comments kept) | ruamel.yaml |
| `git.py` | `ls_remote(url, ref)`, `fetch(url, commit, dest)`; runs `git` with `GIT_TERMINAL_PROMPT=0`, surfaces stderr | subprocess |
| `packages.py` | Put a package in place (clone for remote, read in place for local), classify its shape, list its primitives | git.py, manifest.py |
| `resolver.py` | Breadth-first walk, pins, conflicts, cycles, depth limit | packages.py |
| `lockfile.py` | Read and write the lockfile subset; `content_hash(dir)`, `file_hash(path)` | PyYAML |
| `targets.py` | The two `Target` definitions: root folder, rules dir, skills dir, hook file and format | — |
| `deploy/rules.py` | Rule files to write per target | python-frontmatter |
| `deploy/skills.py` | Skill directories to copy per target | — |
| `deploy/hooks.py` | Hook entries and scripts per target; merge into and strip from native hook files | — |
| `reconcile.py` | Gather the plans, check collisions, apply writes, clean up, build the new lockfile | all deploy modules, lockfile.py |

Each deploy module is pure: it takes packages and a target and returns a plan, and never writes. Only `reconcile.apply` touches the project tree, which keeps the deploy code unit-testable without a filesystem fixture.

Core types (dataclasses):

- `DepSpec`: raw entry, `kind` (`remote` | `local`), `url` (as written, without `#ref`), `host`, `port`, `repo_path`, `ref`, `skills`, `local_path`, `key` (identity).
- `ResolvedPackage`: `spec`, `commit`, `depth`, `parent_key`, `source_dir`, `shape`, `name`, `version`, `content_hash`, `deps: list[DepSpec]`.
- `Plan`: `files: dict[relpath, PlannedFile(content: bytes, owner: key)]`, `skill_dirs: dict[relpath, owner]`, `hooks: dict[target, list[HookEntry]]`, plus warnings.

## 3. Manifest and dependency grammar

### 3.1 `apm.yml`

- Required: `name` and `version`, both strings. An unquoted `version: 1.0` is a float and fails, matching upstream.
- `targets`: a list, subset of `{claude, antigravity}`. Absent means `[claude]`. `target:` (singular) is rejected with a hint to use `targets:`, because upstream treats using both keys as a conflict and apm-lite only writes `targets`.
- `dependencies.apm`: a list.
- Ignored with a warning: other top-level keys (`scripts`, `compilation`, `policy`, `marketplace`, `includes`, `devDependencies`, `lifecycle`, `allowExecutables`, `registries`, `$schema`) and other `dependencies` keys (`mcp`, `lsp`). Upstream keeps unknown keys, so leaving them in the file is safe.

### 3.2 Dependency entries

| Entry | `host` | `repo_path` | `ref` | `key` |
|---|---|---|---|---|
| `https://git.corp.example/team/pkg.git#v1.0` | `git.corp.example` | `team/pkg` | `v1.0` | `git.corp.example/team/pkg` |
| `ssh://git@git.corp.example:7999/team/pkg.git#main` | `git.corp.example` (port 7999) | `team/pkg` | `main` | `git.corp.example/team/pkg` |
| `git@git.corp.example:team/pkg.git` | `git.corp.example` | `team/pkg` | none | `git.corp.example/team/pkg` |
| `./packages/x` | — | — | — | `./packages/x` (anchored, see §4.4) |

- `.git` is stripped from `repo_path`. Case is kept.
- A trailing `#<ref>` is split off the URL string.
- The clone URL is the entry exactly as written minus `#ref`, so the transport (https vs ssh, port, user) is preserved.
- Object form: `{git: <url>, ref: <ref>, skills: [...]}` or `{path: <local>, skills: [...]}`. `ref:` overrides a `#ref` in `git:`.
- `skills:` must be a non-empty list of strings. It's deduped and sorted.
- Local paths start with `./`, `../`, `/` or `~/` and must end in a named directory.
- Rejected (R5): no scheme and no local prefix (shorthand or scheme-free host), `http://`, `git:` + `path:`, and the keys `alias`, `targets`, `type`, `allow_insecure`, `version`. Each error message shows the supported forms.
- Single-label hosts are fine (R6). No FQDN check.
- SCP form can't carry a port, so a numeric first path segment is rejected, as upstream does.

### 3.3 Writing entries (R33)

- `apm install <dep>` appends the string exactly as typed. Upstream rewrites to a scheme-free form, which loses ssh; upstream still reads apm-lite's form, so it stays inside the subset.
- With `--skill a --skill b`: writes `{git: <url as typed>, ref: <ref>, skills: [a, b]}`, or `{path: <local>, skills: [...]}`. If an entry for the same key exists, its `skills` are merged.
- `apm uninstall <dep>` removes the matching entry (§7).

## 4. Packages and resolution

### 4.1 Placement

- Remote packages go in `apm_modules/<repo_path>/`, upstream's layout. `.git` is removed after checkout.
- If two different keys map to the same `apm_modules/<repo_path>` (same path on two hosts), the resolver raises `ApmError` naming both.
- Local packages are read in place and never copied.
- `git.fetch(url, commit, dest)`:
  1. `git init` + `git fetch --depth 1 <url> <commit>` + `git checkout FETCH_HEAD`.
  2. If the server refuses to fetch by SHA, fall back to `git clone <url>` + `git checkout <commit>`.
- `git.ls_remote(url, ref)`:
  - With no ref, resolve `HEAD`.
  - A 40-hex SHA is returned as-is without a network call.
  - Otherwise try `refs/tags/<ref>` (preferring the peeled `^{}` commit), then `refs/heads/<ref>`. If neither exists, raise `ApmError` naming the URL and ref.

### 4.2 Shape detection (R8)

Checked in order on `source_dir`:

| Check | Shape | Lockfile `package_type` |
|---|---|---|
| root `SKILL.md` and `apm.yml` | single skill | `hybrid` |
| root `SKILL.md` | single skill | `claude_skill` |
| `skills/*/SKILL.md` | collection | `skill_bundle` |
| `apm.yml` and `.apm/` | APM package | `apm_package` |
| otherwise | error | — |

- `name`: from the package's `apm.yml` if present, else the last segment of `repo_path` or the local path.
- `version`: from `apm.yml` if present, else omitted.
- `deps`: `dependencies.apm` from the package's `apm.yml`, if present, whatever the shape (R11).
- An APM package may also contain `skills/*/` (upstream treats that as `skill_bundle`). apm-lite follows the table: `skills/*/SKILL.md` wins over `.apm/`.
- `skills:` on a collection that names a missing skill, or `skills:` on any other shape, is an `ApmError` (R10).
- The root project contributes only its `.apm/` primitives. A root `SKILL.md` or `skills/` in the project is ignored, matching upstream (`install/services.py:700-779`).

### 4.3 Walk (R13–R17)

```
queue = root deps at depth 1, in declared order
seen = {}
while queue:
    spec, depth, parent = queue.pop_front()
    if spec.key in seen:
        if spec.ref != seen[spec.key].ref: warn("<key>: using <winner ref> (requested <loser ref> by <parent>)")
        continue
    if depth > 50: error
    if spec is local and parent is remote: error (R16)
    commit = pinned_commit(spec)  or  git.ls_remote(spec.url, spec.ref)
    pkg = packages.place(spec, commit)       # skip fetch if dir's content_hash == locked content_hash
    verify content_hash against lock (R19)
    seen[spec.key] = pkg
    queue.extend(pkg.deps at depth+1, parent=spec.key)
```

- `pinned_commit(spec)` returns the locked commit if the locked `resolved_ref` equals `spec.ref`. It returns nothing in `update` mode for the keys being updated.
- Breadth-first order guarantees "shallowest wins". Declared order breaks ties.
- Cycles hit `seen` and stop.
- `--frozen` runs the R20 checks before the walk and uses only pinned commits. A missing pin is an error, not a network lookup.
- `ls-remote` is skipped when the ref is a 40-hex SHA.
- Fetching is sequential. (`ponytail:` parallel fetch if large trees make installs slow.)

### 4.4 Local dependencies

- Relative paths are resolved against the declaring package's `source_dir`. For the root, that's the project directory.
- The key is the path as written for root deps. For transitive deps it's the resolved path made relative to the project root.
- Local packages get no `content_hash` and aren't pin-checked, matching upstream.

## 5. Lockfile

### 5.1 Format

Written with `yaml.safe_dump(sort_keys=False)`. Keys in each dependency follow upstream's `LockedDependency.to_dict` order (`deps/lockfile.py:351-434`). Falsy fields are omitted.

```yaml
lockfile_version: '1'
apm_version: 0.1.0
dependencies:
- repo_url: team/pkg
  name: pkg
  host: git.corp.example
  port: 7999                      # only when non-default
  resolved_commit: 3f2a...        # 40 hex
  resolved_ref: v1.0
  version: 1.0.0                  # package's apm.yml version, if any
  depth: 2                        # only when != 1
  resolved_by: team/base          # parent's repo_url, only when depth > 1
  package_type: apm_package
  deployed_files:
  - .claude/rules/pkg-style.md
  - .claude/skills/review
  - .claude/skills/review/SKILL.md
  deployed_file_hashes:
    .claude/rules/pkg-style.md: sha256:...
    .claude/skills/review/SKILL.md: sha256:...
  content_hash: sha256:...
  skill_subset: [review]          # only when skills: was given
- repo_url: _local/x
  name: x
  package_type: claude_skill
  deployed_files: [...]
  deployed_file_hashes: {...}
  source: local
  local_path: ./packages/x
local_deployed_files: [...]       # root project's own .apm/ output
local_deployed_file_hashes: {...}
```

- `lockfile_version` is the string `'1'`. Upstream rejects the integer.
- Dependencies are sorted by `(depth, repo_url)`.
- No `generated_at` and no `deployments` block. Upstream rebuilds the ledger from `deployed_files`, and an empty `deployments: []` would make it drop tracking.
- `deployed_files` lists files plus skill directory roots (no trailing slash). `deployed_file_hashes` covers files only.
- Hook config files (`settings.json`, `hooks.json`) are not in `deployed_files`. Copied hook scripts are.
- Reading: unknown keys are ignored. `lockfile_version` other than `'1'` or `'2'` is an error. A dependency without `repo_url` is an error.
- The lockfile key for a remote dependency is `host/repo_url`, and for a local one `local_path`, matching upstream `models/dependency/identity.py:184-235`.

### 5.2 Hashes

- `content_hash(dir)` matches upstream `utils/content_hash.py:25-86` byte for byte. Walk the tree and skip symlinks, any path with a `.git` or `__pycache__` component, and a root-level `.apm-pin`. Sort by POSIX relative path. Feed `relpath.encode()` then the file bytes into one sha256. Output `sha256:<hex>`.
- `file_hash(path)` matches `utils/content_hash.py:89-153`: sha256 of the content, with CRLF converted to LF first when the file is valid UTF-8 without NUL bytes. Output `sha256:<hex>`.
- Golden values: before the old tree is deleted, compute both hashes with upstream's functions on fixture trees and freeze them in `tests/fixtures/hash_golden.json`.

## 6. Deployment

### 6.1 Targets

| | Claude | Antigravity |
|---|---|---|
| root | `.claude/` | `.agents/` |
| rules | `.claude/rules/<name>.md` | `.agents/rules/<name>.md` |
| skills | `.claude/skills/<name>/` | `.agents/skills/<name>/` |
| hook config | `.claude/settings.json`, key `hooks` | `.agents/hooks.json`, key `apm` |
| hook scripts | `.claude/hooks/<pkg>/` | `.agents/hooks/<pkg>/` |
| hook ownership sidecar | `.claude/apm-hooks.json` | `.agents/apm-hooks.json` |

Target folders are created when needed.

### 6.2 Rules (R21)

- Source: `.apm/instructions/*.instructions.md`, direct children only. `<name>` is the file name minus `.instructions.md`.
- `applyTo` is split on top-level commas, respecting `{a,b}` braces.
- Claude, with `applyTo`: `---\npaths:\n  - "<glob>"\n---\n\n<body>` (one list item per glob, each JSON-quoted).
- Claude, without `applyTo`: `<body>` only. Claude loads rules without `paths` unconditionally.
- Antigravity, with `applyTo`: `---\ntrigger: glob\nglobs: "<g1>,<g2>"\n---\n\n<body>`.
- Antigravity, without `applyTo`: `---\ntrigger: always_on\n---\n\n<body>`.
- All other source frontmatter keys are dropped.
- Antigravity differs from upstream in two places, both following the [Antigravity rules docs](https://www.antigravity.google/docs/rules):
  - `globs` is one comma-separated string where upstream writes a YAML list.
  - `always_on` is written explicitly, because the docs list `trigger` as required.

### 6.3 Skills (R22)

- Single-skill package: copy `source_dir` to `<skills>/<name>/`. `<name>` is the last segment of `repo_path` or of the local path.
- Collection: copy each `skills/<n>/` (or only those in `skills:`) to `<skills>/<n>/`.
- APM package: copy each `.apm/skills/<n>/` to `<skills>/<n>/`.
- Skipped when copying: `.git/`, `.apm/`, symlinks, `__pycache__/`.

### 6.4 Hooks (R23–R25)

Source selection per package and target:
1. Use `.apm/hooks/hooks-<target>.json` if present.
2. Otherwise use every other `.apm/hooks/*.json` (excluding all `hooks-<t>.json`).

Source grammar:
- The file is either `{"hooks": {Event: [entries]}}` or a naked `{Event: [entries]}`.
- An entry is nested `{matcher, hooks: [handler]}` or flat `handler`.
- A handler is `{type?: "command", command: str, timeout?: int}`. A handler without `command` is skipped with a warning.

Script handling:
- Tokens matching `./<path>` in a command, or `${CLAUDE_PLUGIN_ROOT}/<path>` (also `${PLUGIN_ROOT}`), resolve against the hook file's folder first, then the package root.
- The package's `.apm/hooks/` folder, minus `*.json`, is copied to the target's script folder. `<pkg>` is the last segment of `repo_path`, the local folder name, or the root project's `name`.
- Commands are rewritten:
  - Claude: `"${CLAUDE_PROJECT_DIR}/.claude/hooks/<pkg>/<rel>"`, quoted.
  - Antigravity: `.agents/hooks/<pkg>/<rel>`.

Native entry shapes:
- Claude: always nested, `{"matcher": <matcher or "*">, "hooks": [{"type": "command", "command": ..., "timeout": ...}]}`. Event names pass through unchanged.
- Antigravity:
  - `PreToolUse` and `PostToolUse` are nested, as for Claude.
  - `PreInvocation`, `PostInvocation` and `Stop` are flat lists of handlers.
  - Any other event is skipped with a warning (R25).

Merge and strip, rebuilt from scratch on every reconcile:
1. Read the native file (missing means `{}`) and the sidecar.
2. Claude: delete every entry under `hooks.<Event>` that equals a sidecar entry once `_apm_source` is removed. Drop events left empty. Antigravity: delete the `apm` key.
3. Add the planned entries in package order. Claude: append under `hooks.<Event>`. Antigravity: build `{"apm": {Event: [...]}}`.
4. Write the native file with `json.dumps(indent=2)` plus a newline. Write the sidecar as `{Event: [entry + {"_apm_source": <owner>}]}`, or delete it if there are no entries.

Ownership markers match upstream: `repo_url` for remote packages, `_local/<name>` for local packages and the root project.

## 7. Reconcile, cleanup, commands

### 7.1 Reconcile

`reconcile(manifest, old_lock, mode, only_target=None)`:

1. Resolve (§4.3).
2. Plan:
   - For every package plus the root project, and every target in scope, collect rule files, skill directories and hook entries.
   - Package order is the resolver's breadth-first order, then the root project last.
3. Collision check (R26), before any write:
   - Same path planned by two owners: error naming both.
   - Planned path exists on disk, isn't in `old_lock`, and has different content: error with a hint to move the file.
   - Identical content: adopt it silently.
4. Apply:
   - Write planned files, skipping writes whose content is unchanged.
   - Copy skill directories.
   - Merge hooks.
5. Cleanup (R28–R31): for each path in `old_lock` (dependencies plus `local_deployed_files`) that isn't planned:
   - Skip anything outside `.claude/` or `.agents/`, or that escapes the project after resolving symlinks.
   - If the recorded hash differs from the current file hash, keep the file and warn ("edited since install").
   - Otherwise delete it. Delete skill directory roots only once they're empty.
   - Delete `apm_modules/<repo_path>` for remote keys that are no longer in the tree, then prune empty parent folders inside `apm_modules/`.
6. Add `apm_modules/` to `.gitignore` if missing (R27), using upstream's block `\n# APM dependencies\napm_modules/\n`.
7. Save the lockfile, then `apm.yml` if it was edited.

With `--target <t>` (R34), steps 2–5 cover only `<t>`. Deployed paths under the other target's root are carried from `old_lock` into the new lockfile unchanged.

R38 is met because all validation and resolution errors happen in steps 1–3, before any write.
- A failure during step 4 (disk full, permissions) can leave deployed files half-written. In that case the lockfile and `apm.yml` are left as they were, so the next `apm install` redoes the work.
- `ponytail:` no transactional rollback of deployed files; add a staging directory if partial writes show up in practice.

### 7.2 Commands

| Command | Behaviour |
|---|---|
| `apm init` | R32. Writes `apm.yml` (name = folder name). Error if it exists. |
| `apm install` | `reconcile(mode=install)` |
| `apm install <dep> [--skill N]...` | Parse `<dep>`, add the entry in memory (§3.3), reconcile, save. A dep that's already declared without `--skill` is a no-op plus a reconcile. |
| `apm install --frozen` | `reconcile(mode=frozen)`. Can't be combined with `<dep>`. |
| `apm install --target <t>` | `reconcile(only_target=t)` |
| `apm uninstall <dep>` | Match against: the entry string as written, the `key`, or a local path. No match: error that lists the declared deps. Then remove the entry, reconcile, save. |
| `apm update [<dep>]` | `reconcile(mode=update, keys=<dep key or all>)` |
| `apm deps list` | From the lockfile: `name  source  ref  commit[:7]`, one per line, sorted. |
| `apm deps tree` | From the lockfile: the root's deps, children indented under `resolved_by`. |
| `apm --version` | Prints the apm-lite version. |
| `-v/--verbose` (global) | Echo each `git` command and the files written or deleted. |

## 8. Errors and output

- One exception type, `ApmError(message, hint)`. The CLI prints `error: <message>` and, if present, `hint: <hint>` to stderr, then exits 1. Anything else is a bug and shows a traceback.
- Git failures include the command that failed (without credentials; URLs are shown as written) and git's stderr. Git is run with `GIT_TERMINAL_PROMPT=0` so a missing credential fails fast instead of hanging.
- Warnings print as `warning: ...` to stderr. Output uses `click.echo`/`click.style` only.

## 9. Testing

- pytest, offline, no tokens (N8). Dev deps: pytest, ruff.
- **Fake internal Git server:** a `git_server` fixture creates bare repos under `tmp_path`, commits and tags package trees, and writes a temp `GIT_CONFIG_GLOBAL` with `url."file://<tmp>/".insteadOf` rules for `https://git.test/` and `ssh://git@git.test/`. This exercises the real URL, `ls-remote`, fetch-by-SHA and clone paths. A server setting `uploadpack.allowReachableSHA1InWant=false` covers the fallback.
- **Unit:**
  - dependency parsing: accept and reject tables, with cases ported from upstream `tests/unit` for URL parsing
  - shape detection
  - rule transforms for both targets
  - hook source parsing, command rewriting, and merge and strip, including preserving the user's own entries
  - lockfile round-trip and field order
  - `content_hash` and `file_hash` against the golden fixture
- **CLI flows** (click `CliRunner` in `tmp_path`):
  - `init` → `install <url>` → files land; the lockfile matches
  - reinstall is a no-op (no fetch, no writes)
  - `update` moves a branch pin
  - `uninstall` removes files and hook entries
  - `--frozen` passes when in sync and fails on drift
  - collision between two packages
  - an untracked existing file
  - a user-edited tracked file kept on cleanup
  - `--target antigravity` leaves Claude files tracked
  - a transitive local dep under a remote parent is rejected
  - a cycle is skipped
  - `http://` and shorthand are rejected
- **Upstream compatibility (optional):** when `APM_UPSTREAM_BIN` is set (e.g. Homebrew `apm` 0.33.0), copy a project apm-lite produced and run upstream `apm deps list` and `apm install --frozen --no-policy` against it, using the same `git_server` fixture. Assert exit 0 for both. Upstream may rewrite the lockfile (it adds its own `deployments` block), so content isn't compared. Skipped otherwise.

## 10. Repo layout after the cut

Kept or new:

```
src/apm_lite/            new package
tests/                   new suite (old tests/ deleted)
docs/reference.md        apm.yml subset, dependency forms, lockfile fields, target paths, commands
docs/authoring-packages.md  layouts, rules, skills, hooks, per-target hook files
docs/specs/              these specs
README.md                what it is, install from clone, quickstart, PATH clash note
CONTRIBUTING.md          uv sync, pytest, ruff
PRINCIPLES.md            trimmed: no invented frontmatter, portability, reliability over magic
CHANGELOG.md             restarted at 0.1.0
LICENSE                  kept (MIT requires the Microsoft copyright line)
pyproject.toml           dist apm-lite, entry apm = "apm_lite.cli:main", deps per N7
uv.lock                  regenerated
.gitignore, .editorconfig, .gitattributes, .pre-commit-config.yaml (ruff)
```

Deleted:
- `src/apm_cli/`, the old `tests/`
- `docs/` (everything except `docs/specs/` and the two new docs)
- `scripts/`, `build/`, `packages/`, `templates/`, `devcontainer/`
- `.github/`, `.apm/`, `.agents/`, `.vscode/`, `.gitmodules`
- `install.sh`, `install.ps1`
- `CONFORMANCE.md`, `CONFORMANCE.json`, `GOVERNANCE.md`, `MANIFESTO.md`, `SUPPORT.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `AUTHORS`, `NOTICE`, `Makefile`
- root `apm.yml` and `apm.lock.yaml`

Order:

1. Build `src/apm_lite/` and the new tests alongside the old tree.
2. Generate the hash golden file from upstream.
3. Switch `pyproject.toml`.
4. Delete the old material.
5. Write the docs.

## 11. Known ceilings and risks

- **Sequential fetch:** fine for tens of packages. Parallelise later if needed.
- **No global git cache:** each project clones its own packages. A warm `apm_modules/` with matching content hashes skips fetching.
- **Partial writes on I/O failure:** see §7.1.
- **`apm_modules/<repo_path>` ignores host,** so `a.corp/team/x` and `b.corp/team/x` collide. Upstream has the same layout. apm-lite detects it and errors rather than overwriting.
- **Antigravity rule frontmatter** (`globs` as a string, explicit `always_on`) follows the docs, not upstream. Verify against a real `agy` run early in implementation.
- **Antigravity hook commands are relative** (`.agents/hooks/<pkg>/x.sh`). Upstream does the same, which assumes `agy` runs hooks from the project root. Verify with `agy`.
- **Hook matchers are harness-specific;** authors who need both targets ship `hooks-claude.json` and `hooks-antigravity.json`. Documented in `docs/authoring-packages.md`.

## 12. Traceability

| Requirements | Section |
|---|---|
| R1–R7 | §3 |
| R8–R12 | §4.2, §6 |
| R13–R17 | §4.3, §4.4 |
| R18–R20 | §4.3, §5 |
| R21–R27 | §6, §7.1 |
| R28–R31 | §7.1 |
| R32–R38 | §7 |
| N1–N3 | §4.1, §8 |
| N4 | §3, §5, §9 |
| N5–N9 | §9, §10 |
