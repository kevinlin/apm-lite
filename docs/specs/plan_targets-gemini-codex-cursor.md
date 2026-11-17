# apm-lite 0.2.0: targets cursor, codex, gemini — implementation plan

- Date: 2026-11-17
- Spec: [requirements](requirements_apm-lite.md) (R2, R20–R24a, R26, R30; R25, R28, R29, R37 must keep holding for the new targets) and [design](design_apm-lite.md) (§3.1, §6, §7.1, §7.3, §9, §11).
- Upstream reference: `git show 280b8a7b:src/apm_cli/<path>`. Nothing from it is imported.
- Every task ends with `uv run pytest -q` and `uv run ruff check src tests` clean.
- A failing spec test means fix the code or raise a spec change. Tests aren't weakened to pass.

## Task 1: shared target data (driver)

Files: `src/apm_lite/targets.py`, `src/apm_lite/manifest.py`, `src/apm_lite/fs.py`, `tests/test_manifest.py`, `tests/test_fs.py`.

- `Target` gains these fields. Defaults keep the `claude` and `antigravity` entries as they are, apart from the fields each sets below.

  | field | meaning | claude | antigravity | cursor | codex | gemini |
  |---|---|---|---|---|---|---|
  | `skills_root` | parent of `skills/` | `.claude` | `.agents` | `.agents` | `.agents` | `.agents` |
  | `rule_ext` | rule file extension, `None` = no rules | `.md` | `.md` | `.mdc` | `None` | `None` |
  | `hook_key` | top-level key in the hook file | `hooks` | `apm` | `hooks` | `hooks` | `hooks` |
  | `owned_container` | `hook_key` belongs to apm-lite alone | no | yes | no | no | no |
  | `hook_defaults` | top-level keys set when entries are added | — | — | `{"version": 1}` | — | — |
  | `event_map` | source event → native event | — | — | 8 events (§6.4) | — | 5 entries (§6.4) |
  | `allowed_events` | supported native events, `None` = any | `None` | 5 events | Cursor's 21 | Codex's 14 | `None` |
  | `skipped_events` | never supported | Pre/PostInvocation | — | — | — | Pre/PostInvocation |
  | `nested_events` | events using `{matcher, hooks}`; `None` = all | `None` | Pre/PostToolUse | none (all flat) | `None` | `None` |
  | `default_matcher` | matcher written when the source has none | `*` | `*` | `None` | `None` | `None` |
  | `timeout_ms` | write timeouts in milliseconds | no | no | no | no | yes |
  | `script_prefix` | replaces the script token's folder | `${CLAUDE_PROJECT_DIR}/.claude/hooks` | `hooks` | `.cursor/hooks` | `$(git rev-parse --show-toplevel)/.codex/hooks` | `.gemini/hooks` |
  | `force_quote` | always double-quote the rewritten token | yes | no | no | yes | no |

- `Target.native_event(event)` returns `event_map.get(event, event)`. `supports` and `is_nested` take the native name.
- `manifest.TARGET_NAMES = tuple(TARGETS)`. The `targets` hint lists the five names.
- `fs.ALLOWED_ROOTS` gains `.cursor`, `.codex`, `.gemini`.
- Tests: each new name is accepted; `targets: [copilot]` is rejected (the existing `cursor` rejection case moves to `copilot`); `check_destination` accepts a path under each new root.

Acceptance: `uv run pytest tests/test_manifest.py tests/test_fs.py -q`.

## Task 2: rules (fast_worker)

Files: `src/apm_lite/deploy/rules.py`, `tests/test_rules.py`. Design §6.2.

- `render_rule(text, "cursor")`:
  - `---\napplyTo: "**/*.py"\ndescription: Python style\n---\nUse hints.\n` → `---\ndescription: "Python style"\nglobs: **/*.py\nalwaysApply: false\n---\n\nUse hints.\n`
  - `applyTo: "a/**/*.py, b/{x,y}/*.ts"`, no description → `---\nglobs: a/**/*.py, b/{x,y}/*.ts\nalwaysApply: false\n---\n\n<body>`
  - no frontmatter → `---\nalwaysApply: true\n---\n\n<body>`
  - a glob with a newline or other control character → `ApmError`;
  - `applyTo: ['src/foo,bar/*.py', 'tests/**']` (a top-level comma inside one glob) → `ApmError`; `applyTo: ['a/{x,y}/*.ts']` is fine. Reuse `_split_top_level` for the check.
- `render_rule` for `codex` or `gemini` raises `ValueError` (they have no format); so does an unknown name such as `copilot`.
- `plan_rules(pkg, target)`: path is `f"{target.rules_dir}/{name}{target.rule_ext}"`; returns `{}` when `target.rule_ext is None`.
- New `has_rules(pkg) -> bool`: true when the package would contribute at least one rule source. It shares the source listing with `plan_rules` (one private generator, no duplicated filtering).
- Tests: the cases above; `plan_rules` gives `.cursor/rules/x.mdc`; `{}` for codex and gemini; `has_rules` true/false, including a non-APM package shape and a symlinked instructions folder.

Acceptance: `uv run pytest tests/test_rules.py -q`.

## Task 3: hooks (deep_reasoner)

Files: `src/apm_lite/deploy/hooks.py`, `tests/test_hooks.py`. Design §6.4. Upstream: `integration/hook_native_formats.py` (`_cursor_matcher`, `_render_nested_document`), `integration/hook_integrator.py` (`_HOOK_EVENT_MAP`, `_rewrite_command_for_target`).

- No branch on `target.name` is left in `rewrite_command`, `plan_hooks` or `merge`; they read the Task 1 fields. The Cursor matcher table is the one exception and is selected by a `Target` field or the name, whichever is smaller.
- `rewrite_command`: `<script_prefix>/<pkg>/<rel>`; double quotes always when `force_quote`, source quoting kept otherwise. Existing Claude and Antigravity outputs don't change.
- `timeout` must be a positive integer (bools, `0` and negatives are an `ApmError` naming the file), for every target.
- Events: translate with `native_event`, then `supports`. The warning names the source event and the target. `HookEntry.event` is the native name.
- Shapes, for the source `{"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "./check.sh", "timeout": 10}]}], "Stop": [{"command": "echo done"}]}}` from package `pkg`:
  - cursor: `preToolUse: [{"type": "command", "command": ".cursor/hooks/pkg/check.sh", "timeout": 10, "matcher": "Shell"}]`, `stop: [{"type": "command", "command": "echo done", "loop_limit": null}]`
  - codex: `PreToolUse: [{"matcher": "Bash", "hooks": [{"type": "command", "command": "\"$(git rev-parse --show-toplevel)/.codex/hooks/pkg/check.sh\"", "timeout": 10}]}]`, `Stop: [{"hooks": [{"type": "command", "command": "echo done"}]}]`
  - gemini: `BeforeTool: [{"matcher": "Bash", "hooks": [{"type": "command", "command": ".gemini/hooks/pkg/check.sh", "timeout": 10000}]}]`; a `preToolUse` source event also lands in `BeforeTool`, `SessionEnd: [{"hooks": [{"type": "command", "command": "echo done"}]}]`
- Cursor matcher: every row of the design §6.4 list has a test, including each rejected form and a native-event source (`preToolUse` with matcher `Shell`, `beforeShellExecution` with a regex) passing through unchanged.
- Codex quoting follows Claude's rules: an unquoted, double-quoted or single-quoted source token all become one double-quoted token.
- Skipped events: `Notification` for cursor and codex; `PreInvocation` for cursor, codex and gemini; each warns.
- `hooks-cursor.json`, `hooks-codex.json`, `hooks-gemini.json` each replace the generic files for their target and are ignored by the other four. The existing tests that use `hooks-cursor.json` as a foreign harness file move to `hooks-copilot.json`.
- `merge`:
  - cursor, codex, gemini: user entries under `hooks` survive install and strip; a user entry equal to an owned one is kept once (multiset); other top-level keys (`mcpServers`, `theme`, `version`) keep their values and order.
  - cursor: `version: 1` added when entries are added and `version` is absent; an existing `version` kept; `version` not removed on strip.
  - idempotent: merging the same plan into its own output changes nothing, for all five targets.
- Bundle files land under `<target.hooks_dir>/<pkg>/` for the new targets.

Acceptance: `uv run pytest tests/test_hooks.py -q`.

## Task 4: reconcile (driver)

Files: `src/apm_lite/reconcile.py`, `tests/test_reconcile.py`, `tests/test_skills.py`. Design §6.3, §6.4 step 1, §7.1.

- Skills are planned once per distinct `target.skills_dir` per package.
- One warning per command when a configured target has `rule_ext is None` and any package `has_rules`.
- A target's native hook file is read only when its sidecar exists or it has planned entries; otherwise the target is skipped in the hook-file loop. A malformed file that is read fails with the comment hint from design §6.4.
- One warning when `codex` is configured, a Codex command was rewritten to a script path, and the project folder has no `.git`.
- Tests:
  - the existing `test_bad_hook_json_prevents_any_deployment` matrix is rewritten to the design §9 read matrix (it currently expects a malformed `.agents/hooks.json` to fail a Claude-only project, which R26 no longer says);
  - every new design §9 case: shared skills folder (including edited and user-added files, and two packages claiming one skill), rules warning, retiring each new target with user, edited and duplicate hook entries, fault injection between native file and sidecar for the new targets, bad timeout leaving a user's `.codex/hooks.json` untouched, all five targets together;
  - `.gemini/settings.json` with `//` comments: untouched when no package ships hooks; an error with the hint, and no writes, when one does.

Acceptance: `uv run pytest tests/test_reconcile.py tests/test_skills.py -q`.

## Task 5: end-to-end gate (e2e_tester)

Files: `tests/e2e/`. The author-package scenario runs for all five targets through the installed `apm` command: native rule file (or none, with the warning), skill under the right folder, hook entry in the native shape, and the deployed hook command executed from the directory that harness uses (`.agents/` for Antigravity, the project root for the rest, with `CLAUDE_PROJECT_DIR` and `GEMINI_PROJECT_DIR` set to the project). User-added hook entries and other keys survive install and uninstall. The Codex command is run from a subfolder of a git-initialised project and from its root. This proves the deployed paths resolve under each harness's documented cwd and environment. It doesn't prove a harness discovers the files, fires the event or matches the matcher; Task 7 covers that for Cursor and Codex.

Acceptance: `uv run pytest tests/e2e -q`.

## Task 6: docs and version (fast_worker)

- `README.md`, `docs/user-guide/reference.md`, `docs/user-guide/authoring-packages.md`: the five targets, target paths table, rule formats, per-target hook files, events, command paths, write roots, the Cursor "import Claude hooks" note, and the Gemini limits from design §11.
- `src/apm_lite/cli.py` group docstring, `pyproject.toml` description.
- Version `0.2.0` in `pyproject.toml` and `src/apm_lite/__init__.py`; `--version` asserts; `tests/fixtures/compat/apm.lock.yaml` regenerated with `APM_LITE_REGEN_COMPAT=1` (`apm_version` is the only changed line).
- `CHANGELOG.md`: `## 0.2.0 (2026-11-17)`.

Acceptance: `uv run pytest -q`; `uv run apm --version` prints `0.2.0`.

## Task 7: live probe (operator)

Scratch project outside the repo, `git init`-ed, with `targets: [cursor, codex]` and a local package: one always-on rule holding a codeword, one skill holding a different codeword, a `SessionStart` hook and a `PreToolUse` hook with matcher `Bash`, each appending its event name and `pwd` to a marker file. Run `cursor-agent` and `codex` non-interactively with a prompt that asks for the rule's codeword, asks to use the skill, and asks to run one shell command (`echo probe`) so the tool hook has something to fire on. For Codex, run once from the project root and once from a subfolder.

Record per CLI: CLI version; the trust and hook-review state (Codex needs a trusted project and reviewed hooks; say how that was set or that it couldn't be); whether the rule codeword came back (Cursor only); whether the skill codeword came back; each marker line and the `pwd` in it.

Each check ends as `verified`, `failed` or `not verified (reason)`. A check that couldn't run is `not verified`, never a pass. Results go to `docs/verification/test-results.md` and design §11.

Acceptance: the result table with marker-file evidence under `.handoff/evidence/T8/`.

## Task 8: traceability review (driver)

Requirements → code → tests for R2, R20–R24a, R26, R30, and R25, R28, R29, R37 for the new targets. Fix gaps. Full suite and ruff clean.
