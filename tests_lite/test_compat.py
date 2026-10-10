"""apm-lite output must match the committed fixtures that upstream APM's readers accept (N4).

The fixtures in tests_lite/fixtures/compat/ were verified with upstream's own code by
tests_lite/compat/verify_upstream.py (output in upstream_verified.txt). This test rebuilds the
fixture project and asserts apm-lite still writes exactly those bytes. Set APM_LITE_REGEN_COMPAT=1
to rewrite the fixtures, then rerun verify_upstream.py before committing them.
"""

import json
import os
import subprocess
from pathlib import Path

from click.testing import CliRunner

from apm_lite.cli import cli
from apm_lite.lockfile import read
from apm_lite.manifest import load

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
