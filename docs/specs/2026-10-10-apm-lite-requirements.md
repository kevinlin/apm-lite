# apm-lite: requirements

- Date: 2026-10-10
- Status: draft, pending review
- Companion: [design spec](2026-10-10-apm-lite-design.md)

## Purpose

apm-lite is a cut-down fork of APM (Agent Package Manager) v0.33.0, for corporate use only. A developer declares the agent context a project needs (rules, skills, hooks) in `apm.yml`. `apm install` pulls those packages from internal Git servers or local paths and deploys them for Claude Code and, optionally, Antigravity CLI. A lockfile makes the result reproducible.

The fork is owned outright. Nothing is merged back from upstream. Upstream's file formats are kept so packages and manifests can still move between the two tools.

## Users

- **Consumers:** developers in one company who install packages into their project repos.
- **Authors:** teams in the same company who publish packages as ordinary repos on the internal Git server (tag a commit to release).

## Success criteria

- A developer clones a project, runs `apm install`, and gets the same `.claude/` (and `.agents/`) setup as everyone else on the same lockfile.
- apm-lite talks to nothing except the Git URLs written in `apm.yml` files.
- An `apm.yml` and `apm.lock.yaml` written by apm-lite are accepted by upstream APM 0.33.0.
- The codebase is small enough for one person to read in a day (target: about 3k lines of source).

## Scope

### In

- Sources: internal Git over `https://`, `ssh://` and scp-style `git@host:path`; local paths.
- Targets: Claude Code (default) and Antigravity CLI (opt-in).
- Primitives: rules (APM "instructions"), skills, hooks.
- Package shapes: APM package (`apm.yml` + `.apm/`), bare skill repo (root `SKILL.md`), skill collection (`skills/*/SKILL.md`) with optional subset.
- Transitive dependencies, lockfile with pinned commits, content-hash check.
- Commands: `init`, `install` (with `--frozen`, `--target`, `--skill`), `uninstall`, `update`, `deps list`, `deps tree`.
- Project scope only.
- macOS and Linux.

### Out

- Public GitHub/GitLab handling, `owner/repo` shorthand, scheme-free `host/path`, `http://`.
- Marketplace, plugins (`plugin.json`, Claude plugins, Copilot plugins), registries, MCP registry.
- MCP servers, LSP servers, agents, commands, prompts, chatmodes, canvas, workflows.
- Subdirectory packages (`git:` + `path:`), single-file packages, aliases, per-dependency `targets:`.
- Semver ranges on refs.
- Token handling and auth helpers (git does all auth).
- `compile`, `audit`, `policy`, `approve`/`deny`, `pack`/`publish`, `run`/`scripts`/`runtime`, `self-update`, `outdated`, `prune`, `view`, `find`, `doctor`, `config`, `cache`, `mcp`, `marketplace`, `lifecycle`, `experimental`.
- Org policy (`apm-policy.yml`), hidden-Unicode scan, executable approval.
- User scope (`-g`), Windows, every harness other than Claude Code and Antigravity.
- Public CI, release pipeline, Homebrew/PyPI/curl installers, docs website.

## Functional requirements

### Manifest

- **R1.** `apm.yml` must have `name` (string) and `version` (string). Optional: `description`, `author`, `license`, `targets`, `dependencies.apm`.
- **R2.** `targets` is a list containing only `claude` and/or `antigravity`. If absent, the target is `[claude]`. An empty list, an unknown name, or the `target:` singular key is an error with a hint.
- **R3.** Any other top-level key, and any `dependencies` key other than `apm`, is ignored with one warning per key. This applies to the root manifest and to every dependency's manifest.
- **R4.** A dependency entry is one of:
  - an `https://`, `ssh://` or `git@host:path` URL, optionally ending in `#<ref>`;
  - a local path starting with `./`, `../`, `/` or `~/`;
  - an object `{git: <url>, ref?: <ref>, skills?: [names]}`;
  - an object `{path: <local path>, skills?: [names]}`.
- **R5.** These are rejected with a message that names the supported forms: `owner/repo` shorthand, scheme-free `host/path`, `http://`, `git:` with `path:`, and the object keys `alias`, `targets`, `type`, `allow_insecure`, `version`.
- **R6.** HTTPS URLs with a single-label host (`https://gitserver/team/x.git`) are accepted.
- **R7.** A ref is a branch, a tag, or a full 40-hex commit SHA. No ref means the remote's default branch.

### Packages

- **R8.** A fetched package is classified in this order:
  1. root `SKILL.md`: the package is one skill;
  2. `skills/*/SKILL.md`: a skill collection;
  3. `apm.yml` with `.apm/`: an APM package;
  4. anything else: error "not an apm package".
- **R9.** An APM package provides rules from `.apm/instructions/*.instructions.md`, skills from `.apm/skills/*/` and hooks from `.apm/hooks/*.json`.
- **R10.** `skills: [...]` on a collection deploys only the named skills. A name with no match is an error. `skills:` on a single-skill package is an error.
- **R11.** A package's own `dependencies.apm` is followed regardless of shape.
- **R12.** The root project's own `.apm/` primitives deploy like a dependency's.

### Resolution

- **R13.** Dependencies resolve transitively, breadth first. Each package is identified by host plus repo path. A remote dependency declared with https and with ssh is the same package.
- **R14.** When the same package is requested at two different refs, the shallowest request wins; on a tie, the first declared. A warning names both refs.
- **R15.** Cycles are skipped. Depth beyond 50 is an error.
- **R16.** A local path is resolved relative to the declaring package's directory. A local path declared by a remote package is an error.
- **R17.** On `install`, a dependency whose lockfile entry has the same declared ref is fetched at the locked commit. Otherwise the ref is resolved against the remote.

### Lockfile

- **R18.** `apm install` writes `apm.lock.yaml` recording, per dependency: identity, host, resolved ref, resolved commit, depth, parent, package type, skill subset, deployed files with hashes, and a content hash (remote only).
- **R19.** On install, a fetched remote package whose content hash differs from the lockfile fails the install with a tamper/force-push message.
- **R20.** `apm install --frozen` fails if the lockfile is missing, if any remote dependency is absent from it, or if a declared ref differs from the locked one. Otherwise it installs exactly the locked commits.

### Deployment

- **R21.** Rules deploy to `.claude/rules/<name>.md` and/or `.agents/rules/<name>.md`, with `applyTo` translated to each target's native frontmatter. A rule without `applyTo` is always active on both targets.
- **R22.** Skills deploy to `.claude/skills/<name>/` and/or `.agents/skills/<name>/`. `.git`, `.apm/` and symlinks are not copied.
- **R23.** Hooks merge into `.claude/settings.json` (key `hooks`) and/or `.agents/hooks.json` (entry `apm`), leaving everything else in those files untouched. Hook scripts are copied into the target folder and their paths rewritten.
- **R24.** A package may ship `hooks-claude.json` and/or `hooks-antigravity.json`. If one exists, it replaces the package's other hook files for that target.
- **R25.** A hook event that Antigravity doesn't support is skipped for Antigravity with a warning.
- **R26.** If two sources would write the same path, install fails and names both. If a path already exists, isn't tracked in the lockfile, and has different content, install fails and tells the user to move it. Identical content is adopted.
- **R27.** `apm_modules/` is added to `.gitignore` if missing.

### Cleanup

- **R28.** Every install, update and uninstall removes files the previous lockfile tracked that are no longer wanted.
- **R29.** A tracked file whose current hash differs from the recorded hash is kept, with a warning.
- **R30.** Only paths under `.claude/` or `.agents/` inside the project are ever deleted.
- **R31.** `apm_modules/` entries for packages no longer in the tree are deleted.

### Commands

- **R32.** `apm init` writes a starter `apm.yml` (name from the folder, `version: "0.1.0"`, `targets: [claude]`, empty `dependencies.apm`). It refuses if `apm.yml` exists.
- **R33.** `apm install <dep> [--skill NAME ...]` adds the dependency to `apm.yml` exactly as typed (keeping its transport), then installs. `--skill` writes the object form with `skills:`.
- **R34.** `apm install --target <t>` deploys only to `<t>` for this run. Files previously deployed to the other target are left in place and stay tracked.
- **R35.** `apm uninstall <dep>` removes the entry from `apm.yml` and reconciles. `<dep>` matches the entry as written, its identity (`host/path`), or a local path.
- **R36.** `apm update [<dep>]` ignores lockfile pins (all, or only that dependency's) and reconciles.
- **R37.** `apm deps list` prints each locked package with source, ref and short commit. `apm deps tree` prints the dependency tree.
- **R38.** A failed command leaves `apm.yml` and `apm.lock.yaml` as they were.

## Non-functional requirements

- **N1. Network:** the only network access is `git` subprocesses aimed at URLs from `apm.yml` files. No HTTP client, no update check, no telemetry.
- **N2. Auth:** apm-lite reads no tokens or credential env vars. Whatever works for `git clone <url>` on the machine works for apm-lite, including `insteadOf` rewrites.
- **N3. Writes:** only `apm.yml`, `apm.lock.yaml`, `.gitignore`, `apm_modules/`, `.claude/` and `.agents/` in the project. Nothing in `$HOME`.
- **N4. Compatibility:** files apm-lite writes are a strict subset of upstream APM 0.33.0 formats. Field names and meanings are unchanged; upstream must accept them without errors.
- **N5. Platform:** macOS and Linux, Python 3.10+, `git` on PATH.
- **N6. Install:** clone the repo, then `uv tool install .` or `pipx install .`. Distribution name `apm-lite`, command `apm`.
- **N7. Dependencies:** runtime deps limited to click, PyYAML, ruamel.yaml and python-frontmatter.
- **N8. Tests:** the full suite runs offline with no tokens.
- **N9. Docs:** `README.md`, `docs/reference.md`, `docs/authoring-packages.md`, `CONTRIBUTING.md`, `PRINCIPLES.md`, `CHANGELOG.md`, plus `docs/specs/`. Nothing else.

## Assumptions

- Internal Git servers allow shallow clones. Fetching by commit SHA may not be allowed; a full-clone fallback covers that.
- Teams release packages by tagging commits.
- Antigravity CLI reads `.agents/rules/*.md`, `.agents/skills/*/SKILL.md` and `.agents/hooks.json` at project scope, as its docs state ([rules](https://www.antigravity.google/docs/rules), [skills](https://www.antigravity.google/docs/skills), [hooks](https://www.antigravity.google/docs/hooks)).
- The `apm` command clashing with an upstream `apm` on PATH is acceptable; the README documents it.
