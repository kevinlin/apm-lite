# apm-lite

apm-lite installs rules, skills and hooks from Git servers into Claude Code and Antigravity projects. You list the packages a project needs in `apm.yml`. `apm install` fetches them with `git` (or reads a local path), deploys them into `.claude/` and `.agents/`, and records the exact commits in `apm.lock.yaml`, so two developers get the same files. It is a cut-down fork of APM (Agent Package Manager) v0.33.0 and keeps upstream's `apm.yml` and `apm.lock.yaml` formats, so manifests and packages can move between the two tools. apm-lite opens no network connections itself: all access goes through `git`, with whatever credentials your `git clone` already uses.

## Install

You need Python 3.10 or later, `pip`, `git` on your PATH, and macOS or Linux.

### From a clone

```sh
git clone <your internal mirror of this repo>
cd apm-lite
pip install .
```

The command is `apm`. If pip warns that its script directory isn't on your PATH, add that directory or pass `--user` and add `~/.local/bin`.

### Without a clone

All of apm-lite is one file, so if `git clone` of this repo is blocked you can copy two raw files instead: `src/apm_lite.py` and `requirements.txt`.

```sh
curl -fsSLO <raw URL of this repo>/src/apm_lite.py
curl -fsSLO <raw URL of this repo>/requirements.txt
pip install -r requirements.txt
python apm_lite.py --version
```

Run it as `python apm_lite.py <command>`, or add an alias to get the `apm` command the rest of this README uses:

```sh
alias apm='python /path/to/apm_lite.py'
```

That name clashes with upstream APM's `apm`. If both are installed, whichever comes first on your PATH runs. Uninstall one, or reorder PATH.

## Quickstart

```sh
cd your-project
apm init
apm install https://git.corp.example/team/agent-rules.git#v1.0.0
```

`apm init` writes a starter `apm.yml`. `apm install <url>` adds the dependency to it exactly as you typed it, deploys the package, and writes `apm.lock.yaml`. Commit `apm.yml` and `apm.lock.yaml`. A teammate who clones the repo runs `apm install` and gets the same result. `apm_modules/`, the fetch cache, is added to `.gitignore` for you.

Claude Code is the default target. To deploy for Antigravity as well, set `targets: [claude, antigravity]` in `apm.yml`.

## Commands

| Command | What it does |
|---|---|
| `apm init` | Write a starter `apm.yml`. Refuses if one exists. |
| `apm install` | Install every dependency in `apm.yml` and write the lockfile. |
| `apm install <dep>` | Add `<dep>` to `apm.yml`, then install. |
| `apm install <dep> --skill NAME` | Same, but deploy only the named skills of a skill collection. Repeatable. |
| `apm install --frozen` | Install exactly what `apm.lock.yaml` records. Fails if `apm.yml` and the lockfile disagree. |
| `apm uninstall <dep>` | Remove `<dep>` from `apm.yml` and delete what it deployed. |
| `apm update [<dep>]` | Ignore the lockfile pins (all, or just `<dep>`), re-resolve refs and install. |
| `apm deps list` | Print each locked package with its source, ref and short commit. |
| `apm deps tree` | Print the locked packages as a dependency tree. |
| `apm --version` | Print the version. |

Add `-v` before the command to see each `git` call and each file written or deleted.

## Documentation

- [docs/user-guide/reference.md](docs/user-guide/reference.md): `apm.yml`, dependency forms, the lockfile, target paths, command flags, and how updates and edits are handled.
- [docs/user-guide/authoring-packages.md](docs/user-guide/authoring-packages.md): how to build and release a package of rules, skills and hooks.
