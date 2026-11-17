import pytest

from apm_lite import (
    TARGETS,
    ApmError,
    DepSpec,
    ResolvedPackage,
    has_rules,
    plan_rules,
    render_rule,
    split_apply_to,
)

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
    with pytest.raises(ValueError, match="copilot"):
        render_rule(SRC, "copilot")


@pytest.mark.parametrize("target", ["codex", "gemini"])
def test_targets_without_a_rule_format_are_rejected(target):
    with pytest.raises(ValueError, match=target):
        render_rule(SRC, target)


def test_cursor_scoped_with_description():
    src = '---\napplyTo: "**/*.py"\ndescription: Python style\n---\nUse hints.\n'
    assert render_rule(src, "cursor") == (
        '---\ndescription: "Python style"\nglobs: **/*.py\nalwaysApply: false\n---\n\nUse hints.\n'
    )


def test_cursor_globs_are_one_comma_joined_scalar_without_description():
    src = '---\napplyTo: "a/**/*.py, b/{x,y}/*.ts"\n---\nBody\n'
    assert render_rule(src, "cursor") == (
        "---\nglobs: a/**/*.py, b/{x,y}/*.ts\nalwaysApply: false\n---\n\nBody\n"
    )


def test_cursor_without_frontmatter_is_always_apply():
    assert render_rule("Just text.\n", "cursor") == "---\nalwaysApply: true\n---\n\nJust text.\n"


def test_cursor_unscoped_keeps_description():
    src = "---\ndescription: Always on\n---\nAlways.\n"
    assert render_rule(src, "cursor") == (
        '---\ndescription: "Always on"\nalwaysApply: true\n---\n\nAlways.\n'
    )


def test_cursor_description_is_json_quoted():
    src = "---\napplyTo: a/**\ndescription: 'say \"hi\" é'\n---\nB\n"
    assert render_rule(src, "cursor") == (
        '---\ndescription: "say \\"hi\\" \\u00e9"\nglobs: a/**\nalwaysApply: false\n---\n\nB\n'
    )


@pytest.mark.parametrize("description", ['""', "3", "[a, b]", "null"])
def test_cursor_omits_empty_or_non_string_description(description):
    src = f"---\napplyTo: a/**\ndescription: {description}\n---\nB\n"
    assert render_rule(src, "cursor") == "---\nglobs: a/**\nalwaysApply: false\n---\n\nB\n"


def test_cursor_crlf_source_same_as_lf():
    assert render_rule(SRC.replace("\n", "\r\n"), "cursor") == render_rule(SRC, "cursor")


def test_cursor_drops_other_frontmatter_and_keeps_one_trailing_newline():
    src = "---\napplyTo: a/**\nalwaysApply: true\nextra: x\n---\n\n\nBody\n\n\n"
    assert render_rule(src, "cursor") == "---\nglobs: a/**\nalwaysApply: false\n---\n\nBody\n"


@pytest.mark.parametrize("glob", ["a\\nb/**", "a\\tb/**", "a\\u001bb/**", "a\\u007fb/**"])
def test_cursor_rejects_a_control_character_in_a_glob(glob):
    with pytest.raises(ApmError, match="control character"):
        render_rule(f'---\napplyTo: "{glob}"\n---\nB\n', "cursor")


def test_cursor_rejects_a_top_level_comma_inside_one_glob():
    src = "---\napplyTo: ['src/foo,bar/*.py', 'tests/**']\n---\nB\n"
    with pytest.raises(ApmError, match="comma"):
        render_rule(src, "cursor")


def test_cursor_accepts_a_comma_inside_braces_in_a_list_item():
    src = "---\napplyTo: ['a/{x,y}/*.ts']\n---\nB\n"
    assert render_rule(src, "cursor") == "---\nglobs: a/{x,y}/*.ts\nalwaysApply: false\n---\n\nB\n"


def test_claude_and_antigravity_do_not_apply_the_cursor_glob_checks():
    src = "---\napplyTo: ['src/foo,bar/*.py']\n---\nB\n"
    assert render_rule(src, "claude") == '---\npaths:\n  - "src/foo,bar/*.py"\n---\n\nB\n'
    assert render_rule(src, "antigravity") == (
        '---\ntrigger: glob\nglobs: "src/foo,bar/*.py"\n---\n\nB\n'
    )


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


def test_plan_rules_cursor_writes_mdc(tmp_path):
    _w(tmp_path, ".apm/instructions/x.instructions.md", SRC)
    files = plan_rules(_pkg(tmp_path), TARGETS["cursor"])
    assert set(files) == {".cursor/rules/x.mdc"}
    planned = files[".cursor/rules/x.mdc"]
    assert planned.content == render_rule(SRC, "cursor").encode()
    assert planned.mode == 0o644
    assert planned.owner == "git.test/team/pkg"


@pytest.mark.parametrize("target", ["codex", "gemini"])
def test_plan_rules_deploys_nothing_for_targets_without_rules(tmp_path, target):
    _w(tmp_path, ".apm/instructions/x.instructions.md", SRC)
    assert plan_rules(_pkg(tmp_path), TARGETS[target]) == {}


def test_plan_rules_cursor_glob_error_names_the_file(tmp_path):
    bad = "---\napplyTo: ['src/foo,bar/*.py']\n---\nB\n"
    _w(tmp_path, ".apm/instructions/bad.instructions.md", bad)
    with pytest.raises(ApmError, match=r"bad\.instructions\.md") as caught:
        plan_rules(_pkg(tmp_path), TARGETS["cursor"])
    assert "comma" in caught.value.message


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


def test_symlinked_apm_folder_is_not_followed(tmp_path):  # R21/R30: no files from outside
    from apm_lite import TARGETS, DepSpec, ResolvedPackage, plan_rules

    outside = tmp_path / "outside" / "instructions"
    outside.mkdir(parents=True)
    (outside / "secret.instructions.md").write_text("from outside")
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / ".apm").symlink_to(tmp_path / "outside")
    spec = DepSpec(raw="x", kind="remote", key="git.test/t/p", repo_path="t/p")
    pkg = ResolvedPackage(
        spec=spec,
        commit="a" * 40,
        ref_is_immutable=True,
        depth=1,
        parent_key=None,
        parent_repo_url=None,
        source_dir=pkg_dir,
        package_type="apm_package",
        name="p",
        version=None,
        content_hash=None,
    )
    assert plan_rules(pkg, TARGETS["claude"]) == {}


def test_has_rules_true_for_an_apm_package_with_a_rule(tmp_path):
    _w(tmp_path, ".apm/instructions/style.instructions.md", SRC)
    assert has_rules(_pkg(tmp_path)) is True


def test_has_rules_true_for_the_root_project_whatever_its_type(tmp_path):
    _w(tmp_path, ".apm/instructions/mine.instructions.md", SRC)
    assert has_rules(_pkg(tmp_path, "claude_skill", depth=0)) is True


def test_has_rules_false_without_an_instructions_dir(tmp_path):
    assert has_rules(_pkg(tmp_path)) is False


def test_has_rules_false_for_an_empty_instructions_dir(tmp_path):
    (tmp_path / ".apm/instructions").mkdir(parents=True)
    assert has_rules(_pkg(tmp_path)) is False


def test_has_rules_ignores_files_that_are_not_rule_sources(tmp_path):
    _w(tmp_path, ".apm/instructions/notes.md", "not a rule")
    _w(tmp_path, ".apm/instructions/sub/deep.instructions.md", SRC)
    assert has_rules(_pkg(tmp_path)) is False


@pytest.mark.parametrize("ptype", ["claude_skill", "hybrid", "skill_bundle"])
def test_has_rules_false_for_a_non_apm_package_shape(tmp_path, ptype):
    _w(tmp_path, ".apm/instructions/style.instructions.md", SRC)
    assert has_rules(_pkg(tmp_path, ptype)) is False


def test_has_rules_false_for_a_symlinked_rule_source(tmp_path):
    outside = _w(tmp_path / "elsewhere", "secret.txt", "secret")
    link = tmp_path / ".apm/instructions/leak.instructions.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    assert has_rules(_pkg(tmp_path)) is False


def test_has_rules_false_for_a_symlinked_instructions_folder(tmp_path):
    outside = tmp_path / "outside"
    _w(outside, "secret.instructions.md", "from outside")
    pkg_dir = tmp_path / "pkg"
    (pkg_dir / ".apm").mkdir(parents=True)
    (pkg_dir / ".apm/instructions").symlink_to(outside)
    assert has_rules(_pkg(pkg_dir)) is False


def test_has_rules_does_not_validate_names_or_content(tmp_path):
    _w(tmp_path, ".apm/instructions/bad name.instructions.md", "---\napplyTo: [unclosed\n---\n")
    assert has_rules(_pkg(tmp_path)) is True
