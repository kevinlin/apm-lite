"""Filesystem safety: name checks, containment, atomic writes and the undo log (design §7.1, §7.3)."""

from __future__ import annotations

import re

from apm_lite.errors import ApmError

SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def safe_name(name: str) -> str:
    """Return *name* if it is safe to use as one path component."""
    if not name or not SAFE_NAME_RE.match(name) or name in (".", ".."):
        raise ApmError(
            f"unsafe name {name!r}",
            hint="names may only contain letters, digits, '.', '_' and '-'",
        )
    return name
