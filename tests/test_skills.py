import pytest

from apm_lite.deploy.skills import plan_skills, to_hyphen_case, validate_skill_name
from apm_lite.errors import ApmError
from apm_lite.model import DepSpec, ResolvedPackage
from apm_lite.targets import TARGETS


def _pkg(tmp_path, ptype, selected=None, path="team/My_Skills", depth=1):
    spec = DepSpec(raw="x", kind="remote", key=f"git.test/{path}", repo_path=path)
    return ResolvedPackage(
        spec=spec,
        commit="a" * 40,
        ref_is_immutable=True,
        depth=depth,
        parent_key=None,
        parent_repo_url=None,
        source_dir=tmp_path,
        package_type=ptype,
        name="n",
        version=None,
        content_hash=None,
        skills_selected=selected,
    )


def _w(p, text="x", exe=False):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    if exe:
        p.chmod(0o755)


def test_hyphen_case():
    assert to_hyphen_case("My_Skills") == "my-skills"
    assert to_hyphen_case("myRepo Name") == "my-repo-name"


def test_hyphen_case_ported_behaviour():
    assert to_hyphen_case("owner/MyPackage") == "my-package"
    assert to_hyphen_case("__a..b__") == "ab"
    assert len(to_hyphen_case("a" * 100)) == 64


def test_validate_skill_name():
    for ok in ("a", "my-skill", "a1-b2", "a" * 64):
        assert validate_skill_name(ok), ok
    for bad in ("", "Bad_Name", "-a", "a-", "a--b", "a_b", "a" * 65):
        assert not validate_skill_name(bad), bad


def test_single_skill_whole_dir_normalised_name(tmp_path):  # Review Focus 3
    _w(tmp_path / "SKILL.md", "---\nname: s\ndescription: d\n---\nx")
    _w(tmp_path / "scripts/run.sh", exe=True)
    _w(tmp_path / ".git/HEAD")
    _w(tmp_path / ".apm/x")
    files, roots, warnings = plan_skills(_pkg(tmp_path, "claude_skill"), TARGETS["claude"])
    assert set(files) == {
        ".claude/skills/my-skills/SKILL.md",
        ".claude/skills/my-skills/scripts/run.sh",
    }
    assert files[".claude/skills/my-skills/scripts/run.sh"].mode == 0o755
    assert roots == {".claude/skills/my-skills": "git.test/team/My_Skills"}
    assert warnings == []


def test_collection_subset_and_warnings(tmp_path):
    _w(tmp_path / "skills/a/SKILL.md", "---\nname: a\n---\nno description")
    _w(tmp_path / "skills/Bad_Name/SKILL.md", "---\ndescription: d\n---\n")
    _w(tmp_path / "skills/c/SKILL.md", "---\ndescription: d\n---\n")
    _, roots, warnings = plan_skills(
        _pkg(tmp_path, "skill_bundle", ("a", "Bad_Name")), TARGETS["antigravity"]
    )
    assert set(roots) == {".agents/skills/a", ".agents/skills/Bad_Name"}
    assert len(warnings) == 2


def test_collection_without_subset_deploys_every_skill(tmp_path):
    _w(tmp_path / "skills/a/SKILL.md", "---\ndescription: d\n---\n")
    _w(tmp_path / "skills/b/SKILL.md", "---\ndescription: d\n---\n")
    _w(tmp_path / "skills/b/ref/notes.md")
    _w(tmp_path / "skills/not-a-skill/README.md")
    files, roots, warnings = plan_skills(_pkg(tmp_path, "skill_bundle"), TARGETS["claude"])
    assert set(roots) == {".claude/skills/a", ".claude/skills/b"}
    assert set(files) == {
        ".claude/skills/a/SKILL.md",
        ".claude/skills/b/SKILL.md",
        ".claude/skills/b/ref/notes.md",
    }
    assert warnings == []


def test_collection_unsafe_selected_name_is_an_error(tmp_path):
    _w(tmp_path / "skills/a/SKILL.md", "---\ndescription: d\n---\n")
    with pytest.raises(ApmError):
        plan_skills(_pkg(tmp_path, "skill_bundle", ("a", "../x")), TARGETS["claude"])


def test_hybrid_is_a_single_skill(tmp_path):
    _w(tmp_path / "SKILL.md", "---\ndescription: d\n---\n")
    _w(tmp_path / "apm.yml", "name: n\nversion: 1\n")
    files, roots, _ = plan_skills(_pkg(tmp_path, "hybrid"), TARGETS["claude"])
    assert set(roots) == {".claude/skills/my-skills"}
    assert ".claude/skills/my-skills/apm.yml" in files


def test_apm_package_deploys_apm_skills_only(tmp_path):
    _w(tmp_path / ".apm/skills/review/SKILL.md", "---\ndescription: d\n---\n")
    _w(tmp_path / ".apm/skills/review/.apm/inner")
    _w(tmp_path / ".apm/instructions/style.instructions.md")
    _w(tmp_path / "skills/ignored/SKILL.md", "---\ndescription: d\n---\n")
    files, roots, warnings = plan_skills(_pkg(tmp_path, "apm_package"), TARGETS["claude"])
    assert set(files) == {".claude/skills/review/SKILL.md"}
    assert roots == {".claude/skills/review": "git.test/team/My_Skills"}
    assert warnings == []


def test_root_project_uses_apm_skills_whatever_its_type(tmp_path):
    _w(tmp_path / ".apm/skills/mine/SKILL.md", "---\ndescription: d\n---\n")
    _w(tmp_path / "SKILL.md", "---\ndescription: d\n---\n")
    _w(tmp_path / "skills/other/SKILL.md", "---\ndescription: d\n---\n")
    files, roots, _ = plan_skills(_pkg(tmp_path, "hybrid", depth=0), TARGETS["claude"])
    assert set(files) == {".claude/skills/mine/SKILL.md"}
    assert roots == {".claude/skills/mine": "."}


def test_skips_symlinks_and_pycache_at_any_depth(tmp_path):
    _w(tmp_path / "SKILL.md", "---\ndescription: d\n---\n")
    _w(tmp_path / "scripts/run.py")
    _w(tmp_path / "scripts/__pycache__/run.pyc")
    _w(tmp_path / "scripts/.git/config")
    _w(tmp_path / "elsewhere/secret.txt", "secret")
    (tmp_path / "leak.txt").symlink_to(tmp_path / "elsewhere/secret.txt")
    (tmp_path / "leakdir").symlink_to(tmp_path / "elsewhere")
    files, _, _ = plan_skills(_pkg(tmp_path, "claude_skill"), TARGETS["claude"])
    assert set(files) == {
        ".claude/skills/my-skills/SKILL.md",
        ".claude/skills/my-skills/scripts/run.py",
        ".claude/skills/my-skills/elsewhere/secret.txt",
    }


def test_modes_follow_any_executable_bit(tmp_path):
    _w(tmp_path / "SKILL.md", "---\ndescription: d\n---\n")
    _w(tmp_path / "plain.txt")
    _w(tmp_path / "group.sh")
    (tmp_path / "group.sh").chmod(0o710)
    files, _, _ = plan_skills(_pkg(tmp_path, "claude_skill"), TARGETS["claude"])
    assert files[".claude/skills/my-skills/plain.txt"].mode == 0o644
    assert files[".claude/skills/my-skills/group.sh"].mode == 0o755
    assert files[".claude/skills/my-skills/SKILL.md"].owner == "git.test/team/My_Skills"


def test_skill_without_description_warns(tmp_path):
    _w(tmp_path / "SKILL.md", "---\nname: s\n---\nbody")
    _, _, warnings = plan_skills(_pkg(tmp_path, "claude_skill"), TARGETS["claude"])
    assert len(warnings) == 1 and "description" in warnings[0]


def test_skill_with_invalid_frontmatter_warns_instead_of_crashing(tmp_path):
    _w(tmp_path / "SKILL.md", "---\ndescription: [unclosed\n---\nbody")
    files, _, warnings = plan_skills(_pkg(tmp_path, "claude_skill"), TARGETS["claude"])
    assert set(files) == {".claude/skills/my-skills/SKILL.md"}
    assert len(warnings) == 1


def test_single_skill_name_that_hyphen_cases_to_nothing_is_an_error(tmp_path):
    _w(tmp_path / "SKILL.md", "---\ndescription: d\n---\n")
    with pytest.raises(ApmError):
        plan_skills(_pkg(tmp_path, "claude_skill", path="team/___"), TARGETS["claude"])


def test_symlinked_skills_folders_are_not_followed(tmp_path):  # R21/R30
    outside = tmp_path / "outside"
    _w(outside / "skills/a/SKILL.md", "---\ndescription: d\n---\n")
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "skills").symlink_to(outside / "skills")
    (pkg_dir / ".apm").symlink_to(outside)
    for ptype, selected in (
        ("skill_bundle", ("a",)),
        ("skill_bundle", None),
        ("apm_package", None),
    ):
        pkg = _pkg(pkg_dir, ptype, selected)
        assert plan_skills(pkg, TARGETS["claude"])[0] == {}, ptype
