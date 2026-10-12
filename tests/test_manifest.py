import pytest

from apm_lite import ApmError, add_entry, dump_manifest, init_text, load, parse_entry, remove_entry


@pytest.mark.parametrize(
    "entry,host,port,path,ref,key",
    [
        (
            "https://git.corp.example/team/pkg.git#v1.0",
            "git.corp.example",
            None,
            "team/pkg",
            "v1.0",
            "git.corp.example/team/pkg",
        ),
        (
            "ssh://git@git.corp.example:7999/team/pkg.git#main",
            "git.corp.example",
            7999,
            "team/pkg",
            "main",
            "git.corp.example/team/pkg",
        ),
        (
            "ssh://git@git.corp.example:22/team/pkg",
            "git.corp.example",
            None,
            "team/pkg",
            None,
            "git.corp.example/team/pkg",
        ),
        (
            "git@git.corp.example:group/sub/Pkg.git",
            "git.corp.example",
            None,
            "group/sub/Pkg",
            None,
            "git.corp.example/group/sub/Pkg",
        ),
    ],
)
def test_parse_remote(entry, host, port, path, ref, key):
    d = parse_entry(entry, allow_local=False)
    assert (d.kind, d.host, d.port, d.repo_path, d.ref, d.key) == (
        "remote",
        host,
        port,
        path,
        ref,
        key,
    )
    assert d.url == entry.split("#")[0]


def test_parse_object_ref_overrides_and_skills_sorted():
    d = parse_entry(
        {"git": "https://git.corp.example/t/s.git#v1", "ref": "v2", "skills": ["b", "a", "b"]},
        allow_local=False,
    )
    assert (d.ref, d.skills) == ("v2", ("a", "b"))


@pytest.mark.parametrize(
    "bad",
    [
        "team/pkg",
        "git.corp.example/team/pkg",
        "http://git.corp.example/t/p.git",
        "https://gitserver/t/p.git",
        "https://git..corp/t/p.git",
        "https://git_corp.example/t/p.git",
        "https://git.corp.example/pkg.git",
        "https://git.corp.example/t/p@x.git",
        "ssh://git@git.corp.example/t/p.git#release@candidate",
        "git@git.corp.example:7999/t/p.git",
        "https://git.corp.example/t/../p.git",
        "https://git.corp.example/t/p.git#1.2.3",
        {"git": "https://git.corp.example/t/p.git", "path": "sub"},
        {"git": "https://git.corp.example/t/p.git", "alias": "x"},
        {"git": "https://git.corp.example/t/p.git", "version": "1"},
        {"git": "https://git.corp.example/t/p.git", "skills": []},
    ],
)
def test_parse_rejects(bad):
    with pytest.raises(ApmError):
        parse_entry(bad, allow_local=True)


def test_local_only_when_allowed():
    assert parse_entry("./packages/x", allow_local=True).key == "./packages/x"
    with pytest.raises(ApmError):
        parse_entry("./packages/x", allow_local=False)


def _write(p, text):
    p.write_text(text)
    return p


def test_load_defaults_and_warnings(project):
    warnings = []
    m = load(
        _write(
            project / "apm.yml",
            "name: p\nversion: '1.0'\nscripts: {a: b}\ndependencies:\n  mcp: []\n",
        ),
        is_root=True,
        warn=warnings.append,
    )
    assert m.targets == ("claude",) and m.deps == []
    assert len(warnings) == 2


@pytest.mark.parametrize(
    "text",
    [
        "version: '1'\n",
        "name: p\nversion: 1.0\n",
        "name: ' '\nversion: '1'\n",
        "name: p\nversion: '1'\ntargets: []\n",
        "name: p\nversion: '1'\ntargets: [cursor]\n",
        "name: p\nversion: '1'\ntarget: claude\n",
    ],
)
def test_load_rejects(project, text):
    with pytest.raises(ApmError):
        load(_write(project / "apm.yml", text), is_root=True)


def test_dependency_manifest_rejects_local(project):
    with pytest.raises(ApmError):
        load(
            _write(project / "apm.yml", "name: p\nversion: '1'\ndependencies:\n  apm: [./x]\n"),
            is_root=False,
            declared_by="git.test/t/p",
        )


def test_root_local_duplicate_folder_name_rejected(project):
    with pytest.raises(ApmError):
        load(
            _write(
                project / "apm.yml",
                "name: p\nversion: '1'\ndependencies:\n  apm: [./a/base, ./b/base]\n",
            ),
            is_root=True,
        )


def test_add_and_remove_keep_comments_and_transport(project):
    m = load(
        _write(project / "apm.yml", init_text("p").replace("apm: []", "apm: []  # deps")),
        is_root=True,
    )
    add_entry(m, "git@git.corp.example:team/pkg.git#v1", None)
    add_entry(m, "https://git.corp.example/team/skills.git#v2", ["b"])
    add_entry(m, "https://git.corp.example/team/skills.git#v2", ["a"])
    text = dump_manifest(m).decode()
    assert "git@git.corp.example:team/pkg.git#v1" in text
    assert "skills:\n" in text and "- a\n" in text and "- b\n" in text
    remove_entry(m, "git.corp.example/team/pkg")
    assert "team/pkg" not in dump_manifest(m).decode()
    with pytest.raises(ApmError):
        remove_entry(m, "nope")


@pytest.mark.parametrize("name", ["proj", "1.0", "true", "2024", "my: proj", "#x", "a b"])
def test_init_text_round_trips_any_folder_name(project, name):
    m = load(_write(project / "apm.yml", init_text(name)), is_root=True)
    assert (m.name, m.version, m.targets, m.deps) == (name, "0.1.0", ("claude",), [])


def test_init_text_is_plain_for_ordinary_names():
    assert init_text("p") == (
        'name: p\nversion: "0.1.0"\ntargets:\n  - claude\ndependencies:\n  apm: []\n'
    )


def test_removing_the_last_entry_keeps_a_loadable_manifest(project):
    m = load(
        _write(project / "apm.yml", init_text("p").replace("apm: []", "apm: []  # deps")),
        is_root=True,
    )
    add_entry(m, "https://git.corp.example/team/pkg.git#v1", None)
    remove_entry(m, "https://git.corp.example/team/pkg.git#v1")
    again = load(_write(project / "apm.yml", dump_manifest(m).decode()), is_root=True)
    assert again.deps == [] and "# deps" in dump_manifest(m).decode()


def test_add_existing_dependency_only_edits_when_subset_grows(project):
    m = load(_write(project / "apm.yml", init_text("p")), is_root=True)
    url = "https://git.corp.example/team/skills.git#v2"
    add_entry(m, url, None)
    m.edited = False
    add_entry(m, url, ["a"])  # the entry already means "all skills"
    add_entry(m, url, None)
    assert m.edited is False and len(m.deps) == 1 and m.deps[0].skills is None


def test_add_subset_merges_into_entry_written_as_object(project):
    m = load(_write(project / "apm.yml", init_text("p")), is_root=True)
    url = "https://git.corp.example/team/skills.git"
    add_entry(m, url + "#v2", ["b"])
    add_entry(m, url + "#v2", ["a", "b"])
    add_entry(m, url + "#v3", ["c"])  # another ref is another request
    reloaded = load(_write(project / "apm.yml", dump_manifest(m).decode()), is_root=True)
    assert [(d.ref, d.skills) for d in reloaded.deps] == [("v2", ("a", "b")), ("v3", ("c",))]


def test_remove_matches_entry_as_written_key_and_local_path(project):
    text = init_text("p").replace(
        "apm: []", "apm:\n    - ./packages/x\n    - ssh://git@git.corp.example/t/p.git"
    )
    m = load(_write(project / "apm.yml", text), is_root=True)
    remove_entry(m, "./packages/x")
    assert [d.key for d in m.deps] == ["git.corp.example/t/p"]
    remove_entry(m, "ssh://git@git.corp.example/t/p.git")
    assert m.deps == [] and "packages/x" not in dump_manifest(m).decode()


def test_rejections_name_the_supported_forms():
    with pytest.raises(ApmError, match=r"git@host:group/repo\.git#ref.*\./local/path"):
        parse_entry("team/pkg", allow_local=True)
    with pytest.raises(ApmError) as e:
        parse_entry("https://git.corp.example/t/p.git#1.2.3", allow_local=True)
    assert e.value.hint == "use the tag name, e.g. 'v1.2.3'"
