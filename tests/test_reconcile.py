import json
import os

import pytest

from apm_lite import reconcile as rc
from apm_lite.errors import ApmError
from apm_lite.git import Git
from apm_lite.manifest import load
from apm_lite.model import PlannedFile
from apm_lite.targets import TARGETS

RULE = "---\napplyTo: '**/*.py'\n---\nUse black.\n"
HOOKS = json.dumps({"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "./c.sh"}]}]})


def _pkg_repo(git_server, path="t/base", extra=None):
    r = git_server.repo(path)
    files = {
        "apm.yml": f"name: {path.split('/')[-1]}\nversion: '1.0.0'\n",
        ".apm/instructions/style.instructions.md": RULE,
        ".apm/hooks/hooks.json": HOOKS,
        ".apm/hooks/c.sh": "#!/bin/sh\n",
        **(extra or {}),
    }
    c = r.commit(files, exec_files=(".apm/hooks/c.sh",))
    return r, c


def _proj(project, deps, targets="[claude]"):
    body = "".join(f"    - {d}\n" for d in deps)
    (project / "apm.yml").write_text(
        f"name: proj\nversion: '0.1.0'\ntargets: {targets}\ndependencies:\n  apm:\n{body}"
        if deps
        else f"name: proj\nversion: '0.1.0'\ntargets: {targets}\n"
    )
    return load(project / "apm.yml", is_root=True)


def _run(project, m, **kw):
    return rc.reconcile(project, m, git=Git(), warn=lambda s: None, **kw)


def _snapshot(project):
    out = {}
    for p in sorted(project.rglob("*")):
        rel = p.relative_to(project).as_posix()
        if rel.startswith("apm_modules") or not p.is_file():
            continue
        out[rel] = (p.read_bytes(), os.stat(p).st_mode & 0o777)
    return out


def test_install_deploys_and_reinstall_is_noop(git_server, project):
    r, c = _pkg_repo(git_server)
    m = _proj(project, [r.https])
    res = _run(project, m)
    assert res.lock.deps[m.deps[0].key].resolved_commit == c
    assert (project / ".claude/rules/style.md").read_text().startswith("---\npaths:")
    assert os.stat(project / ".claude/hooks/base/c.sh").st_mode & 0o111
    settings = json.loads((project / ".claude/settings.json").read_text())
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert "apm_modules/" in (project / ".gitignore").read_text()
    before = {p: os.stat(project / p).st_mtime_ns for p in _snapshot(project)}
    res2 = _run(project, load(project / "apm.yml", is_root=True))
    assert res2.written == [] and res2.deleted == []
    assert {p: os.stat(project / p).st_mtime_ns for p in _snapshot(project)} == before


def test_classify_table(tmp_path):
    p = tmp_path / "f"
    pf = PlannedFile(b"new", 0o644, "o")
    assert rc.classify(p, pf, None) == "write"
    p.write_bytes(b"new")
    assert rc.classify(p, pf, None) == "noop"
    os.chmod(p, 0o755)
    assert rc.classify(p, pf, None) == "chmod"
    os.chmod(p, 0o644)
    p.write_bytes(b"old")
    from apm_lite.lockfile import file_hash

    assert rc.classify(p, pf, file_hash(b"old")) == "write"
    assert rc.classify(p, pf, file_hash(b"orig")) == "keep-edited"
    assert rc.classify(p, pf, None) == "conflict"


def test_user_edit_kept_on_reinstall_and_cleanup(git_server, project):
    r, _ = _pkg_repo(git_server)
    m = _proj(project, [r.https])
    _run(project, m)
    rule = project / ".claude/rules/style.md"
    rule.write_text("mine")
    res = _run(project, load(project / "apm.yml", is_root=True))
    assert rule.read_text() == "mine" and res.kept_edited == [".claude/rules/style.md"]
    _run(project, _proj(project, []))
    assert rule.read_text() == "mine"
    assert not (project / ".claude/hooks/base/c.sh").exists()


def test_untracked_conflict_and_package_collision(git_server, project):
    r, _ = _pkg_repo(git_server)
    (project / ".claude/rules").mkdir(parents=True)
    (project / ".claude/rules/style.md").write_text("hand written")
    with pytest.raises(ApmError, match=r"style.md"):
        _run(project, _proj(project, [r.https]))
    (project / ".claude/rules/style.md").unlink()
    r2, _ = _pkg_repo(git_server, "t/other")
    with pytest.raises(ApmError, match=r"t/other|t/base"):
        _run(project, _proj(project, [r.https, r2.https]))


def test_retire_antigravity(git_server, project):
    r, _ = _pkg_repo(git_server)
    (project / ".agents").mkdir()
    (project / ".agents/hooks.json").write_text(json.dumps({"mine": {"Stop": []}}))
    _run(project, _proj(project, [r.https], "[claude, antigravity]"))
    assert (project / ".agents/rules/style.md").exists()
    assert "apm" in json.loads((project / ".agents/hooks.json").read_text())
    _run(project, _proj(project, [r.https], "[claude]"))
    assert not (project / ".agents/rules/style.md").exists()
    assert json.loads((project / ".agents/hooks.json").read_text()) == {"mine": {"Stop": []}}
    assert not (project / ".agents/apm-hooks.json").exists()


@pytest.mark.parametrize("fail_at", ["apply", "hook_sidecar", "lockfile", "interrupt_cleanup"])
def test_fault_injection_restores_tree(git_server, project, monkeypatch, fail_at):
    r, _ = _pkg_repo(git_server)
    _run(project, _proj(project, [r.https]))
    r.commit(
        {
            "apm.yml": "name: base\nversion: '2.0.0'\n",  # style rule dropped -> cleanup deletes
            ".apm/instructions/other.instructions.md": "new",  # new file -> apply writes
            ".apm/hooks/hooks.json": HOOKS.replace(
                "Bash", "Edit"
            ),  # hooks change -> sidecar rewritten
            ".apm/hooks/c.sh": "#!/bin/sh\n",
        },
        exec_files=(".apm/hooks/c.sh",),
    )
    snap = _snapshot(project)
    real_write = rc.UndoLog.write
    real_delete = rc.UndoLog.delete

    def write(self, rel, data, mode=0o644):
        if (
            (fail_at == "apply" and rel.endswith("other.md"))
            or (fail_at == "hook_sidecar" and rel.endswith("apm-hooks.json"))
            or (fail_at == "lockfile" and rel == "apm.lock.yaml")
        ):
            raise OSError("injected")
        return real_write(self, rel, data, mode)

    def delete(self, rel):
        if fail_at == "interrupt_cleanup":
            raise KeyboardInterrupt
        return real_delete(self, rel)

    # Scope the injected faults: monkeypatch.undo() would also revert git_env's offline git config.
    with monkeypatch.context() as patch:
        patch.setattr(rc.UndoLog, "write", write)
        patch.setattr(rc.UndoLog, "delete", delete)
        with pytest.raises((OSError, KeyboardInterrupt)):
            _run(project, load(project / "apm.yml", is_root=True), mode="update")
    assert _snapshot(project) == snap
    _run(project, load(project / "apm.yml", is_root=True), mode="update")
    assert (project / ".claude/rules/other.md").exists()


def test_root_output_and_lock_ownership(git_server, project):
    from apm_lite.lockfile import file_hash, read

    r, commit = _pkg_repo(git_server)
    m = _proj(project, [r.https], "[claude, antigravity]")
    rules = project / ".apm/instructions"
    rules.mkdir(parents=True)
    (rules / "root.instructions.md").write_text("Root rule.")
    skill = project / ".apm/skills/root-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\ndescription: Root skill.\n---\n")
    hooks = project / ".apm/hooks"
    hooks.mkdir()
    (hooks / "hooks.json").write_text(HOOKS)
    (hooks / "c.sh").write_text("#!/bin/sh\n")
    root = rc.root_package(project, m)
    assert root.depth == 0 and root.package_type == "apm_package"
    assert root.spec.kind == "local" and root.spec.key == "." and root.name == m.name
    result = _run(project, m)
    assert result.lock == read(project / "apm.lock.yaml")
    dep = result.lock.deps[m.deps[0].key]
    assert dep.resolved_commit == commit and dep.content_hash
    for target in (".claude", ".agents"):
        rule = f"{target}/rules/root.md"
        assert rule in result.lock.local_deployed_files
        assert result.lock.local_deployed_file_hashes[rule] == file_hash(
            (project / rule).read_bytes()
        )
        assert f"{target}/skills/root-skill" in result.lock.local_deployed_files
        assert f"{target}/hooks/proj/c.sh" in result.lock.local_deployed_files
        assert f"{target}/rules/style.md" in dep.deployed_files
        sidecar = json.loads((project / target / "apm-hooks.json").read_text())
        assert [entry["_apm_source"] for entry in sidecar["PreToolUse"]] == [
            "t/base",
            "_local/proj",
        ]
        assert f"{target}/apm-hooks.json" not in dep.deployed_files
    assert result.lock.local_deployed_files == sorted(result.lock.local_deployed_files)


def test_edited_hash_and_warnings_survive_update_and_cleanup(git_server, project):
    r, _ = _pkg_repo(git_server)
    m = _proj(project, [r.https])
    first = _run(project, m)
    rel = ".claude/rules/style.md"
    recorded = first.lock.deps[m.deps[0].key].deployed_file_hashes[rel]
    (project / rel).write_text("mine")
    messages = []
    result = rc.reconcile(project, m, git=Git(), warn=messages.append, mode="update")
    assert result.lock.deps[m.deps[0].key].deployed_file_hashes[rel] == recorded
    assert result.warnings == messages and any("edited since install" in s for s in messages)
    messages.clear()
    result = rc.reconcile(project, _proj(project, []), git=Git(), warn=messages.append)
    assert result.kept_edited == [rel] and (project / rel).read_text() == "mine"
    assert any(rel in s and "edited since install" in s for s in messages)
    assert not (project / "apm_modules").exists()


def test_skill_subset_cleanup_prunes_nested_directories(git_server, project):
    r = git_server.repo("t/skills")
    skill = "---\ndescription: A skill.\n---\n"
    r.commit({"skills/a/SKILL.md": skill, "skills/a/assets/x.txt": "x", "skills/b/SKILL.md": skill})
    m = _proj(project, [f"{{git: '{r.https}', skills: [a]}}"])
    result = _run(project, m)
    dep = result.lock.deps[m.deps[0].key]
    assert dep.skill_subset == ["a"]
    assert ".claude/skills/a" in dep.deployed_files
    assert ".claude/skills/a" not in dep.deployed_file_hashes
    assert not (project / ".claude/skills/b").exists()
    _run(project, _proj(project, []))
    assert not (project / ".claude/skills/a").exists()


def _bad(project, rel):
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{")
    return path


def _plain_repo(git_server):  # a package with a rule and no hooks
    r = git_server.repo("t/plain")
    r.commit(
        {"apm.yml": "name: plain\nversion: '1'\n", ".apm/instructions/p.instructions.md": RULE}
    )
    return r


@pytest.mark.parametrize("name", TARGETS)
def test_unused_hook_file_is_never_read(git_server, project, name):  # R26
    other = "claude" if name != "claude" else "antigravity"
    path = _bad(project, TARGETS[name].hook_file)
    r, _ = _pkg_repo(git_server)
    _run(project, _proj(project, [r.https], f"[{other}]"))  # target not configured, no sidecar
    assert path.read_text() == "{"
    _run(project, _proj(project, [_plain_repo(git_server).https], f"[{name}]"))  # no hooks to add
    assert path.read_text() == "{"
    assert not (project / TARGETS[name].sidecar).exists()


@pytest.mark.parametrize("name", TARGETS)
@pytest.mark.parametrize("case", ["hooks_to_add", "sidecar_exists", "bad_sidecar"])
def test_bad_hook_json_prevents_any_deployment(git_server, project, name, case):  # R26
    target = TARGETS[name]
    other = "claude" if name != "claude" else "antigravity"
    r, _ = _pkg_repo(git_server)
    if case == "hooks_to_add":
        path, m = _bad(project, target.hook_file), _proj(project, [r.https], f"[{name}]")
    elif case == "sidecar_exists":  # entries to strip, even from an empty sidecar
        path, m = _bad(project, target.hook_file), _proj(project, [r.https], f"[{other}]")
        (project / target.sidecar).write_text("{}")
    else:
        path, m = _bad(project, target.sidecar), _proj(project, [r.https], f"[{other}]")
    snap = _snapshot(project)
    with pytest.raises(ApmError, match=path.name):
        _run(project, m)
    assert _snapshot(project) == snap


def test_commented_gemini_settings(git_server, project):  # R26: never rewritten
    settings = project / ".gemini/settings.json"
    settings.parent.mkdir()
    text = '{\n  // mine\n  "theme": "dark"\n}\n'
    settings.write_text(text)
    _run(project, _proj(project, [_plain_repo(git_server).https], "[gemini]"))
    assert settings.read_text() == text
    r, _ = _pkg_repo(git_server)
    snap = _snapshot(project)
    with pytest.raises(ApmError, match=r"settings\.json") as exc:
        _run(project, _proj(project, [r.https], "[gemini]"))
    assert "comments" in exc.value.hint
    snap["apm.yml"] = _snapshot(project)["apm.yml"]  # _proj rewrote the manifest itself
    assert _snapshot(project) == snap


def test_bad_timeout_leaves_user_codex_hooks_alone(git_server, project):  # R22a
    r = git_server.repo("t/neg")
    hooks = json.dumps({"Stop": [{"command": "echo x", "timeout": -1}]})
    r.commit({"apm.yml": "name: neg\nversion: '1'\n", ".apm/hooks/hooks.json": hooks})
    mine = project / ".codex/hooks.json"
    mine.parent.mkdir()
    mine.write_text('{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo me"}]}]}}')
    m = _proj(project, [r.https], "[codex]")
    snap = _snapshot(project)
    with pytest.raises(ApmError, match="timeout"):
        _run(project, m)
    assert _snapshot(project) == snap


def test_shared_agents_skills_folder(git_server, project):  # R21
    r = git_server.repo("t/skills")
    r.commit({"skills/a/SKILL.md": "---\ndescription: A.\n---\n", "skills/a/ref.md": "ref"})
    dep = [r.https]
    res = _run(project, _proj(project, dep, "[antigravity, codex, cursor, gemini]"))
    files = res.lock.deps[next(iter(res.lock.deps))].deployed_files
    assert files == [".agents/skills/a", ".agents/skills/a/SKILL.md", ".agents/skills/a/ref.md"]
    assert not (project / ".cursor").exists() and not (project / ".codex").exists()
    (project / ".agents/skills/a/ref.md").write_text("edited")
    (project / ".agents/skills/a/mine.md").write_text("mine")
    res = _run(project, _proj(project, dep, "[codex]"))  # one sharing target left: nothing moves
    assert res.deleted == [] and (project / ".agents/skills/a/SKILL.md").exists()
    _run(project, _proj(project, dep, "[claude]"))  # the last one is gone
    assert (project / ".claude/skills/a/ref.md").read_text() == "ref"
    assert not (project / ".agents/skills/a/SKILL.md").exists()
    assert (project / ".agents/skills/a/ref.md").read_text() == "edited"
    assert (project / ".agents/skills/a/mine.md").read_text() == "mine"


def test_shared_skill_claimed_by_two_packages_still_fails(git_server, project):
    a, b = git_server.repo("t/a"), git_server.repo("t/b")
    a.commit({"skills/shared/SKILL.md": "---\ndescription: d\n---\n"})
    b.commit({"skills/shared/SKILL.md": "---\ndescription: d\n---\n"})
    m = _proj(project, [a.https, b.https], "[codex, cursor]")
    snap = _snapshot(project)
    with pytest.raises(ApmError, match=r"t/a.*t/b|t/b.*t/a"):
        _run(project, m)
    assert _snapshot(project) == snap


def test_rules_warning_for_targets_without_rules(git_server, project):  # R20
    r, _ = _pkg_repo(git_server)
    (project / ".git").mkdir()
    res = _run(project, _proj(project, [r.https], "[codex, cursor, gemini]"))
    assert [w for w in res.warnings if "rules aren't deployed" in w] == [
        "rules aren't deployed for codex, gemini: these targets read rules only "
        "from AGENTS.md / GEMINI.md, which apm-lite doesn't write"
    ]
    assert (project / ".cursor/rules/style.mdc").exists()
    assert not (project / ".codex/rules").exists() and not (project / ".gemini/rules").exists()
    res = _run(project, _proj(project, [r.https], "[claude]"))
    assert not any("rules aren't deployed" in w for w in res.warnings)


def test_codex_warns_when_the_project_is_not_a_git_root(git_server, project):
    r, _ = _pkg_repo(git_server)
    res = _run(project, _proj(project, [r.https], "[codex]"))
    assert any("git root" in w and "codex" in w for w in res.warnings)
    (project / ".git").mkdir()
    res = _run(project, _proj(project, [r.https], "[codex, claude]"))
    assert not any("git root" in w for w in res.warnings)


@pytest.mark.parametrize("name", ["cursor", "codex", "gemini"])
def test_retire_new_target_keeps_user_hooks(git_server, project, name):  # R28, R29
    target = TARGETS[name]
    extra = {
        ".apm/hooks/hooks.json": json.dumps(
            {
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "./c.sh"}]}],
                "Stop": [{"command": "echo stop"}],
            }
        )
    }
    r, _ = _pkg_repo(git_server, extra=extra)
    (project / ".git").mkdir()
    native = project / target.hook_file
    native.parent.mkdir()
    pre, stop = target.native_event("PreToolUse"), target.native_event("Stop")
    native.write_text(json.dumps({"theme": "dark", "hooks": {stop: [{"command": "echo mine"}]}}))
    _run(project, _proj(project, [r.https], f"[claude, {name}]"))
    doc = json.loads(native.read_text())
    assert (project / target.hooks_dir / "base/c.sh").exists()
    assert (project / ".cursor/rules/style.mdc").exists() == (name == "cursor")
    managed_pre, managed_stop = doc["hooks"][pre][-1], doc["hooks"][stop][-1]
    edited = dict(managed_stop, note="edited by me")
    doc["hooks"][stop][-1] = edited  # the user edits a managed entry...
    doc["hooks"][pre].insert(0, dict(managed_pre))  # ...and copies another one
    native.write_text(json.dumps(doc))
    _run(project, _proj(project, [r.https], "[claude]"))
    after = json.loads(native.read_text())
    assert after["theme"] == "dark"
    assert after["hooks"] == {stop: [{"command": "echo mine"}, edited], pre: [managed_pre]}
    assert not (project / target.sidecar).exists()
    assert not (project / target.hooks_dir / "base/c.sh").exists()
    assert not (project / ".cursor/rules/style.mdc").exists()
    assert (project / ".claude/rules/style.md").exists()


@pytest.mark.parametrize("name", ["cursor", "codex", "gemini"])
def test_fault_between_hook_file_and_sidecar_restores_both(git_server, project, monkeypatch, name):
    r, _ = _pkg_repo(git_server)
    (project / ".git").mkdir()
    native = project / TARGETS[name].hook_file
    native.parent.mkdir()
    native.write_text('{"hooks": {}}')
    m = _proj(project, [r.https], f"[{name}]")
    snap = _snapshot(project)
    real_write = rc.UndoLog.write

    def write(self, rel, data, mode=0o644):
        if rel.endswith("apm-hooks.json"):
            assert native.read_text() != '{"hooks": {}}'  # the native file was written first
            raise OSError("injected")
        return real_write(self, rel, data, mode)

    with monkeypatch.context() as patch:
        patch.setattr(rc.UndoLog, "write", write)
        with pytest.raises(OSError, match="injected"):
            _run(project, m)
    assert _snapshot(project) == snap
    _run(project, m)
    assert (project / TARGETS[name].sidecar).exists()


def test_all_five_targets_together(git_server, project):
    skill = {".apm/skills/s/SKILL.md": "---\ndescription: S.\n---\n"}
    r, _ = _pkg_repo(git_server, extra=skill)
    (project / ".git").mkdir()
    m = _proj(project, [r.https], "[claude, antigravity, cursor, codex, gemini]")
    res = _run(project, m)
    rules = sorted(p.relative_to(project).as_posix() for p in project.glob(".*/rules/*"))
    assert rules == [".agents/rules/style.md", ".claude/rules/style.md", ".cursor/rules/style.mdc"]
    skills = sorted(p.relative_to(project).as_posix() for p in project.glob(".*/skills/s/SKILL.md"))
    assert skills == [".agents/skills/s/SKILL.md", ".claude/skills/s/SKILL.md"]

    def doc(name):
        return json.loads((project / TARGETS[name].hook_file).read_text())

    def nested(command, **extra):
        return [{"matcher": "Bash", "hooks": [{"type": "command", "command": command}], **extra}]

    assert doc("claude") == {
        "hooks": {"PreToolUse": nested('"${CLAUDE_PROJECT_DIR}/.claude/hooks/base/c.sh"')}
    }
    assert doc("antigravity") == {"apm": {"PreToolUse": nested("hooks/base/c.sh")}}
    assert doc("cursor") == {
        "hooks": {
            "preToolUse": [
                {"type": "command", "command": ".cursor/hooks/base/c.sh", "matcher": "Shell"}
            ]
        },
        "version": 1,
    }
    assert doc("codex") == {
        "hooks": {"PreToolUse": nested('"$(git rev-parse --show-toplevel)/.codex/hooks/base/c.sh"')}
    }
    assert doc("gemini") == {"hooks": {"BeforeTool": nested(".gemini/hooks/base/c.sh")}}
    for name, target in TARGETS.items():
        assert os.stat(project / target.hooks_dir / "base/c.sh").st_mode & 0o111, name
    assert len([w for w in res.warnings if "rules aren't deployed" in w]) == 1
    again = _run(project, load(project / "apm.yml", is_root=True))
    assert again.written == [] and again.deleted == []


def test_case_collision_and_skill_root_collision(git_server, project):
    base = git_server.repo("t/base")
    base.commit(
        {"apm.yml": "name: base\nversion: '1'\n", ".apm/instructions/Style.instructions.md": RULE}
    )
    other, _ = _pkg_repo(git_server, "t/other")
    m = _proj(project, [base.https, other.https])
    snap = _snapshot(project)
    with pytest.raises(ApmError, match=r"t/base.*t/other|t/other.*t/base"):
        _run(project, m)
    assert _snapshot(project) == snap
    a = git_server.repo("t/a")
    b = git_server.repo("t/b")
    a.commit({"skills/shared/SKILL.md": "---\ndescription: d\n---\n"})
    b.commit({"skills/shared/SKILL.md": "---\ndescription: d\n---\n"})
    with pytest.raises(ApmError, match=r"t/a.*t/b|t/b.*t/a"):
        _run(project, _proj(project, [a.https, b.https]))


@pytest.mark.parametrize("rel", [".claude/rules", ".agents/hooks.json", ".gitignore"])
def test_symlink_destination_is_rejected_before_writes(git_server, project, tmp_path, rel):
    r, _ = _pkg_repo(git_server)
    m = _proj(project, [r.https], "[claude, antigravity]")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = project / rel
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    with pytest.raises(ApmError, match="symlink"):
        _run(project, m)
    assert list(outside.iterdir()) == []
    assert not (project / ".claude/hooks").exists()
    assert not (project / "apm.lock.yaml").exists()


def test_gitignore_exact_line_and_metadata_rollback(project, monkeypatch):
    from apm_lite import manifest as manifest_module

    m = _proj(project, [])
    (project / ".gitignore").write_text("other/apm_modules/\n")
    (project / ".gitignore").chmod(0o600)
    m.doc["description"] = "edited"
    m.edited = True
    snap = _snapshot(project)
    real_write = rc.UndoLog.write

    def write(self, rel, data, mode=0o644):
        if rel == "apm.lock.yaml":
            raise OSError("injected")
        return real_write(self, rel, data, mode)

    monkeypatch.setattr(rc.UndoLog, "write", write)
    with pytest.raises(OSError, match="injected"):
        _run(project, m)
    assert _snapshot(project) == snap
    monkeypatch.undo()
    _run(project, m)
    assert (project / "apm.yml").read_bytes() == manifest_module.dump(m)
    assert (project / ".gitignore").read_text().splitlines().count("apm_modules/") == 1
    _run(project, _proj(project, []))
    assert (project / ".gitignore").read_text().splitlines().count("apm_modules/") == 1


def test_chmod_keeps_bytes_and_unrelated_permission_bits(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(b"same")
    path.chmod(0o600)
    assert rc.classify(path, PlannedFile(b"same", 0o644, "."), None) == "noop"
    path.chmod(0o700)
    assert rc.classify(path, PlannedFile(b"same", 0o755, "."), None) == "noop"


def test_reinstall_makes_no_git_calls(git_server, project):  # R38: no fetches
    r, _ = _pkg_repo(git_server)
    _run(project, _proj(project, [r.https]))
    lines = []
    rc.reconcile(
        project,
        load(project / "apm.yml", is_root=True),
        git=Git(verbose=True, echo=lines.append),
        warn=lambda s: None,
    )
    assert lines == []


def test_executable_bit_only_change_is_deployed(git_server, project):  # R25 chmod branch
    r, _ = _pkg_repo(git_server)
    _run(project, _proj(project, [r.https]))
    script = project / ".claude/hooks/base/c.sh"
    assert os.stat(script).st_mode & 0o111
    r.commit(
        {
            "apm.yml": "name: base\nversion: '1.0.0'\n",
            ".apm/instructions/style.instructions.md": RULE,
            ".apm/hooks/hooks.json": HOOKS,
            ".apm/hooks/c.sh": "#!/bin/sh\n",
        }
    )  # same bytes, no longer executable
    res = _run(project, load(project / "apm.yml", is_root=True), mode="update")
    assert ".claude/hooks/base/c.sh" in res.written
    assert not os.stat(script).st_mode & 0o111


def test_existing_settings_without_hook_changes_keep_their_bytes(project):  # R22: no rewrite
    settings = project / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"permissions":{"allow":["Bash"]}}')  # compact, user-formatted
    agents = project / ".agents/hooks.json"
    agents.parent.mkdir()
    agents.write_text('{"mine": {"Stop": []}}')
    _run(project, _proj(project, []))
    assert settings.read_text() == '{"permissions":{"allow":["Bash"]}}'
    assert agents.read_text() == '{"mine": {"Stop": []}}'
