# End-to-end verification plan

Date: 2026-10-10 (Asia/Singapore)

## Scope and assumptions

Verify the consumer and package-author workflows in [the requirements](../specs/requirements_apm-lite.md), [reference](../user-guide/reference.md), and [authoring guide](../user-guide/authoring-packages.md). The design spec supplies target format and hook working-directory expectations. Existing implementation plans and tests are supporting evidence, not acceptance results.

This is a verification task. Add repeatable tests and evidence, without changing product behavior. All fixtures use synthetic content in temporary directories. Remote-style dependencies use real Git subprocesses and temporary bare repositories, reached through isolated `insteadOf` configuration. This exercises parsing, resolution, fetching and deployment, but does not validate a corporate server's TLS, SSH or credentials.

End-to-end tests and the native fixture builder live in `tests/e2e/`. Pytest discovers the tests during normal runs. Generated output and evidence live in `tests/e2e/output/`, which Git ignores. The plan and result documents stay in `docs/verification/`.

Run Claude-only and Antigravity-only variants, plus a mixed-target project. Compare output bytes, modes, lock entries, exit codes and warnings. These are functional test variants, not model evaluations. Live harness probes use only a synthetic scratch project, and must distinguish artifact correctness from actual rule/skill loading and hook execution.

The requested `docs/verification` artifacts are an explicit addition to N9's documentation inventory. No implementation design change is planned, so no product implementation or design commit is needed.

## Numbered tasks

1. Inspect specs, guides, existing tests and CLI boundaries. Record the revision and environment, then run the existing tests as a baseline.
2. Add subprocess tests using the installed `apm` entry point and existing Git fixtures. Reuse compatibility fixture construction. Verify workflow outcomes on disk rather than trusting summary messages.
3. Run the scenarios below and retain verbose output and JUnit results. For expected errors, compare the project file bytes and modes before and after, excluding the documented fetch-cache exception.
4. Install the package using `uv tool install .` into temporary tool/bin/cache directories. Run the resulting command from outside the source tree to check distribution and entry-point wiring.
5. Probe deployed synthetic context with available native harnesses. Execute deployed hook commands with each target's documented environment/cwd as a separate deterministic check. If live execution is unavailable, record the exact limitation, not a pass.
6. Run `uv run pytest -q` and `uv run ruff check src tests`. Save a requirement coverage matrix, scenario results, discovered defects and unverified boundaries in `test-results.md`.

## Scenarios and acceptance checks

| ID | Workflow | Acceptance checks | Requirements |
|---|---|---|---|
| E01 | Consumer quickstart and command errors | Missing manifest/lock hints; init fields and duplicate refusal; help/version; invalid combinations exit 2; failed add leaves manifest unchanged | R1, R2, R32, R33, R36, R37 |
| E02 | Complete mixed-target install | HTTPS tag, SSH with port, scp, collection object, local APM package, root primitives, transitive dependency; expected rules/skills/scripts, lock fields and upstream golden bytes | R4, R7, R8, R10, R11, R13, R17, R20–R22, R27, N4 |
| E03 | Author's local package test, both target variants | Local paths and missing-description warning; native rule frontmatter; copied executable script runs; user hook/config survives install and uninstall; source JSON excluded | R8, R11, R12, R20–R22, R28, R29, R34 |
| E04 | Reinstall and teammate reproduction | Reinstall invokes no Git subprocess and changes no file bytes, modes or mtimes; fresh project with same manifest/lock/local sources produces identical managed artifacts, including after branch advances | R16–R19, R25, R38, reproducibility success criterion |
| E05 | Branch movement and updates | Normal/frozen installs keep pins; selective transitive update moves only selected package; update-all moves remaining package; list/tree match lock and hierarchy | R16, R17, R19, R35, R36 |
| E06 | Skill selection and cleanup | Repeated `--skill` unions subsets; invalid name fails without metadata/deploy changes; subset drift fails frozen; uninstall by identity removes managed files/cache | R9, R19, R28, R31, R33, R34 |
| E07 | Ref conflicts, breadth-first precedence and cycles | HTTPS/SSH deduplicate; subset union; all-skills request wins; mutable conflict warns; immutable conflict names chains; cycle terminates; depth 50/51 boundary from existing integration tests | R9, R13–R15 |
| E08 | Edited and adopted files, retired targets | User rule edits survive reinstall/cleanup; identical untracked output adopted; untracked conflict and case collision reject; removed target's rules, skills, scripts and owned hooks removed; user entries survive | R25, R28, R29, R31 |
| E09 | Validation and write boundaries | Bad manifest/ref forms, transitive local path, malformed source/config/sidecar JSON, unowned Antigravity container, symlinked destination and outside-hook script reject; snapshots unchanged | R1–R6, R9, R10, R22, R25, R26, R30, R37 |
| E10 | Tamper check | Altered recorded content hash fails at locked commit with tamper message; protected files unchanged | R18, R37 |
| E11 | Hook target selection | Target-specific file replaces generic hooks; unsupported events warned/skipped; executable modes and rewritten commands correct | R22–R24 |
| E12 | Mid-transaction error/interrupt | Existing fault-injection tests fail writes/cleanup and raise KeyboardInterrupt after prior mutations; all protected bytes/modes restored | R37 |
| E13 | Isolated distribution install | Temporary `uv tool install .`, installed `apm --version`, init/install/frozen/uninstall outside source checkout; no global tool replacement | N5–N7 |
| E14 | Native harness consumption | Hidden rule codeword returned, skill's hidden answer read through skill invocation, hook marker independently observed; tool-scoped rule exercised by reading a matching file | R20–R24, Antigravity assumption |

## Evidence and coverage rules

- A passing scenario requires its file/exit/output assertions to pass. Live model claims alone do not prove hook execution.
- Existing unit and integration tests cover grammar tables, merge edge cases, depth limit, SHA-fetch fallback, mode handling and injected rollback. Report this layer separately from new subprocess end-to-end checks.
- Static checks inspect runtime imports, subprocess environment, dependencies and source size for N1–N3/N7. They do not prove absence of network activity under every possible Git/helper configuration.
- N5 is verified on the current macOS/Python only. Linux and Python 3.10–3.12 require another environment.
- N6's `uv` route is checked. `pipx` is a separate unchecked route unless available and exercised.
- N4 includes a current comparison with committed upstream-verified goldens. A new upstream executable round trip is conditional on `APM_UPSTREAM_BIN`.
- SIGKILL/power loss is outside R37's rollback guarantee. Deterministic KeyboardInterrupt injection is separate from a physical terminal Ctrl-C during deployment.
- Keep raw command output under `tests/e2e/output/`. Retain no credentials or private project contents.

## Reproduction

Use a writable UV cache in this sandbox:

```sh
export UV_CACHE_DIR=/private/tmp/apm-lite-verification-uv
mkdir -p tests/e2e/output
uv run pytest tests/e2e/test_e2e_subprocess.py -v -s --junitxml=tests/e2e/output/e2e.xml
uv run pytest -q --junitxml=tests/e2e/output/suite.xml
uv run ruff check src tests
```

Native harness and isolated tool-install commands, versions and actual results will be recorded in the result document. Temporary project paths in raw output are specific to one run.
