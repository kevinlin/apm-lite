"""The `apm` command line (design §8)."""

from __future__ import annotations

from pathlib import Path

import click

from apm_lite import __version__, lockfile, manifest
from apm_lite.errors import ApmError
from apm_lite.fs import atomic_write, check_destination
from apm_lite.git import Git
from apm_lite.reconcile import Result, reconcile

MANIFEST = "apm.yml"
LOCKFILE = "apm.lock.yaml"


class _Group(click.Group):
    """Turn `ApmError` into `error:` / `hint:` lines on stderr and exit status 1."""

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except ApmError as exc:
            click.echo(f"error: {exc.message}", err=True)
            if exc.hint:
                click.echo(f"hint: {exc.hint}", err=True)
            ctx.exit(1)


def _warn(message: str) -> None:
    click.echo(f"warning: {message}", err=True)


def _git(ctx: click.Context) -> Git:
    return Git(verbose=ctx.obj["verbose"], echo=lambda line: click.echo(line, err=True))


def _load(project: Path) -> manifest.Manifest:
    return manifest.load(project / MANIFEST, is_root=True, warn=_warn)


def _report(ctx: click.Context, result: Result) -> None:
    # reconcile() already warned (through _warn) about kept edits and skipped content.
    if ctx.obj["verbose"]:
        for path in result.written:
            click.echo(f"wrote {path}")
        for path in result.deleted:
            click.echo(f"deleted {path}")
    click.echo(
        f"{len(result.lock.deps)} package(s) locked; "
        f"{len(result.written)} file(s) written, {len(result.deleted)} deleted"
    )


def _select_key(m: manifest.Manifest, project: Path, selector: str) -> str:
    """Map a user selector to a dependency key: a declared entry, or any locked package."""
    for spec in m.deps:
        if manifest.entry_matches(spec, selector):
            return spec.key
    lock = lockfile.read(project / LOCKFILE)
    if lock and selector in lock.deps:
        return selector
    raise ApmError(
        f"no dependency matches {selector!r}",
        hint="use the entry as written in apm.yml, or its host/path key from 'apm deps list'",
    )


@click.group(cls=_Group)
@click.version_option(__version__, prog_name="apm-lite")
@click.option("-v", "--verbose", is_flag=True, help="Show git commands and file changes.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Install rules, skills and hooks from internal Git into Claude Code and Antigravity."""
    ctx.obj = {"verbose": verbose}


@cli.command()
def init() -> None:
    """Create a starter apm.yml in the current directory."""
    project = Path.cwd()
    path = check_destination(project, MANIFEST)
    if path.exists():
        raise ApmError(f"{MANIFEST} already exists")
    atomic_write(path, manifest.init_text(project.name).encode())
    click.echo(f"created {MANIFEST}")


@cli.command()
@click.argument("dep", required=False)
@click.option("--skill", "skills", multiple=True, help="Install only this skill (repeatable).")
@click.option("--frozen", is_flag=True, help="Install exactly apm.lock.yaml; fail on drift.")
@click.pass_context
def install(ctx: click.Context, dep: str | None, skills: tuple[str, ...], frozen: bool) -> None:
    """Install every dependency, or add DEP to apm.yml and install."""
    if frozen and dep:
        raise click.UsageError("--frozen can't be combined with a new dependency")
    if skills and not dep:
        raise click.UsageError("--skill needs a DEP")
    project = Path.cwd()
    m = _load(project)
    if dep:
        manifest.add_entry(m, dep, list(skills) or None)
    mode = "frozen" if frozen else "install"
    _report(ctx, reconcile(project, m, mode=mode, git=_git(ctx), warn=_warn))


@cli.command()
@click.argument("dep")
@click.pass_context
def uninstall(ctx: click.Context, dep: str) -> None:
    """Remove DEP from apm.yml and delete what it deployed."""
    project = Path.cwd()
    m = _load(project)
    manifest.remove_entry(m, dep)
    _report(ctx, reconcile(project, m, mode="install", git=_git(ctx), warn=_warn))


@cli.command()
@click.argument("dep", required=False)
@click.pass_context
def update(ctx: click.Context, dep: str | None) -> None:
    """Re-resolve refs for every dependency, or only DEP, and install."""
    project = Path.cwd()
    m = _load(project)
    keys = {_select_key(m, project, dep)} if dep else None
    result = reconcile(project, m, mode="update", update_keys=keys, git=_git(ctx), warn=_warn)
    _report(ctx, result)


@cli.group()
def deps() -> None:
    """Show installed dependencies from apm.lock.yaml."""


def _read_lock() -> lockfile.Lock:
    lock = lockfile.read(Path.cwd() / LOCKFILE)
    if lock is None:
        raise ApmError(f"no {LOCKFILE} here", hint="run 'apm install' first")
    return lock


def _line(dep: lockfile.LockedDep) -> str:
    ref = dep.resolved_ref or ("local" if dep.source == "local" else "HEAD")
    commit = (dep.resolved_commit or "")[:7]
    return f"{dep.name or dep.repo_url}  {dep.key}  {ref}  {commit}".rstrip()


@deps.command("list")
def deps_list() -> None:
    """One line per locked package: name, source, ref, commit."""
    lock = _read_lock()
    for key in sorted(lock.deps):
        click.echo(_line(lock.deps[key]))


@deps.command("tree")
def deps_tree() -> None:
    """Locked packages as a tree, children under the package that declared them."""
    lock = _read_lock()
    children: dict[str | None, list[lockfile.LockedDep]] = {}
    for dep in sorted(lock.deps.values(), key=lambda d: d.key):
        children.setdefault(dep.resolved_by if dep.depth > 1 else None, []).append(dep)

    def walk(parent: str | None, indent: int, seen: frozenset[str]) -> None:
        for dep in children.get(parent, []):
            click.echo("  " * indent + _line(dep))
            if dep.repo_url not in seen:
                walk(dep.repo_url, indent + 1, seen | {dep.repo_url})

    walk(None, 0, frozenset())


def main() -> None:
    cli(prog_name="apm")
