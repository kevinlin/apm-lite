import pytest

from apm_lite import ApmError, Git, Lock, LockedDep, load, resolve


def _apm(name, deps=()):
    lines = "".join(f"    - {d}\n" for d in deps)
    return (
        f"name: {name}\nversion: '1.0.0'\ndependencies:\n  apm:\n{lines}"
        if deps
        else f"name: {name}\nversion: '1.0.0'\n"
    )


def _root(project, deps):
    (project / "apm.yml").write_text(_apm("root", deps))
    return load(project / "apm.yml", is_root=True)


def _res(project, m, lock=None, mode="install", keys=None, warn=None):
    return resolve(
        project, m, lock, mode=mode, update_keys=keys, git=Git(), warn=(warn or (lambda s: None))
    )


def _lock_of(pkgs):
    return Lock(
        deps={
            p.spec.key: LockedDep(
                repo_url=p.repo_url,
                host=p.spec.host,
                resolved_commit=p.commit,
                resolved_ref=p.spec.ref,
                depth=p.depth,
                content_hash=p.content_hash,
            )
            for p in pkgs
            if p.spec.kind == "remote"
        }
    )


def test_transitive_bfs_and_shallowest_wins(git_server, project):
    leaf = git_server.repo("t/leaf")
    l1 = leaf.commit({"SKILL.md": "s"})
    leaf.branch("dev")
    l2 = leaf.commit({"SKILL.md": "s2"})
    assert l1 != l2
    base = git_server.repo("t/base")
    base.commit({"apm.yml": _apm("base", [leaf.https + "#dev"]), ".apm/x": ""})
    warnings = []
    pkgs = _res(project, _root(project, [base.ssh, leaf.scp]), warn=warnings.append)
    assert [p.spec.key for p in pkgs] == ["git.test/t/base", "git.test/t/leaf"]
    assert pkgs[1].commit == l2 and pkgs[1].depth == 1 and warnings


def test_immutable_conflict_errors(git_server, project):
    leaf = git_server.repo("t/leaf")
    leaf.commit({"SKILL.md": "1"})
    leaf.tag("v1")
    leaf.commit({"SKILL.md": "2"})
    leaf.tag("v2")
    base = git_server.repo("t/base")
    base.commit({"apm.yml": _apm("base", [leaf.https + "#v2"]), ".apm/x": ""})
    with pytest.raises(ApmError, match="t/leaf"):
        _res(project, _root(project, [leaf.https + "#v1", base.https]))


def test_pins_reused_and_moved_tag_no_false_conflict(git_server, project):
    leaf = git_server.repo("t/leaf")
    c1 = leaf.commit({"SKILL.md": "1"})
    leaf.tag("v1")
    m = _root(project, [leaf.https + "#v1", leaf.https + "#" + c1])
    pkgs = _res(project, m)
    leaf.commit({"SKILL.md": "2"})
    leaf.tag("v1", force=True)  # tag moved upstream
    again = _res(project, m, lock=_lock_of(pkgs))
    assert again[0].commit == c1


def test_update_moves_branch_and_frozen_checks(git_server, project):
    leaf = git_server.repo("t/leaf")
    c1 = leaf.commit({"SKILL.md": "1"})
    m = _root(project, [leaf.https + "#main"])
    lock = _lock_of(_res(project, m))
    c2 = leaf.commit({"SKILL.md": "2"})
    assert _res(project, m, lock=lock)[0].commit == c1
    assert _res(project, m, lock=lock, mode="update")[0].commit == c2
    assert _res(project, m, lock=lock, mode="frozen")[0].commit == c1
    with pytest.raises(ApmError, match="--frozen"):
        _res(project, _root(project, [leaf.https + "#v9"]), lock=lock, mode="frozen")
    with pytest.raises(ApmError, match="--frozen"):
        _res(project, _root(project, []), lock=lock, mode="frozen")  # stale entry
    with pytest.raises(ApmError, match="--frozen"):
        _res(project, m, lock=None, mode="frozen")


def test_subset_union_and_cycle(git_server, project):
    col = git_server.repo("t/col")
    col.commit({"skills/a/SKILL.md": "a", "skills/b/SKILL.md": "b"})
    base = git_server.repo("t/base")
    base.commit(
        {
            "apm.yml": _apm(
                "base", ["{git: " + col.https + ", skills: [b]}", "https://git.test/t/base.git"]
            ),
            ".apm/x": "",
        }
    )
    pkgs = _res(project, _root(project, ["{git: " + col.https + ", skills: [a]}", base.https]))
    col_pkg = next(p for p in pkgs if p.spec.key == "git.test/t/col")
    assert col_pkg.skills_selected == ("a", "b") and len(pkgs) == 2


def test_local_root_dep_with_remote_child(git_server, project):
    leaf = git_server.repo("t/leaf")
    leaf.commit({"SKILL.md": "s"})
    local = project / "packages/x"
    local.mkdir(parents=True)
    (local / "apm.yml").write_text(_apm("x", [leaf.https]))
    (local / ".apm").mkdir()
    pkgs = _res(project, _root(project, ["./packages/x"]))
    assert [p.spec.kind for p in pkgs] == ["local", "remote"] and pkgs[0].commit is None
    assert pkgs[1].parent_repo_url == "_local/x"


@pytest.mark.parametrize("all_first", [False, True])
def test_subset_all_overrides_union(git_server, project, all_first):
    col = git_server.repo("t/col")
    col.commit({"skills/a/SKILL.md": "a", "skills/b/SKILL.md": "b"})
    deps = ["{git: " + col.https + ", skills: [a]}", col.https]
    if all_first:
        deps.reverse()
    assert _res(project, _root(project, deps))[0].skills_selected is None


@pytest.mark.parametrize("first_subset", [False, True])
def test_duplicate_missing_skill_is_rejected(git_server, project, first_subset):
    col = git_server.repo("t/col")
    col.commit({"skills/a/SKILL.md": "a"})
    first = "{git: " + col.https + ", skills: [a]}" if first_subset else col.https
    with pytest.raises(ApmError, match="missing skill 'missing'"):
        _res(project, _root(project, [first, "{git: " + col.ssh + ", skills: [missing]}"]))


def test_duplicate_subset_on_single_skill_is_rejected(git_server, project):
    leaf = git_server.repo("t/leaf")
    leaf.commit({"SKILL.md": "s"})
    with pytest.raises(ApmError, match="'skills' requires a skill collection"):
        _res(project, _root(project, [leaf.https, "{git: " + leaf.https + ", skills: [a]}"]))


@pytest.mark.parametrize("keys", [set(), {"git.test/t/a"}, None])
def test_update_only_selected_keys(git_server, project, keys):
    a, b = git_server.repo("t/a"), git_server.repo("t/b")
    a1, b1 = a.commit({"SKILL.md": "a1"}), b.commit({"SKILL.md": "b1"})
    manifest = _root(project, [a.https, b.https])
    lock = _lock_of(_res(project, manifest))
    a2, b2 = a.commit({"SKILL.md": "a2"}), b.commit({"SKILL.md": "b2"})
    pkgs = _res(project, manifest, lock, mode="update", keys=keys)
    assert [p.commit for p in pkgs] == [
        a2 if keys is None or "git.test/t/a" in keys else a1,
        b2 if keys is None or "git.test/t/b" in keys else b1,
    ]


@pytest.mark.parametrize("pin", [None, "a" * 39, "z" * 40])
def test_invalid_pin_resolves_on_install_and_errors_when_frozen(git_server, project, pin):
    leaf = git_server.repo("t/leaf")
    commit = leaf.commit({"SKILL.md": "s"})
    manifest = _root(project, [leaf.https])
    lock = Lock(deps={"git.test/t/leaf": LockedDep(repo_url="t/leaf", resolved_commit=pin)})
    with pytest.raises(ApmError, match=r"^--frozen: ") as exc:
        _res(project, manifest, lock, mode="frozen")
    assert exc.value.hint == "run 'apm install' to update apm.lock.yaml"
    assert _res(project, manifest, lock)[0].commit == commit


def test_frozen_checks_missing_transitive_pin(git_server, project):
    leaf = git_server.repo("t/leaf")
    leaf.commit({"SKILL.md": "s"})
    base = git_server.repo("t/base")
    base.commit({"apm.yml": _apm("base", [leaf.https]), ".apm/x": ""})
    manifest = _root(project, [base.https])
    lock = _lock_of(_res(project, manifest))
    del lock.deps["git.test/t/leaf"]
    with pytest.raises(ApmError, match=r"^--frozen: .*git.test/t/leaf") as exc:
        _res(project, manifest, lock, mode="frozen")
    assert exc.value.hint == "run 'apm install' to update apm.lock.yaml"


def test_frozen_checks_duplicate_sha(git_server, project):
    leaf = git_server.repo("t/leaf")
    c1 = leaf.commit({"SKILL.md": "1"})
    manifest = _root(project, [leaf.https])
    lock = _lock_of(_res(project, manifest))
    c2 = leaf.commit({"SKILL.md": "2"})
    with pytest.raises(ApmError, match=r"^--frozen: .*SHA"):
        _res(project, _root(project, [leaf.https, leaf.ssh + "#" + c2]), lock, mode="frozen")
    assert (
        _res(project, _root(project, [leaf.https, leaf.ssh + "#" + c1]), lock, mode="frozen")[
            0
        ].commit
        == c1
    )


def test_frozen_checks_merged_subset(git_server, project):
    col = git_server.repo("t/col")
    col.commit({"skills/a/SKILL.md": "a", "skills/b/SKILL.md": "b"})
    manifest = _root(
        project, ["{git: " + col.https + ", skills: [a]}", "{git: " + col.https + ", skills: [b]}"]
    )
    pkgs = _res(project, manifest)
    lock = _lock_of(pkgs)
    lock.deps[pkgs[0].spec.key].skill_subset = ["b", "a"]
    assert _res(project, manifest, lock, mode="frozen")[0].skills_selected == ("a", "b")
    lock.deps[pkgs[0].spec.key].skill_subset = ["a"]
    with pytest.raises(ApmError, match=r"^--frozen: .*skill subset"):
        _res(project, manifest, lock, mode="frozen")


@pytest.mark.parametrize("ref", [None, "main", "v1", "sha"])
def test_frozen_winner_does_not_query_refs(git_server, project, monkeypatch, ref):
    leaf = git_server.repo("t/leaf")
    commit = leaf.commit({"SKILL.md": "s"})
    leaf.tag("v1")
    ref = commit if ref == "sha" else ref
    manifest = _root(project, [leaf.https + ("#" + ref if ref else "")])
    lock = _lock_of(_res(project, manifest))

    def no_query(self, url):
        pytest.fail(f"unexpected ls_remote: {url}")

    monkeypatch.setattr(Git, "ls_remote", no_query)
    assert _res(project, manifest, lock, mode="frozen")[0].commit == commit


def test_frozen_duplicate_branch_does_not_query_refs(git_server, project, monkeypatch):
    leaf = git_server.repo("t/leaf")
    c1 = leaf.commit({"SKILL.md": "1"})
    manifest = _root(project, [leaf.https + "#main", leaf.ssh])
    lock = _lock_of(_res(project, manifest))
    leaf.commit({"SKILL.md": "2"})

    def no_query(self, url):
        pytest.fail(f"unexpected ls_remote: {url}")

    monkeypatch.setattr(Git, "ls_remote", no_query)
    assert _res(project, manifest, lock, mode="frozen")[0].commit == c1


def test_frozen_duplicate_tag_and_moved_winner_tag(git_server, project):
    leaf = git_server.repo("t/leaf")
    c1 = leaf.commit({"SKILL.md": "1"})
    leaf.tag("v1")
    leaf.tag("same")
    manifest = _root(project, [leaf.https + "#v1", leaf.ssh + "#same"])
    lock = _lock_of(_res(project, manifest))
    leaf.commit({"SKILL.md": "2"})
    leaf.tag("v1", force=True)
    assert _res(project, manifest, lock, mode="frozen")[0].commit == c1
    leaf.tag("same", force=True)
    with pytest.raises(ApmError, match="t/leaf"):
        _res(project, manifest, lock, mode="frozen")


def test_frozen_tag_conflict_queries_only_duplicate_url(git_server, project, monkeypatch):
    leaf = git_server.repo("t/leaf")
    leaf.commit({"SKILL.md": "1"})
    leaf.tag("v1")
    manifest = _root(project, [leaf.https + "#v1"])
    lock = _lock_of(_res(project, manifest))
    leaf.commit({"SKILL.md": "2"})
    leaf.tag("v2")
    manifest = _root(project, [leaf.https + "#v1", leaf.ssh + "#v2"])
    queries = []
    run = Git.run

    def record_run(self, args, cwd=None):
        if args[0] == "ls-remote":
            queries.append(args[-1])
        return run(self, args, cwd)

    monkeypatch.setattr(Git, "run", record_run)
    with pytest.raises(ApmError) as exc:
        _res(project, manifest, lock, mode="frozen")
    assert queries == [leaf.ssh]
    assert str(exc.value).startswith("--frozen: ")
    assert exc.value.hint == "run 'apm install' to update apm.lock.yaml"


def test_pinned_branch_and_sha_warn_instead_of_immutable_conflict(git_server, project):
    leaf = git_server.repo("t/leaf")
    leaf.commit({"SKILL.md": "1"})
    manifest = _root(project, [leaf.https + "#main"])
    lock = _lock_of(_res(project, manifest))
    c2 = leaf.commit({"SKILL.md": "2"})
    warnings = []
    pkgs = _res(
        project,
        _root(project, [leaf.https + "#main", leaf.ssh + "#" + c2]),
        lock,
        warn=warnings.append,
    )
    assert pkgs[0].commit == lock.deps[pkgs[0].spec.key].resolved_commit
    assert len(warnings) == 1 and "main" in warnings[0] and c2 in warnings[0]


def test_immutable_conflict_checks_losing_requests_and_names_chains(git_server, project):
    leaf = git_server.repo("t/leaf")
    leaf.commit({"SKILL.md": "1"})
    leaf.tag("v1")
    leaf.commit({"SKILL.md": "2"})
    leaf.tag("v2")
    bases = []
    for name, ref in (("a", "v1"), ("b", "v2")):
        base = git_server.repo("t/" + name)
        base.commit({"apm.yml": _apm(name, [leaf.https + "#" + ref]), ".apm/x": ""})
        bases.append(base.https)
    with pytest.raises(ApmError) as exc:
        _res(project, _root(project, [leaf.https, *bases]))
    assert all(s in str(exc.value) for s in ("t/a", "t/b", "t/leaf", "v1", "v2"))


def test_local_aliases_are_rejected_before_placement(git_server, project):
    leaf = git_server.repo("t/leaf")
    leaf.commit({"SKILL.md": "s"})
    local = project / "packages/x"
    local.mkdir(parents=True)
    (local / "SKILL.md").write_text("s")
    (project / "alias").symlink_to(local, target_is_directory=True)
    with pytest.raises(ApmError, match=r"same.*directory"):
        _res(project, _root(project, [leaf.https, "./packages/x", "./alias"]))
    assert not (project / "apm_modules").exists()


def test_frozen_local_dependency_is_not_pin_checked(project):
    local = project / "x"
    local.mkdir()
    (local / "SKILL.md").write_text("s")
    stale_local = LockedDep(repo_url="_local/old", source="local", local_path="./old")
    pkgs = _res(project, _root(project, ["./x"]), Lock(deps={"./old": stale_local}), mode="frozen")
    assert pkgs[0].source_dir == local and pkgs[0].content_hash is None
    assert not (project / "apm_modules").exists()


def test_overlaps_are_checked_before_any_placement(git_server, project):
    a = git_server.repo("t/x")
    a.commit({"SKILL.md": "s"})
    with pytest.raises(ApmError, match="overlapping package destinations"):
        _res(project, _root(project, [a.https, "https://other.test/t/x/sub.git"]))
    assert not (project / "apm_modules").exists()


def test_transitive_overlap_preserves_existing_parent(git_server, project):
    a = git_server.repo("t/x")
    a.commit({"apm.yml": _apm("x", ["https://other.test/t/x/sub.git"]), ".apm/x": "keep"})
    with pytest.raises(ApmError, match="overlapping package destinations"):
        _res(project, _root(project, [a.https]))
    assert (project / "apm_modules/t/x/.apm/x").read_text() == "keep"


@pytest.mark.parametrize("length", [50, 51])
def test_depth_limit(project, tmp_path, monkeypatch, length):
    import apm_lite as packages
    from apm_lite import parse_entry

    def place(proj, spec, commit, locked, git):
        return tmp_path, "sha256:fake"

    def inspect(source, spec, *, warn):
        i = int(spec.repo_path.rsplit("/", 1)[-1])
        deps = (
            [
                parse_entry(
                    f"https://git.test/t/{i + 1}.git", allow_local=False, declared_by=spec.key
                )
            ]
            if i < length
            else []
        )
        return packages.PackageInfo("apm_package", str(i), "1.0.0", deps)

    monkeypatch.setattr(packages, "place", place)
    monkeypatch.setattr(packages, "inspect", inspect)
    monkeypatch.setattr(Git, "ls_remote", lambda self, url: {"HEAD": "a" * 40})
    manifest = _root(project, ["https://git.test/t/1.git"])
    if length == 50:
        pkgs = _res(project, manifest)
        assert len(pkgs) == 50 and pkgs[-1].depth == 50
    else:
        with pytest.raises(ApmError, match=r"depth.*50"):
            _res(project, manifest)
