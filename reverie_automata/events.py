"""An append-only record of what the engine did, for a person to read later.

The cycle directories already hold everything: the plan, each task's whole tool
transcript, the outcome. What they do not hold is the SHAPE of a run. To see
that a machine tried the same failing approach four nights running, or that it
stopped delegating after a rejection, you have to open twenty directories and
hold them in your head at once.

So every decision worth reconstructing also lands here as one json line: cheap,
ordered, greppable, and never rewritten. No network, no schema migration, no
daemon. If this file is deleted the engine does not notice, which is the
correct dependency direction for an observation surface: watching the work must
never be able to break the work.

This is not a log for debugging the code. It is the trace of the reasoning: at
each point where the machine chose, what it chose and what made it choose.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def emit(home, kind: str, **fields) -> None:
    """Record one decision. Failure here is silence, never an exception:
    observation is a context path, and context paths fail open."""
    try:
        rec = {"at": time.time(), "kind": kind}
        rec.update(fields)
        p = Path(home) / "events.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def read(home, kinds: set[str] | None = None, limit: int = 0) -> list[dict]:
    p = Path(home) / "events.jsonl"
    out: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # a torn last line is normal on an append-only file
            if kinds and rec.get("kind") not in kinds:
                continue
            out.append(rec)
    except OSError:
        return []
    return out[-limit:] if limit else out
