"""apm-lite output must match the committed fixtures that upstream APM's readers accept (N4).

The fixtures in tests/fixtures/compat/ were verified with upstream APM 0.33.0's own readers
(output in upstream_verified.txt). The verifier, tests_lite/compat/verify_upstream.py, was removed
with upstream at the cut-over; it is in git history at commit 8fa81d5d. This test rebuilds the
fixture project and asserts apm-lite still writes exactly those bytes. If the fixtures ever need
to change (APM_LITE_REGEN_COMPAT=1 rewrites them), re-verify them against upstream first, for
example with the optional round trip below (APM_UPSTREAM_BIN).
"""

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from apm_lite import cli, load, read

FIXTURES = Path(__file__).parent / "fixtures" / "compat"
RULE = "---\napplyTo: '**/*.py'\n---\nUse black.\n"
SKILL = "---\nname: {n}\ndescription: d\n---\nSkill {n}.\n"
HOOKS = json.dumps({"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "./check.sh"}]}]})

MANIFEST = """\
name: compat
version: "0.1.0"
targets:
  - claude
  - antigravity
dependencies:
  apm:
    - https://git.test/t/base.git#v1.0.0
    - ssh://git@git.test:7999/t/hooks.git#main
    - git@git.test:t/single.git
    - git: https://git.test/t/skills.git
      ref: v2
      skills:
        - a
        - c
    - ./packages/own
"""


def _build(git_server, git_env, project: Path) -> None:
    leaf = git_server.repo("t/leaf")
    leaf.commit({"SKILL.md": SKILL.format(n="leaf")})
    leaf.tag("v1")
    base = git_server.repo("t/base")
    base.commit(
        {
            "apm.yml": "name: base\nversion: '1.0.0'\ndependencies:\n  apm:\n"
            f"    - {leaf.https}#v1\n",
            ".apm/instructions/style.instructions.md": RULE,
            ".apm/skills/review/SKILL.md": SKILL.format(n="review"),
        }
    )
    base.tag("v1.0.0")
    hooks = git_server.repo("t/hooks")
    hooks.commit(
        {
            "apm.yml": "name: hooks\nversion: '2.0.0'\n",
            ".apm/hooks/hooks.json": HOOKS,
            ".apm/hooks/check.sh": "#!/bin/sh\nexit 0\n",
        },
        exec_files=(".apm/hooks/check.sh",),
    )
    single = git_server.repo("t/single")
    single.commit({"SKILL.md": SKILL.format(n="single")})
    skills = git_server.repo("t/skills")
    skills.commit({f"skills/{n}/SKILL.md": SKILL.format(n=n) for n in ("a", "b", "c")})
    skills.tag("v2")
    # ssh with a non-default port needs its own rewrite on the fake server.
    subprocess.run(
        [
            "git", "config", "--file", str(git_env), "--add",
            f"url.file://{git_server.root}/.insteadOf", "ssh://git@git.test:7999/",
        ],
        check=True,
    )  # fmt: skip
    own = project / "packages" / "own"
    (own / ".apm" / "instructions").mkdir(parents=True)
    (own / "apm.yml").write_text("name: own\nversion: '0.0.1'\n")
    (own / ".apm/instructions/own.instructions.md").write_text("Own rule.\n")
    (project / ".apm" / "instructions").mkdir(parents=True)
    (project / ".apm/instructions/project.instructions.md").write_text(RULE)
    (project / "apm.yml").write_text(MANIFEST)


def _expected(project: Path) -> dict:
    m = load(project / "apm.yml", is_root=True)
    lock = read(project / "apm.lock.yaml")
    entries = [
        {
            "raw": spec.raw if isinstance(spec.raw, str) else dict(spec.raw),
            "ref": spec.ref,
            "key": spec.key,
            "install_path": f"apm_modules/{spec.repo_path}" if spec.kind == "remote" else None,
        }
        for spec in m.deps
    ]
    # Skill directory roots are tracked too, with no hash, as upstream's ledger expects.
    deployed = {
        path: {"owner": owner, "hash": digest}
        for path, (owner, digest) in sorted(lock.all_tracked().items())
    }
    return {"entries": entries, "deployed": deployed}


def test_output_matches_upstream_verified_fixtures(git_server, git_env, project, monkeypatch):
    _build(git_server, git_env, project)
    monkeypatch.chdir(project)
    res = CliRunner().invoke(cli, ["install"], catch_exceptions=False)
    assert res.exit_code == 0, res.stderr
    produced = {
        "apm.yml": (project / "apm.yml").read_bytes(),
        "apm.lock.yaml": (project / "apm.lock.yaml").read_bytes(),
        "expected.json": (json.dumps(_expected(project), indent=2) + "\n").encode(),
    }
    if os.environ.get("APM_LITE_REGEN_COMPAT") == "1":
        FIXTURES.mkdir(parents=True, exist_ok=True)
        for name, data in produced.items():
            (FIXTURES / name).write_bytes(data)
    for name, data in produced.items():
        assert data == (FIXTURES / name).read_bytes(), f"{name} differs from the verified fixture"


@pytest.mark.skipif(not os.environ.get("APM_UPSTREAM_BIN"), reason="APM_UPSTREAM_BIN not set")
def test_upstream_round_trip(git_server, git_env, project, monkeypatch):
    """Upstream `apm install --frozen` on an apm-lite project keeps commits, user content and hooks."""
    _build(git_server, git_env, project)
    monkeypatch.chdir(project)
    (project / ".claude").mkdir()
    user_hook = {"matcher": "*", "hooks": [{"type": "command", "command": "echo mine"}]}
    settings = {"permissions": {"allow": ["Bash"]}, "hooks": {"Stop": [user_hook]}}
    (project / ".claude/settings.json").write_text(json.dumps(settings))
    assert CliRunner().invoke(cli, ["install"], catch_exceptions=False).exit_code == 0
    lite_lock = read(project / "apm.lock.yaml")
    lite_settings = json.loads((project / ".claude/settings.json").read_text())

    env = {**os.environ, "APM_E2E_TESTS": "1", "APM_POLICY_DISABLE": "1"}
    proc = subprocess.run(
        [os.environ["APM_UPSTREAM_BIN"], "install", "--frozen", "--no-policy"],
        cwd=project, env=env, capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    upstream_lock = read(project / "apm.lock.yaml")
    for key, dep in lite_lock.deps.items():
        assert upstream_lock.deps[key].resolved_commit == dep.resolved_commit, key
    upstream_settings = json.loads((project / ".claude/settings.json").read_text())
    assert upstream_settings["permissions"] == {"allow": ["Bash"]}
    assert user_hook in upstream_settings["hooks"]["Stop"]
    for event, entries in lite_settings["hooks"].items():
        assert len(upstream_settings["hooks"][event]) == len(entries), event
    assert (project / ".claude/skills/a/SKILL.md").exists()
    assert not (project / ".claude/skills/b").exists()
