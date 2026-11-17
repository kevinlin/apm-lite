# Manual QA: one package, five targets

The checks in `targets.feature` (IDs `TGT-01` to `TGT-24`), as a procedure a person can follow with a shell. `test_e2e_targets.py` runs the same checks automatically. Work through the sections in order; each starts from a fresh project.

Every "expect" below is exact. Where it says a file is exactly some JSON, key order and whitespace don't matter, but every key, value and list order does.

## 0. Prerequisites and helpers

- macOS or Linux, `git`, `python3`, `/bin/sh`.
- `apm` on `PATH` is the apm-lite build under test: `apm --version` prints `0.2.0` or later. (Without an install, run `uv sync --extra dev` in the repo and `export PATH="$PWD/.venv/bin:$PATH"`.)
- Nothing here touches the repo. Everything lives in a scratch folder.

```sh
export QA=$(mktemp -d)

# Fresh project next to the package. $1 = YAML list of targets, $2 = "git" to run git init.
newproj() {
  cd "$QA" && rm -rf proj && mkdir proj && cd proj || return
  printf 'name: consumer\nversion: 0.1.0\ntargets: %s\ndependencies:\n  apm:\n    - ../local-package\n' "$1" > apm.yml
  if [ "$2" = git ]; then git init -q; fi
}

# teq FILE 'expected text' (\n is a newline): prints OK or the difference.
teq() { printf -- "$2" | diff - "$1" >/dev/null && echo "OK   $1" || { echo "DIFF $1"; cat "$1"; }; }

# jeq FILE 'expected JSON': exact comparison of the parsed JSON.
jeq() {
  python3 - "$1" "$2" <<'PY'
import json, sys
actual, expected = json.load(open(sys.argv[1])), json.loads(sys.argv[2])
print(("OK   " if actual == expected else "DIFF ") + sys.argv[1])
if actual != expected:
    print(json.dumps(actual, indent=1))
PY
}

# hookcmd FILE KEY...: print the command stored at that path of a hook file (numbers index lists).
hookcmd() {
  python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
for k in sys.argv[2:]:
    d = d[int(k) if k.isdigit() else k]
print(d)' "$@"
}

# snap [mtime]: path, mode and hash of every file under the current folder (and mtime if asked).
snap() {
  python3 - "$1" <<'PY'
import hashlib, os, sys
for d, _, names in sorted(os.walk(".")):
    if ".git" in d.split(os.sep):
        continue
    for n in sorted(names):
        p = os.path.join(d, n)
        if os.path.islink(p):
            continue
        s = os.stat(p)
        mtime = s.st_mtime_ns if sys.argv[1] == "mtime" else ""
        print(p, oct(s.st_mode), mtime, hashlib.sha256(open(p, "rb").read()).hexdigest()[:12])
PY
}
```

### The author package (build once)

```sh
mkdir -p "$QA/local-package/.apm/"{instructions,hooks,skills/review/references}
cd "$QA/local-package"
printf "name: local\nversion: '1'\n" > apm.yml
printf -- "---\napplyTo: '**/*.py'\n---\nUse hints.\n" > .apm/instructions/local.instructions.md
printf -- "---\nname: review\ndescription: Review code.\n---\nReview.\n" > .apm/skills/review/SKILL.md
printf "Read the guide.\n" > .apm/skills/review/references/guide.md
mkdir .apm/skills/review/.git && echo "not deployed" > .apm/skills/review/.git/secret
ln -s "$QA/outside" .apm/skills/review/link
cat > .apm/hooks/check.sh <<'EOF'
#!/bin/sh
printf 'HOOK_OK\n'
EOF
chmod 755 .apm/hooks/check.sh
cat > .apm/hooks/hooks.json <<'EOF'
{
  "PreToolUse": [{"matcher": "*", "hooks": [{"command": "./check.sh", "timeout": 10}]}],
  "PreInvocation": [{"command": "echo invocation"}],
  "SessionStart": [{"command": "echo session"}],
  "Notification": [{"command": "echo notification"}]
}
EOF
```

## 1. Rules

### TGT-01 A rule with applyTo becomes the native rule file

For each row: `newproj "[<target>]" && apm install`, then run the check.

| target | check |
|---|---|
| claude | `teq .claude/rules/local.md '---\npaths:\n  - "**/*.py"\n---\n\nUse hints.\n'` |
| antigravity | `teq .agents/rules/local.md '---\ntrigger: glob\nglobs: "**/*.py"\n---\n\nUse hints.\n'` |
| cursor | `teq .cursor/rules/local.mdc '---\nglobs: **/*.py\nalwaysApply: false\n---\n\nUse hints.\n'` |

Expect `OK` for each. Then `find . -path '*/rules/*'` lists that one file and nothing else.

### TGT-02 Codex and Gemini: no rule file, one warning

For `codex` and for `gemini`: `newproj "[<target>]" && apm install`.

Expect: exit 0. `find . -name rules -o -name 'local.*'` prints nothing. stderr has exactly one `warning:` line that contains `AGENTS.md` and the target's name.

### TGT-03 The rules warning is printed once and names only the targets without rules

Run `newproj "<targets>" && apm install` for each row and count the `warning:` lines that contain `AGENTS.md`.

| targets | count | the warning names |
|---|---|---|
| `[codex, gemini]` | 1 | codex and gemini |
| `[cursor, codex]` | 1 | codex only |
| `[claude, antigravity, cursor]` | 0 | nothing |

### TGT-04 A rule without applyTo is always active

Replace the package rule: `cd "$QA/local-package/.apm/instructions" && rm local.instructions.md && printf 'Always hints.\n' > always.instructions.md`. For each row: `newproj "[<target>]" && apm install`, then:

| target | check |
|---|---|
| claude | `teq .claude/rules/always.md 'Always hints.\n'` |
| antigravity | `teq .agents/rules/always.md '---\ntrigger: always_on\n---\n\nAlways hints.\n'` |
| cursor | `teq .cursor/rules/always.mdc '---\nalwaysApply: true\n---\n\nAlways hints.\n'` |

Put the package back for the rest of the procedure: `rm always.instructions.md && printf -- "---\napplyTo: '**/*.py'\n---\nUse hints.\n" > local.instructions.md`.

## 2. Skills

For both scenarios the skill must have exactly two files and nothing else: `find <skills dir>/review | sort` prints the folder, `SKILL.md`, `references`, `references/guide.md`. No `.git`, no `link`. Check the content with
`teq <dir>/review/SKILL.md '---\nname: review\ndescription: Review code.\n---\nReview.\n'` and `teq <dir>/review/references/guide.md 'Read the guide.\n'`.

### TGT-05 Claude

`newproj "[claude]" && apm install`. Skills dir is `.claude/skills`. Expect `ls -d .agents` to fail: there is no `.agents` folder.

### TGT-06 Antigravity, Cursor, Codex, Gemini

For each of the four: `newproj "[<target>]" && apm install`. Skills dir is `.agents/skills`. Expect `ls .agents/skills` to print only `review`, and `ls -d .claude/skills .cursor/skills .codex/skills .gemini/skills` to find none of them.

## 3. Hook files, scripts and events

### TGT-07 to TGT-11 The hook file in each target's native shape

For each row: `newproj "[<target>]" && apm install`, then the `jeq` check. After each, the hook files of the four other targets (the **File** column of the other rows) must not exist.

TGT-07, claude:

```sh
jeq .claude/settings.json '{"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/.claude/hooks/local-package/check.sh\"", "timeout": 10}]}], "SessionStart": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo session"}]}], "Notification": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo notification"}]}]}}'
```

TGT-08, antigravity:

```sh
jeq .agents/hooks.json '{"apm": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "hooks/local-package/check.sh", "timeout": 10}]}], "PreInvocation": [{"type": "command", "command": "echo invocation"}]}}'
```

TGT-09, cursor (flat entries, `version` added):

```sh
jeq .cursor/hooks.json '{"version": 1, "hooks": {"preToolUse": [{"type": "command", "command": ".cursor/hooks/local-package/check.sh", "timeout": 10, "matcher": "*"}], "sessionStart": [{"type": "command", "command": "echo session"}]}}'
```

TGT-10, codex:

```sh
jeq .codex/hooks.json '{"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "\"$(git rev-parse --show-toplevel)/.codex/hooks/local-package/check.sh\"", "timeout": 10}]}], "SessionStart": [{"hooks": [{"type": "command", "command": "echo session"}]}]}}'
```

TGT-11, gemini (`PreToolUse` lands as `BeforeTool`, timeout in milliseconds):

```sh
jeq .gemini/settings.json '{"hooks": {"BeforeTool": [{"matcher": "*", "hooks": [{"type": "command", "command": ".gemini/hooks/local-package/check.sh", "timeout": 10000}]}], "SessionStart": [{"hooks": [{"type": "command", "command": "echo session"}]}], "Notification": [{"hooks": [{"type": "command", "command": "echo notification"}]}]}}'
```

Hook files: `.claude/settings.json`, `.agents/hooks.json`, `.cursor/hooks.json`, `.codex/hooks.json`, `.gemini/settings.json`.

### TGT-12 Hook scripts are copied, the source JSON is not

For each target and its root (`claude .claude`, `antigravity .agents`, `cursor .cursor`, `codex .codex`, `gemini .gemini`): `newproj "[<target>]" && apm install`, then:

```sh
ls <root>/hooks/local-package          # prints check.sh only
diff "$QA/local-package/.apm/hooks/check.sh" <root>/hooks/local-package/check.sh && echo OK   # same content
ls -l <root>/hooks/local-package/check.sh                                                      # -rwxr-xr-x (755)
```

### TGT-13 Unsupported events are skipped with a warning

For each row: `newproj "[<target>]" && apm install`. Expect exactly one `warning:` line per listed event, containing the event name and the target name, and no warning for any other event name.

| target | events skipped |
|---|---|
| claude | PreInvocation |
| antigravity | SessionStart, Notification |
| cursor | PreInvocation, Notification |
| codex | PreInvocation, Notification |
| gemini | PreInvocation |

(Codex also prints the git warning of TGT-17 when the project is not a git repository. That line names no event.)

## 4. Running the deployed hook command

Run the stored command with `/bin/sh -c`, from the folder the harness uses. Expect exit status 0 and exactly `HOOK_OK` on stdout.

### TGT-14 Claude, Antigravity, Cursor, Gemini

```sh
# claude: project root, CLAUDE_PROJECT_DIR set
newproj "[claude]" && apm install
C=$(hookcmd .claude/settings.json hooks PreToolUse 0 hooks 0 command); echo "$C"
(cd "$QA/proj" && CLAUDE_PROJECT_DIR="$QA/proj" /bin/sh -c "$C"; echo "exit $?")

# antigravity: .agents/
newproj "[antigravity]" && apm install
C=$(hookcmd .agents/hooks.json apm PreToolUse 0 hooks 0 command); echo "$C"
(cd "$QA/proj/.agents" && /bin/sh -c "$C"; echo "exit $?")

# cursor: project root
newproj "[cursor]" && apm install
C=$(hookcmd .cursor/hooks.json hooks preToolUse 0 command); echo "$C"
(cd "$QA/proj" && /bin/sh -c "$C"; echo "exit $?")

# gemini: project root, GEMINI_PROJECT_DIR set
newproj "[gemini]" && apm install
C=$(hookcmd .gemini/settings.json hooks BeforeTool 0 hooks 0 command); echo "$C"
(cd "$QA/proj" && GEMINI_PROJECT_DIR="$QA/proj" /bin/sh -c "$C"; echo "exit $?")
```

Each prints the command, then `HOOK_OK`, then `exit 0`.

### TGT-15 Codex from the project root

```sh
newproj "[codex]" git && apm install
C=$(hookcmd .codex/hooks.json hooks PreToolUse 0 hooks 0 command); echo "$C"
(cd "$QA/proj" && /bin/sh -c "$C"; echo "exit $?")
```

Expect `HOOK_OK` and `exit 0`.

### TGT-16 Codex from a subfolder

Continue in the project from TGT-15:

```sh
mkdir -p "$QA/proj/src/app/ui"
(cd "$QA/proj/src/app/ui" && /bin/sh -c "$C"; echo "exit $?")
```

Expect `HOOK_OK` and `exit 0`.

### TGT-17 Codex warns when the project is not a git repository

```sh
newproj "[codex]" git && apm install     # no warning mentions git
newproj "[codex]" && apm install         # exactly one warning mentions codex and git
```

Expect exit 0 both times.

## 5. User entries, uninstall, retirement

### TGT-18 The user's entries and other keys survive install and uninstall

For each row: `newproj "[<target>]"`, create the user's file, `apm install`, check the file, `apm uninstall ../local-package`, check the file again. `mkdir -p` the folder first.

claude:

```sh
newproj "[claude]"; mkdir -p .claude
cat > .claude/settings.json <<'EOF'
{"permissions": {"allow": ["Read"]}, "hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo user-stop"}]}], "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-pre"}]}]}}
EOF
apm install
jeq .claude/settings.json '{"permissions": {"allow": ["Read"]}, "hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo user-stop"}]}], "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-pre"}]}, {"matcher": "*", "hooks": [{"type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/.claude/hooks/local-package/check.sh\"", "timeout": 10}]}], "SessionStart": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo session"}]}], "Notification": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo notification"}]}]}}'
apm uninstall ../local-package
jeq .claude/settings.json '{"permissions": {"allow": ["Read"]}, "hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo user-stop"}]}], "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-pre"}]}]}}'
```

antigravity (the user's container is `user`; apm owns `apm`):

```sh
newproj "[antigravity]"; mkdir -p .agents
cat > .agents/hooks.json <<'EOF'
{"user": {"Stop": [{"command": "echo user-stop"}], "PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "echo user-pre"}]}]}}
EOF
apm install
jeq .agents/hooks.json '{"user": {"Stop": [{"command": "echo user-stop"}], "PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "echo user-pre"}]}]}, "apm": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "hooks/local-package/check.sh", "timeout": 10}]}], "PreInvocation": [{"type": "command", "command": "echo invocation"}]}}'
apm uninstall ../local-package
jeq .agents/hooks.json '{"user": {"Stop": [{"command": "echo user-stop"}], "PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "echo user-pre"}]}]}}'
```

cursor (a user `stop` hook; `version` is added on install and stays after uninstall):

```sh
newproj "[cursor]"; mkdir -p .cursor
cat > .cursor/hooks.json <<'EOF'
{"hooks": {"stop": [{"type": "command", "command": "echo user-stop"}], "preToolUse": [{"type": "command", "command": "echo user-pre", "matcher": "Shell"}]}}
EOF
apm install
jeq .cursor/hooks.json '{"hooks": {"stop": [{"type": "command", "command": "echo user-stop"}], "preToolUse": [{"type": "command", "command": "echo user-pre", "matcher": "Shell"}, {"type": "command", "command": ".cursor/hooks/local-package/check.sh", "timeout": 10, "matcher": "*"}], "sessionStart": [{"type": "command", "command": "echo session"}]}, "version": 1}'
apm uninstall ../local-package
jeq .cursor/hooks.json '{"hooks": {"stop": [{"type": "command", "command": "echo user-stop"}], "preToolUse": [{"type": "command", "command": "echo user-pre", "matcher": "Shell"}]}, "version": 1}'
```

codex:

```sh
newproj "[codex]"; mkdir -p .codex
cat > .codex/hooks.json <<'EOF'
{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo user-stop"}]}], "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-pre"}]}]}}
EOF
apm install
jeq .codex/hooks.json '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo user-stop"}]}], "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-pre"}]}, {"matcher": "*", "hooks": [{"type": "command", "command": "\"$(git rev-parse --show-toplevel)/.codex/hooks/local-package/check.sh\"", "timeout": 10}]}], "SessionStart": [{"hooks": [{"type": "command", "command": "echo session"}]}]}}'
apm uninstall ../local-package
jeq .codex/hooks.json '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo user-stop"}]}], "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-pre"}]}]}}'
```

gemini (`mcpServers` is another top-level key):

```sh
newproj "[gemini]"; mkdir -p .gemini
cat > .gemini/settings.json <<'EOF'
{"mcpServers": {"docs": {"command": "docs-server", "args": ["--stdio"]}}, "hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "echo user-end"}]}], "BeforeTool": [{"matcher": "run_shell_command", "hooks": [{"type": "command", "command": "echo user-pre", "timeout": 5000}]}]}}
EOF
apm install
jeq .gemini/settings.json '{"mcpServers": {"docs": {"command": "docs-server", "args": ["--stdio"]}}, "hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "echo user-end"}]}], "BeforeTool": [{"matcher": "run_shell_command", "hooks": [{"type": "command", "command": "echo user-pre", "timeout": 5000}]}, {"matcher": "*", "hooks": [{"type": "command", "command": ".gemini/hooks/local-package/check.sh", "timeout": 10000}]}], "SessionStart": [{"hooks": [{"type": "command", "command": "echo session"}]}], "Notification": [{"hooks": [{"type": "command", "command": "echo notification"}]}]}}'
apm uninstall ../local-package
jeq .gemini/settings.json '{"mcpServers": {"docs": {"command": "docs-server", "args": ["--stdio"]}}, "hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "echo user-end"}]}], "BeforeTool": [{"matcher": "run_shell_command", "hooks": [{"type": "command", "command": "echo user-pre", "timeout": 5000}]}]}}'
```

Expect `OK` on every `jeq`.

### TGT-19 Uninstall removes what the package deployed

For each row: `newproj "[<target>]" && apm install`, confirm the four paths exist, `apm uninstall ../local-package`, then confirm none of them exists.

| target | rule file | skill | script | sidecar |
|---|---|---|---|---|
| claude | `.claude/rules/local.md` | `.claude/skills/review` | `.claude/hooks/local-package/check.sh` | `.claude/apm-hooks.json` |
| antigravity | `.agents/rules/local.md` | `.agents/skills/review` | `.agents/hooks/local-package/check.sh` | `.agents/apm-hooks.json` |
| cursor | `.cursor/rules/local.mdc` | `.agents/skills/review` | `.cursor/hooks/local-package/check.sh` | `.cursor/apm-hooks.json` |
| codex | none | `.agents/skills/review` | `.codex/hooks/local-package/check.sh` | `.codex/apm-hooks.json` |
| gemini | none | `.agents/skills/review` | `.gemini/hooks/local-package/check.sh` | `.gemini/apm-hooks.json` |

Check with `ls -d <paths>`: all found before, none found after.

### TGT-20 Retiring a target

For each pair (`target` / `keep`): claude / antigravity, antigravity / claude, cursor / claude, codex / claude, gemini / claude.

```sh
newproj "[<target>, <keep>]"
# create the user's own file for <target> exactly as in TGT-18 (its mkdir, cat and the file content)
apm install
(cd <keep root> && snap) > "$QA/keep-before.txt"       # <keep root> is .claude or .agents
sed -i.bak 's/^targets:.*/targets: [<keep>]/' apm.yml && rm apm.yml.bak
apm install
(cd <keep root> && snap) | diff - "$QA/keep-before.txt" && echo "keep root unchanged"
```

Expect, for `<target>`: no rule file, no skill folder, no `hooks/local-package/check.sh`, no `apm-hooks.json` (paths as in TGT-19), and its hook file equal to the **after uninstall** JSON of TGT-18 (cursor keeps `"version": 1`). Expect, for `<keep>`: its hook file equal to its TGT-07 to TGT-11 JSON, and `keep root unchanged`.

## 6. The shared `.agents/skills` folder

### TGT-21 Written once

```sh
newproj "[antigravity, cursor, codex, gemini]" && apm install
```

Expect: exit 0 and no collision error. `ls .agents/skills` prints `review`; the skill has the two files of section 2. `ls -d .claude/skills .cursor/skills .codex/skills .gemini/skills` finds none. In `apm.lock.yaml`, under the package's `deployed_files`, each of `.agents/skills/review`, `.agents/skills/review/SKILL.md` and `.agents/skills/review/references/guide.md` appears exactly once (`grep -c` each).

### TGT-22 Stays until the last sharer is removed

Continue in the project from TGT-21. After each edit run `apm install` and check `teq .agents/skills/review/SKILL.md '---\nname: review\ndescription: Review code.\n---\nReview.\n'` (expect `OK`, with `references/guide.md` still present).

```sh
sed -i.bak 's/^targets:.*/targets: [cursor, codex, gemini]/' apm.yml && rm apm.yml.bak && apm install
sed -i.bak 's/^targets:.*/targets: [codex, gemini]/' apm.yml && rm apm.yml.bak && apm install
sed -i.bak 's/^targets:.*/targets: [gemini]/' apm.yml && rm apm.yml.bak && apm install
# gemini is the last sharer: replace it
sed -i.bak 's/^targets:.*/targets: [claude]/' apm.yml && rm apm.yml.bak && apm install
ls -d .agents/skills/review    # fails: removed
ls .claude/skills/review       # SKILL.md references
```

## 7. Reinstall and all five together

### TGT-23 A second install writes nothing

For each of `[claude]`, `[antigravity]`, `[cursor]`, `[codex]`, `[gemini]` and `[claude, antigravity, cursor, codex, gemini]`:

```sh
newproj "<targets>" && apm install
snap mtime > "$QA/before.txt"
apm install            # stdout contains: 0 file(s) written, 0 deleted
snap mtime | diff - "$QA/before.txt" && echo "nothing changed"
```

Expect the stdout line and `nothing changed`.

### TGT-24 All five targets in one project

```sh
newproj "[claude, antigravity, cursor, codex, gemini]" git && apm install
```

Expect, all from this one install:

1. Rule files as in TGT-01 for claude, antigravity and cursor; no rule file for codex or gemini.
2. Each hook file exactly as in TGT-07 to TGT-11.
3. `.claude/hooks/local-package/check.sh`, `.agents/hooks/local-package/check.sh`, `.cursor/hooks/local-package/check.sh`, `.codex/hooks/local-package/check.sh` and `.gemini/hooks/local-package/check.sh`, each as in TGT-12.
4. A warning for each skipped event of TGT-13 (`PreInvocation` for claude, cursor, codex and gemini; `SessionStart` and `Notification` for antigravity; `Notification` for cursor and codex).
5. Exactly one `AGENTS.md` warning, naming codex and gemini.
6. `.claude/skills/review` and `.agents/skills/review` hold the skill (section 2); `.agents/skills` holds only `review`; no `.cursor/skills`, `.codex/skills` or `.gemini/skills`.
7. The five PreToolUse commands print `HOOK_OK` when run as in TGT-14, and the Codex command also from the project root and from `src/app/ui` (TGT-15, TGT-16).

## Result sheet

Mark each ID `pass`, `fail` (with what you saw) or `not run`.

| ID | Result | ID | Result | ID | Result |
|---|---|---|---|---|---|
| TGT-01 | | TGT-09 | | TGT-17 | |
| TGT-02 | | TGT-10 | | TGT-18 | |
| TGT-03 | | TGT-11 | | TGT-19 | |
| TGT-04 | | TGT-12 | | TGT-20 | |
| TGT-05 | | TGT-13 | | TGT-21 | |
| TGT-06 | | TGT-14 | | TGT-22 | |
| TGT-07 | | TGT-15 | | TGT-23 | |
| TGT-08 | | TGT-16 | | TGT-24 | |
