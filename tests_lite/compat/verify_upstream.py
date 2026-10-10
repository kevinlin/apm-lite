"""Check apm-lite's committed compatibility fixtures with upstream APM's own readers (plan Task 12).

Run while src/apm_cli/ still exists:  uv run python tests_lite/compat/verify_upstream.py
Exits 0 when upstream reads every fixture with the meaning apm-lite intends: same refs, no alias,
same identity keys and apm_modules paths, and the same owner and hash for every deployed file.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from apm_cli.core.deployment_ledger import DeploymentLedgerCodec
from apm_cli.deps.lockfile import LockFile
from apm_cli.models.apm_package import APMPackage
from apm_cli.models.dependency.reference import DependencyReference

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "compat"


def main() -> int:
    expected = json.loads((FIXTURES / "expected.json").read_text())
    problems: list[str] = []
    lines: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / "apm.yml").write_bytes((FIXTURES / "apm.yml").read_bytes())
        (project / "packages" / "own").mkdir(parents=True)
        pkg = APMPackage.from_apm_yml(project / "apm.yml", source_path=project)
        upstream_deps = pkg.get_apm_dependencies()
        lines.append(f"APMPackage.from_apm_yml: {len(upstream_deps)} apm dependencies")
        if len(upstream_deps) != len(expected["entries"]):
            problems.append(f"dependency count {len(upstream_deps)} != {len(expected['entries'])}")

        modules = project / "apm_modules"
        for entry in expected["entries"]:
            raw = entry["raw"]
            dep = (
                DependencyReference.parse(raw)
                if isinstance(raw, str)
                else DependencyReference.parse_from_dict(raw)
            )
            got = {
                "ref": dep.reference,
                "alias": dep.alias,
                "key": dep.get_unique_key(),
                "install_path": (
                    dep.get_install_path(modules).relative_to(project).as_posix()
                    if entry["install_path"]
                    else None
                ),
            }
            want = {**{k: entry[k] for k in ("ref", "key", "install_path")}, "alias": None}
            status = "ok" if got == want else "MISMATCH"
            lines.append(f"{status}: {raw!r} -> {got}")
            if got != want:
                problems.append(f"{raw!r}: upstream {got} != apm-lite {want}")

    lockfile = LockFile.read(FIXTURES / "apm.lock.yaml")
    if lockfile is None:
        problems.append("LockFile.read returned None")
    else:
        ledger = DeploymentLedgerCodec.from_lockfile(lockfile)
        owners = {
            record.locator.value: (record.active_owner, record.content_hash)
            for record in ledger.records.values()
        }
        lines.append(f"LockFile.read + ledger rebuild: {len(owners)} deployed records")
        for path, want in expected["deployed"].items():
            got = owners.get(path)
            if got != (want["owner"], want["hash"]):
                problems.append(f"{path}: upstream ledger {got} != apm-lite {want}")
        extra = sorted(set(owners) - set(expected["deployed"]))
        if extra:
            problems.append(f"upstream ledger has records apm-lite doesn't track: {extra}")

    print("\n".join(lines))
    if problems:
        print("\nFAILED:\n" + "\n".join(f"- {p}" for p in problems))
        return 1
    print("\nupstream APM accepts the apm-lite fixtures with the same meaning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
