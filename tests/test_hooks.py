import json
from dataclasses import replace

import pytest

from apm_lite.deploy.hooks import merge, plan_hooks, read_json, rewrite_command
from apm_lite.errors import ApmError
from apm_lite.model import DepSpec, HookEntry, ResolvedPackage
from apm_lite.targets import TARGETS


def _pkg(root):
    spec = DepSpec(raw="x", kind="remote", key="git.test/team/pkg", repo_path="team/pkg")
    return ResolvedPackage(
        spec=spec,
        commit="a" * 40,
        ref_is_immutable=True,
        depth=1,
        parent_key=None,
        parent_repo_url=None,
        source_dir=root,
        package_type="apm_package",
        name="pkg",
        version=None,
        content_hash=None,
    )


def _hooks(root, name, doc, scripts=("check.sh",)):
    d = root / ".apm/hooks"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(doc))
    for s in scripts:
        (d / s).write_text("#!/bin/sh\n")
        (d / s).chmod(0o755)
    return d


@pytest.mark.parametrize(
    "cmd,expected",
    [
        ("./check.sh", '"${CLAUDE_PROJECT_DIR}/.claude/hooks/pkg/check.sh"'),
        ('bash "./check.sh" --x', 'bash "${CLAUDE_PROJECT_DIR}/.claude/hooks/pkg/check.sh" --x'),
        ("bash './check.sh'", 'bash "${CLAUDE_PROJECT_DIR}/.claude/hooks/pkg/check.sh"'),
        (
            "${CLAUDE_PLUGIN_ROOT}/.apm/hooks/check.sh",
            '"${CLAUDE_PROJECT_DIR}/.claude/hooks/pkg/check.sh"',
        ),
        ("echo hi", "echo hi"),
    ],
)
def test_rewrite_claude(tmp_path, cmd, expected):
    d = _hooks(tmp_path, "h.json", {})
    assert (
        rewrite_command(
            cmd,
            target=TARGETS["claude"],
            label="pkg",
            hook_file_dir=d,
            hooks_root=d,
            package_root=tmp_path,
        )
        == expected
    )


def test_rewrite_antigravity_relative(tmp_path):
    d = _hooks(tmp_path, "h.json", {})
    assert (
        rewrite_command(
            "./check.sh",
            target=TARGETS["antigravity"],
            label="pkg",
            hook_file_dir=d,
            hooks_root=d,
            package_root=tmp_path,
        )
        == "hooks/pkg/check.sh"
    )


def test_script_outside_hooks_rejected(tmp_path):
    d = _hooks(tmp_path, "h.json", {})
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/x.sh").write_text("x")
    with pytest.raises(ApmError):
        rewrite_command(
            "${CLAUDE_PLUGIN_ROOT}/scripts/x.sh",
            target=TARGETS["claude"],
            label="pkg",
            hook_file_dir=d,
            hooks_root=d,
            package_root=tmp_path,
        )


def test_plan_selects_target_file_and_skips_events(tmp_path):
    _hooks(
        tmp_path,
        "hooks.json",
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "./check.sh", "timeout": 5}],
                    }
                ],
                "SessionStart": [{"command": "./check.sh"}],
                "PreInvocation": [{"command": "./check.sh"}],
            }
        },
    )
    _hooks(
        tmp_path,
        "hooks-antigravity.json",
        {
            "PreToolUse": [{"matcher": "run_command", "hooks": [{"command": "./check.sh"}]}],
            "Stop": [{"command": "./check.sh"}],
        },
    )
    c, c_files, c_warn = plan_hooks(_pkg(tmp_path), TARGETS["claude"])
    assert [e.event for e in c] == ["PreToolUse", "SessionStart"] and len(c_warn) == 1
    assert c[0].entry == {
        "matcher": "Bash",
        "hooks": [
            {
                "type": "command",
                "command": '"${CLAUDE_PROJECT_DIR}/.claude/hooks/pkg/check.sh"',
                "timeout": 5,
            }
        ],
    }
    assert c[1].entry["matcher"] == "*"
    assert c_files[".claude/hooks/pkg/check.sh"].mode == 0o755
    assert ".claude/hooks/pkg/hooks.json" not in c_files
    a, _, _ = plan_hooks(_pkg(tmp_path), TARGETS["antigravity"])
    assert [e.event for e in a] == ["PreToolUse", "Stop"]
    assert a[1].entry == {"type": "command", "command": "hooks/pkg/check.sh"}


def _entry(cmd, event="PreToolUse"):
    return HookEntry(
        event=event,
        entry={"matcher": "*", "hooks": [{"type": "command", "command": cmd}]},
        marker="team/pkg",
        owner="git.test/team/pkg",
    )


def test_claude_merge_keeps_user_content_and_duplicates():  # Review Focus 2
    user = {"matcher": "*", "hooks": [{"type": "command", "command": "a"}]}
    native = {
        "permissions": {"allow": ["Bash"]},
        "hooks": {"PreToolUse": [user, dict(user)]},
        "model": "x",
    }
    sidecar = {"PreToolUse": [dict(user, _apm_source="team/pkg")]}
    new_native, new_sidecar = merge(native, sidecar, [_entry("b")], TARGETS["claude"], True)
    assert list(new_native) == ["permissions", "hooks", "model"]
    assert new_native["hooks"]["PreToolUse"] == [user, _entry("b").entry]
    assert new_sidecar == {"PreToolUse": [dict(_entry("b").entry, _apm_source="team/pkg")]}


def test_retired_target_strips_and_deletes_sidecar():
    e = _entry("b").entry
    native, sidecar = merge(
        {"hooks": {"PreToolUse": [e]}},
        {"PreToolUse": [dict(e, _apm_source="t")]},
        [],
        TARGETS["claude"],
        False,
    )
    assert native == {} and sidecar is None


def test_antigravity_container_ownership():
    t = TARGETS["antigravity"]
    with pytest.raises(ApmError):
        merge({"apm": {"Stop": []}}, {}, [], t, True)
    owned = {"type": "command", "command": "x"}
    with pytest.raises(ApmError):
        merge(
            {"apm": {"Stop": [{"command": "edited"}]}},
            {"Stop": [dict(owned, _apm_source="t")]},
            [],
            t,
            True,
        )
    new, side = merge(
        {"mine": {"Stop": []}, "apm": {"Stop": [owned]}},
        {"Stop": [dict(owned, _apm_source="t")]},
        [HookEntry("Stop", {"type": "command", "command": "y"}, "t", "o")],
        t,
        True,
    )
    assert new == {"mine": {"Stop": []}, "apm": {"Stop": [{"type": "command", "command": "y"}]}}
    assert side == {"Stop": [{"type": "command", "command": "y", "_apm_source": "t"}]}


def test_read_json_malformed(tmp_path):
    (tmp_path / "s.json").write_text("{nope")
    with pytest.raises(ApmError):
        read_json(tmp_path / "s.json")


def test_read_json_missing_and_object(tmp_path):
    path = tmp_path / "settings.json"
    assert read_json(path) == {}
    doc = {"permissions": {"allow": ["Bash"]}, "model": "x"}
    path.write_text(json.dumps(doc))
    assert read_json(path) == doc


@pytest.mark.parametrize("content", ["[]", "null", '"text"', "1", b"\xff"])
def test_read_json_invalid_names_path(tmp_path, content):
    path = tmp_path / "settings.json"
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    with pytest.raises(ApmError, match=r"settings\.json"):
        read_json(path)


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("variable", ["CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT"])
@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_rewrite_plugin_roots_and_quotes(tmp_path, target, variable, quote):
    d = _hooks(tmp_path, "h.json", {})
    command = f"bash {quote}${{{variable}}}/.apm/hooks/check.sh{quote} --x"
    expected = {
        "claude": '"${CLAUDE_PROJECT_DIR}/.claude/hooks/pkg/check.sh"',
        "antigravity": f"{quote}hooks/pkg/check.sh{quote}",
        "cursor": f"{quote}.cursor/hooks/pkg/check.sh{quote}",
        "codex": '"$(git rev-parse --show-toplevel)/.codex/hooks/pkg/check.sh"',
        "gemini": f"{quote}.gemini/hooks/pkg/check.sh{quote}",
    }[target]
    assert (
        rewrite_command(
            command,
            target=TARGETS[target],
            label="pkg",
            hook_file_dir=d,
            hooks_root=d,
            package_root=tmp_path,
        )
        == f"bash {expected} --x"
    )


def test_rewrite_root_fallback_and_multiple_paths(tmp_path):
    d = _hooks(tmp_path, "h.json", {}, scripts=("check.sh", "check.sh.bak"))
    command = "./.apm/hooks/check.sh && .\\check.sh.bak && ./check.sh"
    rewritten = rewrite_command(
        command,
        target=TARGETS["claude"],
        label="pkg",
        hook_file_dir=d,
        hooks_root=d,
        package_root=tmp_path,
    )
    prefix = '"${CLAUDE_PROJECT_DIR}/.claude/hooks/pkg/'
    assert rewritten == f'{prefix}check.sh" && {prefix}check.sh.bak" && {prefix}check.sh"'


@pytest.mark.parametrize("kind", ["missing", "directory", "file_symlink", "dir_symlink", "outside"])
def test_rewrite_rejects_unsafe_scripts_with_hint(tmp_path, kind):
    d = _hooks(tmp_path, "h.json", {})
    command = "./bad.sh"
    if kind == "directory":
        (d / "bad.sh").mkdir()
    elif kind == "file_symlink":
        (d / "bad.sh").symlink_to(d / "check.sh")
    elif kind == "dir_symlink":
        (d / "linked").symlink_to(d, target_is_directory=True)
        command = "./linked/check.sh"
    elif kind == "outside":
        (tmp_path / "bad.sh").write_text("x")
    with pytest.raises(ApmError) as exc:
        rewrite_command(
            command,
            target=TARGETS["claude"],
            label="pkg",
            hook_file_dir=d,
            hooks_root=d,
            package_root=tmp_path,
        )
    assert ".apm/hooks/" in exc.value.hint


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("name", ["bad$HOME.sh", "bad`id`.sh", 'bad"quote.sh'])
def test_rewrite_rejects_shell_expansion_in_path(tmp_path, target, name):
    d = _hooks(tmp_path, "h.json", {}, scripts=(name,))
    with pytest.raises(ApmError):
        rewrite_command(
            f"'./{name}'",
            target=TARGETS[target],
            label="pkg",
            hook_file_dir=d,
            hooks_root=d,
            package_root=tmp_path,
        )


def test_plan_empty_package(tmp_path):
    assert plan_hooks(_pkg(tmp_path), TARGETS["claude"]) == ([], {}, [])


def test_plan_override_ignores_malformed_generic_and_other_targets(tmp_path):
    d = _hooks(tmp_path, "hooks-claude.json", {"Stop": [{"command": "./check.sh"}]})
    (d / "hooks.json").write_text("{broken")
    (d / "hooks-antigravity.json").write_text("{broken")
    (d / "hooks-copilot.json").write_text("{broken")
    entries, _, warnings = plan_hooks(_pkg(tmp_path), TARGETS["claude"])
    assert [e.event for e in entries] == ["Stop"]
    assert warnings == []


def test_plan_generic_sources_in_filename_order(tmp_path):
    d = _hooks(tmp_path, "z.json", {"Stop": [{"command": "echo z"}]})
    _hooks(tmp_path, "a.json", {"Stop": [{"command": "echo a"}]})
    (d / "hooks-copilot.json").write_text("{broken")
    entries, _, warnings = plan_hooks(_pkg(tmp_path), TARGETS["claude"])
    assert [e.entry["hooks"][0]["command"] for e in entries] == ["echo a", "echo z"]
    assert warnings == []


@pytest.mark.parametrize("target", TARGETS)
def test_plan_bundle_resources_and_ownership(tmp_path, target):
    d = _hooks(tmp_path, "hooks.json", {"Stop": [{"command": "./check.sh"}]})
    assets = d / "lib"
    assets.mkdir()
    (assets / "package.json").write_text('{"type": "module"}')
    (assets / "helper.js").write_text("export {};")
    (assets / "helper.js").chmod(0o640)
    (assets / "linked.sh").symlink_to(d / "check.sh")
    (d / "linked").symlink_to(assets, target_is_directory=True)
    t = TARGETS[target]
    entries, files, warnings = plan_hooks(_pkg(tmp_path), t)
    assert set(files) == {
        f"{t.hooks_dir}/pkg/check.sh",
        f"{t.hooks_dir}/pkg/lib/package.json",
        f"{t.hooks_dir}/pkg/lib/helper.js",
    }
    assert files[f"{t.hooks_dir}/pkg/lib/helper.js"].mode == 0o640
    assert files[f"{t.hooks_dir}/pkg/lib/package.json"].content == b'{"type": "module"}'
    assert all(f.owner == "git.test/team/pkg" for f in files.values())
    assert entries[0].marker == "team/pkg" and entries[0].owner == "git.test/team/pkg"
    assert warnings == []


def test_plan_flattens_handlers_and_warns_missing_commands(tmp_path):
    _hooks(
        tmp_path,
        "hooks.json",
        {
            "Stop": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"command": "echo one", "timeout": 7},
                        {"type": "command"},
                        {"command": "echo two"},
                    ],
                }
            ],
            "SessionStart": [{"command": "./missing.sh"}],
        },
    )
    entries, _, warnings = plan_hooks(_pkg(tmp_path), TARGETS["antigravity"])
    assert [e.entry for e in entries] == [
        {"type": "command", "command": "echo one", "timeout": 7},
        {"type": "command", "command": "echo two"},
    ]
    assert len(warnings) == 2
    assert any("command" in w for w in warnings)
    assert any("SessionStart" in w for w in warnings)


def test_plan_all_missing_handlers_omits_empty_entry(tmp_path):
    _hooks(tmp_path, "hooks.json", {"Stop": [{"hooks": [{"type": "command"}]}]})
    entries, _, warnings = plan_hooks(_pkg(tmp_path), TARGETS["claude"])
    assert entries == [] and len(warnings) == 1


@pytest.mark.parametrize(
    "doc",
    [
        {"hooks": []},
        {"Stop": {}},
        {"Stop": [1]},
        {"Stop": [{"hooks": {}}]},
        {"Stop": [{"command": 1}]},
        {"Stop": [{"command": "echo hi", "type": "prompt"}]},
        {"Stop": [{"matcher": 1, "hooks": [{"command": "echo hi"}]}]},
        {"Stop": [{"matcher": 0, "hooks": [{"command": "echo hi"}]}]},
        {"Stop": [{"matcher": False, "hooks": [{"command": "echo hi"}]}]},
        {"Stop": [{"command": "echo hi", "timeout": "5"}]},
    ],
)
def test_plan_rejects_invalid_source_grammar(tmp_path, doc):
    _hooks(tmp_path, "hooks.json", doc)
    with pytest.raises(ApmError, match=r"hooks\.json"):
        plan_hooks(_pkg(tmp_path), TARGETS["claude"])


def test_plan_malformed_json_names_source(tmp_path):
    d = _hooks(tmp_path, "hooks.json", {})
    (d / "hooks.json").write_text("{broken")
    with pytest.raises(ApmError, match=r"hooks\.json"):
        plan_hooks(_pkg(tmp_path), TARGETS["claude"])


@pytest.mark.parametrize("label", ["../escape", "bad$name", "bad name"])
def test_plan_rejects_unsafe_label(tmp_path, label):
    _hooks(tmp_path, "hooks.json", {"Stop": [{"command": "echo hi"}]})
    pkg = _pkg(tmp_path)
    pkg.depth = 0
    pkg.name = label
    with pytest.raises(ApmError):
        plan_hooks(pkg, TARGETS["claude"])


def test_claude_merge_preserves_edits_order_and_inputs():
    owned = _entry("old").entry
    edited = _entry("edited").entry
    native = {
        "model": "x",
        "hooks": {"Stop": [edited], "PreToolUse": [owned]},
        "permissions": {"allow": ["Bash"]},
    }
    sidecar = {"Stop": [dict(owned, _apm_source="t")], "PreToolUse": [dict(owned, _apm_source="t")]}
    before_native, before_sidecar = json.dumps(native), json.dumps(sidecar)
    new, side = merge(native, sidecar, [_entry("new")], TARGETS["claude"], True)
    assert list(new) == ["model", "hooks", "permissions"]
    assert new["hooks"] == {"Stop": [edited], "PreToolUse": [_entry("new").entry]}
    assert list(new["hooks"]) == ["Stop", "PreToolUse"]
    assert side == {"PreToolUse": [dict(_entry("new").entry, _apm_source="team/pkg")]}
    assert json.dumps(native) == before_native and json.dumps(sidecar) == before_sidecar


def test_claude_strip_uses_canonical_multiset_and_keeps_unowned_entries():
    owned = _entry("old").entry
    reordered = {"hooks": owned["hooks"], "matcher": "*"}
    native = {"hooks": {"PreToolUse": [reordered, owned, owned], "Stop": [_entry("user").entry]}}
    sidecar = {"PreToolUse": [dict(owned, _apm_source="a"), dict(owned, _apm_source="b")]}
    new, side = merge(native, sidecar, [_entry("ignored")], TARGETS["claude"], False)
    assert new == {"hooks": {"PreToolUse": [owned], "Stop": [_entry("user").entry]}}
    assert side is None


@pytest.mark.parametrize("target", TARGETS)
def test_merge_idempotent_with_duplicate_planned_entries(target):
    t = TARGETS[target]
    event = t.native_event("Stop")
    handler = {"type": "command", "command": "x"}
    entry = (
        _entry("x")
        if target == "claude"
        else HookEntry(event, {"hooks": [handler]} if t.is_nested(event) else handler, "t", "o")
    )
    first, side = merge({}, {}, [entry, entry], t, True)
    second = merge(first, side, [entry, entry], t, True)
    assert second == (first, side)
    assert json.dumps(second) == json.dumps((first, side))
    assert "_apm_source" not in json.dumps(first)


def test_antigravity_retired_strips_owned_and_preserves_key_order():
    owned = {"type": "command", "command": "x"}
    native = {"before": {}, "apm": {"Stop": [owned]}, "after": {"Stop": []}}
    sidecar = {"Stop": [dict(owned, _apm_source="t")]}
    new, side = merge(native, sidecar, [], TARGETS["antigravity"], False)
    assert new == {"before": {}, "after": {"Stop": []}} and side is None
    configured, _ = merge(
        native, sidecar, [HookEntry("Stop", owned, "t", "o")], TARGETS["antigravity"], True
    )
    assert list(configured) == ["before", "apm", "after"]
    assert "apm" in native


@pytest.mark.parametrize("configured", [True, False])
def test_antigravity_extra_owned_duplicate_is_an_edit(configured):
    owned = {"type": "command", "command": "x"}
    with pytest.raises(ApmError) as exc:
        merge(
            {"apm": {"Stop": [owned, owned]}},
            {"Stop": [dict(owned, _apm_source="t")]},
            [],
            TARGETS["antigravity"],
            configured,
        )
    assert "another" in exc.value.hint


@pytest.mark.parametrize("sidecar", [{"Stop": [{}]}, {"Stop": [1]}, {"Stop": {}}])
def test_merge_rejects_malformed_ownership_sidecar(sidecar):
    with pytest.raises(ApmError):
        merge({}, sidecar, [], TARGETS["claude"], False)


def test_merge_rejects_malformed_claude_hooks():
    with pytest.raises(ApmError):
        merge({"hooks": []}, {}, [], TARGETS["claude"], False)


def test_generic_hooks_named_hooks_star_are_sources_and_no_json_is_bundled(tmp_path):
    _hooks(tmp_path, "hooks-lint.json", {"Stop": [{"command": "./check.sh"}]})
    _hooks(tmp_path, "hooks-antigravity.json", {"Stop": [{"command": "./check.sh"}]})
    (tmp_path / ".apm/hooks/package.json").write_text("{}")
    entries, files, _ = plan_hooks(_pkg(tmp_path), TARGETS["claude"])
    assert [e.event for e in entries] == ["Stop"]
    assert sorted(files) == [".claude/hooks/pkg/check.sh", ".claude/hooks/pkg/package.json"]


def test_only_apm_packages_deploy_hooks(tmp_path):
    _hooks(tmp_path, "hooks.json", {"Stop": [{"command": "./check.sh"}]})
    pkg = _pkg(tmp_path)
    for package_type in ("skill_bundle", "claude_skill", "hybrid"):
        pkg.package_type = package_type
        assert plan_hooks(pkg, TARGETS["claude"]) == ([], {}, [])


@pytest.mark.parametrize("cmd", ["../shared/x.sh", "bash ../x.sh", 'sh "../x.sh"', "x=../y.sh"])
def test_parent_relative_script_rejected(tmp_path, cmd):
    d = _hooks(tmp_path, "h.json", {})
    with pytest.raises(ApmError, match=r"\.\./"):
        rewrite_command(
            cmd,
            target=TARGETS["claude"],
            label="pkg",
            hook_file_dir=d,
            hooks_root=d,
            package_root=tmp_path,
        )


@pytest.mark.parametrize(
    "target,prefix,force_quote",
    [
        ("claude", "${CLAUDE_PROJECT_DIR}/.claude/hooks", True),
        ("antigravity", "hooks", False),
        ("cursor", ".cursor/hooks", False),
        ("codex", "$(git rev-parse --show-toplevel)/.codex/hooks", True),
        ("gemini", ".gemini/hooks", False),
    ],
)
@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_rewrite_relative_quotes(tmp_path, target, prefix, force_quote, quote):
    d = _hooks(tmp_path, "hooks.json", {})
    expected_quote = '"' if force_quote else quote
    assert (
        rewrite_command(
            f"sh {quote}./check.sh{quote} --flag",
            target=TARGETS[target],
            label="pkg",
            hook_file_dir=d,
            hooks_root=d,
            package_root=tmp_path,
        )
        == f"sh {expected_quote}{prefix}/pkg/check.sh{expected_quote} --flag"
    )


@pytest.mark.parametrize(
    "target,expected",
    [
        (
            "cursor",
            {
                "preToolUse": [
                    {
                        "type": "command",
                        "command": ".cursor/hooks/pkg/check.sh",
                        "timeout": 10,
                        "matcher": "Shell",
                    }
                ],
                "stop": [{"type": "command", "command": "echo done", "loop_limit": None}],
            },
        ),
        (
            "codex",
            {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": '"$(git rev-parse --show-toplevel)/.codex/hooks/pkg/check.sh"',
                                "timeout": 10,
                            }
                        ],
                    }
                ],
                "Stop": [{"hooks": [{"type": "command", "command": "echo done"}]}],
            },
        ),
        (
            "gemini",
            {
                "BeforeTool": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": ".gemini/hooks/pkg/check.sh",
                                "timeout": 10000,
                            }
                        ],
                    }
                ],
                "SessionEnd": [{"hooks": [{"type": "command", "command": "echo done"}]}],
            },
        ),
    ],
)
def test_new_target_native_shapes(tmp_path, target, expected):
    _hooks(
        tmp_path,
        "hooks.json",
        {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"command": "./check.sh", "timeout": 10}]}
                ],
                "Stop": [{"command": "echo done"}],
            }
        },
    )
    entries, _, warnings = plan_hooks(_pkg(tmp_path), TARGETS[target])
    actual = {}
    for entry in entries:
        actual.setdefault(entry.event, []).append(entry.entry)
    assert json.dumps(actual) == json.dumps(expected)
    assert warnings == []


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("timeout", [True, False, 0, -1, 1.5, "10", None])
def test_timeout_must_be_positive_integer(tmp_path, target, timeout):
    _hooks(tmp_path, "hooks.json", {"Stop": [{"command": "echo done", "timeout": timeout}]})
    with pytest.raises(ApmError, match=r"timeout.*hooks\.json"):
        plan_hooks(_pkg(tmp_path), TARGETS[target])


@pytest.mark.parametrize("target", ["codex", "gemini"])
@pytest.mark.parametrize("matcher", [None, "", "*", ".*", "Bash | Read"])
def test_nested_matcher_is_preserved_without_default(tmp_path, target, matcher):
    raw = {"command": "echo done"}
    if matcher is not None:
        raw["matcher"] = matcher
    _hooks(tmp_path, "hooks.json", {"Stop": [raw]})
    entries, _, warnings = plan_hooks(_pkg(tmp_path), TARGETS[target])
    expected = {"hooks": [{"type": "command", "command": "echo done"}]}
    if matcher is not None:
        expected = {"matcher": matcher, **expected}
    assert entries[0].entry == expected
    assert warnings == []


@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse"])
@pytest.mark.parametrize(
    "matcher,expected",
    [
        (None, None),
        ("", ""),
        ("*", "*"),
        (".*", ".*"),
        ("Bash", "Shell"),
        ("Read", "Read"),
        ("Edit|Write", "Write"),
        ("Write|Edit", "Write"),
        ("Grep", "Grep"),
        ("Task", "Task"),
        ("WebFetch", "WebFetch"),
        ("WebSearch", "WebSearch"),
        (
            "Bash|Read|Edit|Write|Grep|Task|WebFetch|WebSearch",
            "Shell|Read|Write|Grep|Task|WebFetch|WebSearch",
        ),
        ("Read|Bash|Read|Write|Edit|Bash", "Read|Shell|Write"),
    ],
)
def test_cursor_translated_matchers(tmp_path, event, matcher, expected):
    raw = {"hooks": [{"command": "echo one", "timeout": 2}, {"command": "echo two"}]}
    if matcher is not None:
        raw["matcher"] = matcher
    _hooks(tmp_path, "hooks.json", {event: [raw]})
    entries, _, warnings = plan_hooks(_pkg(tmp_path), TARGETS["cursor"])
    assert len(entries) == 2
    assert [entry.event for entry in entries] == [event[0].lower() + event[1:]] * 2
    for entry in entries:
        if expected is None:
            assert "matcher" not in entry.entry
        else:
            assert entry.entry["matcher"] == expected
    assert list(entries[0].entry) == ["type", "command", "timeout"] + (
        ["matcher"] if expected is not None else []
    )
    assert warnings == []


@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse"])
@pytest.mark.parametrize(
    "matcher",
    [
        "Bash.*",
        "Glob",
        "mcp__server__tool",
        "Bash |Read",
        "Bash| Read",
        "Bash | Read",
        "Bash||Read",
        "Edit",
        "Write",
        "Bash|Edit",
        "Read|Write",
    ],
)
def test_cursor_rejects_inexact_translated_matchers(tmp_path, event, matcher):
    _hooks(tmp_path, "hooks.json", {event: [{"matcher": matcher, "command": "echo done"}]})
    with pytest.raises(ApmError, match=r"hooks\.json") as exc:
        plan_hooks(_pkg(tmp_path), TARGETS["cursor"])
    assert "hooks-cursor.json" in exc.value.hint


@pytest.mark.parametrize(
    "event",
    ["UserPromptSubmit", "Stop", "SubagentStop", "SessionStart", "SessionEnd", "PreCompact"],
)
@pytest.mark.parametrize("matcher", [None, "", "*", ".*", "Bash"])
def test_cursor_matchers_on_other_translated_events(tmp_path, event, matcher):
    raw = {"command": "echo done"}
    if matcher is not None:
        raw["matcher"] = matcher
    _hooks(tmp_path, "hooks.json", {event: [raw]})
    if matcher == "Bash":
        with pytest.raises(ApmError, match=r"hooks\.json") as exc:
            plan_hooks(_pkg(tmp_path), TARGETS["cursor"])
        assert "hooks-cursor.json" in exc.value.hint
        return
    entries, _, warnings = plan_hooks(_pkg(tmp_path), TARGETS["cursor"])
    assert entries[0].entry.get("matcher") == matcher
    assert ("loop_limit" in entries[0].entry) == (event in {"Stop", "SubagentStop"})
    assert warnings == []


@pytest.mark.parametrize(
    "event,matcher",
    [
        ("preToolUse", "Shell"),
        ("beforeShellExecution", "^git (status|diff)$"),
        ("postToolUse", "Glob | Read"),
    ],
)
def test_cursor_native_matchers_pass_through(tmp_path, event, matcher):
    _hooks(tmp_path, "hooks-cursor.json", {event: [{"matcher": matcher, "command": "echo done"}]})
    entries, _, warnings = plan_hooks(_pkg(tmp_path), TARGETS["cursor"])
    assert entries[0].event == event
    assert entries[0].entry == {"type": "command", "command": "echo done", "matcher": matcher}
    assert warnings == []


@pytest.mark.parametrize(
    "target,source,native",
    [
        ("cursor", "PreToolUse", "preToolUse"),
        ("cursor", "PostToolUse", "postToolUse"),
        ("cursor", "UserPromptSubmit", "beforeSubmitPrompt"),
        ("cursor", "Stop", "stop"),
        ("cursor", "SubagentStop", "subagentStop"),
        ("cursor", "SessionStart", "sessionStart"),
        ("cursor", "SessionEnd", "sessionEnd"),
        ("cursor", "PreCompact", "preCompact"),
        ("gemini", "PreToolUse", "BeforeTool"),
        ("gemini", "preToolUse", "BeforeTool"),
        ("gemini", "PostToolUse", "AfterTool"),
        ("gemini", "postToolUse", "AfterTool"),
        ("gemini", "Stop", "SessionEnd"),
        ("gemini", "BeforeAgent", "BeforeAgent"),
    ],
)
def test_event_translation_and_sidecar_keys(tmp_path, target, source, native):
    _hooks(tmp_path, "hooks.json", {source: [{"command": "echo done"}]})
    t = TARGETS[target]
    entries, _, warnings = plan_hooks(_pkg(tmp_path), t)
    assert [entry.event for entry in entries] == [native]
    document, sidecar = merge({}, {}, entries, t, True)
    assert list(document["hooks"]) == [native]
    assert list(sidecar) == [native]
    assert warnings == []


@pytest.mark.parametrize(
    "target,event",
    [
        ("cursor", "Notification"),
        ("codex", "Notification"),
        ("cursor", "PreInvocation"),
        ("codex", "PreInvocation"),
        ("gemini", "PreInvocation"),
        ("cursor", "PostInvocation"),
        ("codex", "PostInvocation"),
        ("gemini", "PostInvocation"),
    ],
)
def test_new_target_skipped_events_warn_with_source_and_target(tmp_path, target, event):
    _hooks(tmp_path, "hooks.json", {event: [{"command": "./missing.sh"}]})
    entries, _, warnings = plan_hooks(_pkg(tmp_path), TARGETS[target])
    assert entries == []
    assert len(warnings) == 1
    assert all(text in warnings[0] for text in (event, target, "hooks.json"))


@pytest.mark.parametrize("override", ["cursor", "codex", "gemini"])
@pytest.mark.parametrize("target", TARGETS)
def test_new_target_override_routing(tmp_path, override, target):
    _hooks(tmp_path, "a.json", {"Stop": [{"command": "echo generic a"}]})
    _hooks(tmp_path, "z.json", {"Stop": [{"command": "echo generic z"}]})
    _hooks(tmp_path, f"hooks-{override}.json", {"Stop": [{"command": "echo override"}]})
    entries, files, warnings = plan_hooks(_pkg(tmp_path), TARGETS[target])
    commands = [entry.entry.get("hooks", [entry.entry])[0]["command"] for entry in entries]
    assert commands == (
        ["echo override"] if target == override else ["echo generic a", "echo generic z"]
    )
    assert set(files) == {f"{TARGETS[target].hooks_dir}/pkg/check.sh"}
    assert warnings == []


@pytest.mark.parametrize("target", ["cursor", "codex", "gemini"])
def test_new_target_merge_multiset_preserves_users_edits_and_key_order(tmp_path, target):
    _hooks(tmp_path, "hooks.json", {"Stop": [{"command": "echo owned"}]})
    t = TARGETS[target]
    planned, _, _ = plan_hooks(_pkg(tmp_path), t)
    event, owned = planned[0].event, planned[0].entry
    edited = {**owned, "user_edit": True}
    native = {
        "mcpServers": {"mine": {"command": "server"}},
        "hooks": {event: [owned, dict(owned), edited], "userEvent": [{"command": "echo user"}]},
        "theme": "dark",
        "version": 7,
    }
    sidecar = {
        event: [dict(owned, _apm_source="team/pkg"), {"command": "removed", "_apm_source": "old"}]
    }
    before = json.dumps((native, sidecar, planned[0].entry))
    installed, side = merge(native, sidecar, planned, t, True)
    assert installed["hooks"][event] == [owned, edited, owned]
    assert merge(installed, side, planned, t, True) == (installed, side)
    stripped, side = merge(installed, side, planned, t, False)
    expected = {
        **native,
        "hooks": {event: [owned, edited], "userEvent": [{"command": "echo user"}]},
    }
    assert stripped == expected and side is None
    assert list(installed) == list(stripped) == list(native)
    assert list(installed["hooks"]) == list(stripped["hooks"]) == [event, "userEvent"]
    for key in ("mcpServers", "theme", "version"):
        assert installed[key] == stripped[key] == native[key]
    assert json.dumps((native, sidecar, planned[0].entry)) == before


@pytest.mark.parametrize("target", ["cursor", "codex", "gemini"])
def test_install_then_strip_keeps_identical_user_entry_once(tmp_path, target):
    _hooks(tmp_path, "hooks.json", {"Stop": [{"command": "echo same"}]})
    t = TARGETS[target]
    planned, _, _ = plan_hooks(_pkg(tmp_path), t)
    event, entry = planned[0].event, planned[0].entry
    installed, side = merge({"hooks": {event: [dict(entry)]}}, {}, planned, t, True)
    assert installed["hooks"][event] == [entry, entry]
    stripped, side = merge(installed, side, [], t, False)
    assert stripped["hooks"][event] == [entry]
    assert side is None


def test_cursor_version_only_added_with_planned_entries_and_kept_on_strip(tmp_path):
    _hooks(tmp_path, "hooks.json", {"Stop": [{"command": "echo done"}]})
    t = TARGETS["cursor"]
    planned, _, _ = plan_hooks(_pkg(tmp_path), t)
    assert merge({"theme": "dark"}, {}, [], t, True) == ({"theme": "dark"}, None)
    assert merge({}, {}, planned, t, False) == ({}, None)
    installed, side = merge({"theme": "dark"}, {}, planned, t, True)
    assert installed["version"] == 1
    assert merge(installed, side, [], t, False) == ({"theme": "dark", "version": 1}, None)
    for version in (0, 2, None, "custom"):
        installed, _ = merge({"version": version}, {}, planned, t, True)
        assert installed["version"] == version


def test_target_fields_drive_rewrite_plan_and_merge(tmp_path):
    _hooks(tmp_path, "hooks.json", {"Source": [{"command": "./check.sh", "timeout": 1}]})
    t = replace(
        TARGETS["claude"],
        name="custom",
        root=".custom",
        script_prefix="custom-prefix",
        force_quote=False,
        event_map={"Source": "Native"},
        allowed_events=frozenset({"Native"}),
        default_matcher=None,
        timeout_ms=True,
        hook_key="customHooks",
        hook_defaults={"customVersion": 2},
    )
    entries, files, warnings = plan_hooks(_pkg(tmp_path), t)
    expected = {
        "hooks": [{"type": "command", "command": "custom-prefix/pkg/check.sh", "timeout": 1000}]
    }
    assert entries[0].entry == expected and entries[0].event == "Native"
    assert set(files) == {".custom/hooks/pkg/check.sh"}
    assert warnings == []
    installed, side = merge({}, {}, entries, t, True)
    assert installed == {"customHooks": {"Native": [expected]}, "customVersion": 2}
    assert merge(installed, side, [], t, False) == ({"customVersion": 2}, None)


@pytest.mark.parametrize(
    "target,events",
    [
        (
            "cursor",
            "sessionStart sessionEnd preToolUse postToolUse postToolUseFailure subagentStart "
            "subagentStop beforeShellExecution afterShellExecution beforeMCPExecution "
            "afterMCPExecution beforeReadFile afterFileEdit beforeSubmitPrompt preCompact stop "
            "afterAgentResponse afterAgentThought beforeTabFileRead afterTabFileEdit workspaceOpen",
        ),
        (
            "codex",
            "PreModelRequest PostModelRequest PreToolUse PermissionRequest PostToolUse PreCompact "
            "PostCompact SessionStart SessionEnd UserPromptSubmit SubagentStart SubagentStop Stop Interrupt",
        ),
    ],
)
def test_supported_native_events_pass_through(tmp_path, target, events):
    events = events.split()
    _hooks(tmp_path, "hooks.json", {event: [{"command": "echo done"}] for event in events})
    entries, _, warnings = plan_hooks(_pkg(tmp_path), TARGETS[target])
    assert [entry.event for entry in entries] == events
    assert warnings == []
    if target == "cursor":
        assert all(entry.entry == {"type": "command", "command": "echo done"} for entry in entries)


def test_gemini_aliases_share_native_event_in_source_order(tmp_path):
    _hooks(
        tmp_path,
        "hooks.json",
        {"PreToolUse": [{"command": "echo first"}], "preToolUse": [{"command": "echo second"}]},
    )
    t = TARGETS["gemini"]
    planned, _, warnings = plan_hooks(_pkg(tmp_path), t)
    installed, side = merge({}, {}, planned, t, True)
    assert list(installed["hooks"]) == list(side) == ["BeforeTool"]
    assert [entry["hooks"][0]["command"] for entry in installed["hooks"]["BeforeTool"]] == [
        "echo first",
        "echo second",
    ]
    assert warnings == []


def test_read_commented_gemini_settings_names_file_and_explains_comments(tmp_path):
    settings = tmp_path / ".gemini/settings.json"
    settings.parent.mkdir()
    settings.write_text('{\n  // user setting\n  "theme": "dark"\n}\n')
    with pytest.raises(ApmError, match=r"settings\.json") as exc:
        read_json(settings)
    assert exc.value.hint == (
        "apm can't keep comments in this file: remove them, or keep this package's hooks out of gemini"
    )


@pytest.mark.parametrize("target", ["claude", "cursor", "codex", "gemini"])
@pytest.mark.parametrize("edited_timeout", [True, 1.0])
def test_merge_keeps_numeric_type_edits_in_owned_entries(target, edited_timeout):
    t = TARGETS[target]
    event = t.native_event("Stop")
    owned = {"type": "command", "command": "echo owned", "timeout": 1}
    edited = {**owned, "timeout": edited_timeout}
    if t.is_nested(event):
        owned, edited = {"hooks": [owned]}, {"hooks": [edited]}
    native = {"hooks": {event: [edited]}}
    stripped, side = merge(native, {event: [dict(owned, _apm_source="pkg")]}, [], t, False)
    assert json.dumps(stripped) == json.dumps(native)
    assert side is None


@pytest.mark.parametrize("target", ["cursor", "codex", "gemini"])
def test_new_target_override_ignores_malformed_unselected_sources(tmp_path, target):
    d = _hooks(tmp_path, f"hooks-{target}.json", {"Stop": [{"command": "echo selected"}]})
    (d / "hooks.json").write_text("{broken")
    for other in TARGETS.keys() - {target}:
        (d / f"hooks-{other}.json").write_text("{broken")
    entries, _, warnings = plan_hooks(_pkg(tmp_path), TARGETS[target])
    assert len(entries) == 1
    assert warnings == []
