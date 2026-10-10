"""Ref classification, ported from upstream APM's semver and identity predicates.

Upstream sources: `marketplace/semver.py:34`, `deps/registry/semver.py:15-38` and
`models/dependency/identity.py:34, 269-282`. apm-lite never resolves semver ranges, so a ref that
upstream would treat as a range (or reject as a malformed one) is an error here, which keeps the
two tools in agreement about which refs are plain branch or tag names.
"""

from __future__ import annotations

import re
from typing import Literal

from apm_lite.errors import ApmError

_SEMVER_RE = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_RANGE_OPERATORS = (">=", "<=", ">", "<", "^", "~", "=")
_WILDCARD_RE = re.compile(r"^\d+\.\d+\.[xX*]$")
_RANGE_PREFIX_RE = re.compile(r"^(>=|<=|>|<|\^|~|=)")
_FULL_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")


def is_semver_range(spec: str) -> bool:
    parts = spec.strip().split()
    return bool(parts) and all(_is_range_component(p) for p in parts)


def _is_range_component(c: str) -> bool:
    if not c:
        return False
    if _WILDCARD_RE.match(c):
        return True
    for op in _RANGE_OPERATORS:
        if c.startswith(op):
            return _SEMVER_RE.match(c[len(op) :]) is not None
    return _SEMVER_RE.match(c) is not None


def is_full_sha(ref: str | None) -> bool:
    return ref is not None and _FULL_SHA_RE.fullmatch(ref) is not None


def classify(ref: str | None) -> Literal["none", "sha", "literal"]:
    """Return the kind of `ref`, or raise `ApmError` for a ref apm-lite doesn't accept."""
    if ref is None:
        return "none"
    if "@" in ref:
        raise ApmError(
            f"ref {ref!r} contains '@'",
            hint="upstream APM reads '@' in a ref as an alias; use a ref without it",
        )
    if is_full_sha(ref):
        return "sha"
    if is_semver_range(ref) or _RANGE_PREFIX_RE.match(ref.strip()):
        raise ApmError(
            f"ref {ref!r} looks like a semver version or range, which apm-lite doesn't resolve",
            hint="use the tag name, e.g. 'v1.2.3'",
        )
    return "literal"
