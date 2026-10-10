"""Documented consumer/author workflows through real `apm` processes.

Plan and results: docs/verification/. Git transport uses the existing offline server.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.test_compat import FIXTURES, _build
from tests.test_reconcile import _snapshot

SKILL = "---\nname: review\ndescription: Review synthetic code.\n---\nRevision "


@pytest.fixture
def apm(git_env, monkeypatch):
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    executable = Path(sys.executable).with_name("apm")
    assert executable.is_file(), "Install the project's console entry point first"

    def run(project, *args, expected=0):
        result = subprocess.run(
            [str(executable), *args], cwd=project, capture_output=True, text=True, timeout=30
        )
        print(f"\n[{project.name}] apm {' '.join(args)} -> {result.returncode}")
        print(result.stdout + result.stderr, end="")
        assert result.returncode == expected, result.stdout + result.stderr
        return result

    return run


def write_manifest(project, deps, targets=None):
    data = {"name": "consumer", "version": "0.1.0", "dependencies": {"apm": deps}}
    if targets is not None:
        data["targets"] = targets
    (project / "apm.yml").write_text(yaml.safe_dump(data, sort_keys=False))


def lock_entries(project):
    data = yaml.safe_load((project / "apm.lock.yaml").read_text())
    return {entry["repo_url"]: entry for entry in data["dependencies"]}


def test_quickstart_and_usage_errors(apm, project):
    apm(project, "install", expected=1)
    assert list(project.iterdir()) == []
    help_text = apm(project, "--help").stdout
    assert all(
        command in help_text for command in ("init", "install", "uninstall", "update", "deps")
    )
    assert "0.1.0" in apm(project, "--version").stdout
    for command in ("list", "tree"):
        assert "apm install" in apm(project, "deps", command, expected=1).stderr
    apm(project, "init")
    data = yaml.safe_load((project / "apm.yml").read_text())
    assert data == {
        "name": project.name,
        "version": "0.1.0",
        "targets": ["claude"],
        "dependencies": {"apm": []},
    }
    before = _snapshot(project)
    apm(project, "init", expected=1)
    apm(project, "install", "--frozen", expected=1)
    apm(project, "install", "--skill", "review", expected=2)
    apm(project, "install", "--frozen", "https://git.test/t/x.git", expected=2)
    apm(project, "install", "https://git.test/t/missing.git", expected=1)
    assert _snapshot(project) == before
    apm(project, "install")
    assert not (project / ".agents").exists()
    apm(project, "install", "--frozen")


def test_mixed_targets_golden_and_noop(apm, git_server, git_env, project, monkeypatch, tmp_path):
    _build(git_server, git_env, project)
    apm(project, "-v", "install")
    for filename in ("apm.yml", "apm.lock.yaml"):
        assert (project / filename).read_bytes() == (FIXTURES / filename).read_bytes()
    for root in (".claude", ".agents"):
        assert (project / root / "skills/a/SKILL.md").is_file()
        assert not (project / root / "skills/b").exists()
        assert (project / root / "rules/project.md").is_file()
        assert (project / root / "hooks/hooks/check.sh").stat().st_mode & 0o111
    before = _snapshot(project)
    mtimes = {rel: (project / rel).stat().st_mtime_ns for rel in before}
    trace = tmp_path / "git.trace"
    monkeypatch.setenv("GIT_TRACE", str(trace))
    result = apm(project, "install")
    assert "0 file(s) written, 0 deleted" in result.stdout
    assert _snapshot(project) == before
    assert {rel: (project / rel).stat().st_mtime_ns for rel in before} == mtimes
    assert not trace.exists(), "No git subprocess should run on an unchanged reinstall"
    twin = tmp_path / "teammate"
    shutil.copytree(
        project, twin, ignore=shutil.ignore_patterns("apm_modules", ".claude", ".agents")
    )
    apm(twin, "install", "--frozen")
    assert _snapshot(twin) == before


@pytest.mark.parametrize("target,root", [("claude", ".claude"), ("antigravity", ".agents")])
def test_author_local_package_and_hook_execution(apm, project, tmp_path, target, root):
    package = tmp_path / "local-package"
    hooks = package / ".apm/hooks"
    hooks.mkdir(parents=True)
    (package / "apm.yml").write_text("name: local\nversion: '1'\n")
    (hooks / "hooks.json").write_text(
        json.dumps(
            {
                "PreToolUse": [{"matcher": "*", "hooks": [{"command": "./check.sh"}]}],
                "PreInvocation": [{"command": "echo invocation"}],
                "SessionStart": [{"command": "echo session"}],
            }
        )
    )
    (hooks / "check.sh").write_text("#!/bin/sh\nprintf 'HOOK_OK\\n'\n")
    (hooks / "check.sh").chmod(0o755)
    instructions = package / ".apm/instructions"
    instructions.mkdir()
    (instructions / "local.instructions.md").write_text(
        "---\napplyTo: '**/*.py'\n---\nUse hints.\n"
    )
    skill = package / ".apm/skills/review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: review\n---\nReview.\n")
    (skill / ".git").mkdir()
    (skill / ".git/secret").write_text("not deployed")
    (skill / "link").symlink_to(tmp_path / "outside")
    write_manifest(project, [], [target])
    (project / root).mkdir()
    user_hook = {"matcher": "*", "hooks": [{"type": "command", "command": "echo user"}]}
    native = (
        {"permissions": {"allow": ["Read"]}, "hooks": {"Stop": [user_hook]}}
        if target == "claude"
        else {"user": {"Stop": [{"command": "echo user"}]}}
    )
    config = project / root / ("settings.json" if target == "claude" else "hooks.json")
    config.write_text(json.dumps(native))
    result = apm(project, "install", "../local-package")
    assert "description" in result.stderr
    assert ("PreInvocation" if target == "claude" else "SessionStart") in result.stderr
    rule = (project / root / "rules/local.md").read_text()
    expected = (
        '---\npaths:\n  - "**/*.py"\n---\n\nUse hints.\n'
        if target == "claude"
        else '---\ntrigger: glob\nglobs: "**/*.py"\n---\n\nUse hints.\n'
    )
    assert rule == expected
    assert not (project / root / "skills/review/.git").exists()
    assert not (project / root / "skills/review/link").exists()
    assert not (project / root / "hooks/local-package/hooks.json").exists()
    merged = json.loads(config.read_text())
    event_map = merged["hooks"] if target == "claude" else merged["apm"]
    command = event_map["PreToolUse"][0]["hooks"][0]["command"]
    proc = subprocess.run(
        ["/bin/sh", "-c", command],
        cwd=project if target == "claude" else project / root,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(project)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    print(f"Executed deployed {target} hook: {proc.stdout.strip()}")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "HOOK_OK\n"
    entry = next(
        entry
        for entry in lock_entries(project).values()
        if entry.get("local_path") == "../local-package"
    )
    assert entry["source"] == "local" and "content_hash" not in entry
    assert (project / root / "hooks/local-package/check.sh").stat().st_mode & 0o111
    apm(project, "uninstall", "../local-package")
    assert not (project / root / "rules/local.md").exists()
    assert not (project / root / "skills/review").exists()
    assert not (project / root / "hooks/local-package/check.sh").exists()
    remaining = json.loads(config.read_text())
    for key, value in native.items():
        assert remaining[key] == value
    assert not (project / root / "apm-hooks.json").exists()


def test_pins_selective_update_and_fresh_teammate(apm, git_server, project, tmp_path):
    leaf = git_server.repo("t/leaf")
    first_leaf = leaf.commit({"SKILL.md": SKILL + "1"})
    base = git_server.repo("t/base")
    files = {
        "apm.yml": f"name: base\nversion: '1'\ndependencies:\n  apm:\n    - {leaf.ssh}#main\n",
        ".apm/instructions/base.instructions.md": "Base 1.\n",
    }
    first_base = base.commit(files)
    apm(project, "init")
    apm(project, "install", base.https + "#main")
    tree = apm(project, "deps", "tree").stdout.splitlines()
    assert "t/base" in tree[0] and tree[1].startswith("  ") and "t/leaf" in tree[1]
    listing = apm(project, "deps", "list").stdout
    assert first_base[:7] in listing and first_leaf[:7] in listing
    old = _snapshot(project)
    new_leaf = leaf.commit({"SKILL.md": SKILL + "2"})
    new_base = base.commit({**files, ".apm/instructions/base.instructions.md": "Base 2.\n"})
    apm(project, "install")
    apm(project, "install", "--frozen")
    assert _snapshot(project) == old
    twin = tmp_path / "teammate"
    twin.mkdir()
    for name in ("apm.yml", "apm.lock.yaml", ".gitignore"):
        shutil.copy2(project / name, twin / name)
    apm(twin, "install", "--frozen")
    assert _snapshot(twin) == old
    apm(project, "update", "git.test/t/leaf")
    entries = lock_entries(project)
    assert entries["t/base"]["resolved_commit"] == first_base
    assert entries["t/leaf"]["resolved_commit"] == new_leaf
    assert (project / ".claude/rules/base.md").read_text() == "Base 1.\n"
    apm(project, "update")
    assert lock_entries(project)["t/base"]["resolved_commit"] == new_base
    assert (project / ".claude/rules/base.md").read_text() == "Base 2.\n"
    apm(project, "uninstall", "git.test/t/base")
    assert lock_entries(project) == {}
    assert not (project / ".claude/skills/leaf").exists()
    assert not (project / "apm_modules").exists()


def test_subset_merge_and_frozen_drift(apm, git_server, project):
    bundle = git_server.repo("t/bundle")
    bundle.commit({f"skills/{name}/SKILL.md": SKILL + name for name in ("a", "b", "c")})
    apm(project, "init")
    apm(project, "install", bundle.scp, "--skill", "a")
    apm(project, "install", bundle.scp, "--skill", "c")
    data = yaml.safe_load((project / "apm.yml").read_text())
    assert data["dependencies"]["apm"] == [{"git": bundle.scp, "skills": ["a", "c"]}]
    assert not (project / ".claude/skills/b").exists()
    before = _snapshot(project)
    apm(project, "install", bundle.scp, "--skill", "missing", expected=1)
    assert _snapshot(project) == before
    write_manifest(project, [{"git": bundle.scp, "skills": ["a"]}])
    before = _snapshot(project)
    apm(project, "install", "--frozen", expected=1)
    assert _snapshot(project) == before
    apm(project, "install")
    assert not (project / ".claude/skills/c").exists()
    assert (project / ".claude/skills/a/SKILL.md").is_file()


def test_transport_identity_ref_conflict_and_cycle(apm, git_server, project):
    leaf = git_server.repo("t/leaf")
    first = leaf.commit({"SKILL.md": SKILL + "1"})
    leaf.tag("v1", first)
    second = leaf.commit({"SKILL.md": SKILL + "2"})
    leaf.tag("v2", second)
    write_manifest(project, [leaf.https + "#v1", leaf.ssh + "#v2"])
    before = _snapshot(project)
    result = apm(project, "install", expected=1)
    assert "v1" in result.stderr and "v2" in result.stderr and "conflict" in result.stderr
    assert _snapshot(project) == before
    write_manifest(project, [leaf.https + "#main", leaf.scp + "#v1"])
    result = apm(project, "install")
    assert "warning:" in result.stderr and "main" in result.stderr and "v1" in result.stderr
    assert len(lock_entries(project)) == 1
    assert lock_entries(project)["t/leaf"]["resolved_commit"] == second
    cycle = git_server.repo("t/cycle")
    cycle.commit(
        {
            "apm.yml": f"name: cycle\nversion: '1'\ndependencies:\n  apm:\n    - {cycle.https}\n",
            ".apm/instructions/cycle.instructions.md": "Cycle.\n",
        }
    )
    apm(project, "install", cycle.https)
    assert len(lock_entries(project)) == 2


@pytest.mark.parametrize(
    "case",
    ["config", "sidecar", "unowned", "symlink", "conflict", "outside-script", "transitive-local"],
)
def test_failed_install_preserves_files(apm, git_server, project, tmp_path, case):
    repo = git_server.repo("t/base")
    files = {
        "apm.yml": "name: base\nversion: '1'\n",
        ".apm/instructions/base.instructions.md": "Base.\n",
    }
    if case == "outside-script":
        files.update(
            {
                "outside.sh": "#!/bin/sh\n",
                ".apm/hooks/hooks.json": '{"Stop": [{"command": "./outside.sh"}]}',
            }
        )
    if case == "transitive-local":
        files["apm.yml"] += "dependencies:\n  apm:\n    - ./other\n"
    repo.commit(files)
    write_manifest(project, [], ["claude", "antigravity"])
    (project / ".claude").mkdir()
    if case in {"config", "sidecar"}:
        (
            project / ".claude" / ("settings.json" if case == "config" else "apm-hooks.json")
        ).write_text("{broken")
    elif case == "unowned":
        (project / ".agents").mkdir()
        (project / ".agents/hooks.json").write_text('{"apm": {"Stop": []}}')
    elif case == "symlink":
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "sentinel").write_text("untouched")
        (project / ".claude/rules").symlink_to(outside, target_is_directory=True)
    elif case == "conflict":
        (project / ".claude/rules").mkdir()
        (project / ".claude/rules/base.md").write_text("user-owned")
    before = _snapshot(project)
    result = apm(project, "install", repo.https, expected=1)
    assert "error:" in result.stderr
    assert _snapshot(project) == before
    if case == "symlink":
        assert sorted(p.name for p in outside.iterdir()) == ["sentinel"]


def test_tampered_lock_hash_rejected(apm, git_server, project):
    repo = git_server.repo("t/single")
    repo.commit({"SKILL.md": SKILL + "1"})
    apm(project, "init")
    apm(project, "install", repo.https)
    path = project / "apm.lock.yaml"
    data = yaml.safe_load(path.read_text())
    data["dependencies"][0]["content_hash"] = "sha256:" + "0" * 64
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    before = _snapshot(project)
    result = apm(project, "install", "--frozen", expected=1)
    assert "tamper" in result.stderr or "force-push" in result.stderr
    assert _snapshot(project) == before


def test_user_edit_adoption_and_target_retirement(apm, git_server, git_env, project):
    _build(git_server, git_env, project)
    (project / ".claude/rules").mkdir(parents=True)
    (project / ".claude/rules/own.md").write_text("Own rule.\n")
    apm(project, "install")
    rule = project / ".claude/rules/style.md"
    rule.write_text("My edit.\n")
    result = apm(project, "install")
    assert "edited since install" in result.stderr and rule.read_text() == "My edit.\n"
    manifest = project / "apm.yml"
    data = yaml.safe_load(manifest.read_text())
    data["targets"] = ["claude"]
    manifest.write_text(yaml.safe_dump(data, sort_keys=False))
    apm(project, "install")
    assert not (project / ".agents/apm-hooks.json").exists()
    assert json.loads((project / ".agents/hooks.json").read_text()) == {}
    assert not any(p.is_file() for p in (project / ".agents").rglob("*") if p.name != "hooks.json")
    result = apm(project, "uninstall", "git.test/t/base")
    assert "edited since install" in result.stderr and rule.read_text() == "My edit.\n"
    assert not (project / ".claude/skills/leaf").exists()
    assert not (project / "apm_modules/t/base").exists()
    assert not (project / "apm_modules/t/leaf").exists()
