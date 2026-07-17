"""Blast radius: the after-the-fact ledger of what a cycle actually touched.

Observability, not a cage: the agent is free to act, but the watch set
(``protected_paths``, the files a cycle is NOT normally expected to touch) is
mtime-snapshotted before the tasks run and compared after. Files that changed,
appeared, or VANISHED all surface in the outcome. A deletion is the most
destructive change an agent can make to a watched file, so it must never be
the one change this ledger misses; deleted paths carry a ``deleted: `` prefix.
"""
from __future__ import annotations

from pathlib import Path


def snapshot(watch: list) -> dict[str, float]:
    """Map every file under the watch paths to its mtime. Missing paths and
    permission errors are skipped: the ledger observes, it never blocks."""
    snap: dict[str, float] = {}
    for base in watch:
        base = Path(base).expanduser()
        try:
            if base.is_file():
                snap[str(base)] = base.stat().st_mtime
            elif base.is_dir():
                for q in base.rglob("*"):
                    if q.is_file() and "__pycache__" not in str(q):
                        snap[str(q)] = q.stat().st_mtime
        except OSError:
            continue
    return snap


def diff(before: dict[str, float], after: dict[str, float]) -> list[str]:
    """Changed or new files, then deletions flagged with a ``deleted: `` prefix."""
    changed = sorted(k for k, v in after.items() if before.get(k) != v)
    deleted = sorted(k for k in before if k not in after)
    return changed + [f"deleted: {k}" for k in deleted]
