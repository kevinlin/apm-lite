"""Build synthetic native-harness probe projects with an installed apm command.

Usage: python tests/e2e/native_fixture.py /absolute/path/to/apm /absolute/scratch/directory
This creates fixtures only. Native harness commands are in docs/verification/test-results.md.
"""

import json
import subprocess
import sys
from pathlib import Path

import yaml

apm, scratch = sys.argv[1:]
for target in ("claude", "antigravity"):
    project = Path(scratch) / target
    project.mkdir(parents=True, exist_ok=False)
    subprocess.run([apm, "init"], cwd=project, check=True)
    manifest = project / "apm.yml"
    data = yaml.safe_load(manifest.read_text())
    data["targets"] = [target]
    manifest.write_text(yaml.safe_dump(data, sort_keys=False))
    files = {
        ".apm/instructions/always.instructions.md": "When asked for the workspace secret word, answer E2E_RULE_WALNUT.\n",
        ".apm/instructions/python.instructions.md": "---\napplyTo: '**/*.py'\n---\nThe Python codeword is E2E_PY_THYME.\n",
        ".apm/skills/verification-probe/SKILL.md": "---\nname: verification-probe\ndescription: Answer the verification probe question.\n"
        "---\nThe verification skill answer is E2E_SKILL_QUINCE. Reply with that answer.\n",
        ".apm/hooks/mark.sh": '#!/bin/sh\ncat >/dev/null\nprintf "%s|%s\\n" "$1" "$PWD" >> hook-fired.log\n',
        "src/probe.py": "print('synthetic fixture')\n",
    }
    hooks = {
        "PreToolUse": [
            {"matcher": "*", "hooks": [{"type": "command", "command": "./mark.sh tool"}]}
        ],
    }
    if target == "antigravity":
        hooks["PreInvocation"] = [{"type": "command", "command": "./mark.sh invocation"}]
    files[".apm/hooks/hooks.json"] = json.dumps(hooks)
    for rel, text in files.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (project / ".apm/hooks/mark.sh").chmod(0o755)
    subprocess.run([apm, "install"], cwd=project, check=True)
    subprocess.run([apm, "install", "--frozen"], cwd=project, check=True)
    print(f"Prepared {target}: {project}")
