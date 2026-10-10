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

A rule source is Markdown with optional frontmatter. The one field that matters is `applyTo`: a string of comma-separated globs, or a list. Commas inside `{...}` or `[...]` don't split. Every other frontmatter key is dropped.

```markdown
---
applyTo: "**/*.py,src/**/*.{ts,tsx}"
---

Use type hints in all new Python code.
```

apm-lite writes each rule in the target's own format:

| `applyTo` | Claude: `.claude/rules/<name>.md` | Antigravity: `.agents/rules/<name>.md` |
|---|---|---|
| set | `paths:` list in frontmatter, one JSON-quoted glob per item | `trigger: glob` and `globs:` as one comma-separated string |
| not set | the body alone (Claude loads a rule without `paths` all the time) | `trigger: always_on` |

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

All packages share the `rules/` folder. Two packages that deploy the same name collide, and the install fails naming both. Prefix rule names with the package name, as in `my-package-style`.

## Skills

A skill is a folder with a `SKILL.md`. The folder is copied whole into `.claude/skills/<name>/` and `.agents/skills/<name>/`. The copy skips `.git`, `.apm` and `__pycache__` folders and any symlink. A file with an executable bit stays executable.

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
| `timeout` | Optional. An integer. |

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

### One file per target

Matchers and event names belong to one tool, so a hook that suits Claude may not suit Antigravity. When you need both, ship two files:

- `hooks-claude.json` replaces every other hook source for Claude.
- `hooks-antigravity.json` replaces every other hook source for Antigravity.

A target with no file of its own uses the generic `*.json` sources. Files named `hooks-<harness>.json` for any other harness (`copilot`, `vscode`, `cursor`, `codex`, `gemini`, `windsurf`, `kiro`) are ignored. They are not copied either.

### Scripts

A command can name a script in one of three ways:

- `./<path>`, resolved against the hook file's folder, then against the package root;
- `${CLAUDE_PLUGIN_ROOT}/<path>`, resolved against the package root;
- `${PLUGIN_ROOT}/<path>`, resolved the same way.

The script must be a regular file inside `.apm/hooks/`, not a symlink. Anything else is an error that tells you to move the script, and anything it needs, into `.apm/hooks/`. That keeps each hook bundle self-contained. A path that starts with `../` is also an error, because it can't be rewritten and would resolve against the harness's working directory at runtime. A command with no such path, such as `echo done`, is copied as written.

The whole `.apm/hooks/` folder is copied to `.claude/hooks/<pkg>/` and `.agents/hooks/<pkg>/`, keeping relative paths and file modes. The hook source files are not copied. `package.json` is, so a `package.json` that sets `"type": "module"` still applies to your `.js` scripts. `<pkg>` is the last segment of the repo path, the folder name of a local package, or the project's `name` for the project's own hooks. Set the executable bit (`chmod +x`) on scripts and commit it.

apm-lite rewrites each script path to the copy:

| Target | A command of `./guard.sh` becomes |
|---|---|
| Claude | `"${CLAUDE_PROJECT_DIR}/.claude/hooks/<pkg>/guard.sh"`, always in double quotes |
| Antigravity | `hooks/<pkg>/guard.sh` |

A single-quoted path in a Claude command gets double quotes, because the shell doesn't expand variables inside single quotes. A script path containing `$`, a backtick or `"` is an error.

### Events

| Event | Claude | Antigravity |
|---|---|---|
| `PreToolUse` | deployed | deployed, nested with a matcher |
| `PostToolUse` | deployed | deployed, nested with a matcher |
| `Stop` | deployed | deployed, as a flat handler list |
| `PreInvocation` | skipped with a warning | deployed, as a flat handler list |
| `PostInvocation` | skipped with a warning | deployed, as a flat handler list |
| any other event | deployed as written | skipped with a warning |

Claude entries are always nested, with the matcher `*` when you give none. For Antigravity's flat events, a matcher is dropped. apm-lite doesn't validate Claude's event names. It passes them through.

### What apm-lite owns

apm-lite adds its entries to `.claude/settings.json` under `hooks`, and to `.agents/hooks.json` under the name `apm`. It leaves everything else in those files alone, and a sidecar file records which entries are its own. In `.agents/hooks.json`, the `apm` container belongs to apm-lite. If it holds a hook you added, the install fails and asks you to move your hooks under another name.

### Antigravity runs hooks from `.agents/`

Antigravity runs hook commands with `.agents/` as the working directory, not the project root. That is why the commands above are `hooks/<pkg>/<script>`. A script that needs the project root should read `workspacePaths` from the JSON it receives on stdin. Antigravity doesn't set an environment variable for it.

A failing `PreToolUse` hook blocks every tool call in Antigravity. A wrong script path does that. Test your hooks locally before you tag a release.

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

- `.claude/` and `.agents/` hold the rules, skills and hook scripts you expect;
- `.claude/settings.json` and `.agents/hooks.json` hold the hook entries, with script paths that exist;
- the warnings the install printed.

For Antigravity, add `antigravity` to `targets:` in the scratch `apm.yml`, and run the agent there to exercise the hooks. Clean up with `apm uninstall ../my-package`.

When it behaves, push your commit and test the same install from your Git server:

```sh
apm uninstall ../my-package
apm install https://git.corp.example/team/my-package.git#main
```
