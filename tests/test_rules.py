import pytest

from apm_lite.deploy.rules import plan_rules, render_rule, split_apply_to
from apm_lite.errors import ApmError
from apm_lite.model import DepSpec, ResolvedPackage
from apm_lite.targets import TARGETS

SRC = '---\napplyTo: "**/*.py,**/*.{ts,tsx}"\ndescription: d\n---\n\n# Style\nUse black.\n'


def test_split_apply_to():
    assert split_apply_to("**/*.py, **/*.{ts,tsx},,") == ["**/*.py", "**/*.{ts,tsx}"]
    assert split_apply_to(["a/**", " b "]) == ["a/**", "b"]
    assert split_apply_to(None) == []


def test_split_apply_to_keeps_commas_inside_braces_and_brackets():
    assert split_apply_to("a/{b,c}/*.py,d/[x,y].md") == ["a/{b,c}/*.py", "d/[x,y].md"]


def test_split_apply_to_list_drops_empty_and_non_string_items():
    assert split_apply_to(["a/**", "  ", 3, None, "b"]) == ["a/**", "b"]


def test_split_apply_to_ignores_other_types():
    assert split_apply_to(3) == []
    assert split_apply_to({"a": 1}) == []


def test_claude_scoped():
    assert render_rule(SRC, "claude") == (
        '---\npaths:\n  - "**/*.py"\n  - "**/*.{ts,tsx}"\n---\n\n# Style\nUse black.\n'
    )


def test_antigravity_scoped():
    assert render_rule(SRC, "antigravity") == (
        '---\ntrigger: glob\nglobs: "**/*.py,**/*.{ts,tsx}"\n---\n\n# Style\nUse black.\n'
    )


def test_unscoped_both():
    src = "---\ndescription: d\n---\nAlways.\n"
    assert render_rule(src, "claude") == "Always.\n"
    assert render_rule(src, "antigravity") == "---\ntrigger: always_on\n---\n\nAlways.\n"


def test_crlf_source_same_as_lf():  # Review Focus 1
    assert render_rule(SRC.replace("\n", "\r\n"), "claude") == render_rule(SRC, "claude")


def test_crlf_source_same_as_lf_antigravity():
    assert render_rule(SRC.replace("\n", "\r\n"), "antigravity") == render_rule(SRC, "antigravity")


def test_source_without_frontmatter_is_unscoped():
    assert render_rule("Just text.\n", "claude") == "Just text.\n"
    assert render_rule("Just text.\n", "antigravity") == (
        "---\ntrigger: always_on\n---\n\nJust text.\n"
    )


def test_output_ends_with_exactly_one_newline():
    src = "---\napplyTo: a/**\n---\n\n\nBody\n\n\n"
    assert render_rule(src, "claude") == '---\npaths:\n  - "a/**"\n---\n\nBody\n'
    assert render_rule("---\ndescription: d\n---\nBody", "claude") == "Body\n"


def test_globs_are_json_quoted_and_ascii():
    src = "---\napplyTo: 'docs/é/**,say\"hi\"'\n---\nB\n"
    assert render_rule(src, "claude") == (
        '---\npaths:\n  - "docs/\\u00e9/**"\n  - "say\\"hi\\""\n---\n\nB\n'
    )
    assert render_rule(src, "antigravity") == (
        '---\ntrigger: glob\nglobs: "docs/\\u00e9/**,say\\"hi\\""\n---\n\nB\n'
    )


def test_unknown_target_rejected():
    with pytest.raises(ValueError, match="cursor"):
        render_rule(SRC, "cursor")


def _pkg(tmp_path, ptype="apm_package", depth=1):
    spec = DepSpec(raw="x", kind="remote", key="git.test/team/pkg", repo_path="team/pkg")
    return ResolvedPackage(
        spec=spec,
        commit="a" * 40,
        ref_is_immutable=True,
        depth=depth,
        parent_key=None,
        parent_repo_url=None,
        source_dir=tmp_path,
        package_type=ptype,
        name="pkg",
        version=None,
        content_hash=None,
    )


def _w(tmp_path, rel, text="x"):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_plan_rules_apm_package(tmp_path):
    _w(tmp_path, ".apm/instructions/style.instructions.md", SRC)
    _w(tmp_path, ".apm/instructions/notes.md", "not a rule")
    _w(tmp_path, ".apm/instructions/sub/deep.instructions.md", SRC)
    pkg = _pkg(tmp_path)
    claude = plan_rules(pkg, TARGETS["claude"])
    assert set(claude) == {".claude/rules/style.md"}
    planned = claude[".claude/rules/style.md"]
    assert planned.content == render_rule(SRC, "claude").encode()
    assert planned.mode == 0o644
    assert planned.owner == "git.test/team/pkg"
    agy = plan_rules(pkg, TARGETS["antigravity"])
    assert set(agy) == {".agents/rules/style.md"}
    assert agy[".agents/rules/style.md"].content == render_rule(SRC, "antigravity").encode()


def test_plan_rules_root_project_whatever_its_type(tmp_path):
    _w(tmp_path, ".apm/instructions/mine.instructions.md", "---\ndescription: d\n---\nHi\n")
    files = plan_rules(_pkg(tmp_path, "claude_skill", depth=0), TARGETS["claude"])
    assert set(files) == {".claude/rules/mine.md"}
    assert files[".claude/rules/mine.md"].owner == "."


@pytest.mark.parametrize("ptype", ["claude_skill", "hybrid", "skill_bundle"])
def test_plan_rules_other_shapes_deploy_no_rules(tmp_path, ptype):
    _w(tmp_path, ".apm/instructions/style.instructions.md", SRC)
    assert plan_rules(_pkg(tmp_path, ptype), TARGETS["claude"]) == {}


def test_plan_rules_no_instructions_dir(tmp_path):
    assert plan_rules(_pkg(tmp_path), TARGETS["claude"]) == {}


def test_plan_rules_bom_source_keeps_its_scope(tmp_path):
    _w(tmp_path, ".apm/instructions/style.instructions.md", "\ufeff" + SRC)
    planned = plan_rules(_pkg(tmp_path), TARGETS["claude"])[".claude/rules/style.md"]
    assert planned.content == render_rule(SRC, "claude").encode()


def test_plan_rules_unsafe_name_is_an_error(tmp_path):
    _w(tmp_path, ".apm/instructions/bad name.instructions.md", SRC)
    with pytest.raises(ApmError):
        plan_rules(_pkg(tmp_path), TARGETS["claude"])


def test_plan_rules_malformed_frontmatter_names_the_file(tmp_path):
    _w(tmp_path, ".apm/instructions/broken.instructions.md", "---\napplyTo: [unclosed\n---\nB\n")
    with pytest.raises(ApmError, match=r"broken\.instructions\.md"):
        plan_rules(_pkg(tmp_path), TARGETS["claude"])


def test_plan_rules_skips_symlinked_source(tmp_path):
    outside = _w(tmp_path / "elsewhere", "secret.txt", "secret")
    link = tmp_path / ".apm/instructions/leak.instructions.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    assert plan_rules(_pkg(tmp_path), TARGETS["claude"]) == {}
