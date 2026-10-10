# Changelog

## 0.1.0 (2026-10-10)

First release of apm-lite, forked from APM v0.33.0.

- Installs rules, skills and hooks from Git (`https://`, `ssh://`, `git@host:path`) and from local paths in the root `apm.yml`.
- Deploys to Claude Code (default) and Antigravity CLI (opt-in with `targets:`).
- Commands: `init`, `install` (with `--frozen` and `--skill`), `uninstall`, `update`, `deps list`, `deps tree`.
- Writes `apm.yml` and `apm.lock.yaml` in the subset of upstream APM's formats that apm-lite supports.
- Keeps files you edited, refuses to overwrite files it doesn't manage, and rolls back every file a failed or interrupted command changed.
