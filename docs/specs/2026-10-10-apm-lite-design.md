# apm-lite: design

- Date: 2026-10-10
- Status: revised after spec review rounds 1–2, consensus reached (see §13)
- Implements: [requirements](2026-10-10-apm-lite-requirements.md) (R1–R38, N1–N9)
- Upstream reference: APM v0.33.0, commit `280b8a7b` on this repo's `main`. File references like `deps/lockfile.py:351` are relative to upstream `src/apm_cli/` at that commit.

## 1. Approach

Rewrite a small core in a new package, `src/apm_lite/`, with upstream as the reference. Port specific behaviour (dependency URL parsing, ref classification, rule frontmatter transforms, hook merge format, lockfile writer, content hash) by reading upstream and writing the minimum equivalent. Don't vendor upstream modules: they import marketplace, policy, auth and host backends.

Rejected alternatives:

- **Prune in place:** the remaining code would still be shaped for 10 targets, many hosts and two scopes. It has three target resolvers, a hook IR layer, and a Claude MCP adapter that subclasses Copilot's. We'd likely keep 40–60k lines, plus 608k lines of tests to triage.
- **Vendor upstream modules into a new skeleton:** the modules pull half the tree back in through imports.

The new package is built next to `src/apm_cli/` so upstream stays readable during the port. The old tree is deleted once the new one passes its tests and the compatibility fixtures are verified (§10).

## 2. Architecture

```
cli ──> manifest.load(apm.yml) ──> resolver.resolve(manifest, old_lock, mode)
                                        │  uses git.py, packages.py (writes apm_modules/)
                                        v
                               [ResolvedPackage, ...]
                                        │
                     reconcile.plan(packages, targets, old_lock) ──> Plan
                                        │  uses deploy/rules, skills, hooks
                                        v
                     reconcile.apply(plan) ──> .claude/, .agents/
                                        │
                     reconcile.commit ──> apm.yml + apm.lock.yaml
```

| Module | Responsibility | Depends on |
|---|---|---|
| `cli.py` | Click commands and flags. Catches `ApmError`, prints `error:` / `hint:`, exits 1 | everything below |
| `errors.py` | `ApmError(message, hint=None)` | — |
| `fs.py` | `check_destination(path)` (containment and symlink check, §7.3), `atomic_write(path, bytes, mode)`, `safe_name(s)`, `UndoLog` (§7.1) | — |
| `manifest.py` | Load and validate `apm.yml`; parse dependency entries into `DepSpec`; add and remove entries with ruamel round-trip (comments kept) | ruamel.yaml |
| `refs.py` | Ref classification ported from upstream: SHA, semver-looking (rejected), literal | — |
| `git.py` | `ls_remote(url)` → refs map; `fetch(url, commit, dest)`; runs `git` with `GIT_TERMINAL_PROMPT=0`, surfaces stderr | subprocess |
| `packages.py` | Put a remote package into `apm_modules/` (reuse or fetch), classify a package's shape, list its primitives | git.py, fs.py |
| `resolver.py` | Breadth-first walk, pins, immutable-conflict check, subsets, cycles, depth, frozen checks | packages.py, refs.py |
| `lockfile.py` | Read and write the lockfile subset; `content_hash(dir)`, `file_hash(bytes)` | PyYAML |
| `targets.py` | The two `Target` definitions: root folder, rules dir, skills dir, hook file, sidecar, supported events | — |
| `deploy/rules.py` | Rule files to write per target | python-frontmatter |
| `deploy/skills.py` | Skill files to write per target | python-frontmatter |
| `deploy/hooks.py` | Hook entries and script files per target; merge into and strip from native hook files | — |
| `reconcile.py` | Gather plans, run collision and edit checks, apply writes, clean up, commit metadata | all deploy modules, lockfile.py, fs.py |

Two modules write to disk. `packages.py` writes only under `apm_modules/`. `reconcile.py` writes the deploy roots and the root metadata files. The deploy modules are pure: they take packages and a target and return planned files and hook entries, which keeps them unit-testable without a filesystem fixture.

Core types (dataclasses):

- `DepSpec`: raw entry, `kind` (`remote` | `local`), `url` (as written, without `#ref`), `host`, `port`, `repo_path`, `ref`, `skills`, `local_path`, `key` (identity), `declared_by` (key of the declaring package, or `None` for the root).
- `ResolvedPackage`: `spec`, `commit`, `ref_is_immutable`, `depth`, `parent_key`, `source_dir`, `shape`, `name`, `version`, `content_hash`, `skills_selected`, `deps: list[DepSpec]`.
- `PlannedFile`: `content: bytes`, `mode: int`, `owner: key`.
- `Plan`: `files: dict[relpath, PlannedFile]`, `skill_roots: dict[relpath, owner]`, `hooks: dict[target, list[HookEntry]]`, `warnings`.

## 3. Manifest and dependency grammar

### 3.1 `apm.yml`

- Required: `name` and `version`, both strings with non-whitespace content. An unquoted `version: 1.0` is a float and fails, matching upstream (`models/apm_package.py:470-477`).
- `targets`: a list, subset of `{claude, antigravity}`. Absent means `[claude]`. `target:` (singular) is rejected with a hint to use `targets:`.
- `dependencies.apm`: a list.
- Ignored with a warning and kept unchanged on save: other top-level keys and other `dependencies` keys. apm-lite never adds them. If such a key is invalid for upstream, it was already invalid before apm-lite touched the file.

### 3.2 Dependency entries

| Entry | `host` | `repo_path` | `ref` | `key` |
|---|---|---|---|---|
| `https://git.corp.example/team/pkg.git#v1.0` | `git.corp.example` | `team/pkg` | `v1.0` | `git.corp.example/team/pkg` |
| `ssh://git@git.corp.example:7999/team/pkg.git#main` | `git.corp.example` (port 7999) | `team/pkg` | `main` | `git.corp.example/team/pkg` |
| `git@git.corp.example:team/pkg.git` | `git.corp.example` | `team/pkg` | none | `git.corp.example/team/pkg` |
| `./packages/x` (root only) | — | — | — | `./packages/x` |

- `.git` is stripped from `repo_path`. Case is kept.
- A trailing `#<ref>` is split off the URL string.
- The clone URL is the entry exactly as written minus `#ref`, so the transport (https vs ssh, port, user) is preserved.
- Object form: `{git: <url>, ref: <ref>, skills: [...]}` or `{path: <local>, skills: [...]}`. `ref:` overrides a `#ref` in `git:`.
- `skills:` must be a non-empty list of strings. It's deduped and sorted.
- Validation, matching upstream where upstream is stricter:
  - HTTPS host must pass upstream's `is_valid_fqdn`, ported verbatim (`utils/github_host.py:287`, `:1009`). That rejects single labels, `git..corp` and `git_corp.example`.
  - `@` anywhere in `repo_path` or the ref is rejected. Upstream reads `#release@candidate` as ref `release` plus alias `candidate` (`models/dependency/reference.py:608`).
  - `repo_path` must have at least two segments (`models/dependency/reference.py:1606`).
  - Each `repo_path` segment matches `[A-Za-z0-9._-]+` and isn't `.` or `..`.
  - SCP form can't carry a port, so a numeric first path segment is rejected.
- Rejected (R5), each with a message showing the supported forms: no scheme and no local prefix (shorthand or scheme-free host), `http://`, `git:` + `path:`, and the keys `alias`, `targets`, `type`, `allow_insecure`, `version`.
- Local paths start with `./`, `../`, `/` or `~/` and must end in a named directory. They're only accepted in the root `apm.yml` (R10).

### 3.3 Refs (R6)

`refs.classify(ref)` returns `none`, `sha` (40 hex), or `literal`. A ref that upstream classifies as `semver` (`models/dependency/identity.py:269` `_is_valid_registry_semver_range`), or that upstream rejects as an invalid range (`:280` `_looks_like_invalid_semver_range`), raises `ApmError` with the hint "use the tag name, e.g. `v1.2.3`". Both predicates are ported verbatim, and their test cases with them, so apm-lite and upstream agree on which refs are literal.

### 3.4 Writing entries (R33)

- `apm install <dep>` appends the string exactly as typed. Upstream rewrites to a scheme-free form, which loses ssh; upstream still reads apm-lite's form, so it stays inside the subset.
- With `--skill a --skill b`: writes `{git: <url as typed>, ref: <ref>, skills: [a, b]}`, or `{path: <local>, skills: [...]}`. If an entry for the same key exists, its `skills` are merged.
- `apm uninstall <dep>` removes the matching entry (§8).

## 4. Packages and resolution

### 4.1 Placement (remote)

- Destination: `apm_modules/<repo_path>/`, upstream's layout. Before placement, the resolver checks every pair of package destinations. If one is equal to another (case-insensitive) or is an ancestor of another (for example `team/x` and `team/x/sub`), the install fails naming both packages.
- Reuse: if the commit to install equals the locked commit, and the destination exists with a `content_hash` equal to the locked one, the directory is used as-is with no fetch.
- Otherwise fetch fresh:
  1. Create an empty temp dir under `apm_modules/.tmp/`. Run `git init`, `git fetch --depth 1 <url> <commit>`, `git checkout FETCH_HEAD`.
  2. If the server refuses to fetch by SHA, delete that temp dir, create a new one, and run `git clone <url>` + `git checkout <commit>`. A commit not reachable from any advertised ref fails here with git's error.
  3. Remove `.git`, compute `content_hash`, then replace the destination with the temp dir (remove old, rename new).
- Hash check (R18): if the commit installed equals the locked commit, its `content_hash` must equal the locked one; otherwise the install fails with a tamper/force-push message. At a new commit, the new hash is recorded.
- `git.ls_remote(url)` runs once per URL per command and returns `{ref name: commit}`, keeping the peeled `^{}` commit for annotated tags. Ref lookup for a literal ref tries `refs/tags/<ref>` (immutable), then `refs/heads/<ref>` (mutable). No ref means `HEAD` (mutable). A 40-hex SHA needs no lookup and is immutable. A ref found in neither is an `ApmError` naming the URL and ref.

### 4.2 Shape detection (R7)

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
- `deps`: `dependencies.apm` from the package's `apm.yml`, if present, whatever the shape (R10). A local-path entry there is an `ApmError`.
- An APM package may also contain `skills/*/`. The table decides: `skills/*/SKILL.md` wins over `.apm/`.
- `skills:` naming a missing skill, or `skills:` on any shape other than a collection, is an `ApmError` (R9).
- The root project contributes only its `.apm/` primitives. A root `SKILL.md` or `skills/` in the project is ignored, matching upstream (`install/services.py:700-779`).

### 4.3 Walk (R13–R16, R19)

```
queue = root deps at depth 1, in declared order
seen = {}            # key -> ResolvedPackage
requests = {}        # key -> list of (spec, chain, commit_if_immutable)
while queue:
    spec, depth, chain = queue.pop_front()
    if depth > 50: error
    record_request(spec, chain)                  # immutable-conflict check, see below
    if spec.key in seen:
        merge_subset(seen[spec.key], spec)       # union; no subset means all
        if spec.ref != seen[spec.key].spec.ref: warn(winner ref, loser ref, chain)
        continue
    commit = choose_commit(spec, mode)
    pkg = packages.place(spec, commit)           # §4.1
    seen[spec.key] = pkg
    queue.extend(pkg.deps at depth+1, chain + [spec.key])
```

- **Order:** breadth first, so the shallowest request wins; declared order breaks ties. Cycles hit `seen` and stop.
- **`choose_commit`:**
  - `install`: if the lockfile has this key with a 40-hex `resolved_commit` and `resolved_ref == spec.ref`, use the locked commit. Otherwise resolve with `ls_remote`.
  - `update` (all, or the named key): always resolve with `ls_remote`. Other keys behave as `install`.
  - `frozen`: see below.
- **Immutable conflicts (R14):** each request with a tag or SHA ref is resolved to a commit. A SHA is its own commit. A tag whose ref equals the locked `resolved_ref` (and the mode isn't `update` for that key) takes the locked commit, so a moved tag doesn't cause a false conflict; upstream does the same (`deps/immutable_requirements.py:175`). Other tags use `ls_remote` (one call per URL, reused). If two immutable requests for the same key resolve to different commits, raise `ApmError` naming both request chains (`deps/immutable_requirements.py:85`).
- **Frozen (R19):**
  - Before the walk: the lockfile must exist.
  - For every remote request (winner or duplicate): the key must be in the lockfile with a 40-hex commit. A SHA ref must equal the locked commit. The winning request's ref must equal `resolved_ref`. Local requests are exempt.
  - The winner installs at the locked commit, with no `ls_remote` for it. Immutable-conflict checks still run; the only remote query frozen mode makes is `ls_remote` for a duplicate tag request whose ref differs from `resolved_ref`.
  - After the walk: any remote lockfile entry not reached, or a merged skill subset that differs from the locked `skill_subset`, is an error.
- **Subsets:** `skills_selected` is the union of every request's `skills:`. One request without `skills:` means all skills. The lockfile `skill_subset` records the union, or nothing when all are selected.
- **Fetching** is sequential. (`ponytail:` parallel fetch if large trees make installs slow.)

### 4.4 Local dependencies (root only)

- Relative paths are resolved against the project root. Two root entries resolving to the same real directory, or sharing a final folder name (both would be `_local/<name>`), are an `ApmError`.
- The key and lockfile `local_path` are the path as written in `apm.yml`.
- Local packages are read in place, never copied into `apm_modules/`, get no `content_hash`, and aren't pin-checked. This matches upstream.
- A local package may declare remote dependencies; those are walked normally. It may not declare local ones (R10).

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
  skill_subset: [review]          # only when a subset was selected
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
- Hook config files (`settings.json`, `hooks.json`) and sidecars are not in `deployed_files`. Copied hook scripts are.
- A tracked file the user edited (§7.2) keeps its previous recorded hash, so it stays flagged as edited.
- Reading:
  - unknown keys are ignored;
  - a `lockfile_version` other than `'1'` or `'2'` is an error;
  - a dependency without `repo_url` is an error;
  - a remote entry without a 40-hex `resolved_commit` is treated as unpinned (`install`), or as drift (`--frozen`).
- The lockfile key is `host/repo_url` for a remote dependency and `local_path` for a local one, matching upstream `models/dependency/identity.py:184-235`.

### 5.2 Hashes

- `content_hash(dir)` matches upstream `utils/content_hash.py:25-86` byte for byte:
  - Walk the tree, skipping symlinks, any path with a `.git` or `__pycache__` component, and a root-level `.apm-pin`.
  - Sort by POSIX relative path.
  - Feed `relpath.encode()`, then the file bytes, into one sha256.
  - Output `sha256:<hex>`.
- `file_hash(bytes)` matches `utils/content_hash.py:89-153`: sha256 of the content, with CRLF converted to LF first when the bytes are valid UTF-8 with no NUL. Output `sha256:<hex>`.
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
| events skipped (R24) | `PreInvocation`, `PostInvocation` | anything except `PreToolUse`, `PostToolUse`, `PreInvocation`, `PostInvocation`, `Stop` |

Target folders are created when needed, through `fs.check_destination`.

### 6.2 Rules (R20)

- Source: `.apm/instructions/*.instructions.md`, direct children only. `<name>` is the file name minus `.instructions.md`, and must pass `fs.safe_name`.
- `applyTo` is split on top-level commas, respecting `{a,b}` braces.

| `applyTo` | Claude output | Antigravity output |
|---|---|---|
| set | `---\npaths:\n  - "<glob>"\n---\n\n<body>`, one list item per glob, each JSON-quoted | `---\ntrigger: glob\nglobs: "<g1>,<g2>"\n---\n\n<body>` |
| not set | `<body>` only; Claude loads rules without `paths` unconditionally | `---\ntrigger: always_on\n---\n\n<body>` |

- All other source frontmatter keys are dropped.
- Antigravity output differs from upstream in two places, both following the [Antigravity rules docs](https://www.antigravity.google/docs/rules):
  - `globs` is one comma-separated string where upstream writes a YAML list.
  - `always_on` is written explicitly, because the docs list `trigger` as required.

### 6.3 Skills (R21, R12)

- Single-skill package: copy `source_dir` to `<skills>/<name>/`. `<name>` is `to_hyphen_case` of the last segment of `repo_path` or of the local path, ported from upstream `integration/skill_integrator.py:99`.
- Collection: copy each selected `skills/<n>/` to `<skills>/<n>/`.
- APM package: copy each `.apm/skills/<n>/` to `<skills>/<n>/`.
- For collection and APM-package skills, a `<n>` that fails upstream's `validate_skill_name` gets a warning and is used as-is. A `<n>` that fails `fs.safe_name` is an error.
- Skipped when copying: `.git/`, `.apm/`, symlinks, `__pycache__/`. File modes are kept.
- A `SKILL.md` without a `description` in its frontmatter gets a warning (R12).

### 6.4 Hooks (R22–R26)

Source selection, per package and target:
1. Use `.apm/hooks/hooks-<target>.json` if present.
2. Otherwise use every other `.apm/hooks/*.json`, excluding all `hooks-<t>.json`.

Source grammar:
- The file is either `{"hooks": {Event: [entries]}}` or a naked `{Event: [entries]}`.
- An entry is nested `{matcher, hooks: [handler]}` or flat `handler`.
- A handler is `{type?: "command", command: str, timeout?: int}`. A handler without `command` is skipped with a warning.
- Malformed source JSON is an `ApmError` naming the file.

Scripts:
- Script tokens in a command:
  - `./<path>` resolves against the hook file's folder, then against the package root.
  - `${CLAUDE_PLUGIN_ROOT}/<path>` or `${PLUGIN_ROOT}/<path>` resolves against the package root only.
- A resolved script must be a regular file inside `.apm/hooks/` (no symlink). Anything else is an `ApmError` with the hint to move the script, and anything it needs, into `.apm/hooks/`. This keeps each hook bundle self-contained, including any `package.json` that sets the module type for `.js` scripts.
- Copy: the whole `.apm/hooks/` folder, minus the JSON files used as hook sources, goes to `<target>/hooks/<pkg>/` with paths relative to `.apm/hooks/` and file modes kept. Each copied file goes through the normal checks (§7.2).
- `<pkg>` is the last segment of `repo_path`, the local folder name, or the root project's `name`, and must pass `fs.safe_name`.
- Command rewrite (ported from `integration/hook_integrator.py:576-599`):
  - Claude: `${CLAUDE_PROJECT_DIR}/.claude/hooks/<pkg>/<rel>`, always in double quotes. An unquoted or double-quoted token gets double quotes. A single-quoted token has its single quotes replaced by double quotes, because the shell doesn't expand variables inside single quotes. A path with `$`, a backtick or `"` is an `ApmError`.
  - Antigravity: `.agents/hooks/<pkg>/<rel>`.

Native entry shapes:
- Claude: always nested: `{"matcher": <matcher or "*">, "hooks": [{"type": "command", "command": ..., "timeout": ...}]}`. Event names pass through, except the skipped ones (§6.1).
- Antigravity:
  - `PreToolUse` and `PostToolUse` are nested, as for Claude.
  - `PreInvocation`, `PostInvocation` and `Stop` are flat lists of handlers.

Ownership and merge. This runs in the plan phase, so every failure happens before any write.
1. Read the native file and the sidecar for **both** targets, configured or not. A missing file means `{}`. Malformed JSON in either is an `ApmError` (R26).
2. Strip apm-lite's previous entries:
   - Claude: for each sidecar entry (with `_apm_source` removed), remove **one** equal entry from `hooks.<Event>` (a multiset match, as in `integration/hook_ownership.py:18`). Sidecar entries with no match were edited or removed by the user and are left alone. Drop events left empty.
   - Antigravity: if the native file has an `apm` key and there's no sidecar, raise `ApmError` (a user container named `apm`). If the `apm` container differs from the container the sidecar describes, raise `ApmError` telling the user to move their edits under another hook name. Otherwise remove the `apm` key.
3. For each configured target, add the planned entries in package order. Claude appends under `hooks.<Event>`. Antigravity builds `{"apm": {Event: [...]}}`.
4. Native file: written (atomically, `json.dumps(indent=2)` plus a newline) only if its bytes change. Sidecar: written as `{Event: [entry + {"_apm_source": <owner>}]}` only if its bytes change, or deleted when there are no entries. Both go through the undo log (§7.1), so an interrupted run never leaves a native file and its sidecar out of step. A target no longer in `targets:` ends with its entries stripped and its sidecar deleted (R28).

Ownership markers match upstream: `repo_url` for remote packages, `_local/<name>` for local packages and the root project.

## 7. Reconcile

### 7.1 Steps

`reconcile(manifest, old_lock, mode)`:

1. **Resolve** (§4.3). This may write under `apm_modules/` only.
2. **Plan:** for every package plus the root project, and every configured target, collect rule files, skill files, skill roots, hook entries and hook scripts. Package order is the resolver's breadth-first order, then the root project last. Hook files and sidecars are read and validated (§6.4).
3. **Check** (R25, R30), before any write in the project tree:
   - Every planned and to-be-deleted path passes `fs.check_destination` (§7.3).
   - Duplicate planned paths (case-insensitive) are detected while collecting, and raise `ApmError` naming both owners.
   - Each planned file is classified (§7.2).
4. **Apply:** write planned files classified `write` with `fs.atomic_write`. Write the hook files.
5. **Clean up** (R28, R29, R31): for each path in `old_lock` (dependencies plus `local_deployed_files`) that isn't planned:
   - if the current file hash differs from the recorded hash, keep the file and warn ("edited since install");
   - otherwise delete it;
   - delete skill directory roots once they're empty;
   - delete `apm_modules/<repo_path>` for remote keys no longer in the tree, then prune empty parent folders and `apm_modules/.tmp/`.
6. **Gitignore:** append upstream's block `\n# APM dependencies\napm_modules/\n` to `.gitignore` if no line equals `apm_modules/` (R27).
7. **Commit metadata:** atomically replace `apm.yml` (if edited) and then the lockfile. A file whose bytes are unchanged isn't written (R38).

**Undo log (R37).** Steps 4–7 run inside a `fs.UndoLog`. Before each write or delete in the project tree, the log records the path, its previous bytes (or "absent") and its mode. If any exception escapes steps 4–7, `KeyboardInterrupt` included, the log replays in reverse: previous bytes and modes are restored, and files that didn't exist are deleted. Then the original error is re-raised. If a restore itself fails, the error message lists the paths that weren't restored. `apm_modules/` changes from step 1 aren't logged, because it's a cache that the next run checks by commit and content hash (§4.1).

`ponytail:` the undo log lives in memory, so a hard kill (SIGKILL, power loss) during steps 4–7 can leave partial changes. Deployed files normally live in the project's git, so `git status` shows them. Add an on-disk journal if hard kills turn out to matter.

### 7.2 Per-file classification

For each planned path `p` with content `c`:

| Situation | Action |
|---|---|
| `p` exists with bytes equal to `c` and the same executable bit | `noop`; record `file_hash(c)`. Bytes equal to the plan count as managed output, whoever wrote them (R25) |
| `p` exists with bytes equal to `c`, executable bit differs | `chmod` to the planned mode |
| `p` missing | `write` |
| `p` tracked in `old_lock` (any owner) and current hash equals the recorded hash | `write` (also covers a path moving to a new owner) |
| `p` tracked and current hash differs from the recorded hash | `keep-edited`: don't write, warn, record the old hash |
| `p` exists, untracked, bytes differ | `ApmError`: the file isn't managed by apm; move or delete it |

A failed run is undone by the undo log (§7.1), so the classification never has to guess at half-finished output from an earlier run.

### 7.3 Containment

`fs.check_destination(p)` raises `ApmError` unless all of these hold:
- `p` is relative to the project root, with no `..` component;
- its first component is `.claude`, `.agents` or `apm_modules`, or `p` is one of `apm.yml`, `apm.lock.yaml`, `.gitignore`;
- no existing component of `p` below the project root is a symlink;
- the resolved real path stays inside the project root's real path.

Every write and delete goes through it, including those in `packages.py`. `fs.atomic_write` creates its temp file (`.<name>.<random>.tmp`) in the destination's own directory, so the destination's check covers it. Names that become path components (`repo_path` segments, rule names, skill names, `<pkg>`) must also pass `fs.safe_name`: `[A-Za-z0-9._-]+`, not `.` or `..`.

## 8. Commands, errors, output

| Command | Behaviour |
|---|---|
| `apm init` | R32. Writes `apm.yml` (name = folder name). Error if it exists. |
| `apm install` | `reconcile(mode=install)` |
| `apm install <dep> [--skill N]...` | Parse `<dep>`, add the entry in memory (§3.4), reconcile, commit. A dep that's already declared, given without `--skill`, just triggers a reconcile. |
| `apm install --frozen` | `reconcile(mode=frozen)`. Can't be combined with `<dep>`. |
| `apm uninstall <dep>` | Match against the entry string as written, the `key`, or a local path. No match: error listing the declared deps. Otherwise remove the entry, reconcile, commit. |
| `apm update [<dep>]` | `reconcile(mode=update, keys=<dep key or all>)` |
| `apm deps list` | From the lockfile: `name  source  ref  commit[:7]`, one per line, sorted. |
| `apm deps tree` | From the lockfile: root deps, with children indented under `resolved_by`. |
| `apm --version` | Prints the apm-lite version. |
| `-v/--verbose` (global) | Echo each `git` command, fallback fetches, and files written or deleted. |

- **Errors:** one exception type, `ApmError(message, hint)`. The CLI prints `error: <message>` and, if present, `hint: <hint>` to stderr, then exits 1. Anything else is a bug and shows a traceback.
- **Git failures:** the message includes the failing command (URLs as written) and git's stderr. Git runs with `GIT_TERMINAL_PROMPT=0`, which stops git's own terminal prompts. SSH and askpass helpers keep their own behaviour.
- **Warnings:** printed as `warning: ...` to stderr. All output uses `click.echo` and `click.style`.

## 9. Testing

pytest, offline, no tokens (N8). Dev deps: pytest, ruff.

**Fake internal Git server.** A `git_server` fixture:
- creates bare repos under `tmp_path`, commits and tags package trees;
- writes a temp `GIT_CONFIG_GLOBAL` with `url."file://<tmp>/".insteadOf` rules for `https://git.test/` and `ssh://git@git.test/`. This exercises the real URL, `ls-remote`, fetch-by-SHA and clone paths.
- Fallback test: set `uploadpack.allowReachableSHA1InWant=false` and `uploadpack.allowAnySHA1InWant=false` on a server repo, pin a historical commit that no ref advertises, and assert, through the verbose log, that the clone fallback ran and installed that commit.

**Unit tests:**
- dependency parsing: accept and reject tables, with cases ported from upstream URL-parsing tests, plus `#ref@alias`, `@` in paths, and FQDN edge cases (`git..corp`, `git_corp.example`, single label);
- ref classification, with cases ported from upstream semver tests;
- shape detection;
- rule transforms for both targets;
- hook source parsing and command rewrite: unquoted, double-quoted and single-quoted tokens, `$` in a path, and a script outside `.apm/hooks/` rejected;
- hook merge and strip: duplicate user entry kept, edited Antigravity container rejected, malformed JSON rejected;
- `fs.check_destination` with symlinked `.claude/` and `..` names;
- per-file classification table (§7.2), every row, including an executable-bit-only change;
- `UndoLog`: restore of overwritten, created and deleted files, and modes;
- lockfile round-trip and field order;
- `content_hash` and `file_hash` against the golden fixture.

**CLI flows** (click `CliRunner` in `tmp_path`):
- `init` → `install <url>`: files land, and the lockfile matches.
- Reinstall: no fetch, and no file is written: bytes and mtimes are unchanged, including hook configs, sidecars, `.gitignore` and the lockfile (R38).
- `update` moves a branch pin and deploys the new commit's content. Covers finding 5 of §13.
- A changed declared ref installs the new commit without a tamper error.
- A modified `apm_modules/<pkg>/` dir is re-fetched at the locked commit.
- An edited `content_hash` in the lockfile fails the install with the tamper message.
- A moved tag whose locked commit is still reachable installs the locked commit with no conflict. A force-push that makes the locked commit unreachable fails with git's error.
- A package update that only changes a script's executable bit updates the deployed mode.
- `uninstall` removes files and hook entries.
- `--frozen`: passes when in sync; fails for each R19 drift case.
- Collision between two packages; an untracked existing file; a user-edited tracked file kept on reinstall, on update, and on cleanup.
- Removing `antigravity` from `targets:` deletes its files and strips its hooks.
- Conflicting immutable refs fail; branch-vs-tag conflicts warn.
- Subset union across two requests.
- Rejected inputs: a local dep inside a package, `http://`, shorthand, a semver-looking ref.
- Cycle skipped; overlapping `apm_modules` paths rejected.
- Fault injection, run for `install`, `update` and `install <new-dep>`: raise (a) in the middle of applying files, (b) between writing a native hook file and its sidecar, (c) on the lockfile replace after `apm.yml` was replaced, and (d) `KeyboardInterrupt` during cleanup. For each, assert the project tree outside `apm_modules/` is byte- and mode-identical to a snapshot taken before the command, and that the next command succeeds.

**Upstream compatibility (N4):**
- *Mandatory, during implementation:* generate golden fixtures with apm-lite (an `apm.yml` with every supported entry form, and a lockfile with remote, transitive, subset and local entries). Verify them with upstream's own code while `src/apm_cli/` still exists, checking meaning, not just that loading succeeds:
  - `APMPackage.from_apm_yml` on the manifest, and `DependencyReference.parse` / `parse_from_dict` on each entry: assert upstream's ref, alias (none), identity key and `apm_modules` install path equal apm-lite's.
  - `LockFile.read` plus the deployment ledger rebuild: assert each deployed file's owner and hash equal apm-lite's.
  Commit the fixtures and the verification script's output, and assert in the suite that apm-lite's output equals the fixtures byte for byte.
- *Optional round trip:* when `APM_UPSTREAM_BIN` is set (e.g. Homebrew `apm` 0.33.0), copy an apm-lite project and run upstream `apm install --frozen --no-policy` against the same `git_server`. Assert:
  - exit 0;
  - the same packages installed at the same commits;
  - the user's own hook entries and untracked files are preserved;
  - Claude hook entry counts are unchanged;
  - selected skills only.
  Upstream may rewrite the lockfile and Antigravity rule frontmatter, so those aren't compared.

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
AGENTS.md                agent instructions
PRINCIPLES.md            trimmed: no invented frontmatter, portability, reliability over magic
CHANGELOG.md             restarted at 0.1.0
LICENSE                  kept (MIT requires the Microsoft copyright line)
pyproject.toml           dist apm-lite, entry apm = "apm_lite.cli:main", deps per N7
uv.lock                  not tracked (records index URLs; the repo is public)
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
2. Generate and verify the golden fixtures (hashes and N4 format) against upstream.
3. Switch `pyproject.toml`.
4. Delete the old material.
5. Write the docs.

## 11. Known ceilings and risks

- **Sequential fetch:** fine for tens of packages. Parallelise later if needed.
- **No global git cache:** each project clones its own packages. A warm `apm_modules/` at the locked commit skips fetching.
- **In-memory undo log:** errors and Ctrl-C roll back fully; a hard kill during deployment can leave partial changes (§7.1).
- **Antigravity rule frontmatter** (`globs` as a string, explicit `always_on`) follows the docs, not upstream. **Antigravity hook commands are relative** (`.agents/hooks/<pkg>/x.sh`), as upstream does, which assumes `agy` runs hooks from the project root. Plan Task 0 checks both with `agy` 1.3.3 before the rule and hook tasks are accepted; record its result here.
- **Hook matchers are harness-specific.** Authors who need both targets ship `hooks-claude.json` and `hooks-antigravity.json`. Documented in `docs/authoring-packages.md`.
- **Claude hook events aren't validated** beyond skipping Antigravity-only events. Claude's event list changes often, and an unknown event is ignored by Claude rather than breaking it.

## 12. Traceability

| Requirements | Section |
|---|---|
| R1–R6 | §3 |
| R7–R12 | §4.2, §4.4, §6.3 |
| R13–R16 | §4.1, §4.3 |
| R17–R19 | §4.1, §4.3, §5 |
| R20–R27 | §6, §7 |
| R28–R31 | §6.4, §7 |
| R32–R38 | §7.1, §8 |
| N1–N3 | §4.1, §7.3, §8 |
| N4 | §3, §5, §9 |
| N5–N9 | §9, §10 |

## 13. Review log

Round 1, `deep_reasoner` (codex), job `job-2026-10-10T15-20-50-95693-spec-review`. Dispositions:

| # | Finding (tag) | Disposition |
|---|---|---|
| 1 | Writes lack containment checks (blocking) | Accepted: §7.3 `fs.check_destination` on every write and delete, plus `safe_name` |
| 2 | Reinstall overwrites user-edited tracked files (blocking) | Accepted: §7.2 per-file classification |
| 3 | Hook stripping can delete user entries (blocking) | Accepted: multiset strip, `apm` container ownership check, JSON validated in the plan phase (§6.4) |
| 4 | R38 save order (blocking) | Accepted: atomic writes, metadata committed last, recovery via §7.2, fault-injection tests |
| 5 | Warm cache defeats update (blocking) | Accepted: reuse only at the locked commit; hash check only on replay (§4.1) |
| 6 | Manifests that break N4 (blocking) | Accepted, except one part. R6 single-label hosts dropped; dotted HTTPS host, two-segment path and non-empty `name`/`version` required. Declined: validating upstream-only fields, because apm-lite keeps them unchanged and never creates them (§3.1) |
| 7 | Ref semantics differ from upstream (blocking) | Accepted: semver-looking refs rejected (§3.3); conflicting immutable requests are an error (§4.3) |
| 8 | Local identity and anchoring (blocking) | Resolved by scope decision (Kevin): local paths in the root `apm.yml` only (§4.4) |
| 9 | `--target` ownership gaps (blocking) | Resolved by scope decision (Kevin): `--target` dropped. A target removed from `targets:` gets files and hooks cleaned up (§6.4, §7.1) |
| 10 | Overlapping and case-aliased paths (blocking) | Accepted: ancestor and case-insensitive checks for `apm_modules/` and deployments (§4.1, §7.1) |
| 11 | Hook script deployment incomplete (blocking) | Accepted: referenced scripts copied, modes kept, non-source JSON kept, quote-aware rewrite (§6.4) |
| 12 | Repeated subsets and frozen semantics (blocking) | Accepted: subset union; frozen checks every request and requires a full commit (§4.3) |
| 13 | Compatibility test too weak (blocking) | Accepted in part: mandatory golden fixtures verified with upstream's reader, and a stronger optional round trip (§9). A mandatory live upstream run isn't possible once the old tree is deleted |
| 14 | Skill and hook validation (advisory) | Accepted in part: single-skill names normalised, missing `description` warned, Antigravity-only events skipped for Claude. Declined: full Claude event validation (§11) |
| 15 | Fetch fallback and its fixture (advisory) | Accepted (§4.1, §9) |
| 16 | Unverifiable statements (advisory) | Accepted: success criteria, R38, frozen drift cases, network, auth and size wording made checkable |
| 17 | R30/R31 contradiction; "only apply writes" (advisory) | Accepted (R30, §2) |

Round 2, same session, job `job-2026-10-10T15-20-50-95693-spec-review-r2`. The reviewer withdrew all three declined points (6 part 4, 13, 14). New findings and dispositions:

| # | Finding (tag) | Disposition |
|---|---|---|
| 1 | Two metadata replaces still break R37; temp files outside the allowlist (blocking) | Accepted: undo log restores `apm.yml` if the lockfile replace fails; temp files sit beside their destination (§7.1, §7.3) |
| 2 | Hook config and sidecar can diverge after interruption (blocking) | Accepted: both go through the undo log (§6.4, §7.1) |
| 3 | Recovery assumed the next command rebuilds the same plan (blocking) | Accepted: replaced plan-based recovery with an in-memory undo log; hard kill documented as the limit (§7.1, §11) |
| 4 | Byte-equal row drops edit protection (blocking) | Accepted by explicit policy: bytes equal to the plan count as managed output (R25, §7.2) |
| 5 | Frozen vs local; tag resolution vs pins (blocking) | Accepted: commit checks remote-only; a tag matching the lockfile takes the locked commit; R19 states the one remote query frozen makes (§4.3) |
| 6 | `#ref@alias` and weak host check (blocking) | Accepted: `@` rejected in path and ref; upstream `is_valid_fqdn` ported; fixtures assert parsed meaning (§3.2, §9) |
| 7 | Executable bit ignored by classification (blocking) | Accepted (§7.2) |
| 8 | Single-quoted Claude paths (blocking) | Accepted: always double quotes (§6.4) |
| 9 | Hook resource context (advisory) | Accepted by narrowing: referenced scripts must live in `.apm/hooks/`, which is copied whole (R22, §6.4) |
| 10 | `_local/<name>` collisions (advisory) | Accepted: duplicate final folder names rejected (§4.4) |
| 11 | Reproducibility criterion inputs (advisory) | Accepted (requirements, success criteria) |
| 12 | Test details (advisory) | Accepted (§9) |

Status: consensus. No blocking finding declined. Round cap (2) reached, so no further automatic review.
