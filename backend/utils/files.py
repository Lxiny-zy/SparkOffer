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
from pathlib import Path


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
