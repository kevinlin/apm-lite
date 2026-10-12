import pytest

from apm_lite import (
    ApmError,
    Git,
    LockedDep,
    check_overlaps,
    content_hash,
    inspect,
    install_dir,
    parse_entry,
    place,
)

APM = "name: base\nversion: '1.0.0'\n"


def test_place_fetches_then_reuses(git_server, project):
    r = git_server.repo("Team/Base")
    c = r.commit({"apm.yml": APM, ".apm/instructions/a.instructions.md": "x"})
    spec = parse_entry(r.https, allow_local=False)
    src, h = place(project, spec, c, None, Git())
    assert src == project / "apm_modules/Team/Base" and h == content_hash(src)
    locked = LockedDep(repo_url="Team/Base", host="git.test", resolved_commit=c, content_hash=h)
    lines = []
    place(project, spec, c, locked, Git(verbose=True, echo=lines.append))
    assert lines == []  # reused, no git call


def test_place_refetches_modified_cache_and_detects_tamper(git_server, project):
    r = git_server.repo("team/base")
    c = r.commit({"apm.yml": APM})
    spec = parse_entry(r.https, allow_local=False)
    src, h = place(project, spec, c, None, Git())
    (src / "apm.yml").write_text("hacked")
    locked = LockedDep(repo_url="team/base", host="git.test", resolved_commit=c, content_hash=h)
    src, h2 = place(project, spec, c, locked, Git())
    assert h2 == h and (src / "apm.yml").read_text() == APM
    bad = LockedDep(
        repo_url="team/base", host="git.test", resolved_commit=c, content_hash="sha256:0"
    )
    with pytest.raises(ApmError, match="content hash"):
        place(project, spec, c, bad, Git())


def test_new_commit_records_new_hash(git_server, project):
    r = git_server.repo("team/base")
    c1 = r.commit({"apm.yml": APM})
    c2 = r.commit({"apm.yml": APM + "#2\n"})
    spec = parse_entry(r.https, allow_local=False)
    _, h1 = place(project, spec, c1, None, Git())
    locked = LockedDep(repo_url="team/base", host="git.test", resolved_commit=c1, content_hash=h1)
    _, h2 = place(project, spec, c2, locked, Git())
    assert h2 != h1


def test_overlaps():
    with pytest.raises(ApmError):
        check_overlaps({"a": "team/x", "b": "team/x/sub"})
    with pytest.raises(ApmError):
        check_overlaps({"a": "Team/X", "b": "team/x"})
    check_overlaps({"a": "team/x", "b": "team/xy"})


@pytest.mark.parametrize(
    "files,ptype",
    [
        ({"SKILL.md": "s", "apm.yml": APM}, "hybrid"),
        ({"SKILL.md": "s"}, "claude_skill"),
        ({"skills/a/SKILL.md": "s"}, "skill_bundle"),
        ({"apm.yml": APM, ".apm/skills/a/SKILL.md": "s"}, "apm_package"),
    ],
)
def test_inspect_shapes(tmp_path, files, ptype):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    spec = parse_entry("https://git.test/team/x.git", allow_local=False)
    assert inspect(tmp_path, spec, warn=lambda m: None).package_type == ptype


def test_inspect_rejects(tmp_path):
    spec = parse_entry("https://git.test/team/x.git", allow_local=False)
    with pytest.raises(ApmError, match="not an apm package"):
        inspect(tmp_path, spec, warn=lambda m: None)
    (tmp_path / "SKILL.md").write_text("s")
    sub = parse_entry({"git": "https://git.test/team/x.git", "skills": ["a"]}, allow_local=False)
    with pytest.raises(ApmError):
        inspect(tmp_path, sub, warn=lambda m: None)


@pytest.mark.parametrize("repo_path", ["../x", "/team/x", "team/../x", "team//x", "team/x y"])
def test_install_dir_rejects_unsafe_paths(project, repo_path):
    with pytest.raises(ApmError):
        install_dir(project, repo_path)


@pytest.mark.parametrize("rel", ["apm_modules", "apm_modules/.tmp", "apm_modules/team/base"])
def test_place_rejects_symlink_destinations(project, tmp_path, rel):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    link = project / rel
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    spec = parse_entry("https://git.test/team/base.git", allow_local=False)
    lines = []
    with pytest.raises(ApmError, match="symlink"):
        place(project, spec, "a" * 40, None, Git(verbose=True, echo=lines.append))
    assert lines == [] and (outside / "keep").read_text() == "keep"


def test_place_failures_keep_cache_and_clean_temporary_tree(git_server, project, monkeypatch):
    r = git_server.repo("team/base")
    commit = r.commit({"apm.yml": APM})
    spec = parse_entry(r.https, allow_local=False)
    src, h = place(project, spec, commit, None, Git())
    bad = LockedDep(repo_url="team/base", resolved_commit=commit, content_hash="sha256:0")
    with pytest.raises(ApmError, match="tamper or force-push") as exc:
        place(project, spec, commit, bad, Git())
    assert exc.value.hint
    assert content_hash(src) == h and list((project / "apm_modules/.tmp").iterdir()) == []

    def fail_fetch(url, sha, dest):
        assert url == r.https and sha == "b" * 40 and not dest.exists()
        dest.mkdir()
        (dest / "partial").write_text("partial")
        raise ApmError("fetch failed")

    git = Git()
    monkeypatch.setattr(git, "fetch", fail_fetch)
    with pytest.raises(ApmError, match="fetch failed"):
        place(project, spec, "b" * 40, None, git)
    assert content_hash(src) == h and list((project / "apm_modules/.tmp").iterdir()) == []


@pytest.mark.parametrize("paths", [{"first": "TEAM/X/sub", "second": "team/x"}])
def test_overlap_errors_name_both_packages(paths):
    with pytest.raises(ApmError) as exc:
        check_overlaps(paths)
    assert "first" in str(exc.value) and "second" in str(exc.value)


@pytest.mark.parametrize("root_skill,ptype", [(False, "skill_bundle"), (True, "hybrid")])
def test_inspect_precedence_and_manifest_metadata(tmp_path, root_skill, ptype):
    (tmp_path / "apm.yml").write_text(
        APM + "extra: ignored\ndependencies:\n  apm:\n    - https://git.test/team/child.git\n"
    )
    (tmp_path / ".apm").mkdir()
    skill = tmp_path / "skills/a/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("s")
    if root_skill:
        (tmp_path / "SKILL.md").write_text("s")
    spec = parse_entry("https://git.test/team/x.git", allow_local=False)
    warnings = []
    info = inspect(tmp_path, spec, warn=warnings.append)
    assert (info.package_type, info.name, info.version) == (ptype, "base", "1.0.0")
    assert len(info.deps) == 1
    assert info.deps[0].key == "git.test/team/child" and info.deps[0].declared_by == spec.key
    assert len(warnings) == 1 and "extra" in warnings[0]


@pytest.mark.parametrize("entry", ["https://git.test/team/RepoName.git", "./local/RepoName/"])
def test_inspect_fallback_name_without_manifest(tmp_path, entry):
    (tmp_path / "SKILL.md").write_text("s")
    spec = parse_entry(entry, allow_local=True)
    info = inspect(tmp_path, spec, warn=lambda m: None)
    assert (info.name, info.version, info.deps) == ("RepoName", None, [])


def test_inspect_rejects_transitive_local_dependency(tmp_path):
    (tmp_path / "SKILL.md").write_text("s")
    (tmp_path / "apm.yml").write_text(APM + "dependencies:\n  apm:\n    - ./local/child\n")
    spec = parse_entry("./local/base", allow_local=True)
    with pytest.raises(ApmError, match="local paths are only allowed") as exc:
        inspect(tmp_path, spec, warn=lambda m: None)
    assert spec.key in str(exc.value)


@pytest.mark.parametrize("selected", [("a",), ("missing",), ("../a",)])
def test_inspect_validates_skill_selection(tmp_path, selected):
    skill = tmp_path / "skills/a/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("s")
    spec = parse_entry(
        {"git": "https://git.test/team/x.git", "skills": list(selected)}, allow_local=False
    )
    if selected == ("a",):
        assert inspect(tmp_path, spec, warn=lambda m: None).package_type == "skill_bundle"
    else:
        with pytest.raises(ApmError):
            inspect(tmp_path, spec, warn=lambda m: None)


@pytest.mark.parametrize("manifest,apm_dir", [(True, False), (False, True)])
def test_inspect_requires_manifest_and_apm_directory(tmp_path, manifest, apm_dir):
    if manifest:
        (tmp_path / "apm.yml").write_text(APM)
    if apm_dir:
        (tmp_path / ".apm").mkdir()
    spec = parse_entry("https://git.test/team/x.git", allow_local=False)
    with pytest.raises(ApmError, match="not an apm package"):
        inspect(tmp_path, spec, warn=lambda m: None)
