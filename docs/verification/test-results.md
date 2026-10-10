# End-to-end verification results

Date: 2026-10-10 (Asia/Singapore)

The documented consumer and author workflows passed in the tested environment. No product defect was found. This is a scoped verification result, not certification of every supported platform or Git server.

The [test plan](test-plan.md) was saved before adding the subprocess tests. Product source was unchanged at revision `9a38763d0a6c5d064890f44dc2ae3d87aa49acb2`. Added [subprocess tests](../../tests/e2e/test_e2e_subprocess.py) and a [synthetic native fixture builder](../../tests/e2e/native_fixture.py). Tests and documentation are uncommitted workspace additions.

The end-to-end scripts are grouped under `tests/e2e/` and remain included in normal pytest runs. Generated output and evidence are grouped under `tests/e2e/output/` and ignored by Git. Historical logs retain the file paths used when those runs were performed; the full suite was rerun after moving the files.

## Run summary

| Check | Result | Evidence |
|---|---|---|
| Existing-suite baseline | 343 passed, 1 skipped, 41.93 seconds | Observed before new tests were added; final suite evidence below supersedes this baseline |
| New real-process end-to-end cases | 16 passed, no skips | [Command transcript](../../tests/e2e/output/e2e.log), [JUnit](../../tests/e2e/output/e2e.xml) |
| Final `uv run pytest -q` | 359 passed, 1 skipped, 57.84 seconds | [Suite log](../../tests/e2e/output/suite.log), [JUnit](../../tests/e2e/output/suite.xml), [parsed counts](../../tests/e2e/output/test-summary.json) |
| `uv run ruff check src tests` | All checks passed | [Ruff log](../../tests/e2e/output/ruff.log) |
| Isolated `uv tool install .` | Installed `apm-lite==0.1.0` and its four runtime dependencies; console command worked outside checkout | [Install log](../../tests/e2e/output/tool-install.log), [installed command results](../../tests/e2e/output/installed-command.json) |
| Claude Code native probe | 4/4 functional checks passed | [Response](../../tests/e2e/output/claude-native.stdout), [command, status and hook markers](../../tests/e2e/output/claude-native.json) |
| Antigravity native probe | 4/4 functional checks passed | [Response](../../tests/e2e/output/antigravity-native.stdout), [command, status and hook markers](../../tests/e2e/output/antigravity-native.json) |

The skipped test is `test_upstream_round_trip`: `APM_UPSTREAM_BIN` was not set. The current upstream-verified golden comparison passed both in the existing compatibility test and through a real CLI subprocess. A new upstream executable round trip was not run.

The environment was macOS 26.7.1 arm64, Python 3.13.16, Git 2.56.0 and uv 0.13.0. Native versions were Claude Code 2.1.296 and `agy` 1.3.3. Dependency versions and the base revision are in [environment.json](../../tests/e2e/output/environment.json). Runtime source contains 2,190 nonblank Python lines across 18 files, below the approximate 3k size target (not an acceptance gate).

## Scenario outcomes

| Plan ID | Outcome and observations | Verification layer |
|---|---|---|
| E01 | Missing manifest/lock errors, starter fields, duplicate-init refusal, usage exit 2, version/help and failed dependency add all behaved as documented | Subprocess |
| E02 | Mixed-target fixture installed HTTPS tag, SSH port, scp, selected collection, local package, transitive skill and root rules; manifest and lock bytes matched committed goldens | Subprocess and existing compatibility tests |
| E03 | Local package installed and uninstalled correctly on each target; warning for missing description; correct rule frontmatter; copied executable hook returned `HOOK_OK`; user config/hooks survived; source hook JSON, `.git` and symlink excluded | Two subprocess target variants and direct execution of rewritten commands |
| E04 | Unchanged reinstall preserved bytes, modes and mtimes with no Git trace created; fresh teammate projects reproduced protected files exactly, including pinned branches after server tips advanced | Subprocess |
| E05 | Install/frozen kept old commits; selective transitive update changed only the leaf; update-all advanced the parent; list/tree showed commits and hierarchy; uninstall removed both caches | Subprocess |
| E06 | Repeated `--skill` merged names; missing skill failed without protected-file changes; frozen subset drift failed; ordinary install pruned the removed skill | Subprocess |
| E07 | HTTPS/SSH identity deduplicated; immutable conflict named both refs; mutable conflict warned; cycle terminated. Breadth-first precedence, subset unions/all-skills precedence and depth 50/51 boundaries passed existing tests | Subprocess plus resolver integration |
| E08 | Identical untracked rule was adopted; edited rule survived reinstall and uninstall with warnings; retiring Antigravity removed managed files and sidecar and stripped owned hook container | Subprocess plus existing user-hook preservation/collision tests |
| E09 | Malformed config/sidecar, unowned Antigravity container, destination symlink, untracked conflict, outside-hook script and transitive local dependency all failed without protected-file changes; external sentinel survived | Seven subprocess error variants; grammar/source-JSON edge cases from existing tests |
| E10 | Altered recorded content hash caused frozen install to fail with tamper/force-push warning; protected snapshot unchanged | Subprocess, plus existing cache/tamper tests |
| E11 | Unsupported events warned/skipped in both author variants; target-specific hook overrides, quoting, resources and merge edge cases passed existing tests | Subprocess plus hook unit tests |
| E12 | Injected errors at apply, sidecar and lock writes, and KeyboardInterrupt during cleanup, restored protected bytes/modes | Existing reconciliation and filesystem integration tests |
| E13 | Isolated installed command ran version/list, init, local install, frozen reinstall and uninstall; skill existed after install and was removed after uninstall | Built distribution, outside source checkout |
| E14 | Both native harnesses returned all three hidden context words; hook marker files showed actual invocation from the expected cwd | Live native processes, synthetic fixtures |

Protected-file comparisons exclude `apm_modules/`, as R37 specifies. The tests use real Git operations against temporary bare repositories with isolated `insteadOf` configuration. New subprocess cases also restrict Git to the `file` protocol. They do not stub the product's resolver, deployment or console entry point.

The first focused run had two test assertion failures: the author fixture's folder was `local-package`, but its manifest name was `local`. The test incorrectly assumed the lock's `repo_url` used the folder name. It was corrected to select the local entry by its recorded `local_path`. The rerun and final suite passed. [Initial transcript](../../tests/e2e/output/e2e-initial.log) and [initial JUnit](../../tests/e2e/output/e2e-initial.xml) are retained. No product code was changed to make tests pass.

## Native target comparison

The same synthetic context was deployed separately for each target using the isolated installed command. The prompt contained none of the expected words. Its questions required an already-loaded always-active rule, reading `src/probe.py` to activate the Python-scoped rule, and invoking `verification-probe` to obtain its answer.

| Check | Claude Code | Antigravity |
|---|---|---|
| Always-active rule: `E2E_RULE_WALNUT` | Pass | Pass |
| Python-scoped rule: `E2E_PY_THYME` | Pass | Pass |
| Skill answer: `E2E_SKILL_QUINCE` | Pass; harness reported Skill tool invocation | Pass; harness reported skill invocation |
| Real hook execution | Pass; 2 tool markers, cwd at project root | Pass; 2 invocation and 2 tool markers, cwd at `.agents/` |
| Functional score | 4/4 | 4/4 |
| Process elapsed time | 8.50 seconds | 21.51 seconds |

These scores are four binary functional checks, not a skill-quality benchmark or a model comparison. Rule and skill consumption is evidenced by hidden answers and harness responses; hook execution is independently evidenced by script-created marker files. Internal skill-routing decisions were not separately audited. [Machine-checked native results](../../tests/e2e/output/native-checks.json) include the marker contents. Claude returned `is_error: false` with no permission denials; Antigravity returned `status: SUCCESS`. Neither probe used `--dangerously-skip-permissions`.

## Requirement coverage

| Requirements | Evidence and limit |
|---|---|
| R1–R3 | Manifest unit tables check required strings, target errors, warnings and retained keys/comments; E01 tests actual init/default target behavior |
| R4–R6 | Manifest/ref unit tables cover allowed/rejected forms, ports, semver-looking refs and local constraints; E02/E07 exercise real transports and refs |
| R7–R12 | Package/skill unit tests cover shape precedence and naming; E02/E03/E06 verify deployed shapes, subsets, root rules and warnings |
| R13–R15 | Resolver integration covers breadth first, ref precedence, request chains, union, cycles and maximum depth; E05/E07 verify actual CLI graphs/conflicts |
| R16–R19 | E04–E06/E10 verify pins, fresh-cache reproducibility, updates, frozen subset drift and tamper detection; resolver tests cover other frozen drift forms |
| R20–R24 | E02/E03/E11 check native artifacts, executable commands and event filtering; both live E14 probes check harness consumption; overrides and quoting covered by unit tests |
| R25–R27 | E02/E04/E08/E09 verify adoption, no-op, user edits, conflicts, JSON ownership and gitignore; existing tests cover case collisions and all native/sidecar JSON paths |
| R28–R31 | E05/E06/E08 verify dependency/subset/target cleanup and cache deletion; E09 plus filesystem tests verify containment and symlink rejection |
| R32–R36 | All commands exercised through real processes; E13 also uses isolated built console executable |
| R37 | E01/E06/E09/E10 compare snapshots on failures; existing injected rollback tests cover mutations followed by errors/KeyboardInterrupt; physical Ctrl-C timing not tested |
| R38 | E04 compares bytes/modes/mtimes and observes no Git subprocess trace; existing integration test rejects any Git method call on reinstall |
| N1–N3 | Static imports/env/write-boundary inspection and containment tests passed; Git prompts explicitly disabled. No token-specific env reads found. No syscall/packet capture or real credential failure test |
| N4 | Current generated manifest/lock match previously upstream-verified fixtures. Fresh upstream executable round trip skipped |
| N5 | macOS arm64/Python 3.13 verified. Linux/Python 3.10–3.12 not run |
| N6 | Temporary `uv tool install .` verified. pipx route not run |
| N7 | Exactly click, PyYAML, ruamel.yaml and python-frontmatter declared and installed as runtime dependencies |
| N8 | Unit/integration/subprocess suite used offline Git fixtures, no tokens. Native harness probes are a separate online check |
| N9 | Existing required docs reviewed; `docs/verification` added by explicit task request |

[Static-check evidence](../../tests/e2e/output/static-checks.json) records runtime source hashes and the absence of network-client imports. A literal grep for `urllib` or `requests` is not an appropriate network gate here: `urllib.parse.urlsplit` only parses URLs, and the resolver has a local variable named `requests`. Inspection found no network client. Git inherits the caller environment for Git/SSH/credential helpers; apm-lite does not interpret credentials itself.

## Reproduce the native and distribution checks

Run from this repository. Use a new temporary directory each time:

```sh
scratch=$(mktemp -d /private/tmp/apm-lite-verify.XXXXXX)
export UV_CACHE_DIR=/private/tmp/apm-lite-verification-uv
UV_TOOL_DIR="$scratch/tools" UV_TOOL_BIN_DIR="$scratch/bin" uv tool install .
uv run python tests/e2e/native_fixture.py "$scratch/bin/apm" "$scratch/native"
```

Use this same prompt for both native commands:

```text
This is a synthetic context-loading test. Work only inside this scratch workspace. First give the workspace secret word from your already-loaded rules without searching for it. Then read src/probe.py and give the Python codeword from the applicable rules. Invoke the verification-probe skill and give its answer. Report the three words and whether you invoked the skill. Do not inspect credentials, global configuration, or other projects.
```

In `$scratch/native/claude`, run `claude -p "$prompt" --tools Read,Skill --allowedTools Read,Skill --setting-sources project --no-session-persistence --output-format json`. In `$scratch/native/antigravity`, run `agy -p "$prompt" --mode plan --sandbox --print-timeout 50s --output-format json --log-file "$scratch/agy.log"`. Assign the text above to `prompt` first. The recorded runs had an additional external subprocess timeout of 55 seconds. Check the three response words and `hook-fired.log` (Claude) or `.agents/hook-fired.log` (Antigravity). The JSON evidence contains the exact argv and cwd used.

## Remaining boundaries

No corporate Git server, real TLS/SSH transport, credential helper failure, Linux host, Python 3.10–3.12, pipx installation or fresh upstream executable round trip was exercised. Native probes cover one synthetic matching Python rule and one skill, rather than every possible harness configuration or matcher. Rollback was checked with deterministic KeyboardInterrupt/error injection, not a physical terminal Ctrl-C during a particular deployment write; SIGKILL/power loss remains outside the documented guarantee.

The sandbox initially rejected uv's default home cache. All successful verification used `UV_CACHE_DIR=/private/tmp/apm-lite-verification-uv`. The isolated tool and fixture directories were temporary; the saved logs, JUnit files, test sources and fixture builder are the durable evidence.
