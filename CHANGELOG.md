# Changelog

## 0.2.0 (2026-11-17)

- Adds the deploy targets `cursor`, `codex` and `gemini` to `targets:`, next to `claude` and `antigravity`.
- Cursor gets rules in `.cursor/rules/<name>.mdc`, hooks in `.cursor/hooks.json`, and skills in `.agents/skills/`.
- Codex gets hooks in `.codex/hooks.json` and skills in `.agents/skills/`. Gemini gets hooks in `.gemini/settings.json` and skills in `.agents/skills/`.
- Codex and Gemini get no rules. apm-lite warns once per command when a package has rules and either target is set.
- Antigravity, Cursor, Codex and Gemini share `.agents/skills/`. It is written once, and kept while any of them is in `targets:`.
- A package can ship `hooks-cursor.json`, `hooks-codex.json` and `hooks-gemini.json`. Cursor and Gemini get translated event names, and Cursor gets translated tool matchers. A Claude matcher with no exact Cursor equivalent is an error that suggests `hooks-cursor.json`.
- Codex hook commands run scripts from `$(git rev-parse --show-toplevel)`, so the project folder must be the git root. apm-lite warns when it isn't.
- Gemini hook timeouts are written in milliseconds. Package files still use seconds.
- A hook `timeout` must be a positive integer, for every target. Anything else is an error.
- A hook file is now read only when apm-lite has entries to add or strip there. This also applies to `claude` and `antigravity`. A malformed hook file for a target you don't use no longer fails an install.
- apm-lite can't keep comments in `.gemini/settings.json`. It leaves a commented file alone, and fails before writing when it has to change one.

## 0.1.0 (2026-10-10)

First release of apm-lite, forked from APM v0.33.0.

- Installs rules, skills and hooks from Git (`https://`, `ssh://`, `git@host:path`) and from local paths in the root `apm.yml`.
- Deploys to Claude Code (default) and Antigravity CLI (opt-in with `targets:`).
- Commands: `init`, `install` (with `--frozen` and `--skill`), `uninstall`, `update`, `deps list`, `deps tree`.
- Writes `apm.yml` and `apm.lock.yaml` in the subset of upstream APM's formats that apm-lite supports.
- Keeps files you edited, refuses to overwrite files it doesn't manage, and rolls back every file a failed or interrupted command changed.
