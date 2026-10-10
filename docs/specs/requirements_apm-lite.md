# apm-lite: requirements

- Date: 2026-10-10
- Status: revised after spec review rounds 1–2 (consensus)
- Companion: [design spec](2026-10-10-apm-lite-design.md)

## Purpose

apm-lite is a cut-down fork of APM (Agent Package Manager) v0.33.0, for corporate use only. A developer declares the agent context a project needs (rules, skills, hooks) in `apm.yml`. `apm install` pulls those packages from internal Git servers or local paths and deploys them for Claude Code and, optionally, Antigravity CLI. A lockfile makes the result reproducible.

The fork is owned outright. Nothing is merged back from upstream. Upstream's file formats are kept so packages and manifests can still move between the two tools.

## Users

- **Consumers:** developers in one company who install packages into their project repos.
- **Authors:** teams in the same company who publish packages as ordinary repos on the internal Git server (tag a commit to release).

## Success criteria

- Two developers with the same `apm.yml`, `apm.lock.yaml`, project `.apm/` content and local package content who run `apm install` get byte-identical managed files under `.claude/` and `.agents/`. This excludes files kept as user edits (R29). Files and hook entries the user added are left as they were.
- apm-lite opens no network connections itself. All network access goes through `git` subprocesses for URLs taken from `apm.yml` files, after git's own config (for example `insteadOf`) is applied.
- An `apm.yml` and `apm.lock.yaml` written by apm-lite match golden fixtures that upstream APM 0.33.0's reader accepts (§ design 9).
- Source size target: about 3k non-blank lines in `src/apm_lite/` (`grep -cv '^\s*$'`). A target, not an acceptance gate.

## Scope

### In

- Sources: internal Git over `https://`, `ssh://` and scp-style `git@host:path`; local paths in the root `apm.yml`.
- Targets: Claude Code (default) and Antigravity CLI (opt-in).
- Primitives: rules (APM "instructions"), skills, hooks.
- Package shapes: APM package (`apm.yml` + `.apm/`), bare skill repo (root `SKILL.md`), skill collection (`skills/*/SKILL.md`) with optional subset.
- Transitive remote dependencies, lockfile with pinned commits, content-hash check.
- Commands: `init`, `install` (with `--frozen`, `--skill`), `uninstall`, `update`, `deps list`, `deps tree`.
- Project scope only.
- macOS and Linux.

### Out

- Public GitHub/GitLab handling, `owner/repo` shorthand, scheme-free `host/path`, `http://`, single-label HTTPS hosts.
- Marketplace, plugins (`plugin.json`, Claude plugins, Copilot plugins), registries, MCP registry.
- MCP servers, LSP servers, agents, commands, prompts, chatmodes, canvas, workflows.
- Subdirectory packages (`git:` + `path:`), single-file packages, aliases, per-dependency `targets:`.
- Local paths declared by any package other than the root project.
- Semver ranges on refs.
- A `--target` override flag.
- Token handling and auth helpers (git does all auth).
- `compile`, `audit`, `policy`, `approve`/`deny`, `pack`/`publish`, `run`/`scripts`/`runtime`, `self-update`, `outdated`, `prune`, `view`, `find`, `doctor`, `config`, `cache`, `mcp`, `marketplace`, `lifecycle`, `experimental`.
- Org policy (`apm-policy.yml`), hidden-Unicode scan, executable approval.
- User scope (`-g`), Windows, every harness other than Claude Code and Antigravity.
- Public CI, release pipeline, Homebrew/PyPI/curl installers, docs website.

## Functional requirements

### Manifest

- **R1.** `apm.yml` must have `name` and `version`, each a string that isn't empty or whitespace. Optional: `description`, `author`, `license`, `targets`, `dependencies.apm`.
- **R2.** `targets` is a list containing only `claude` and/or `antigravity`. If absent, the target is `[claude]`. An empty list, an unknown name, or the `target:` singular key is an error with a hint.
- **R3.** Any other top-level key, and any `dependencies` key other than `apm`, is ignored with one warning per key and kept unchanged when apm-lite saves the file. This applies to the root manifest and to every dependency's manifest.
- **R4.** A dependency entry is one of:
  - an `https://`, `ssh://` or `git@host:path` URL, optionally ending in `#<ref>`;
  - a local path starting with `./`, `../`, `/` or `~/` (root `apm.yml` only; two local entries may not share a final folder name);
  - an object `{git: <url>, ref?: <ref>, skills?: [names]}`;
  - an object `{path: <local path>, skills?: [names]}` (root `apm.yml` only).
- **R5.** These are rejected with a message that names the supported forms: `owner/repo` shorthand, scheme-free `host/path`, `http://`, an HTTPS host that upstream's host check rejects (for example a single label or `git_corp.example`), a repo path with fewer than two segments, `@` in the repo path or ref (upstream reads it as an alias), `git:` with `path:`, and the object keys `alias`, `targets`, `type`, `allow_insecure`, `version`.
- **R6.** A ref is a branch, a tag, or a full 40-hex commit SHA. No ref means the remote's default branch. A ref that upstream would read as a semver range or bare version (for example `1.2.3`, `^1.2`, `1.x`) is rejected, with a hint to use the tag name (for example `v1.2.3`).

### Packages

- **R7.** A fetched package is classified in this order:
  1. root `SKILL.md`: the package is one skill;
  2. `skills/*/SKILL.md`: a skill collection;
  3. `apm.yml` with `.apm/`: an APM package;
  4. anything else: error "not an apm package".
- **R8.** An APM package provides rules from `.apm/instructions/*.instructions.md`, skills from `.apm/skills/*/` and hooks from `.apm/hooks/*.json`.
- **R9.** `skills: [...]` on a collection deploys only the named skills. A name with no match is an error. `skills:` on any other shape is an error. When several requests for the same package carry subsets, their union is deployed; a request without `skills:` means all skills.
- **R10.** A package's own `dependencies.apm` is followed regardless of shape. A local path in a package's manifest (local or remote) is an error.
- **R11.** The root project's own `.apm/` primitives deploy like a dependency's.
- **R12.** A skill without a `description` in its `SKILL.md` frontmatter deploys with a warning.

### Resolution

- **R13.** Remote dependencies resolve transitively, breadth first. Each package is identified by host plus repo path. A remote dependency declared with https and with ssh is the same package.
- **R14.** When the same package is requested at different refs:
  - if two of the requests are immutable (tag or SHA) and resolve to different commits, the install fails and names both request chains (a request whose ref matches the lockfile resolves to the locked commit);
  - otherwise the shallowest request wins (first declared on a tie), with a warning naming both refs.
- **R15.** Cycles are skipped. Depth beyond 50 is an error.
- **R16.** On `install`, a dependency whose lockfile entry has the same declared ref and a full commit is installed at the locked commit. Otherwise the ref is resolved against the remote.

### Lockfile

- **R17.** `apm install` writes `apm.lock.yaml` recording, per dependency: identity, host, resolved ref, resolved commit, depth, parent, package type, skill subset, deployed files with hashes, and a content hash (remote only).
- **R18.** When a remote package is installed at its locked commit, its content hash must match the lockfile; a mismatch fails the install with a tamper/force-push message. A package installed at a new commit gets a new recorded hash.
- **R19.** `apm install --frozen` fails on any drift between `apm.yml` and the lockfile:
  - the lockfile is missing;
  - a remote request (direct or transitive) has no lockfile entry, or its entry has no full 40-hex commit;
  - a winning request's declared ref differs from the locked `resolved_ref`;
  - a SHA request differs from the locked commit;
  - the lockfile has a remote entry that the dependency graph no longer reaches;
  - a skill subset differs from the locked `skill_subset`.
  Otherwise it installs exactly the locked commits. It queries a remote only to check that a duplicate tag request with a different ref points at the locked commit. Local dependencies are exempt from commit checks.

### Deployment

- **R20.** Rules deploy to `.claude/rules/<name>.md` and/or `.agents/rules/<name>.md`, with `applyTo` translated to each target's native frontmatter. A rule without `applyTo` is always active on both targets.
- **R21.** Skills deploy to `.claude/skills/<name>/` and/or `.agents/skills/<name>/`. `.git`, `.apm/` and symlinks are not copied. A single-skill package's name is normalised to lowercase hyphen-case, as upstream does.
- **R22.** Hooks merge into `.claude/settings.json` (key `hooks`) and/or `.agents/hooks.json` (entry `apm`), leaving everything else in those files untouched. A package's `.apm/hooks/` folder is copied into the target folder with file modes kept, and commands are rewritten to the copies. A command that references a script outside `.apm/hooks/` is an error.
- **R23.** A package may ship `hooks-claude.json` and/or `hooks-antigravity.json`. If one exists, it replaces the package's other hook files for that target.
- **R24.** A hook event that a target doesn't support is skipped for that target with a warning: for Antigravity, anything other than `PreToolUse`, `PostToolUse`, `PreInvocation`, `PostInvocation`, `Stop`; for Claude, `PreInvocation` and `PostInvocation`.
- **R25.** Before writing a path, apm-lite checks it:
  - two sources planning the same path (compared case-insensitively) is an error naming both;
  - an existing untracked file with different content is an error telling the user to move it; identical content is adopted;
  - an existing tracked file whose content no longer matches its recorded hash was edited by the user: it is kept, with a warning, and not overwritten;
  - a file whose bytes and executable bit already equal the planned output counts as managed output, whoever wrote it.
- **R26.** Malformed JSON in `.claude/settings.json`, `.agents/hooks.json` or an ownership sidecar is an error before any file is written. So is an `apm` entry in `.agents/hooks.json` that apm-lite doesn't own.
- **R27.** `apm_modules/` is added to `.gitignore` if missing.

### Cleanup

- **R28.** Every install, update and uninstall removes files the previous lockfile tracked that are no longer planned, including all files of a target removed from `targets:` and its hook entries.
- **R29.** A tracked file whose current hash differs from the recorded hash is kept, with a warning.
- **R30.** apm-lite only writes or deletes inside the project, under `.claude/`, `.agents/` and `apm_modules/`, plus `apm.yml`, `apm.lock.yaml` and `.gitignore` at the root. A destination whose path passes through a symlink, or resolves outside those roots, is an error.
- **R31.** `apm_modules/` entries for packages no longer in the tree are deleted.

### Commands

- **R32.** `apm init` writes a starter `apm.yml` (name from the folder, `version: "0.1.0"`, `targets: [claude]`, empty `dependencies.apm`). It refuses if `apm.yml` exists.
- **R33.** `apm install <dep> [--skill NAME ...]` adds the dependency to `apm.yml` exactly as typed (keeping its transport), then installs. `--skill` writes the object form with `skills:`.
- **R34.** `apm uninstall <dep>` removes the entry from `apm.yml` and reconciles. `<dep>` matches the entry as written, its identity (`host/path`), or a local path.
- **R35.** `apm update [<dep>]` ignores lockfile pins (all, or only that dependency's) and reconciles.
- **R36.** `apm deps list` prints each locked package with source, ref and short commit. `apm deps tree` prints the dependency tree.
- **R37.** A command that fails with an error, or is interrupted with Ctrl-C, restores every file it changed in the project tree (deployed files, hook configs, sidecars, `.gitignore`, `apm.yml`, `apm.lock.yaml`) to its previous bytes and mode. `apm_modules/` is a cache and isn't restored. A hard kill (SIGKILL, power loss) during deployment can leave partial changes.
- **R38.** Running `apm install` twice in a row with no changes performs no fetches and writes no files.

## Non-functional requirements

- **N1. Network:** apm-lite has no HTTP client, update check or telemetry. The only network access is `git` subprocesses for URLs from `apm.yml` files (git, SSH and credential helpers may apply their own configuration).
- **N2. Auth:** apm-lite reads no tokens or credential env vars. Whatever works for `git clone <url>` on the machine works for apm-lite. Git terminal prompts are disabled so a missing credential fails instead of waiting for input.
- **N3. Writes:** see R30. apm-lite itself writes nothing in `$HOME` (git and its helpers may).
- **N4. Compatibility:** files apm-lite writes are a strict subset of upstream APM 0.33.0 formats, with the same field names and meanings. Golden fixtures of apm-lite output are verified against upstream's reader before the upstream tree is deleted.
- **N5. Platform:** macOS and Linux, Python 3.10+, `git` on PATH.
- **N6. Install:** clone the repo, then `uv tool install .` or `pipx install .`. Distribution name `apm-lite`, command `apm`.
- **N7. Dependencies:** runtime deps limited to click, PyYAML, ruamel.yaml and python-frontmatter.
- **N8. Tests:** the full suite runs offline with no tokens.
- **N9. Docs:** `README.md`, `docs/user-guide/reference.md`, `docs/user-guide/authoring-packages.md`, `CONTRIBUTING.md`, `PRINCIPLES.md`, `CHANGELOG.md`, `AGENTS.md`, plus `docs/specs/`. Nothing else.

## Assumptions

- Internal Git servers allow shallow clones. Fetching by commit SHA may not be allowed; a full-clone fallback covers commits reachable from advertised refs.
- Teams release packages by tagging commits.
- Antigravity CLI reads `.agents/rules/*.md`, `.agents/skills/*/SKILL.md` and `.agents/hooks.json` at project scope, as its docs state ([rules](https://www.antigravity.google/docs/rules), [skills](https://www.antigravity.google/docs/skills), [hooks](https://www.antigravity.google/docs/hooks)).
- The `apm` command clashing with an upstream `apm` on PATH is acceptable; the README documents it.
- If a team later switches a project to upstream APM, upstream rewrites deployed Antigravity rules into its own frontmatter style. That's upstream behaviour, not an apm-lite compatibility break.
