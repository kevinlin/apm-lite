import os
from pathlib import Path

import pytest

from apm_lite.errors import ApmError
from apm_lite.fs import UndoLog, atomic_write, check_destination, safe_name


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a b", "x$y"])
def test_safe_name_rejects(bad):
    with pytest.raises(ApmError):
        safe_name(bad)


@pytest.mark.parametrize("rel", ["src/x", "../x", ".claude/../../x", "/etc/passwd", "apm.yml.bak"])
def test_check_destination_rejects_outside_allowlist(project, rel):
    with pytest.raises(ApmError):
        check_destination(project, rel)


def test_check_destination_rejects_symlinked_root(project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".claude").symlink_to(outside)
    with pytest.raises(ApmError):
        check_destination(project, ".claude/rules/x.md")


def test_check_destination_accepts(project):
    assert check_destination(project, ".claude/rules/x.md") == project / ".claude/rules/x.md"
    assert check_destination(project, "apm.lock.yaml") == project / "apm.lock.yaml"


def test_atomic_write_sets_mode_and_leaves_no_temp(project):
    p = project / ".claude/hooks/pkg/x.sh"
    atomic_write(p, b"#!/bin/sh\n", 0o755)
    assert p.read_bytes() == b"#!/bin/sh\n" and os.stat(p).st_mode & 0o111
    assert [f.name for f in p.parent.iterdir()] == ["x.sh"]


def test_undo_log_restores_everything_on_error(project):
    (project / ".claude").mkdir()
    (project / ".claude/a.md").write_bytes(b"old")
    (project / ".claude/gone.md").write_bytes(b"keep me")
    os.chmod(project / ".claude/gone.md", 0o755)
    with pytest.raises(RuntimeError):
        with UndoLog(project) as log:
            log.write(".claude/a.md", b"new")
            log.write(".claude/new/b.md", b"b")
            log.delete(".claude/gone.md")
            raise RuntimeError("boom")
    assert (project / ".claude/a.md").read_bytes() == b"old"
    assert not (project / ".claude/new").exists()
    assert (project / ".claude/gone.md").read_bytes() == b"keep me"
    assert os.stat(project / ".claude/gone.md").st_mode & 0o777 == 0o755


def test_undo_log_rolls_back_keyboard_interrupt(project):
    with pytest.raises(KeyboardInterrupt):
        with UndoLog(project) as log:
            log.write("apm.yml", b"x")
            raise KeyboardInterrupt
    assert not (project / "apm.yml").exists()


def test_undo_log_keeps_changes_on_success(project):
    with UndoLog(project) as log:
        log.write(".agents/rules/r.md", b"r")
    assert (project / ".agents/rules/r.md").read_bytes() == b"r"


def test_safe_name_accepts():
    assert safe_name("Pkg.name-0_1") == "Pkg.name-0_1"


@pytest.mark.parametrize("rel", ["", ".", ".claude/../.agents/x", "apm.yml/x"])
def test_check_destination_rejects_invalid_relative_paths(project, rel):
    with pytest.raises(ApmError):
        check_destination(project, rel)


@pytest.mark.parametrize("rel", [".agents/rules/x.md", "apm_modules/team/pkg", ".gitignore"])
def test_check_destination_accepts_all_roots(project, rel):
    assert check_destination(project, rel) == project / rel


def test_check_destination_returns_absolute_path(project, monkeypatch):
    monkeypatch.chdir(project.parent)
    assert check_destination(Path(project.name), "apm.yml") == project / "apm.yml"


@pytest.mark.parametrize("component", [".claude", ".claude/rules", ".claude/rules/x.md"])
@pytest.mark.parametrize("target_exists", [True, False])
def test_check_destination_rejects_all_symlinks(project, component, target_exists):
    target = project / ".agents"
    if target_exists:
        target.mkdir()
    link = project / component
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    with pytest.raises(ApmError):
        check_destination(project, ".claude/rules/x.md")


@pytest.mark.parametrize("operation", ["fsync", "chmod", "replace"])
def test_atomic_write_failure_keeps_original_and_removes_temp(project, monkeypatch, operation):
    path = project / "apm.yml"
    path.write_bytes(b"old")
    path.chmod(0o600)

    def fail(*args, **kwargs):
        raise OSError("injected failure")

    monkeypatch.setattr(os, operation, fail)
    with pytest.raises(OSError, match="injected failure"):
        atomic_write(path, b"new", 0o755)
    assert path.read_bytes() == b"old"
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(project.iterdir()) == [path]


def test_undo_log_records_first_state_across_operations_and_path_spellings(project):
    path = project / ".claude/a.md"
    path.parent.mkdir()
    path.write_bytes(b"old")
    path.chmod(0o600)
    log = UndoLog(project)
    log.chmod(".claude/a.md", 0o755)
    assert path.stat().st_mode & 0o777 == 0o755
    log.write(".claude/./a.md", b"new")
    log.delete(".claude/a.md")
    assert not path.exists()
    log.write(".claude/a.md", b"last")
    log.write(".claude/new/x.md", b"first")
    log.delete(".claude/new/x.md")
    log.rmdir_if_empty(".claude/new")
    log.write(".claude/new/x.md", b"last")
    assert log.rollback() == []
    assert path.read_bytes() == b"old"
    assert path.stat().st_mode & 0o777 == 0o600
    assert not (project / ".claude/new").exists()


def test_undo_log_restores_deleted_directories_and_modes_in_reverse_order(project):
    directory = project / ".agents/rules"
    directory.mkdir(parents=True)
    directory.chmod(0o750)
    path = directory / "x.md"
    path.write_bytes(b"old")
    log = UndoLog(project)
    log.delete(".agents/rules/x.md")
    log.rmdir_if_empty(".agents/rules")
    log.rmdir_if_empty(".agents")
    assert not (project / ".agents").exists()
    assert log.rollback() == []
    assert path.read_bytes() == b"old"
    assert directory.stat().st_mode & 0o777 == 0o750


def test_rmdir_if_empty_keeps_nonempty_directory_and_ignores_missing_paths(project):
    directory = project / ".claude"
    directory.mkdir()
    (directory / "x.md").write_bytes(b"keep")
    with UndoLog(project) as log:
        log.rmdir_if_empty(".claude")
        log.rmdir_if_empty(".agents/missing")
        log.delete(".claude/missing.md")
    assert (directory / "x.md").read_bytes() == b"keep"


@pytest.mark.parametrize("operation", ["write", "chmod", "delete", "rmdir_if_empty"])
def test_undo_log_checks_every_mutation(project, tmp_path, operation):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.md").write_bytes(b"keep")
    (project / ".claude").symlink_to(outside)
    log = UndoLog(project)
    args = {"write": (b"new",), "chmod": (0o755,), "delete": (), "rmdir_if_empty": ()}
    with pytest.raises(ApmError):
        getattr(log, operation)(".claude/x.md", *args[operation])
    assert (outside / "x.md").read_bytes() == b"keep"


@pytest.mark.parametrize("operation", ["write", "delete"])
def test_undo_log_rejects_directories_as_files(project, operation):
    directory = project / ".claude"
    directory.mkdir()
    log = UndoLog(project)
    args = (b"new",) if operation == "write" else ()
    with pytest.raises(ApmError):
        getattr(log, operation)(".claude", *args)
    assert directory.is_dir()


def test_undo_log_cleans_created_directories_after_write_failure(project, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("injected failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="injected failure"):
        with UndoLog(project) as log:
            log.write(".claude/new/x.md", b"new")
    assert list(project.iterdir()) == []


def test_rollback_rechecks_containment_and_continues_after_failure(project, tmp_path):
    log = UndoLog(project)
    log.write("apm.yml", b"new")
    log.write(".claude/x.md", b"new")
    (project / ".claude/x.md").unlink()
    (project / ".claude").rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.md").write_bytes(b"keep")
    (project / ".claude").symlink_to(outside)
    assert log.rollback() == [".claude/x.md", ".claude"]
    assert (outside / "x.md").read_bytes() == b"keep"
    assert (project / ".claude").is_symlink()
    assert not (project / "apm.yml").exists()


def test_undo_log_reports_restore_failure_chained_from_original(project, monkeypatch):
    (project / "apm.yml").write_bytes(b"old")
    original = RuntimeError("boom")

    def fail(*args, **kwargs):
        raise OSError("restore failure")

    with pytest.raises(ApmError, match=r"apm\.yml") as caught:
        with UndoLog(project) as log:
            log.write(".gitignore", b"new")
            log.write("apm.yml", b"new")
            monkeypatch.setattr(os, "replace", fail)
            raise original
    assert caught.value.__cause__ is original
    assert not (project / ".gitignore").exists()


def test_undo_log_rolls_back_system_exit_and_removes_created_roots(project):
    with pytest.raises(SystemExit):
        with UndoLog(project) as log:
            log.write(".claude/rules/x.md", b"new")
            raise SystemExit(1)
    assert list(project.iterdir()) == []
