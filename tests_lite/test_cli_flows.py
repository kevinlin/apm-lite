"""End-to-end command flows through the click app (plan Task 11, Review Focus 4-5)."""

from click.testing import CliRunner

from apm_lite.cli import cli

SKILL = "---\ndescription: d\n---\n"


def run(*args):
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)  # click 8.2: stderr separate


def test_install_without_manifest(project, monkeypatch):  # Review Focus 4
    monkeypatch.chdir(project)
    res = run("install")
    assert res.exit_code == 1
    assert "apm init" in res.stderr
    assert list(project.iterdir()) == []


def test_init_install_uninstall_flow(git_server, project, monkeypatch):
    monkeypatch.chdir(project)
    r = git_server.repo("t/skills")
    r.commit({"skills/a/SKILL.md": SKILL, "skills/b/SKILL.md": SKILL})
    assert run("init").exit_code == 0
    assert run("init").exit_code == 1
    assert run("install", r.scp, "--skill", "a").exit_code == 0
    assert (project / ".claude/skills/a/SKILL.md").exists()
    assert not (project / ".claude/skills/b").exists()
    assert r.scp in (project / "apm.yml").read_text()
    assert "git.test/t/skills" in run("deps", "list").stdout
    assert run("install", "--frozen").exit_code == 0
    assert run("uninstall", "git.test/t/skills").exit_code == 0
    assert not (project / ".claude/skills/a").exists()


def test_git_failure_leaves_tree(git_server, project, monkeypatch):  # Review Focus 5
    monkeypatch.chdir(project)
    run("init")
    before = (project / "apm.yml").read_bytes()
    res = run("install", "https://git.test/t/missing.git")
    assert res.exit_code == 1
    assert "git" in res.stderr
    assert (project / "apm.yml").read_bytes() == before
    assert not (project / "apm.lock.yaml").exists()
    assert sorted(p.name for p in project.iterdir()) == ["apm.yml"]


def test_frozen_rejects_with_dep_and_without_lockfile(git_server, project, monkeypatch):
    monkeypatch.chdir(project)
    run("init")
    assert run("install", "--frozen", "https://git.test/t/x.git").exit_code != 0
    assert run("install", "--frozen").exit_code == 1


def test_update_and_tree(git_server, project, monkeypatch):
    monkeypatch.chdir(project)
    run("init")
    leaf = git_server.repo("t/leaf")
    leaf.commit({"SKILL.md": SKILL + "1"})
    base = git_server.repo("t/base")
    base.commit(
        {
            "apm.yml": f"name: base\nversion: '1'\ndependencies:\n  apm:\n    - {leaf.https}\n",
            ".apm/x": "",
        }
    )
    assert run("install", base.https).exit_code == 0
    assert (project / ".claude/skills/leaf/SKILL.md").read_text().endswith("1")
    leaf.commit({"SKILL.md": SKILL + "2"})
    assert run("install").exit_code == 0  # pinned: no move
    assert (project / ".claude/skills/leaf/SKILL.md").read_text().endswith("1")
    assert run("update").exit_code == 0
    assert (project / ".claude/skills/leaf/SKILL.md").read_text().endswith("2")
    tree = run("deps", "tree").stdout
    assert tree.index("t/base") < tree.index("t/leaf")
    assert tree.splitlines()[1].startswith("  ")


def test_update_unknown_dep_errors(git_server, project, monkeypatch):
    monkeypatch.chdir(project)
    run("init")
    res = run("update", "nope")
    assert res.exit_code == 1
    assert "no dependency matches" in res.stderr


def test_version():
    assert "0.1.0" in run("--version").stdout
