"""Acceptance gate: one package deploys correctly to all five targets (design §6, R20-R30).

Every test carries the ID of its scenario in features/targets.feature, as the `scenario` marker
and in its `test_tgt_NN` name. features/targets-qa.md is the same checks as a manual procedure.
Everything goes through the installed `apm` executable (the `apm` fixture in conftest.py), and
deployed hook commands are executed with `/bin/sh -c` from the folder each harness uses.

Test ids of tests that need cursor, codex or gemini must not contain "claude" or "antigravity":
`pytest -k "claude or antigravity"` has to select only tests that pass without the new targets.
"""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from tests.e2e.test_e2e_subprocess import write_manifest

FEATURES = Path(__file__).parent / "features"
PACKAGE_DEP = "../local-package"
SHARING = ["antigravity", "cursor", "codex", "gemini"]  # targets that share .agents/skills
ALL = ["claude", "antigravity", "cursor", "codex", "gemini"]

RULE_SOURCE = "---\napplyTo: '**/*.py'\n---\nUse hints.\n"
ALWAYS_SOURCE = "Always hints.\n"
SKILL_MD = "---\nname: review\ndescription: Review code.\n---\nReview.\n"
GUIDE_MD = "Read the guide.\n"
CHECK_SH = "#!/bin/sh\nprintf 'HOOK_OK\\n'\n"
HOOK_SOURCE = {
    "PreToolUse": [{"matcher": "*", "hooks": [{"command": "./check.sh", "timeout": 10}]}],
    "PreInvocation": [{"command": "echo invocation"}],
    "SessionStart": [{"command": "echo session"}],
    "Notification": [{"command": "echo notification"}],
}
SKILL_TREE = {"SKILL.md": SKILL_MD, "references/guide.md": GUIDE_MD}


def handler(command, timeout=None):
    entry = {"type": "command", "command": command}
    if timeout is not None:
        entry["timeout"] = timeout
    return entry


def group(*handlers, matcher=None):
    entry = {"hooks": list(handlers)}
    return {"matcher": matcher, **entry} if matcher is not None else entry


@dataclass(frozen=True)
class Case:
    """What one target must deploy for the author package, per the table in design §6."""

    name: str
    root: str
    hook_file: str
    skills: str  # folder that holds the `review` skill
    rule: str | None  # rule file for local.instructions.md, None = the target has no rules
    rule_text: str | None
    always_text: str | None  # rule file content for a rule without applyTo
    run_from: str  # folder, relative to the project, the harness runs hook commands from
    env: str | None  # variable the harness sets to the project folder
    command_path: tuple  # where the PreToolUse command sits in hook_file after a fresh install
    hooks: dict  # hook_file after a fresh install of the package
    skipped: tuple  # source events the target can't take (warned about, not written)
    user: dict  # a user's own hook_file
    user_after_install: dict  # ... after `apm install`: user entries first, package entries after
    user_after_uninstall: dict  # ... after `apm uninstall`

    @property
    def sidecar(self):
        return f"{self.root}/apm-hooks.json"

    @property
    def scripts(self):
        return f"{self.root}/hooks/local-package"


USER_STOP = group(handler("echo user-stop"), matcher="*")
USER_PRE = group(handler("echo user-pre"), matcher="Bash")

_CLAUDE_PRE = group(
    handler('"${CLAUDE_PROJECT_DIR}/.claude/hooks/local-package/check.sh"', 10), matcher="*"
)
_CLAUDE_REST = {
    "SessionStart": [group(handler("echo session"), matcher="*")],
    "Notification": [group(handler("echo notification"), matcher="*")],
}
_ANTIGRAVITY_HOOKS = {
    "PreToolUse": [group(handler("hooks/local-package/check.sh", 10), matcher="*")],
    "PreInvocation": [handler("echo invocation")],
}
_ANTIGRAVITY_USER = {
    "user": {
        "Stop": [{"command": "echo user-stop"}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "echo user-pre"}]}],
    }
}
_CURSOR_PRE = {
    "type": "command",
    "command": ".cursor/hooks/local-package/check.sh",
    "timeout": 10,
    "matcher": "*",
}
_CURSOR_START = handler("echo session")
_CURSOR_USER_STOP = handler("echo user-stop")
_CURSOR_USER_PRE = {**handler("echo user-pre"), "matcher": "Shell"}
_CODEX_PRE = group(
    handler('"$(git rev-parse --show-toplevel)/.codex/hooks/local-package/check.sh"', 10),
    matcher="*",
)
_CODEX_START = group(handler("echo session"))
_CODEX_USER_STOP = group(handler("echo user-stop"))
_GEMINI_PRE = group(handler(".gemini/hooks/local-package/check.sh", 10000), matcher="*")
_GEMINI_REST = {
    "SessionStart": [group(handler("echo session"))],
    "Notification": [group(handler("echo notification"))],
}
_GEMINI_MCP = {"mcpServers": {"docs": {"command": "docs-server", "args": ["--stdio"]}}}
_GEMINI_USER_END = group(handler("echo user-end"))
_GEMINI_USER_PRE = group(handler("echo user-pre", 5000), matcher="run_shell_command")

CASES = {
    "claude": Case(
        name="claude",
        root=".claude",
        hook_file=".claude/settings.json",
        skills=".claude/skills",
        rule=".claude/rules/local.md",
        rule_text='---\npaths:\n  - "**/*.py"\n---\n\nUse hints.\n',
        always_text="Always hints.\n",
        run_from="",
        env="CLAUDE_PROJECT_DIR",
        command_path=("hooks", "PreToolUse", 0, "hooks", 0, "command"),
        hooks={"hooks": {"PreToolUse": [_CLAUDE_PRE], **_CLAUDE_REST}},
        skipped=("PreInvocation",),
        user={
            "permissions": {"allow": ["Read"]},
            "hooks": {"Stop": [USER_STOP], "PreToolUse": [USER_PRE]},
        },
        user_after_install={
            "permissions": {"allow": ["Read"]},
            "hooks": {"Stop": [USER_STOP], "PreToolUse": [USER_PRE, _CLAUDE_PRE], **_CLAUDE_REST},
        },
        user_after_uninstall={
            "permissions": {"allow": ["Read"]},
            "hooks": {"Stop": [USER_STOP], "PreToolUse": [USER_PRE]},
        },
    ),
    "antigravity": Case(
        name="antigravity",
        root=".agents",
        hook_file=".agents/hooks.json",
        skills=".agents/skills",
        rule=".agents/rules/local.md",
        rule_text='---\ntrigger: glob\nglobs: "**/*.py"\n---\n\nUse hints.\n',
        always_text="---\ntrigger: always_on\n---\n\nAlways hints.\n",
        run_from=".agents",
        env=None,
        command_path=("apm", "PreToolUse", 0, "hooks", 0, "command"),
        hooks={"apm": _ANTIGRAVITY_HOOKS},
        skipped=("SessionStart", "Notification"),
        user=_ANTIGRAVITY_USER,
        user_after_install={**_ANTIGRAVITY_USER, "apm": _ANTIGRAVITY_HOOKS},
        user_after_uninstall=_ANTIGRAVITY_USER,
    ),
    "cursor": Case(
        name="cursor",
        root=".cursor",
        hook_file=".cursor/hooks.json",
        skills=".agents/skills",
        rule=".cursor/rules/local.mdc",
        rule_text="---\nglobs: **/*.py\nalwaysApply: false\n---\n\nUse hints.\n",
        always_text="---\nalwaysApply: true\n---\n\nAlways hints.\n",
        run_from="",
        env=None,
        command_path=("hooks", "preToolUse", 0, "command"),
        hooks={
            "version": 1,
            "hooks": {"preToolUse": [_CURSOR_PRE], "sessionStart": [_CURSOR_START]},
        },
        skipped=("PreInvocation", "Notification"),
        user={"hooks": {"stop": [_CURSOR_USER_STOP], "preToolUse": [_CURSOR_USER_PRE]}},
        user_after_install={
            "hooks": {
                "stop": [_CURSOR_USER_STOP],
                "preToolUse": [_CURSOR_USER_PRE, _CURSOR_PRE],
                "sessionStart": [_CURSOR_START],
            },
            "version": 1,
        },
        # Design §6.4: `version` is set when entries are added and isn't removed with them.
        user_after_uninstall={
            "hooks": {"stop": [_CURSOR_USER_STOP], "preToolUse": [_CURSOR_USER_PRE]},
            "version": 1,
        },
    ),
    "codex": Case(
        name="codex",
        root=".codex",
        hook_file=".codex/hooks.json",
        skills=".agents/skills",
        rule=None,
        rule_text=None,
        always_text=None,
        run_from="",
        env=None,
        command_path=("hooks", "PreToolUse", 0, "hooks", 0, "command"),
        hooks={"hooks": {"PreToolUse": [_CODEX_PRE], "SessionStart": [_CODEX_START]}},
        skipped=("PreInvocation", "Notification"),
        user={"hooks": {"Stop": [_CODEX_USER_STOP], "PreToolUse": [USER_PRE]}},
        user_after_install={
            "hooks": {
                "Stop": [_CODEX_USER_STOP],
                "PreToolUse": [USER_PRE, _CODEX_PRE],
                "SessionStart": [_CODEX_START],
            }
        },
        user_after_uninstall={"hooks": {"Stop": [_CODEX_USER_STOP], "PreToolUse": [USER_PRE]}},
    ),
    "gemini": Case(
        name="gemini",
        root=".gemini",
        hook_file=".gemini/settings.json",
        skills=".agents/skills",
        rule=None,
        rule_text=None,
        always_text=None,
        run_from="",
        env="GEMINI_PROJECT_DIR",
        command_path=("hooks", "BeforeTool", 0, "hooks", 0, "command"),
        hooks={"hooks": {"BeforeTool": [_GEMINI_PRE], **_GEMINI_REST}},
        skipped=("PreInvocation",),
        user={
            **_GEMINI_MCP,
            "hooks": {"SessionEnd": [_GEMINI_USER_END], "BeforeTool": [_GEMINI_USER_PRE]},
        },
        user_after_install={
            **_GEMINI_MCP,
            "hooks": {
                "SessionEnd": [_GEMINI_USER_END],
                "BeforeTool": [_GEMINI_USER_PRE, _GEMINI_PRE],
                **_GEMINI_REST,
            },
        },
        user_after_uninstall={
            **_GEMINI_MCP,
            "hooks": {"SessionEnd": [_GEMINI_USER_END], "BeforeTool": [_GEMINI_USER_PRE]},
        },
    ),
}
# A second target to keep configured while one is retired, so the manifest is never empty.
# Claude and Antigravity pair up so their retirement tests need no new target.
KEEP = {"claude": "antigravity", "antigravity": "claude"} | dict.fromkeys(
    ["cursor", "codex", "gemini"], "claude"
)


def build_package(root, rule_name="local", rule_source=RULE_SOURCE):
    package = root / "local-package"
    files = {
        "apm.yml": "name: local\nversion: '1'\n",
        f".apm/instructions/{rule_name}.instructions.md": rule_source,
        ".apm/skills/review/SKILL.md": SKILL_MD,
        ".apm/skills/review/references/guide.md": GUIDE_MD,
        ".apm/hooks/hooks.json": json.dumps(HOOK_SOURCE),
        ".apm/hooks/check.sh": CHECK_SH,
    }
    for rel, text in files.items():
        path = package / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (package / ".apm/hooks/check.sh").chmod(0o755)
    skill = package / ".apm/skills/review"
    (skill / ".git").mkdir()
    (skill / ".git/secret").write_text("not deployed")
    (skill / "link").symlink_to(root / "outside")


def start(project, tmp_path, targets, *, git=False, **package):
    build_package(tmp_path, **package)
    write_manifest(project, [PACKAGE_DEP], list(targets))
    if git:
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)


def seed_user_file(project, case):
    path = project / case.hook_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(case.user, indent=2) + "\n")


def load_json(project, rel):
    return json.loads((project / rel).read_text())


def dig(doc, path):
    for key in path:
        doc = doc[key]
    return doc


def entries(base):
    """Every file, folder and symlink under base, as POSIX paths relative to it."""
    return sorted(p.relative_to(base).as_posix() for p in base.rglob("*"))


def files(base):
    return {
        p.relative_to(base).as_posix(): p.read_text()
        for p in sorted(base.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


def assert_skill_deployed(project, skills):
    """The review skill is complete and has no `.git` folder and no symlink."""
    base = project / skills / "review"
    assert entries(base) == ["SKILL.md", "references", "references/guide.md"]
    assert files(base) == SKILL_TREE


def only(lines):
    assert len(lines) == 1, lines
    return lines[0]


def warnings_of(result, tmp_path):
    """Warning lines with the pytest temp folder blanked, so its name can't match a target."""
    roots = {str(tmp_path), str(tmp_path.resolve())}
    lines = [line for line in result.stderr.splitlines() if line.startswith("warning:")]
    for root in roots:
        lines = [line.replace(root, "<tmp>") for line in lines]
    return lines


def rules_warnings(result, tmp_path):
    return [line for line in warnings_of(result, tmp_path) if "AGENTS.md" in line]


def run_hook(project, case, command, cwd=None):
    """Run a deployed command the way the harness would, and check what the script printed."""
    env = {
        k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "GEMINI_PROJECT_DIR")
    }
    if case.env:
        env[case.env] = str(project)
    cwd = cwd or project / case.run_from
    proc = subprocess.run(
        ["/bin/sh", "-c", command], cwd=cwd, env=env, capture_output=True, text=True, timeout=10
    )
    print(f"{case.name} hook from {cwd.relative_to(project) or '.'}: {proc.stdout.strip()}")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "HOOK_OK\n"


def deployed_command(project, case):
    return dig(load_json(project, case.hook_file), case.command_path)


def file_state(base, *, mtimes=False):
    """Bytes and mode of every file under base (and modification time when asked)."""
    state = {}
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(base)
        if path.is_file() and not path.is_symlink() and ".git" not in rel.parts:
            info = path.stat()
            state[rel.as_posix()] = (
                path.read_bytes(),
                info.st_mode,
                info.st_mtime_ns if mtimes else 0,
            )
    return state


# --- Rules ---------------------------------------------------------------------------------


@pytest.mark.scenario("TGT-01")
@pytest.mark.parametrize("name", ["claude", "antigravity", "cursor"])
def test_tgt_01_rule_with_apply_to_becomes_the_native_rule_file(apm, project, tmp_path, name):
    case = CASES[name]
    start(project, tmp_path, [name])
    apm(project, "install")
    assert (project / case.rule).read_text() == case.rule_text
    rule_files = [
        p.relative_to(project).as_posix() for p in project.rglob("*") if p.parent.name == "rules"
    ]
    assert rule_files == [case.rule]


@pytest.mark.scenario("TGT-02")
@pytest.mark.parametrize("name", ["codex", "gemini"])
def test_tgt_02_no_rule_file_and_a_warning_for_codex_and_gemini(apm, project, tmp_path, name):
    start(project, tmp_path, [name])
    result = apm(project, "install")
    leftovers = [p for p in project.rglob("*") if p.name == "rules" or p.name.startswith("local.")]
    assert leftovers == []
    assert name in only(rules_warnings(result, tmp_path))


@pytest.mark.scenario("TGT-03")
@pytest.mark.parametrize(
    "targets,named",
    [
        pytest.param(["codex", "gemini"], ["codex", "gemini"], id="codex-and-gemini"),
        pytest.param(["cursor", "codex"], ["codex"], id="cursor-and-codex"),
        pytest.param(["claude", "antigravity", "cursor"], [], id="all-targets-take-rules"),
    ],
)
def test_tgt_03_rules_warning_is_printed_once_and_names_the_targets(
    apm, project, tmp_path, targets, named
):
    start(project, tmp_path, targets)
    lines = rules_warnings(apm(project, "install"), tmp_path)
    if not named:
        assert lines == []
        return
    line = only(lines)
    assert all(name in line for name in named)
    assert all(
        name not in line for name in ("claude", "antigravity", "cursor") if name not in named
    )


@pytest.mark.scenario("TGT-04")
@pytest.mark.parametrize("name", ["claude", "antigravity", "cursor"])
def test_tgt_04_rule_without_apply_to_is_always_on(apm, project, tmp_path, name):
    case = CASES[name]
    start(project, tmp_path, [name], rule_name="always", rule_source=ALWAYS_SOURCE)
    apm(project, "install")
    rule = Path(case.rule).with_name(f"always{Path(case.rule).suffix}")
    assert (project / rule).read_text() == case.always_text


# --- Skills --------------------------------------------------------------------------------


@pytest.mark.scenario("TGT-05")
def test_tgt_05_claude_skill_lands_in_claude_skills(apm, project, tmp_path):
    start(project, tmp_path, ["claude"])
    apm(project, "install")
    assert_skill_deployed(project, ".claude/skills")
    assert not (project / ".agents").exists()


@pytest.mark.scenario("TGT-06")
@pytest.mark.parametrize("name", SHARING)
def test_tgt_06_other_targets_put_the_skill_in_agents_skills(apm, project, tmp_path, name):
    start(project, tmp_path, [name])
    apm(project, "install")
    assert_skill_deployed(project, ".agents/skills")
    assert [p.name for p in (project / ".agents/skills").iterdir()] == ["review"]
    for root in (".claude", ".cursor", ".codex", ".gemini"):
        assert not (project / root / "skills").exists()


# --- Hook files, scripts, events -----------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("claude", marks=pytest.mark.scenario("TGT-07")),
        pytest.param("antigravity", marks=pytest.mark.scenario("TGT-08")),
        pytest.param("cursor", marks=pytest.mark.scenario("TGT-09")),
        pytest.param("codex", marks=pytest.mark.scenario("TGT-10")),
        pytest.param("gemini", marks=pytest.mark.scenario("TGT-11")),
    ],
)
def test_tgt_07_to_11_hook_file_has_the_native_shape(apm, project, tmp_path, name):
    case = CASES[name]
    start(project, tmp_path, [name])
    apm(project, "install")
    assert load_json(project, case.hook_file) == case.hooks
    for other in CASES.values():
        if other.name != name:
            assert not (project / other.hook_file).exists()


@pytest.mark.scenario("TGT-12")
@pytest.mark.parametrize("name", ALL)
def test_tgt_12_hook_scripts_are_copied_and_the_source_json_is_not(apm, project, tmp_path, name):
    case = CASES[name]
    start(project, tmp_path, [name])
    apm(project, "install")
    script = project / case.scripts / "check.sh"
    assert entries(project / case.scripts) == ["check.sh"]
    assert script.read_text() == CHECK_SH
    assert script.stat().st_mode & 0o777 == 0o755


@pytest.mark.scenario("TGT-13")
@pytest.mark.parametrize("name", ALL)
def test_tgt_13_unsupported_events_are_skipped_with_a_warning(apm, project, tmp_path, name):
    case = CASES[name]
    start(project, tmp_path, [name])
    lines = warnings_of(apm(project, "install"), tmp_path)
    for event in HOOK_SOURCE:
        warned = [line for line in lines if event in line]
        if event in case.skipped:
            assert len(warned) == 1 and name in warned[0], (event, lines)
        else:
            assert warned == [], (event, lines)


# --- Running the deployed hook command -----------------------------------------------------


@pytest.mark.scenario("TGT-14")
@pytest.mark.parametrize("name", ["claude", "antigravity", "cursor", "gemini"])
def test_tgt_14_deployed_hook_command_runs_from_the_harness_folder(apm, project, tmp_path, name):
    case = CASES[name]
    start(project, tmp_path, [name])
    apm(project, "install")
    run_hook(project, case, deployed_command(project, case))


@pytest.mark.scenario("TGT-15")
def test_tgt_15_codex_hook_command_runs_from_the_project_root(apm, project, tmp_path):
    start(project, tmp_path, ["codex"], git=True)
    apm(project, "install")
    run_hook(project, CASES["codex"], deployed_command(project, CASES["codex"]), cwd=project)


@pytest.mark.scenario("TGT-16")
def test_tgt_16_codex_hook_command_runs_from_a_subfolder(apm, project, tmp_path):
    start(project, tmp_path, ["codex"], git=True)
    apm(project, "install")
    deep = project / "src/app/ui"
    deep.mkdir(parents=True)
    run_hook(project, CASES["codex"], deployed_command(project, CASES["codex"]), cwd=deep)


@pytest.mark.scenario("TGT-17")
@pytest.mark.parametrize("git", [True, False], ids=["git-root", "no-git"])
def test_tgt_17_codex_warns_when_the_project_is_not_a_git_repository(apm, project, tmp_path, git):
    start(project, tmp_path, ["codex"], git=git)
    lines = [
        line for line in warnings_of(apm(project, "install"), tmp_path) if "git" in line.lower()
    ]
    if git:
        assert lines == []
    else:
        assert "codex" in only(lines)


# --- User entries, uninstall, retirement ---------------------------------------------------


@pytest.mark.scenario("TGT-18")
@pytest.mark.parametrize("name", ALL)
def test_tgt_18_user_entries_survive_install_and_uninstall(apm, project, tmp_path, name):
    case = CASES[name]
    start(project, tmp_path, [name])
    seed_user_file(project, case)
    apm(project, "install")
    assert load_json(project, case.hook_file) == case.user_after_install
    apm(project, "uninstall", PACKAGE_DEP)
    assert load_json(project, case.hook_file) == case.user_after_uninstall


@pytest.mark.scenario("TGT-19")
@pytest.mark.parametrize("name", ALL)
def test_tgt_19_uninstall_removes_what_the_package_deployed(apm, project, tmp_path, name):
    case = CASES[name]
    start(project, tmp_path, [name])
    apm(project, "install")
    deployed = [
        f"{case.skills}/review",
        f"{case.scripts}/check.sh",
        case.sidecar,
        *([case.rule] if case.rule else []),
    ]
    assert all((project / rel).exists() for rel in deployed)
    apm(project, "uninstall", PACKAGE_DEP)
    assert [rel for rel in deployed if (project / rel).exists()] == []


@pytest.mark.scenario("TGT-20")
@pytest.mark.parametrize("name", ALL)
def test_tgt_20_retiring_a_target_removes_its_files_and_entries(apm, project, tmp_path, name):
    case, keep = CASES[name], CASES[KEEP[name]]
    start(project, tmp_path, [name, keep.name])
    seed_user_file(project, case)
    apm(project, "install")
    assert (project / case.scripts / "check.sh").is_file()
    kept = file_state(project / keep.root)
    write_manifest(project, [PACKAGE_DEP], [keep.name])
    apm(project, "install")
    assert not (project / case.scripts / "check.sh").exists()
    assert not (project / case.sidecar).exists()
    assert not (project / f"{case.skills}/review").exists()
    if case.rule:
        assert not (project / case.rule).exists()
    assert load_json(project, case.hook_file) == case.user_after_uninstall
    assert load_json(project, keep.hook_file) == keep.hooks
    assert file_state(project / keep.root) == kept


# --- The shared .agents/skills folder ------------------------------------------------------


@pytest.mark.scenario("TGT-21")
def test_tgt_21_shared_skills_folder_is_written_once(apm, project, tmp_path):
    start(project, tmp_path, SHARING)
    apm(project, "install")
    assert_skill_deployed(project, ".agents/skills")
    for root in (".claude", ".cursor", ".codex", ".gemini"):
        assert not (project / root / "skills").exists()
    lock = yaml.safe_load((project / "apm.lock.yaml").read_text())
    (package,) = lock["dependencies"]
    skill_paths = [p for p in package["deployed_files"] if p.startswith(".agents/skills/")]
    assert skill_paths == [
        ".agents/skills/review",
        ".agents/skills/review/SKILL.md",
        ".agents/skills/review/references/guide.md",
    ]


@pytest.mark.scenario("TGT-22")
def test_tgt_22_shared_skills_folder_stays_until_the_last_sharer_is_removed(apm, project, tmp_path):
    start(project, tmp_path, SHARING)
    apm(project, "install")
    for remaining in (SHARING[1:], SHARING[2:], SHARING[3:]):
        write_manifest(project, [PACKAGE_DEP], remaining)
        apm(project, "install")
        assert_skill_deployed(project, ".agents/skills")
    write_manifest(project, [PACKAGE_DEP], ["claude"])
    apm(project, "install")
    assert not (project / ".agents/skills/review").exists()
    assert_skill_deployed(project, ".claude/skills")


# --- Reinstall and all five together -------------------------------------------------------


@pytest.mark.scenario("TGT-23")
@pytest.mark.parametrize("targets", [*[[n] for n in ALL], ALL], ids=[*ALL, "five-targets"])
def test_tgt_23_second_install_with_nothing_changed_writes_nothing(apm, project, tmp_path, targets):
    start(project, tmp_path, targets)
    apm(project, "install")
    before = file_state(project, mtimes=True)
    assert before
    result = apm(project, "install")
    assert "0 file(s) written, 0 deleted" in result.stdout
    assert file_state(project, mtimes=True) == before


@pytest.mark.scenario("TGT-24")
def test_tgt_24_all_five_targets_in_one_project(apm, project, tmp_path):
    start(project, tmp_path, ALL, git=True)
    result = apm(project, "install")
    lines = warnings_of(result, tmp_path)
    for case in CASES.values():
        if case.rule:
            assert (project / case.rule).read_text() == case.rule_text
        assert load_json(project, case.hook_file) == case.hooks
        script = project / case.scripts / "check.sh"
        assert script.read_text() == CHECK_SH and script.stat().st_mode & 0o777 == 0o755
        run_hook(project, case, deployed_command(project, case))
        for event in case.skipped:
            assert any(event in line and case.name in line for line in lines), (case.name, event)
    deep = project / "src/app/ui"
    deep.mkdir(parents=True)
    run_hook(project, CASES["codex"], deployed_command(project, CASES["codex"]), cwd=deep)
    assert_skill_deployed(project, ".claude/skills")
    assert_skill_deployed(project, ".agents/skills")
    assert [p.name for p in (project / ".agents/skills").iterdir()] == ["review"]
    for root in (".cursor", ".codex", ".gemini"):
        assert not (project / root / "skills").exists()
    line = only(rules_warnings(result, tmp_path))
    assert "codex" in line and "gemini" in line


# --- The three artefacts agree -------------------------------------------------------------


def test_scenario_ids_agree_across_feature_tests_and_qa_procedure():
    feature = (FEATURES / "targets.feature").read_text()
    tags = re.findall(r"^\s*@(TGT-\d{2})\b", feature, re.MULTILINE)
    titles = re.findall(r"^\s*Scenario(?: Outline)?: (TGT-\d{2})\b", feature, re.MULTILINE)
    tested = set(re.findall(r'scenario\("(TGT-\d{2})"\)', Path(__file__).read_text()))
    qa = set(re.findall(r"\bTGT-\d{2}\b", (FEATURES / "targets-qa.md").read_text()))
    assert tags == titles
    assert len(tags) == len(set(tags))
    assert set(tags) == tested == qa
