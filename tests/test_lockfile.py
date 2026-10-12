import json
from pathlib import Path

import pytest

from apm_lite import ApmError, Lock, LockedDep, content_hash, dump_lock, file_hash, read

GOLDEN = json.loads((Path(__file__).parent / "fixtures/hash_golden.json").read_text())


def test_content_hash_matches_upstream(tmp_path):
    for name, tree in GOLDEN["trees"].items():
        root = tmp_path / name
        root.mkdir()
        for rel, content in tree["files"].items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content.encode("latin1"))
        assert content_hash(root) == tree["hash"], name


def test_file_hash_matches_upstream():
    for name, f in GOLDEN["files"].items():
        assert file_hash(f["bytes_latin1"].encode("latin1")) == f["hash"], name


def _lock():
    return Lock(
        deps={
            "git.test/team/base": LockedDep(
                repo_url="team/base",
                name="base",
                host="git.test",
                resolved_commit="a" * 40,
                resolved_ref="v1",
                version="1.0.0",
                package_type="apm_package",
                deployed_files=[".claude/rules/b.md"],
                deployed_file_hashes={".claude/rules/b.md": "sha256:1"},
                content_hash="sha256:2",
            ),
            "git.test/team/child": LockedDep(
                repo_url="team/child",
                host="git.test",
                port=7999,
                resolved_commit="b" * 40,
                depth=2,
                resolved_by="team/base",
                package_type="skill_bundle",
                skill_subset=["z", "a"],
            ),
            "./packages/x": LockedDep(
                repo_url="_local/x",
                name="x",
                package_type="claude_skill",
                source="local",
                local_path="./packages/x",
            ),
        },
        local_deployed_files=[".claude/rules/own.md"],
        local_deployed_file_hashes={".claude/rules/own.md": "sha256:3"},
    )


def test_dump_shape_and_order():
    text = dump_lock(_lock(), "0.1.0").decode()
    assert text.startswith(
        "lockfile_version: '1'\napm_version: 0.1.0\ndependencies:\n- repo_url: _local/x\n"
    )
    assert "generated_at" not in text and "deployments" not in text and "depth: 1" not in text
    base = text.index("repo_url: team/base")
    child = text.index("repo_url: team/child")
    assert base < child
    block = text[base:child]
    order = [
        k
        for k in (
            "name:",
            "host:",
            "resolved_commit:",
            "resolved_ref:",
            "version:",
            "package_type:",
            "deployed_files:",
            "deployed_file_hashes:",
            "content_hash:",
        )
    ]
    assert [block.index(k) for k in order] == sorted(block.index(k) for k in order)
    assert "skill_subset:\n  - a\n  - z\n" in text or "skill_subset:\n- a\n- z\n" in text


def test_round_trip(tmp_path):
    p = tmp_path / "apm.lock.yaml"
    p.write_bytes(dump_lock(_lock(), "0.1.0"))
    lock = read(p)
    assert set(lock.deps) == {"git.test/team/base", "git.test/team/child", "./packages/x"}
    assert lock.deps["git.test/team/child"].depth == 2
    assert lock.all_tracked()[".claude/rules/own.md"] == (".", "sha256:3")


@pytest.mark.parametrize(
    "text",
    [
        "lockfile_version: 1\n",
        "lockfile_version: '3'\n",
        "lockfile_version: '1'\ndependencies:\n- name: x\n",
        "- a\n",
    ],
)
def test_read_rejects(tmp_path, text):
    p = tmp_path / "apm.lock.yaml"
    p.write_text(text)
    with pytest.raises(ApmError):
        read(p)


def test_read_ignores_unknown_keys(tmp_path):
    p = tmp_path / "apm.lock.yaml"
    p.write_text(
        "lockfile_version: '1'\ndeployments: []\ndependencies:\n- repo_url: t/p\n  host: h.x\n  is_dev: true\n"
    )
    assert "h.x/t/p" in read(p).deps


def test_read_missing_returns_none(tmp_path):
    assert read(tmp_path / "apm.lock.yaml") is None


def test_key_and_all_tracked_owners():
    lock = _lock()
    base = lock.deps["git.test/team/base"]
    assert base.key == "git.test/team/base"
    assert lock.deps["./packages/x"].key == "./packages/x"
    lock.deps["git.test/team/child"].deployed_files = [
        ".claude/skills/s",
        ".claude/skills/s/SKILL.md",
    ]
    lock.deps["git.test/team/child"].deployed_file_hashes = {
        ".claude/skills/s/SKILL.md": "sha256:4"
    }
    tracked = lock.all_tracked()
    assert tracked[".claude/rules/b.md"] == ("git.test/team/base", "sha256:1")
    assert tracked[".claude/skills/s"] == ("git.test/team/child", None)
    assert tracked[".claude/skills/s/SKILL.md"] == ("git.test/team/child", "sha256:4")


def test_round_trip_preserves_every_field(tmp_path):
    p = tmp_path / "apm.lock.yaml"
    p.write_bytes(dump_lock(_lock(), "0.1.0"))
    lock = read(p)
    child = lock.deps["git.test/team/child"]
    assert (child.port, child.resolved_by, child.skill_subset) == (7999, "team/base", ["a", "z"])
    base = lock.deps["git.test/team/base"]
    assert (base.depth, base.resolved_ref, base.version, base.content_hash) == (
        1,
        "v1",
        "1.0.0",
        "sha256:2",
    )
    local = lock.deps["./packages/x"]
    assert (local.source, local.local_path, local.content_hash) == ("local", "./packages/x", None)
    assert dump_lock(lock, "0.1.0") == p.read_bytes()
