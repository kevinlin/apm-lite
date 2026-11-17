"""Shared e2e fixtures: the installed `apm` executable, and the scenario marker."""

import subprocess
import sys
from pathlib import Path

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "scenario(id): the Gherkin scenario in tests/e2e/features/targets.feature this test checks",
    )


@pytest.fixture
def apm(git_env, monkeypatch):
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    executable = Path(sys.executable).with_name("apm")
    assert executable.is_file(), "Install the project's console entry point first"

    def run(project, *args, expected=0):
        result = subprocess.run(
            [str(executable), *args], cwd=project, capture_output=True, text=True, timeout=30
        )
        print(f"\n[{project.name}] apm {' '.join(args)} -> {result.returncode}")
        print(result.stdout + result.stderr, end="")
        assert result.returncode == expected, result.stdout + result.stderr
        return result

    return run
