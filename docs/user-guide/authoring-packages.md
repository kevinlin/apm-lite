# Authoring packages

A package is an ordinary Git repo on your Git server. You release it by tagging a commit. This page covers what goes in the repo, how each part is deployed, and how to test a package before you tag it. For the manifest, lockfile and commands, see [reference.md](reference.md).

## Package shapes

apm-lite looks at the repo in this order and uses the first match.

| # | The repo has | Shape | `package_type` in the lockfile |
|---|---|---|---|
| 1 | `SKILL.md` at the root | One skill | `claude_skill`, or `hybrid` if it also has an `apm.yml` |
| 2 | `skills/*/SKILL.md` | A collection of skills | `skill_bundle` |
| 3 | `apm.yml` and a `.apm/` folder | An APM package | `apm_package` |
| 4 | none of these | Error: not an apm package or skill | |

Because the first match wins, a repo with a root `SKILL.md` is one skill even if it also has a `.apm/` folder, and a repo with `skills/*/SKILL.md` is a collection. Put rules and hooks in an APM package.

An APM package looks like this:

```
my-package/
  apm.yml
  .apm/
    instructions/
      my-package-style.instructions.md
    skills/
      review/
        SKILL.md
    hooks/
      hooks.json
      guard.sh
```

Its `apm.yml` needs `name` and `version`, both quoted strings. It can also declare `dependencies.apm`. apm-lite follows those for every shape, so a package can pull in others. A local path in a package's `apm.yml` is an error. Only the project's own `apm.yml` may use local paths.

A consumer installs a collection with `skills:` to take only some of its skills. A name that matches no skill is an error. `skills:` on any other shape is an error too.

## Rules

Put each rule at `.apm/instructions/<name>.instructions.md`. Only direct children of that folder count. `<name>` is the file name without `.instructions.md`, and it may use only letters, digits, `.`, `_` and `-`.

A rule source is Markdown with optional frontmatter. Two fields matter. `applyTo` is a string of comma-separated globs, or a list. Commas inside `{...}` or `[...]` don't split. `description` is used for Cursor only. Every other frontmatter key is dropped.

```markdown
---
applyTo: "**/*.py,src/**/*.{ts,tsx}"
---

Use type hints in all new Python code.
```

apm-lite writes each rule in the target's own format:

| `applyTo` | Claude: `.claude/rules/<name>.md` | Antigravity: `.agents/rules/<name>.md` | Cursor: `.cursor/rules/<name>.mdc` |
|---|---|---|---|
| set | `paths:` list in frontmatter, one JSON-quoted glob per item | `trigger: glob` and `globs:` as one comma-separated string | `globs:` as one comma-separated line, and `alwaysApply: false` |
| not set | the body alone (Claude loads a rule without `paths` all the time) | `trigger: always_on` | `alwaysApply: true` |

Codex and Gemini have no rules. Their tools read rules only from `AGENTS.md` and `GEMINI.md`, and apm-lite doesn't write those. When a package has rules and `codex` or `gemini` is in `targets:`, apm-lite warns once per command and deploys no rule there. Skills and hooks still deploy.

For the example above, Claude gets:

```markdown
---
paths:
  - "**/*.py"
  - "src/**/*.{ts,tsx}"
---

Use type hints in all new Python code.
```

Antigravity gets:

```markdown
---
trigger: glob
globs: "**/*.py,src/**/*.{ts,tsx}"
---

Use type hints in all new Python code.
```

Cursor gets:

```markdown
---
globs: **/*.py, src/**/*.{ts,tsx}
alwaysApply: false
---

Use type hints in all new Python code.
```

Cursor details:

- `description` is written as a JSON-quoted line when the source has a non-empty string `description`. It is left out otherwise.
- `globs` is always one line. Cursor doesn't read the list form.
- A glob with a control character, or with a comma outside `{...}` and `[...]`, is an error that names the rule. Only a list item can hold such a comma, because a string `applyTo` splits on it. Cursor's handling of these isn't documented, so apm-lite refuses them instead of guessing. List each glob as its own item, or put the comma inside `{a,b}`.

All packages share each target's `rules/` folder. Two packages that deploy the same name collide, and the install fails naming both. Prefix rule names with the package name, as in `my-package-style`.

## Skills

A skill is a folder with a `SKILL.md`. The folder is copied whole into `.claude/skills/<name>/` for Claude and into `.agents/skills/<name>/` for every other target. Antigravity, Cursor, Codex and Gemini share `.agents/skills/`. apm-lite writes each skill there once, however many of them are in `targets:`, and keeps it while any one of them is. The copy skips `.git`, `.apm` and `__pycache__` folders and any symlink. A file with an executable bit stays executable.

Where the skill lives depends on the shape:

| Shape | Skill folders |
|---|---|
| One skill | The repo root is the skill. |
| Collection | `skills/<name>/`, each with a `SKILL.md`. |
| APM package | `.apm/skills/<name>/`. A folder without a `SKILL.md` is skipped. |

Skill names:

- **One skill:** the name comes from the repo, not from `SKILL.md`. It is the last segment of the repo path, converted to lowercase hyphen-case. `My_Skills` becomes `my-skills`. A local package uses its folder name the same way.
- **Collection and APM package:** the folder name is used as written. If it isn't a valid skill name (lowercase letters, digits and single hyphens, up to 64 characters), apm-lite warns and uses it anyway. If it has characters outside letters, digits, `.`, `_` and `-`, the install fails.

A `SKILL.md` without a `description` in its frontmatter deploys with a warning. Write one.

## Hooks

Hooks live in `.apm/hooks/` in an APM package.

### Source files

Every `*.json` file directly inside `.apm/hooks/` is a hook source, except `package.json`, `package-lock.json` and the target-specific files below. Subfolders aren't searched for sources. A source is either:

```json
{ "hooks": { "PreToolUse": [ ... ] } }
```

or the same without the outer `hooks` key:

```json
{ "PreToolUse": [ ... ] }
```

Under each event is a list of entries. An entry is nested, `{matcher, hooks: [handler]}`, or flat, the handler itself. A handler is:

| Field | Rule |
|---|---|
| `type` | Optional. If present it must be `"command"`. |
| `command` | A string. A handler without one is skipped with a warning. |
| `timeout` | Optional. A positive integer, in seconds, for every target. Zero, a negative number, a decimal, a string or a boolean is an error that names the file. |

Other handler fields are dropped. Malformed JSON in a source is an error that names the file.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "./guard.sh", "timeout": 5 }]
      }
    ],
    "Stop": [{ "type": "command", "command": "./notify.sh" }]
  }
}
```

Codex rejects its whole hook file, your own hooks included, when a `timeout` is negative. That is why apm-lite refuses a bad `timeout` for every target before it writes anything. Gemini reads `timeout` in milliseconds. You still write seconds, and apm-lite writes the value times 1000 for Gemini.

### One file per target

Matchers and event names belong to one tool, so a hook that suits Claude may not suit Antigravity, Cursor, Codex or Gemini. When you need more than one, ship a file per target. Each uses the grammar above, with timeouts in seconds, and that target's own event names and matchers:

- `hooks-claude.json` replaces every other hook source for Claude.
- `hooks-antigravity.json` replaces every other hook source for Antigravity.
- `hooks-cursor.json` replaces every other hook source for Cursor.
- `hooks-codex.json` replaces every other hook source for Codex.
- `hooks-gemini.json` replaces every other hook source for Gemini.

A target with no file of its own uses the generic `*.json` sources. No target reads another target's file. Files named `hooks-<harness>.json` for any other harness (`copilot`, `vscode`, `windsurf`, `kiro`) are ignored. They are not copied either.

How the generic sources reach each target:

- Cursor: apm-lite translates a Claude tool matcher on `PreToolUse` and `PostToolUse`, as the next table shows. A matcher with no exact Cursor equivalent is an error that names the hook file and hints to ship `hooks-cursor.json`.
- Codex and Gemini: apm-lite writes the matcher as it is in the source. Gemini's tools have other names than Claude's (its shell tool is `run_shell_command`), so a `Bash` matcher never fires on Gemini unless you ship `hooks-gemini.json` with Gemini's own matchers.

Cursor matcher translation applies only when the source event is a Claude name that apm-lite translates. A matcher on an event that is already a Cursor name, such as `preToolUse` in `hooks-cursor.json`, is written as it is.

| Source matcher | Written for Cursor |
|---|---|
| none, `""`, `*` or `.*` | as it is (left out if absent) |
| `Bash` | `Shell` |
| `Read`, `Grep`, `Task`, `WebFetch`, `WebSearch` | the same name |
| `Edit`, `Write` | `Write` |
| names joined with `\|` | each name translated, joined with `\|`, duplicates removed |

- A tool matcher is accepted only on `PreToolUse` and `PostToolUse`. On any other translated event, a matcher is an error unless it is `""`, `*` or `.*`.
- `Edit` and `Write` must appear together, because Cursor's `Write` covers both and one alone would widen the match.
- A regex, `Glob`, an MCP tool name, or spaces around the `|` is an error.

### Scripts

A command can name a script in one of three ways:

- `./<path>`, resolved against the hook file's folder, then against the package root;
- `${CLAUDE_PLUGIN_ROOT}/<path>`, resolved against the package root;
- `${PLUGIN_ROOT}/<path>`, resolved the same way.

The script must be a regular file inside `.apm/hooks/`, not a symlink. Anything else is an error that tells you to move the script, and anything it needs, into `.apm/hooks/`. That keeps each hook bundle self-contained. A path that starts with `../` is also an error, because it can't be rewritten and would resolve against the harness's working directory at runtime. A command with no such path, such as `echo done`, is copied as written.

The whole `.apm/hooks/` folder is copied to `<root>/hooks/<pkg>/` for each target in `targets:`: `.claude/hooks/<pkg>/`, `.agents/hooks/<pkg>/`, `.cursor/hooks/<pkg>/`, `.codex/hooks/<pkg>/` and `.gemini/hooks/<pkg>/`. Relative paths and file modes are kept. The hook source files are not copied. `package.json` is, so a `package.json` that sets `"type": "module"` still applies to your `.js` scripts. `<pkg>` is the last segment of the repo path, the folder name of a local package, or the project's `name` for the project's own hooks. Set the executable bit (`chmod +x`) on scripts and commit it.

apm-lite rewrites each script path to the copy:

| Target | A command of `./guard.sh` becomes |
|---|---|
| Claude | `"${CLAUDE_PROJECT_DIR}/.claude/hooks/<pkg>/guard.sh"`, always in double quotes |
| Antigravity | `hooks/<pkg>/guard.sh` |
| Cursor | `.cursor/hooks/<pkg>/guard.sh`, with the quoting you wrote |
| Codex | `"$(git rev-parse --show-toplevel)/.codex/hooks/<pkg>/guard.sh"`, always in double quotes |
| Gemini | `.gemini/hooks/<pkg>/guard.sh`, with the quoting you wrote |

A single-quoted path in a Claude or Codex command gets double quotes, because the shell doesn't expand variables inside single quotes. A script path containing `$`, a backtick or `"` is an error for every target.

### Events

apm-lite translates Claude's event names for Cursor and Gemini, as the table shows. It skips, with a warning, an event the target doesn't have.

| Event | Claude | Antigravity | Cursor | Codex | Gemini |
|---|---|---|---|---|---|
| `PreToolUse` | deployed | deployed, nested with a matcher | deployed as `preToolUse` | deployed | deployed as `BeforeTool` |
| `PostToolUse` | deployed | deployed, nested with a matcher | deployed as `postToolUse` | deployed | deployed as `AfterTool` |
| `Stop` | deployed | deployed, as a flat handler list | deployed as `stop` | deployed | deployed as `SessionEnd` |
| `UserPromptSubmit` | deployed | skipped with a warning | deployed as `beforeSubmitPrompt` | deployed | deployed as written |
| `SubagentStop` | deployed | skipped with a warning | deployed as `subagentStop` | deployed | deployed as written |
| `SessionStart` | deployed | skipped with a warning | deployed as `sessionStart` | deployed | deployed as written |
| `SessionEnd` | deployed | skipped with a warning | deployed as `sessionEnd` | deployed | deployed as written |
| `PreCompact` | deployed | skipped with a warning | deployed as `preCompact` | deployed | deployed as written |
| `PreInvocation` | skipped with a warning | deployed, as a flat handler list | skipped with a warning | skipped with a warning | skipped with a warning |
| `PostInvocation` | skipped with a warning | deployed, as a flat handler list | skipped with a warning | skipped with a warning | skipped with a warning |
| any other event | deployed as written | skipped with a warning | deployed as written if it is a Cursor event name, otherwise skipped with a warning | deployed as written if it is a Codex event name, otherwise skipped with a warning | deployed as written |

Cursor's event names are `sessionStart`, `sessionEnd`, `preToolUse`, `postToolUse`, `postToolUseFailure`, `subagentStart`, `subagentStop`, `beforeShellExecution`, `afterShellExecution`, `beforeMCPExecution`, `afterMCPExecution`, `beforeReadFile`, `afterFileEdit`, `beforeSubmitPrompt`, `preCompact`, `stop`, `afterAgentResponse`, `afterAgentThought`, `beforeTabFileRead`, `afterTabFileEdit` and `workspaceOpen`. Codex's are `PreModelRequest`, `PostModelRequest`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop` and `Interrupt`. For Gemini, `preToolUse` and `postToolUse` also become `BeforeTool` and `AfterTool`.

Claude's `Stop` fires after each response. Gemini's `SessionEnd` fires once per session. If the difference matters, ship `hooks-gemini.json`.

The shape of an entry depends on the target:

- Claude: always nested, with the matcher `*` when you give none.
- Antigravity: `PreToolUse` and `PostToolUse` are nested, as for Claude. The other events are flat, and a matcher is dropped.
- Codex and Gemini: always nested, with no default matcher. The matcher is written as in the source, and left out when the source has none.
- Cursor: always flat, one entry per handler, with `timeout` and `matcher` on the entry when you set them. An entry from a `Stop` or `SubagentStop` source also gets `"loop_limit": null`.

apm-lite doesn't validate Claude's or Gemini's event names. It passes them through.

### What apm-lite owns

apm-lite adds its entries under `hooks` in `.claude/settings.json`, `.cursor/hooks.json`, `.codex/hooks.json` and `.gemini/settings.json`, and under the name `apm` in `.agents/hooks.json`. For Cursor it also sets `"version": 1` when it adds entries and the file has no `version`. It never changes an existing `version`, and it doesn't remove `version` when it removes entries. It leaves everything else in those files alone, and a sidecar file (`apm-hooks.json` in the target's folder) records which entries are its own. In `.agents/hooks.json`, the `apm` container belongs to apm-lite. If it holds a hook you added, the install fails and asks you to move your hooks under another name.

apm-lite reads a target's hook file only when it has entries to add there or entries of its own to remove. A hook file for a target the project doesn't use is never read or written. The files are strict JSON to apm-lite. Gemini accepts comments in `.gemini/settings.json`, but apm-lite can't keep them. If that file has comments and apm-lite has to change it, the install fails before anything is written, and the file stays as it was. The error tells the consumer to remove the comments or keep the package's hooks out of Gemini.

### Where hooks run

Each tool runs hook commands from a different folder, and apm-lite writes script paths to match.

- Antigravity runs them with `.agents/` as the working directory, not the project root. That is why its commands are `hooks/<pkg>/<script>`. A script that needs the project root should read `workspacePaths` from the JSON it receives on stdin. Antigravity doesn't set an environment variable for it.
- Cursor runs project hooks from the project root.
- Codex runs them from the session's working directory, so its commands find the scripts from the git root. The project folder must be the git root. apm-lite warns when it writes such a command and the project folder has no `.git`. Codex runs project hooks only in a trusted project, and asks the user to review new or changed hook definitions first. A hook that doesn't fire may be waiting for that review.
- Gemini runs them from the session's working directory. Its commands resolve when Gemini is started in the project folder. apm-lite's Gemini output was not checked against a live Gemini CLI.

A failing `PreToolUse` hook blocks every tool call in Antigravity. A wrong script path does that. Test your hooks locally before you tag a release.

A project that targets both `claude` and `cursor` should turn off Cursor's "import Claude hooks" setting. With it on, Cursor runs each hook twice. apm-lite doesn't detect this.

## Releasing

Commit, then tag:

```sh
git tag v1.2.0
git push origin v1.2.0
```

Consumers pin the tag:

```yaml
dependencies:
  apm:
    - https://git.corp.example/team/my-package.git#v1.2.0
```

- Name tags so they don't look like versions. `v1.2.0` works. A consumer can't use `1.2.0`, `^1.2` or `1.x` as a ref, because apm-lite doesn't resolve semver ranges and rejects them.
- Don't move or delete a tag. Consumers' lockfiles record the commit and a hash of its content. If the content at the locked commit changes, the install fails with a tamper or force-push message.
- Consumers who track a branch get its tip when they run `apm update`.
- Bump `version` in your `apm.yml` when you tag. apm-lite records it in the lockfile but resolves by tag, not by version.

## Test your package locally

Before you tag, install your package into a scratch project through a local path. Only the project's own `apm.yml` may use local paths, so the scratch project is the consumer.

```sh
mkdir scratch && cd scratch
apm init
apm install ../my-package
```

`apm install ../my-package` writes the path as typed into `apm.yml`. apm-lite reads the package in place. It copies nothing into `apm_modules/`, and it records no hash. Edit the package, run `apm install` again, and the deployed files update.

Then check:

- the folders of the targets you set (`.claude/`, `.agents/`, `.cursor/`, `.codex/`, `.gemini/`) hold the rules, skills and hook scripts you expect;
- the hook configs (`.claude/settings.json`, `.agents/hooks.json`, `.cursor/hooks.json`, `.codex/hooks.json`, `.gemini/settings.json`) hold the hook entries, with script paths that exist;
- the warnings the install printed.

To test a target other than Claude, add it to `targets:` in the scratch `apm.yml`, and run that tool there to exercise the hooks. Per target:

- Codex: run `git init` in the scratch folder first, because Codex commands need the project folder to be the git root. Trust the project and review its hooks when Codex asks.
- Gemini: start Gemini in the scratch folder.
- Cursor: if the scratch project also targets `claude`, turn off Cursor's "import Claude hooks" setting.
- A tool matcher: check it on each target. A Claude matcher is translated for Cursor only. Codex and Gemini get it as you wrote it.

Clean up with `apm uninstall ../my-package`.

When it behaves, push your commit and test the same install from your Git server:

```sh
apm uninstall ../my-package
apm install https://git.corp.example/team/my-package.git#main
```
