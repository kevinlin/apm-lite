# Reference

This page covers `apm.yml`, dependency entries, the lockfile, where files land, every command, and how apm-lite treats files it wrote earlier. To build a package, see [authoring-packages.md](authoring-packages.md).

## apm.yml

```yaml
name: my-project
version: "1.0.0"
targets:
  - claude
  - antigravity
dependencies:
  apm:
    - https://git.corp.example/team/base-rules.git#v2.1.0
    - git@git.corp.example:team/hooks.git
    - git: https://git.corp.example/team/skills.git
      ref: main
      skills: [review, deploy]
    - ./packages/local-skill
```

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | A non-empty string. Also the `<pkg>` folder for the project's own hook scripts. |
| `version` | yes | A non-empty string. Quote it: an unquoted `1.0` is a number and fails. |
| `description`, `author`, `license` | no | Kept as written. apm-lite doesn't use them. |
| `targets` | no | A list of `claude` and/or `antigravity`. Absent means `[claude]`. |
| `dependencies.apm` | no | A list of dependency entries (next section). |

Any other top-level key, and any `dependencies` key other than `apm`, is ignored with one warning per key. It stays in the file unchanged when apm-lite saves `apm.yml`. The same goes for every dependency's `apm.yml`.

The project's own `.apm/` folder deploys like a dependency's. A `SKILL.md` or `skills/` folder at the project root does not.

### Rejected forms

| You write | Why it fails |
|---|---|
| no `name` or `version`, an empty one, or a non-string one | Both are required strings. |
| `target: claude` | The singular key isn't supported. Use `targets:`. |
| `targets: []`, or a name other than `claude` and `antigravity` | The list needs at least one known target. |
| `owner/repo`, or `git.corp.example/team/pkg` | Shorthand and scheme-free hosts aren't supported. |
| `http://…` | Use `https://`. |
| `https://git/team/pkg`, `https://git_corp.example/team/pkg` | The host must be a dotted, fully qualified name. |
| `https://git.corp.example/pkg` | The repo path needs at least two segments. |
| an `@` in the repo path or the ref | Upstream APM reads `#release@candidate` as ref `release` plus an alias. |
| `git:` together with `path:` | Subdirectory packages aren't supported. |
| object keys `alias`, `targets`, `type`, `allow_insecure`, `version`, or any key other than `git`, `path`, `ref`, `skills` | Not supported. |
| `ref:` on a `path:` entry | A local path has no ref. |
| a ref such as `1.2.3`, `^1.2` or `1.x` | It reads as a semver version or range. Use the tag name, for example `v1.2.3`. |
| a local path in any `apm.yml` except the project's | Packages can't declare local paths. |
| two local entries with the same final folder name | Both would be recorded as `_local/<name>`. |

Each message names the supported forms.

## Dependency entries

| Entry | Host | Repo path | Ref | Key |
|---|---|---|---|---|
| `https://git.corp.example/team/pkg.git#v1.0` | `git.corp.example` | `team/pkg` | `v1.0` | `git.corp.example/team/pkg` |
| `ssh://git@git.corp.example:7999/team/pkg.git#main` | `git.corp.example` (port 7999) | `team/pkg` | `main` | `git.corp.example/team/pkg` |
| `git@git.corp.example:team/pkg.git` | `git.corp.example` | `team/pkg` | none | `git.corp.example/team/pkg` |
| `./packages/x` (project's `apm.yml` only) | n/a | n/a | n/a | `./packages/x` |

- A local path starts with `./`, `../`, `/` or `~/` and ends in a named directory.
- `.git` is stripped from the repo path. Case is kept.
- A trailing `#<ref>` is split off the URL.
- The clone URL is the entry as written, minus `#<ref>`. https, ssh, the port and the user are all preserved.
- The `host/path` key identifies a package. The same repo declared over https and over ssh is one package.
- The scp form `git@host:path` can't carry a port. Use `ssh://git@host:PORT/path` for that.
- Each segment of the repo path uses only letters, digits, `.`, `_` and `-`.

Two object forms exist:

```yaml
- git: https://git.corp.example/team/skills.git
  ref: v2.0.0          # optional; overrides a #ref in the URL
  skills: [review]     # optional; a collection's subset
- path: ./packages/x   # project's apm.yml only
  skills: [review]
```

`skills:` must be a non-empty list of strings. It is deduplicated and sorted.

### Refs

A ref is a branch, a tag, or a full 40-hex commit SHA. With no ref, apm-lite uses the remote's default branch. A tag wins over a branch of the same name. A tag or SHA is immutable. A branch, and the default branch, is mutable.

### Duplicate requests

Dependencies are resolved breadth first, and a package's own `dependencies.apm` is followed whatever its shape. Cycles are skipped, and nesting deeper than 50 levels is an error.

When one package is requested at different refs:

- If two requests are for tags or SHAs that resolve to different commits, the install fails and names both request chains.
- Otherwise the shallowest request wins, and the first declared wins a tie. apm-lite warns, naming both refs.

When several requests carry `skills:`, the union is deployed. One request without `skills:` means all skills.

## Lockfile

`apm install` writes `apm.lock.yaml` with one entry per dependency. The hashes below are shortened.

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
  version: 1.0.0                  # the package's apm.yml version, if any
  depth: 2                        # only when not 1
  resolved_by: team/base          # the parent's repo_url, only when depth > 1
  package_type: apm_package
  deployed_files:
  - .claude/rules/pkg-style.md
  - .claude/skills/review
  - .claude/skills/review/SKILL.md
  deployed_file_hashes:
    .claude/rules/pkg-style.md: sha256:...
    .claude/skills/review/SKILL.md: sha256:...
  content_hash: sha256:...
  skill_subset:                   # only when a subset was selected
  - review
- repo_url: _local/x
  name: x
  package_type: claude_skill
  deployed_files: [...]
  deployed_file_hashes: {...}
  source: local
  local_path: ./packages/x
local_deployed_files: [...]       # the project's own .apm/ output
local_deployed_file_hashes: {...}
```

- `lockfile_version` is the string `'1'`. apm-lite also reads `'2'`. Any other value is an error.
- Dependencies are sorted by depth, then `repo_url`. Empty fields are left out.
- `package_type` is one of `apm_package`, `claude_skill`, `hybrid` or `skill_bundle` (see [package shapes](authoring-packages.md#package-shapes)).
- `deployed_files` lists files plus each skill's folder, without a trailing slash. `deployed_file_hashes` covers files only.
- Hook config files (`.claude/settings.json`, `.agents/hooks.json`) and the ownership sidecars are not listed. Copied hook scripts are.
- `content_hash` is recorded for remote packages only. Local packages are read in place and have none.
- A file hash is a SHA-256 of the content. In a UTF-8 text file, CRLF counts as LF.
- The lockfile has no `generated_at` and no `deployments` block.
- Unknown keys are ignored when reading. A dependency without `repo_url` is an error. A remote entry without a full commit counts as unpinned on `install` and as drift on `install --frozen`.

On `install`, a dependency whose entry has the same declared ref and a full commit is installed at the locked commit, with no remote lookup. If the package at that commit no longer matches `content_hash`, the install fails with a tamper or force-push message. A package installed at a new commit gets a new hash.

## Target paths

| | Claude (default) | Antigravity (`antigravity` in `targets:`) |
|---|---|---|
| root | `.claude/` | `.agents/` |
| rules | `.claude/rules/<name>.md` | `.agents/rules/<name>.md` |
| skills | `.claude/skills/<name>/` | `.agents/skills/<name>/` |
| hook config | `.claude/settings.json`, key `hooks` | `.agents/hooks.json`, key `apm` |
| hook scripts | `.claude/hooks/<pkg>/` | `.agents/hooks/<pkg>/` |
| hook ownership sidecar | `.claude/apm-hooks.json` | `.agents/apm-hooks.json` |
| events skipped with a warning | `PreInvocation`, `PostInvocation` | anything except `PreToolUse`, `PostToolUse`, `PreInvocation`, `PostInvocation`, `Stop` |

apm-lite merges its hook entries into the hook config and leaves everything else in that file as it was. The sidecar records which entries are apm-lite's, so the next run removes only those.

Antigravity runs hook commands with `.agents/` as the working directory. Commands that reference a copied script are written as `hooks/<pkg>/<script>` for that reason. Claude commands use `"${CLAUDE_PROJECT_DIR}/.claude/hooks/<pkg>/<script>"`.

Removing a target from `targets:` deletes its files and strips its hook entries on the next run.

## Commands

```
apm [-v | --verbose] [--version] <command>
```

| Option | Meaning |
|---|---|
| `-v`, `--verbose` | Before the command name. Echo each `git` command and clone fallback to stderr, and print `wrote <path>` and `deleted <path>` for each file change. |
| `--version` | Print the version and exit. |

Every command that installs ends with a summary line, for example `3 package(s) locked; 8 file(s) written, 0 deleted`.

### `apm init`

Writes `apm.yml` in the current folder: `name` from the folder name, `version: "0.1.0"`, `targets: [claude]` and an empty `dependencies.apm`. Fails if `apm.yml` exists.

### `apm install [DEP] [--skill NAME]... [--frozen]`

- No arguments: install every dependency, deploy, clean up, and write the lockfile. Needs `apm.yml` (the hint says `run 'apm init'`).
- `DEP`: add the entry to `apm.yml` exactly as typed, keeping its transport, then install. If the same package and ref is already declared, this only reinstalls.
- `--skill NAME`: needs `DEP`. Writes the object form with `skills:`, either `{git: <url>, ref: <ref>, skills: [...]}` or `{path: <local>, skills: [...]}`. If the entry already exists, the names are merged into it. An entry with no `skills:` already means all skills.
- `--frozen`: install exactly what the lockfile records, and fail on drift. It can't be combined with `DEP`. See below.

Run twice in a row with nothing changed, `apm install` fetches nothing and writes no files.

### `apm uninstall DEP`

Removes the entry from `apm.yml`, then deploys and cleans up. `DEP` matches the entry as written, its `host/path` key, or a local path. With no match, the error lists the declared dependencies.

### `apm update [DEP]`

Ignores lockfile pins for every dependency, or only for `DEP`, and resolves their refs against the remote again. A branch moves to its current tip. Other dependencies keep their pins. `DEP` is matched like `uninstall`, and it can also be the key of any locked package, such as a transitive one.

### `apm deps list`

Reads the lockfile and prints one line per package, sorted by key: `name  source  ref  commit`. The source is `host/path`, or the local path for a local package. The commit is shortened to 7 characters. Local packages show `local` as the ref, and a package with no ref shows `HEAD`. Fails if there is no lockfile.

### `apm deps tree`

The same lines as a tree: the project's direct dependencies first, with each package's own dependencies indented under it.

## `apm install --frozen`

`--frozen` fails if `apm.yml` and the lockfile disagree, and otherwise installs exactly the locked commits. These cases are drift:

- the lockfile is missing;
- a remote request, direct or transitive, has no lockfile entry, or its entry has no full 40-hex commit;
- the winning request's declared ref differs from the locked `resolved_ref`;
- a SHA request differs from the locked commit;
- the lockfile has a remote entry that the dependency graph no longer reaches;
- a skill subset differs from the locked `skill_subset`.

The only remote query frozen mode makes is for a duplicate tag request whose ref differs from the locked one: it checks that the tag points at the locked commit. Local dependencies are exempt from the commit checks.

## Cleanup and edited files

Before it writes any path, apm-lite checks it:

| Situation | What happens |
|---|---|
| Two sources plan the same path (compared ignoring case) | Error naming both. |
| The file is missing | Written. |
| The file exists, isn't in the lockfile, and its content differs | Error. Move or delete the file, then install again. |
| The file exists, isn't in the lockfile, and its content is identical | Adopted as managed output. |
| The file is in the lockfile and still matches its recorded hash | Overwritten with the new version. |
| The file is in the lockfile and no longer matches its recorded hash | You edited it. It is kept, with a warning, and the old hash stays recorded. |
| The file's bytes and executable bit already match the plan | Left alone, whoever wrote it. |

Every install, update and uninstall also removes files the previous lockfile tracked that are no longer planned. That covers a removed dependency, a removed skill, and every file of a target dropped from `targets:`. A tracked file you edited is kept, with a warning (`kept <path>: edited since install`). To get the package version back, delete the file and install again. `apm_modules/` entries for packages no longer in the tree are deleted too.

apm-lite adds `apm_modules/` to `.gitignore` if no line equals it.

### Where apm-lite writes

Only inside the project: `.claude/`, `.agents/` and `apm_modules/`, plus `apm.yml`, `apm.lock.yaml` and `.gitignore` at the root. A destination that passes through a symlink, or resolves outside those places, is an error. apm-lite writes nothing in your home folder. `git` and its helpers may.

### Failures and interruptions

If a command fails or you press Ctrl-C, apm-lite restores every file it changed in the project to its previous bytes and mode. That covers deployed files, hook configs, sidecars, `.gitignore`, `apm.yml` and `apm.lock.yaml`. The exception is `apm_modules/`, which is a cache and is not restored. A hard kill (`SIGKILL`, power loss) during deployment can leave partial changes. If that happens, `git status` shows them.

Malformed JSON in `.claude/settings.json`, `.agents/hooks.json` or a sidecar is an error before anything is written. So is an `apm` entry in `.agents/hooks.json` that apm-lite doesn't own.

## Errors, warnings and git

- Errors print as `error: <message>`, with an optional `hint: <text>`, on stderr. The exit status is 1.
- Warnings print as `warning: <message>` on stderr.
- Misuse of options, such as `--frozen` together with a dependency, prints Click's usage message and exits 2.
- apm-lite has no HTTP client, update check or telemetry. The only network access is `git`, for the URLs in `apm.yml` files, after git applies its own configuration (for example `insteadOf`).
- apm-lite reads no tokens and no credential variables. Whatever works for `git clone <url>` on your machine works here. It runs git with `GIT_TERMINAL_PROMPT=0`, so a missing credential fails the command instead of waiting for input.
- A git failure shows the failing command and git's own error text.
- Fetching goes by commit. If the server refuses a fetch by SHA, apm-lite falls back to a full clone and checks out the commit. A commit that no advertised ref reaches fails with git's error.
