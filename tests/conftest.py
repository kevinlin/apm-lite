"""Shared fixtures: an offline fake internal Git server and an empty project dir."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest

# Fixed so commit and tag SHAs are reproducible across runs and machines.
COMMIT_DATE = "2026-01-01T00:00:00+00:00"
DEFAULT_BRANCH = "main"
FAKE_HOST = "git.test"


def _git(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        cmd = " ".join(["git", *args])
        raise RuntimeError(f"{cmd} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout.strip()


class ServerRepo:
    """A bare repo on the fake server plus a work clone used to publish to it."""

    def __init__(self, path: str, bare: Path, work: Path):
        self.path = path
        self.bare = bare
        self.work = work

    @property
    def https(self) -> str:
        return f"https://{FAKE_HOST}/{self.path}.git"

    @property
    def ssh(self) -> str:
        return f"ssh://git@{FAKE_HOST}/{self.path}.git"

    @property
    def scp(self) -> str:
        return f"git@{FAKE_HOST}:{self.path}.git"

    def _work_git(self, *args: str) -> str:
        dates = {"GIT_AUTHOR_DATE": COMMIT_DATE, "GIT_COMMITTER_DATE": COMMIT_DATE}
        return _git(*args, cwd=self.work, env=dates)

    def commit(
        self,
        files: dict[str, str | bytes],
        message: str = "c",
        exec_files: Iterable[str] = (),
    ) -> str:
        """Replace the whole tree with `files`, commit, push, and return the SHA."""
        for child in self.work.iterdir():
            if child.name == ".git":
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        executable = set(exec_files)
        for rel, content in files.items():
            dest = self.work / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content.encode() if isinstance(content, str) else content)
            dest.chmod(0o755 if rel in executable else 0o644)
        self._work_git("add", "-A")
        self._work_git("commit", "--allow-empty", "-q", "-m", message)
        self._work_git("push", "-q", "origin", DEFAULT_BRANCH)
        return self._work_git("rev-parse", "HEAD")

    def tag(
        self,
        name: str,
        sha: str | None = None,
        *,
        annotated: bool = False,
        force: bool = False,
    ) -> None:
        target = sha or self._work_git("rev-parse", "HEAD")
        cmd = ["tag"]
        if annotated:
            cmd += ["-a", "-m", name]
        if force:
            cmd.append("-f")
        self._work_git(*cmd, name, target)
        self._work_git("push", "-q", *(["--force"] if force else []), "origin", f"refs/tags/{name}")

    def branch(self, name: str, sha: str | None = None) -> None:
        target = sha or self._work_git("rev-parse", "HEAD")
        self._work_git("push", "-q", "origin", f"{target}:refs/heads/{name}")

    def set_config(self, key: str, value: str) -> None:
        _git("config", key, value, cwd=self.bare)


class GitServer:
    """Serves bare repos under `<tmp>/server/` for the fake host `git.test`."""

    def __init__(self, root: Path, work_root: Path, gitconfig: Path):
        self.root = root
        self.work_root = work_root
        self.gitconfig = gitconfig

    def repo(self, path: str) -> ServerRepo:
        bare = self.root / f"{path}.git"
        work = self.work_root / path
        bare.parent.mkdir(parents=True, exist_ok=True)
        _git("init", "--bare", "-q", str(bare))
        _git("symbolic-ref", "HEAD", f"refs/heads/{DEFAULT_BRANCH}", cwd=bare)
        work.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "-q", str(bare), str(work))
        _git("symbolic-ref", "HEAD", f"refs/heads/{DEFAULT_BRANCH}", cwd=work)
        return ServerRepo(path, bare, work)


@pytest.fixture
def git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    gitconfig = tmp_path / "gitconfig"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@git.test")
    # Every git.test URL resolves to the local fake server, even for repos no test created, so
    # no test can reach the network (N8).
    key = f"url.file://{tmp_path / 'server'}/.insteadOf"
    for prefix in (f"https://{FAKE_HOST}/", f"ssh://git@{FAKE_HOST}/", f"git@{FAKE_HOST}:"):
        _git("config", "--file", str(gitconfig), "--add", key, prefix)
    return gitconfig


@pytest.fixture
def git_server(tmp_path: Path, git_env: Path) -> GitServer:
    return GitServer(tmp_path / "server", tmp_path / "work", git_env)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj
