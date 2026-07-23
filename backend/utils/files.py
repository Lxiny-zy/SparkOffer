"""Filesystem helpers — atomic writes for user-facing text files.

Knowledge-base files and high-freq banks are mutated by concurrent paths
(manual edits, auto-evolution writeback, QA ingestion). A bare
``Path.write_text`` can leave a half-written / empty file if the process dies
mid-write, and two read-modify-write paths can silently clobber each other.
These helpers mirror the temp-file + ``os.replace`` pattern already used in
``memory.py`` / ``ai_config.py`` so every text mutation is crash-atomic.
"""
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def file_mutation_lock_path(path: Path) -> Path:
    """Return the shared sidecar used by every writer of one logical file."""
    path = Path(path)
    return path.with_name(f".{path.name}.lock")


@contextmanager
def exclusive_file_lock(path: Path, *, timeout: float = 30.0):
    """Cross-process exclusive lock backed by a persistent sidecar file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    deadline = time.monotonic() + timeout
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out locking {path}")
                    time.sleep(0.05)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out locking {path}")
                    time.sleep(0.05)
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write ``content`` to ``path`` atomically (temp file in same dir + os.replace).

    os.replace is atomic on the same filesystem, so readers always see either the
    old or the new full content — never a truncated/partial write.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, str(path))
    except BaseException:
        # Best-effort cleanup of the temp file on any failure so we don't litter
        # the knowledge dir with .tmp droppings.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
