"""Thin wrapper around the `git` CLI: ref lookup and fetch-by-commit (design §4.1, §8)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from apm_lite.errors import ApmError

_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")
_PEELED = "^{}"


class Git:
    def __init__(self, verbose: bool = False, echo: Callable[[str], None] = print) -> None:
        self.verbose = verbose
        self.echo = echo
        self._refs: dict[str, dict[str, str]] = {}

    def run(self, args: list[str], cwd: Path | None = None) -> str:
        """Run `git <args>`, return stdout, and raise `ApmError` with git's stderr on failure."""
        cmd = f"git {' '.join(args)}"
        if self.verbose:
            self.echo(cmd)
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise ApmError(f"{cmd} failed", hint=proc.stderr.strip() or None)
        return proc.stdout

    def ls_remote(self, url: str) -> dict[str, str]:
        """Return `{ref name: commit}` for *url*; annotated tags map to their peeled commit."""
        if url not in self._refs:
            refs: dict[str, str] = {}
            for line in self.run(["ls-remote", "--", url]).splitlines():
                sha, _, name = line.partition("\t")
                if name.endswith(_PEELED):
                    name = name[: -len(_PEELED)]
                if sha and name:
                    refs[name] = sha
            self._refs[url] = refs
        return self._refs[url]

    def fetch(self, url: str, commit: str, dest: Path) -> None:
        """Put the tree of *commit* from *url* into the new directory *dest*, without `.git`."""
        if dest.exists():
            raise ApmError(f"cannot fetch into existing path {dest}")
        try:
            try:
                self.run(["init", "-q", "--", str(dest)])
                self.run(["fetch", "-q", "--depth", "1", "--", url, commit], cwd=dest)
                self.run(["checkout", "-q", "FETCH_HEAD"], cwd=dest)
            except ApmError:
                shutil.rmtree(dest, ignore_errors=True)
                if self.verbose:
                    self.echo(f"fallback: full clone of {url}")
                self.run(["clone", "-q", "--", url, str(dest)])
                self.run(["checkout", "-q", commit, "--"], cwd=dest)
            shutil.rmtree(dest / ".git")
        except BaseException:
            shutil.rmtree(dest, ignore_errors=True)
            raise


def resolve_ref(refs: dict[str, str], ref: str | None, url: str) -> tuple[str, bool]:
    """Map *ref* to `(commit, immutable)` using the `ls_remote` result for *url*."""
    if ref is None:
        if "HEAD" not in refs:
            raise ApmError(f"{url} has no HEAD", hint="the repository may be empty")
        return refs["HEAD"], False
    if _SHA_RE.fullmatch(ref):
        return ref, True
    tag = f"refs/tags/{ref}"
    if tag + _PEELED in refs:
        return refs[tag + _PEELED], True
    if tag in refs:
        return refs[tag], True
    branch = f"refs/heads/{ref}"
    if branch in refs:
        return refs[branch], False
    raise ApmError(f"ref {ref!r} not found in {url}", hint="check the tag or branch name")
