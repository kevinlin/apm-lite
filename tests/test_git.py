import subprocess

import pytest

from apm_lite import ApmError, Git, resolve_ref


def test_ls_remote_and_resolve(git_server):
    r = git_server.repo("team/pkg")
    c1 = r.commit({"a.txt": "1"})
    r.tag("v1")
    c2 = r.commit({"a.txt": "2"})
    refs = Git().ls_remote(r.https)
    assert resolve_ref(refs, "v1", r.https) == (c1, True)
    assert resolve_ref(refs, "main", r.https) == (c2, False)
    assert resolve_ref(refs, None, r.https) == (c2, False)
    assert resolve_ref(refs, c1, r.https) == (c1, True)
    with pytest.raises(ApmError):
        resolve_ref(refs, "nope", r.https)


def test_annotated_tag_peeled(git_server):
    r = git_server.repo("team/ann")
    c = r.commit({"a": "1"})
    r.tag("v2", annotated=True)
    assert resolve_ref(Git().ls_remote(r.ssh), "v2", r.ssh) == (c, True)


def test_fetch_checks_out_commit_without_dot_git(git_server, tmp_path):
    r = git_server.repo("team/pkg")
    c1 = r.commit({"a.txt": "1"})
    r.commit({"a.txt": "2"})
    dest = tmp_path / "out"
    Git().fetch(r.scp, c1, dest)
    assert (dest / "a.txt").read_text() == "1" and not (dest / ".git").exists()


def test_fetch_falls_back_to_clone_for_unadvertised_commit(git_server, git_env, tmp_path):
    r = git_server.repo("team/old")
    c1 = r.commit({"a.txt": "1"})
    r.commit({"a.txt": "2"})
    r.set_config("uploadpack.allowReachableSHA1InWant", "false")
    r.set_config("uploadpack.allowAnySHA1InWant", "false")
    # Protocol v2 (git's default since 2.26) ignores the two settings above, so fall back to
    # v0 to model a server that refuses fetch-by-SHA.
    subprocess.run(["git", "config", "--file", str(git_env), "protocol.version", "0"], check=True)
    lines = []
    Git(verbose=True, echo=lines.append).fetch(r.https, c1, tmp_path / "out")
    assert (tmp_path / "out/a.txt").read_text() == "1"
    assert any(line.startswith("fallback: full clone") for line in lines)


def test_git_failure_carries_stderr(git_env):
    with pytest.raises(ApmError) as e:
        Git().ls_remote("https://git.test/missing/repo.git")
    assert "git ls-remote" in e.value.message and e.value.hint


def test_git_runs_with_terminal_prompt_disabled(monkeypatch, tmp_path):  # N2
    import apm_lite as git_module

    monkeypatch.delenv("GIT_TERMINAL_PROMPT", raising=False)
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs["env"])
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)
    Git().run(["status"], cwd=tmp_path)
    assert seen["GIT_TERMINAL_PROMPT"] == "0"
