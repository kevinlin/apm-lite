"""Generate ``hash_golden.json`` from upstream APM's hash functions.

Run once while ``src/apm_cli`` still exists::

    uv run python tests_lite/fixtures/make_hash_golden.py

Trees record only regular files (symlinks cannot be stored in JSON); the symlink in the
``nested`` tree proves upstream skips it, so the recorded hash must match a tree without it.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from apm_cli.utils.content_hash import compute_file_hash, compute_package_hash

OUT = Path(__file__).with_name("hash_golden.json")

TREES: dict[str, dict[str, bytes]] = {
    "nested": {
        "a.txt": b"alpha\n",
        "sub/b.md": b"# b\n",
        "sub/deep/c.txt": b"deep\n",
        "sub/h\u00e9llo.md": "caf\u00e9\n".encode(),
        "sub/.apm-pin": b"nested pin counts\n",
        ".git/x": b"git internals\n",
        "__pycache__/y.pyc": b"\x00bytecode",
        "sub/__pycache__/z.pyc": b"\x00bytecode",
        ".apm-pin": b"root pin is skipped\n",
    },
    "empty": {},
    "crlf_binary": {
        "crlf.txt": b"one\r\ntwo\r\n",
        "bin.dat": b"\x00\x01\xff\r\n\x00",
    },
}

FILES: dict[str, bytes] = {
    "empty": b"",
    "lf_text": b"one\ntwo\n",
    "crlf_text": b"one\r\ntwo\r\n",
    "bare_cr": b"one\rtwo\r\n",
    "utf8_multibyte_crlf": "h\u00e9llo\r\n".encode(),
    "binary_nul": b"a\r\n\x00b\r\n",
    "invalid_utf8": b"\xff\xfe\r\nabc\r\n",
}


def _latin1(data: bytes) -> str:
    return data.decode("latin1")


def _build(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir(parents=True)
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def main() -> None:
    golden: dict[str, dict] = {"trees": {}, "files": {}}
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for name, files in TREES.items():
            root = tmp_path / "trees" / name
            _build(root, files)
            if name == "nested":
                os.symlink("a.txt", root / "link.txt")
            golden["trees"][name] = {
                "files": {rel: _latin1(data) for rel, data in files.items()},
                "hash": compute_package_hash(root),
            }
        for name, data in FILES.items():
            path = tmp_path / "files" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            golden["files"][name] = {
                "bytes_latin1": _latin1(data),
                "hash": compute_file_hash(path),
            }
    OUT.write_text(json.dumps(golden, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
