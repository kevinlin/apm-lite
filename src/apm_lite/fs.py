"""Filesystem safety: name checks, containment, atomic writes and the undo log (design §7.1, §7.3)."""

from __future__ import annotations

import errno
import os
import re
import stat
import tempfile
from pathlib import Path
from types import TracebackType

from apm_lite.errors import ApmError

SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
ALLOWED_ROOTS = (".claude", ".agents", "apm_modules")
ALLOWED_ROOT_FILES = ("apm.yml", "apm.lock.yaml", ".gitignore")


def safe_name(name: str) -> str:
    """Return *name* if it is safe to use as one path component."""
    if not name or not SAFE_NAME_RE.match(name) or name in (".", ".."):
        raise ApmError(
            f"unsafe name {name!r}",
            hint="names may only contain letters, digits, '.', '_' and '-'",
        )
    return name


def has_symlink_between(path: Path, root: Path) -> bool:
    """True if *path* or any of its ancestors below *root* is a symlink (sources and destinations)."""
    path, root = path.absolute(), root.absolute()
    return any(
        part.is_symlink()
        for part in (path, *path.parents)
        if part.is_relative_to(root) and part != root
    )


def check_destination(project: Path, rel: str) -> Path:
    """Validate an allowed project-relative destination without following symlinks."""
    relative = Path(rel)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ApmError(f"unsafe destination {rel!r}: expected a relative path without '..'")
    if relative.parts[0] not in ALLOWED_ROOTS and not (
        len(relative.parts) == 1 and relative.name in ALLOWED_ROOT_FILES
    ):
        raise ApmError(f"destination {rel!r} is outside the allowed project paths")

    project = project.absolute()
    destination = project
    for component in relative.parts:
        destination /= component
        if destination.is_symlink():
            raise ApmError(f"destination {rel!r} passes through a symlink: {destination}")
    if not destination.resolve().is_relative_to(project.resolve()):
        raise ApmError(f"destination {rel!r} resolves outside the project")
    return destination


def atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    """Replace a checked destination using a flushed temporary file beside it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class UndoLog:
    """Keep each path's first state and restore it if the transaction fails."""

    def __init__(self, project: Path) -> None:
        self.project = project.absolute()
        # Missing mode means absent; missing bytes with a mode means a directory.
        self._before: dict[str, tuple[bytes | None, int | None]] = {}

    def _record(self, path: Path) -> None:
        rel = path.relative_to(self.project).as_posix()
        if rel in self._before:
            return
        try:
            previous = path.stat()
        except FileNotFoundError:
            self._before[rel] = (None, None)
            return
        if stat.S_ISREG(previous.st_mode):
            data = path.read_bytes()
        elif stat.S_ISDIR(previous.st_mode):
            data = None
        else:
            raise ApmError(f"destination {rel!r} is not a regular file or directory")
        self._before[rel] = (data, stat.S_IMODE(previous.st_mode))

    def write(self, rel: str, data: bytes, mode: int = 0o644) -> None:
        path = check_destination(self.project, rel)
        if path.is_dir():
            raise ApmError(f"destination {rel!r} is a directory, expected a file")
        missing = []
        parent = path.parent
        while parent != self.project and not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for parent in reversed(missing):
            self._record(parent)
        self._record(path)
        atomic_write(path, data, mode)

    def chmod(self, rel: str, mode: int) -> None:
        path = check_destination(self.project, rel)
        self._record(path)
        path.chmod(mode)

    def delete(self, rel: str) -> None:
        path = check_destination(self.project, rel)
        if path.is_dir():
            raise ApmError(f"destination {rel!r} is a directory, expected a file")
        self._record(path)
        path.unlink(missing_ok=True)

    def rmdir_if_empty(self, rel: str) -> None:
        path = check_destination(self.project, rel)
        self._record(path)
        try:
            path.rmdir()
        except FileNotFoundError:
            return
        except OSError as exc:
            if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                raise

    def rollback(self) -> list[str]:
        failures = []
        for rel, (data, mode) in reversed(self._before.items()):
            try:
                path = check_destination(self.project, rel)
                if mode is None:
                    if path.is_dir():
                        path.rmdir()
                    else:
                        path.unlink(missing_ok=True)
                elif data is None:
                    path.mkdir(parents=True, exist_ok=True)
                    path.chmod(mode)
                else:
                    atomic_write(path, data, mode)
            except BaseException:
                # Restore other paths even when this restoration is interrupted.
                failures.append(rel)
        return failures

    def __enter__(self) -> UndoLog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if exc_type is not None:
            failures = self.rollback()
            if failures:
                raise ApmError(f"rollback failed for: {', '.join(failures)}") from exc
        return False
