Feature: One package deploys to Claude Code, Antigravity, Cursor, Codex CLI and Gemini CLI
  A package author writes rules, skills and hooks once. A developer lists the harnesses in
  `targets:` in apm.yml, and `apm install` puts each primitive where that harness reads it.

  Source of truth: docs/specs/design_apm-lite.md §6 and requirements R20-R30.
  Executable form: tests/e2e/test_e2e_targets.py (pytest running the installed `apm`).
  Every scenario ID below is the `scenario` marker of one or more tests there, and a section
  of tests/e2e/features/targets-qa.md. Tables use \n for a newline.

  Harness facts the scenarios rely on:
    | target      | root     | hook command runs from                      |
    | claude      | .claude  | the project root, CLAUDE_PROJECT_DIR is set |
    | antigravity | .agents  | .agents/                                    |
    | cursor      | .cursor  | the project root                            |
    | codex       | .codex   | any folder inside the git repository        |
    | gemini      | .gemini  | the project root                            |

  Background: an author package and an empty consumer project
    Given a package folder "local-package" next to the project, with these files:
      | file                                    | content                                                        |
      | apm.yml                                 | name: local\nversion: '1'\n                                    |
      | .apm/instructions/local.instructions.md | ---\napplyTo: '**/*.py'\n---\nUse hints.\n                     |
      | .apm/skills/review/SKILL.md             | ---\nname: review\ndescription: Review code.\n---\nReview.\n   |
      | .apm/skills/review/references/guide.md  | Read the guide.\n                                              |
    And the package has the executable script ".apm/hooks/check.sh":
      """
      #!/bin/sh
      printf 'HOOK_OK\n'
      """
    And the package has the hook source ".apm/hooks/hooks.json":
      """
      {
        "PreToolUse": [{"matcher": "*", "hooks": [{"command": "./check.sh", "timeout": 10}]}],
        "PreInvocation": [{"command": "echo invocation"}],
        "SessionStart": [{"command": "echo session"}],
        "Notification": [{"command": "echo notification"}]
      }
      """
    And the skill folder also holds a ".git" folder containing a file, and a symlink "link"
    And an empty project folder "proj" next to the package

  # ---------------------------------------------------------------- Rules

  @TGT-01 @rules
  Scenario Outline: TGT-01 A rule with applyTo becomes the target's native rule file
    Given apm.yml in the project lists the dependency "../local-package" and the target "<target>"
    When I run "apm install" in the project
    Then the command succeeds
    And the file "<path>" contains exactly the text in the "content" column
    And that is the only rule file in the project

    Examples:
      | target      | path                    | content                                                         |
      | claude      | .claude/rules/local.md  | ---\npaths:\n  - "**/*.py"\n---\n\nUse hints.\n                 |
      | antigravity | .agents/rules/local.md  | ---\ntrigger: glob\nglobs: "**/*.py"\n---\n\nUse hints.\n       |
      | cursor      | .cursor/rules/local.mdc | ---\nglobs: **/*.py\nalwaysApply: false\n---\n\nUse hints.\n    |

  @TGT-02 @rules @warning
  Scenario Outline: TGT-02 Codex and Gemini get no rule file, and a warning that names the target
    Given apm.yml in the project lists the dependency "../local-package" and the target "<target>"
    When I run "apm install" in the project
    Then the command succeeds
    And the project holds no "rules" folder and no file named "local.*"
    And stderr has exactly one warning that mentions "AGENTS.md"
    And that warning names "<target>"

    Examples:
      | target |
      | codex  |
      | gemini |

  @TGT-03 @rules @warning
  Scenario Outline: TGT-03 The rules warning is printed once per command and names only the targets without rules
    Given apm.yml in the project lists the dependency "../local-package" and the targets <targets>
    When I run "apm install" in the project
    Then stderr has <count> warning(s) that mention "AGENTS.md"
    And that warning names exactly the targets <named>

    Examples:
      | targets                     | count | named         |
      | codex, gemini               | 1     | codex, gemini |
      | cursor, codex               | 1     | codex         |
      | claude, antigravity, cursor | 0     | none          |

  @TGT-04 @rules
  Scenario Outline: TGT-04 A rule without applyTo is always active
    Given the package rule is ".apm/instructions/always.instructions.md" holding "Always hints.\n" and no frontmatter
    And apm.yml in the project lists the dependency "../local-package" and the target "<target>"
    When I run "apm install" in the project
    Then the file "<path>" contains exactly the text in the "content" column

    Examples:
      | target      | path                     | content                                                  |
      | claude      | .claude/rules/always.md  | Always hints.\n                                          |
      | antigravity | .agents/rules/always.md  | ---\ntrigger: always_on\n---\n\nAlways hints.\n          |
      | cursor      | .cursor/rules/always.mdc | ---\nalwaysApply: true\n---\n\nAlways hints.\n           |

  # ---------------------------------------------------------------- Skills

  @TGT-05 @skills
  Scenario: TGT-05 Claude gets the skill in .claude/skills
    Given apm.yml in the project lists the dependency "../local-package" and the target "claude"
    When I run "apm install" in the project
    Then the folder ".claude/skills/review" holds exactly SKILL.md and references/guide.md
    And both files have the package's content
    And there is no ".git" folder and no "link" inside it
    And the project has no ".agents" folder

  @TGT-06 @skills
  Scenario Outline: TGT-06 Antigravity, Cursor, Codex and Gemini get the skill in .agents/skills
    Given apm.yml in the project lists the dependency "../local-package" and the target "<target>"
    When I run "apm install" in the project
    Then the folder ".agents/skills/review" holds exactly SKILL.md and references/guide.md
    And both files have the package's content
    And there is no ".git" folder and no "link" inside it
    And ".agents/skills" holds nothing but "review"
    And there is no skills folder under .claude, .cursor, .codex or .gemini

    Examples:
      | target      |
      | antigravity |
      | cursor      |
      | codex       |
      | gemini      |

  # ---------------------------------------------------------------- Hook files

  @TGT-07 @hooks
  Scenario: TGT-07 Claude hook file
    Given apm.yml in the project lists the dependency "../local-package" and the target "claude"
    When I run "apm install" in the project
    Then ".claude/settings.json" is exactly this JSON
      """
      {
        "hooks": {
          "PreToolUse": [
            {"matcher": "*", "hooks": [{"type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/.claude/hooks/local-package/check.sh\"", "timeout": 10}]}
          ],
          "SessionStart": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo session"}]}],
          "Notification": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo notification"}]}]
        }
      }
      """
    And the other four targets' hook files do not exist

  @TGT-08 @hooks
  Scenario: TGT-08 Antigravity hook file
    Given apm.yml in the project lists the dependency "../local-package" and the target "antigravity"
    When I run "apm install" in the project
    Then ".agents/hooks.json" is exactly this JSON
      """
      {
        "apm": {
          "PreToolUse": [
            {"matcher": "*", "hooks": [{"type": "command", "command": "hooks/local-package/check.sh", "timeout": 10}]}
          ],
          "PreInvocation": [{"type": "command", "command": "echo invocation"}]
        }
      }
      """
    And the other four targets' hook files do not exist

  @TGT-09 @hooks
  Scenario: TGT-09 Cursor hook file
    Given apm.yml in the project lists the dependency "../local-package" and the target "cursor"
    When I run "apm install" in the project
    Then ".cursor/hooks.json" is exactly this JSON
      """
      {
        "version": 1,
        "hooks": {
          "preToolUse": [
            {"type": "command", "command": ".cursor/hooks/local-package/check.sh", "timeout": 10, "matcher": "*"}
          ],
          "sessionStart": [{"type": "command", "command": "echo session"}]
        }
      }
      """
    And the other four targets' hook files do not exist

  @TGT-10 @hooks
  Scenario: TGT-10 Codex hook file
    Given apm.yml in the project lists the dependency "../local-package" and the target "codex"
    When I run "apm install" in the project
    Then ".codex/hooks.json" is exactly this JSON
      """
      {
        "hooks": {
          "PreToolUse": [
            {"matcher": "*", "hooks": [{"type": "command", "command": "\"$(git rev-parse --show-toplevel)/.codex/hooks/local-package/check.sh\"", "timeout": 10}]}
          ],
          "SessionStart": [{"hooks": [{"type": "command", "command": "echo session"}]}]
        }
      }
      """
    And the other four targets' hook files do not exist

  @TGT-11 @hooks
  Scenario: TGT-11 Gemini hook file, with the timeout in milliseconds
    Given apm.yml in the project lists the dependency "../local-package" and the target "gemini"
    When I run "apm install" in the project
    Then ".gemini/settings.json" is exactly this JSON
      """
      {
        "hooks": {
          "BeforeTool": [
            {"matcher": "*", "hooks": [{"type": "command", "command": ".gemini/hooks/local-package/check.sh", "timeout": 10000}]}
          ],
          "SessionStart": [{"hooks": [{"type": "command", "command": "echo session"}]}],
          "Notification": [{"hooks": [{"type": "command", "command": "echo notification"}]}]
        }
      }
      """
    And the other four targets' hook files do not exist

  @TGT-12 @hooks
  Scenario Outline: TGT-12 Hook scripts are copied into the target's own folder and the source JSON is not
    Given apm.yml in the project lists the dependency "../local-package" and the target "<target>"
    When I run "apm install" in the project
    Then the folder "<root>/hooks/local-package" holds exactly check.sh
    And check.sh has the package's content and mode 755

    Examples:
      | target      | root     |
      | claude      | .claude  |
      | antigravity | .agents  |
      | cursor      | .cursor  |
      | codex       | .codex   |
      | gemini      | .gemini  |

  @TGT-13 @hooks @warning
  Scenario Outline: TGT-13 An event the target can't take is skipped with a warning, and no other event warns
    Given apm.yml in the project lists the dependency "../local-package" and the target "<target>"
    When I run "apm install" in the project
    Then stderr has exactly one warning for each of the events <skipped>, and it names "<target>"
    And stderr has no warning for the other events of the hook source

    Examples:
      | target      | skipped                     |
      | claude      | PreInvocation               |
      | antigravity | SessionStart, Notification  |
      | cursor      | PreInvocation, Notification |
      | codex       | PreInvocation, Notification |
      | gemini      | PreInvocation               |

  # ---------------------------------------------------------------- Running the deployed command

  @TGT-14 @hooks @execution
  Scenario Outline: TGT-14 The deployed PreToolUse command runs from the folder the harness uses
    Given apm.yml in the project lists the dependency "../local-package" and the target "<target>"
    And I ran "apm install" in the project
    And the environment sets <variable> to the project folder (none = nothing extra is set)
    When I run the PreToolUse command from "<hook file>" with `/bin/sh -c` in "<cwd>"
    Then it exits 0
    And it prints "HOOK_OK" and nothing else on stdout

    Examples:
      | target      | hook file             | cwd              | variable           |
      | claude      | .claude/settings.json | the project root | CLAUDE_PROJECT_DIR |
      | antigravity | .agents/hooks.json    | .agents/         | none               |
      | cursor      | .cursor/hooks.json    | the project root | none               |
      | gemini      | .gemini/settings.json | the project root | GEMINI_PROJECT_DIR |

  @TGT-15 @hooks @execution
  Scenario: TGT-15 The Codex command runs from the root of a git-initialised project
    Given the project is a git repository ("git init")
    And apm.yml in the project lists the dependency "../local-package" and the target "codex"
    And I ran "apm install" in the project
    When I run the PreToolUse command from ".codex/hooks.json" with `/bin/sh -c` in the project root
    Then it exits 0
    And it prints "HOOK_OK" and nothing else on stdout

  @TGT-16 @hooks @execution
  Scenario: TGT-16 The Codex command runs from a subfolder of a git-initialised project
    Given the project is a git repository ("git init")
    And apm.yml in the project lists the dependency "../local-package" and the target "codex"
    And I ran "apm install" in the project
    And the project has the folder "src/app/ui"
    When I run the PreToolUse command from ".codex/hooks.json" with `/bin/sh -c` in "src/app/ui"
    Then it exits 0
    And it prints "HOOK_OK" and nothing else on stdout

  @TGT-17 @hooks @warning
  Scenario Outline: TGT-17 Codex warns when the project is not a git repository
    Given the project <state> a git repository
    And apm.yml in the project lists the dependency "../local-package" and the target "codex"
    When I run "apm install" in the project
    Then the command succeeds
    And stderr has <count> warning(s) that mention git, and it names "codex"

    Examples:
      | state  | count |
      | is     | 0     |
      | is not | 1     |

  # ---------------------------------------------------------------- User entries and retirement

  @TGT-18 @hooks @user-entries
  Scenario Outline: TGT-18 The user's own hook entries and other keys survive install and uninstall
    Given the project holds the user's own "<file>":
      """
      <user file>
      """
    And apm.yml in the project lists the dependency "../local-package" and the target "<target>"
    When I run "apm install" in the project
    Then "<file>" holds the user's JSON unchanged
    And under each event the package writes, the user's entries come first and the package's entries
      from TGT-07 to TGT-11 follow
    And Cursor's file also gets "version": 1
    When I run "apm uninstall ../local-package" in the project
    Then "<file>" holds the user's JSON again, with no package entry left
    And Cursor's file keeps "version": 1

    Examples:
      | target      | file                  | user file |
      | claude      | .claude/settings.json | {"permissions": {"allow": ["Read"]}, "hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo user-stop"}]}], "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-pre"}]}]}} |
      | antigravity | .agents/hooks.json    | {"user": {"Stop": [{"command": "echo user-stop"}], "PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "echo user-pre"}]}]}} |
      | cursor      | .cursor/hooks.json    | {"hooks": {"stop": [{"type": "command", "command": "echo user-stop"}], "preToolUse": [{"type": "command", "command": "echo user-pre", "matcher": "Shell"}]}} |
      | codex       | .codex/hooks.json     | {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo user-stop"}]}], "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-pre"}]}]}} |
      | gemini      | .gemini/settings.json | {"mcpServers": {"docs": {"command": "docs-server", "args": ["--stdio"]}}, "hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "echo user-end"}]}], "BeforeTool": [{"matcher": "run_shell_command", "hooks": [{"type": "command", "command": "echo user-pre", "timeout": 5000}]}]}} |

  @TGT-19 @cleanup
  Scenario Outline: TGT-19 Uninstall removes everything the package deployed
    Given apm.yml in the project lists the dependency "../local-package" and the target "<target>"
    And I ran "apm install" in the project
    When I run "apm uninstall ../local-package" in the project
    Then the rule file for "<target>" (none for codex and gemini) no longer exists
    And "<skills>/review" no longer exists
    And "<root>/hooks/local-package/check.sh" no longer exists
    And "<root>/apm-hooks.json" no longer exists

    Examples:
      | target      | root     | skills         |
      | claude      | .claude  | .claude/skills |
      | antigravity | .agents  | .agents/skills |
      | cursor      | .cursor  | .agents/skills |
      | codex       | .codex   | .agents/skills |
      | gemini      | .gemini  | .agents/skills |

  @TGT-20 @cleanup @retire
  Scenario Outline: TGT-20 Removing a target from targets: and reinstalling deletes its rule files, scripts and managed hook entries
    Given the project holds the user's own hook file for "<target>" from TGT-18
    And apm.yml in the project lists the dependency "../local-package" and the targets "<target>" and "<keep>"
    And I ran "apm install" in the project
    When I change the targets in apm.yml to "<keep>" only
    And I run "apm install" in the project
    Then the rule file for "<target>" (none for codex and gemini) no longer exists
    And "<target>"'s skill folder, "hooks/local-package/check.sh" and "apm-hooks.json" no longer exist
    And "<target>"'s hook file holds the user's JSON again, with no package entry left
    And "<keep>"'s hook file is exactly as in its TGT-07 to TGT-11 scenario
    And every file under "<keep>"'s root has the same bytes and mode as before

    Examples:
      | target      | keep        |
      | claude      | antigravity |
      | antigravity | claude      |
      | cursor      | claude      |
      | codex       | claude      |
      | gemini      | claude      |

  # ---------------------------------------------------------------- Shared .agents/skills

  @TGT-21 @skills @shared
  Scenario: TGT-21 With several of antigravity, cursor, codex and gemini, the skill is written once
    Given apm.yml in the project lists the dependency "../local-package" and the targets antigravity, cursor, codex, gemini
    When I run "apm install" in the project
    Then the install succeeds with no path collision
    And the folder ".agents/skills/review" holds exactly SKILL.md and references/guide.md
    And apm.lock.yaml lists ".agents/skills/review", ".agents/skills/review/SKILL.md" and ".agents/skills/review/references/guide.md" once each under the package's deployed_files
    And there is no skills folder under .claude, .cursor, .codex or .gemini

  @TGT-22 @skills @shared @retire
  Scenario: TGT-22 The shared skills folder stays while any sharing target is configured and goes with the last
    Given the project installed the package with the targets antigravity, cursor, codex, gemini
    When I remove one target at a time from targets: in the order antigravity, cursor, codex, and reinstall after each
    Then after each reinstall ".agents/skills/review" still holds the unchanged skill
    When I replace the last sharing target, gemini, with claude and reinstall
    Then ".agents/skills/review" no longer exists
    And ".claude/skills/review" holds the skill

  # ---------------------------------------------------------------- Reinstall and all together

  @TGT-23 @idempotent
  Scenario Outline: TGT-23 A second install with nothing changed writes nothing
    Given apm.yml in the project lists the dependency "../local-package" and the targets <targets>
    And I ran "apm install" in the project
    When I run "apm install" in the project
    Then stdout contains "0 file(s) written, 0 deleted"
    And every file in the project has the same bytes, mode and modification time as before

    Examples:
      | targets                                    |
      | claude                                     |
      | antigravity                                |
      | cursor                                     |
      | codex                                      |
      | gemini                                     |
      | claude, antigravity, cursor, codex, gemini |

  @TGT-24 @all-targets
  Scenario: TGT-24 All five targets in one project
    Given the project is a git repository ("git init")
    And apm.yml in the project lists the dependency "../local-package" and the targets claude, antigravity, cursor, codex, gemini
    When I run "apm install" in the project
    Then each rule file is as in TGT-01, and codex and gemini have none
    And each hook file is exactly as in TGT-07 to TGT-11
    And each target's "hooks/local-package/check.sh" is as in TGT-12
    And every skipped event has its warning, as in TGT-13
    And exactly one warning mentions "AGENTS.md", and it names codex and gemini
    And ".claude/skills/review" and ".agents/skills/review" each hold the skill, and no other skills folder exists
    And each target's PreToolUse command, run as in TGT-14, prints "HOOK_OK"
    And the Codex command also prints "HOOK_OK" from "src/app/ui"
