# Contributing

## Set up

You need Python 3.10 or later, [uv](https://docs.astral.sh/uv/) and `git`.

```sh
uv sync --extra dev
uv run pytest
uv run ruff check src tests
```

The tests run offline and need no tokens. They build a fake Git server from bare repos under a temp folder, and a temporary git config with `insteadOf` rules points `https://git.test/`, `ssh://git@git.test/` and `git@git.test:` at it. Look at `tests/conftest.py` before you add a flow test.

Code style is ruff with a line length of 100, type hints on function signatures, and small focused modules. Commit messages follow `type: summary`, for example `docs: add apm-lite README`.

## uv.lock is not tracked

This repo is public, and `uv.lock` records the index URL it resolved from. A lockfile committed from behind a corporate mirror would publish that URL, so `uv.lock` is in `.gitignore`. Each developer resolves through their own index.

Point uv at your PyPI mirror with an environment variable:

```sh
export UV_DEFAULT_INDEX=https://artifactory.example.com/api/pypi/pypi-remote/simple
```

or once, in `~/.config/uv/uv.toml`:

```toml
[[index]]
url = "https://artifactory.example.com/api/pypi/pypi-remote/simple"
default = true
```

Use your own mirror's URL in place of the example host. Keep credentials in your own configuration and out of the repo. Don't commit an internal host name, a mirror URL or a credential anywhere in this repo.

## Module map

```
cli ──> manifest.load(apm.yml)
    └─> reconcile.reconcile(project, manifest, mode)
          1. resolve   resolver.resolve(...) -> [ResolvedPackage, ...]
                       uses git.py and packages.py, which write only under apm_modules/
          2. plan      deploy/rules, skills, hooks for each package and target
          3. check     collisions, edited files, destinations (fs.check_destination)
          4. apply     inside fs.UndoLog: deployed files, hook files, cleanup
          5. commit    .gitignore, apm.yml, apm.lock.yaml
```

| Module | Responsibility |
|---|---|
| `cli.py` | Click commands and flags. Catches `ApmError`, prints `error:` and `hint:`, exits 1. |
| `errors.py` | `ApmError(message, hint=None)`. |
| `model.py` | The dataclasses the other modules pass around: `DepSpec`, `ResolvedPackage`, `PlannedFile`, `HookEntry`. |
| `fs.py` | `check_destination` (containment and symlink check), `atomic_write`, `safe_name`, `UndoLog`. |
| `manifest.py` | Load and validate `apm.yml`, parse dependency entries, add and remove entries with a comment-keeping round trip. |
| `refs.py` | Ref classification: full SHA, semver-looking (rejected), or literal. |
| `git.py` | `ls_remote` and `fetch`. Runs `git` with `GIT_TERMINAL_PROMPT=0` and shows its stderr. |
| `packages.py` | Put a remote package into `apm_modules/`, classify its shape, list its dependencies. |
| `resolver.py` | Breadth-first walk, lockfile pins, immutable-ref conflicts, skill subsets, cycles, depth limit, `--frozen` checks. |
| `lockfile.py` | Read and write `apm.lock.yaml`. `content_hash` and `file_hash`. |
| `targets.py` | The two `Target` definitions: root folder, rules and skills folders, hook file, sidecar, supported events. |
| `deploy/rules.py` | Plan rule files per target. |
| `deploy/skills.py` | Plan skill files per target. |
| `deploy/hooks.py` | Plan hook entries and scripts per target, and merge into or strip from the native hook files. |
| `reconcile.py` | Gather the plans, run the collision and edit checks, apply writes, clean up, commit metadata. |

Apart from `apm init`, which writes the starter `apm.yml`, two modules write to disk. `packages.py` writes only under `apm_modules/`. `reconcile.py` writes the deploy roots and the root metadata files. The deploy modules are pure: they take a package and a target and return planned files and hook entries, so you can test them without a project folder.

## Rules for changes

- The only runtime dependencies are click, PyYAML, ruamel.yaml and python-frontmatter.
- No HTTP client, update check or telemetry. The only network access is a `git` subprocess.
- Read no tokens or credential variables.
- Every write or delete in the project goes through `fs.check_destination`, and only under `.claude/`, `.agents/`, `apm_modules/`, plus `apm.yml`, `apm.lock.yaml` and `.gitignore` at the root.
- Print through `click.echo`. Errors are `error: ...` and `hint: ...` on stderr, with exit status 1.
- The specs in `docs/specs/` say why the code is shaped this way. Read them before a larger change, and keep `docs/reference.md` and `docs/authoring-packages.md` in step with the code.
