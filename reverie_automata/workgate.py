"""The work gate: is there anything actually due right now?

This is what separates a standing operative from a scheduled script. A cron
runner fires a fixed prompt on a clock whether or not anything needs doing,
and pays a model call every time to be told there was nothing. This engine
asks a cheap deterministic question first, and only wakes the expensive part
when the answer is yes.

The consequence worth stating plainly: **the heartbeat can tick as fast as
you like.** A tick with nothing due costs one indexed query and a directory
scan, and makes zero model calls. Infinite heartbeat means infinite CHECKS,
not infinite spend, which is why a continuously running operative is cheap
and an unbounded one is not.

Due, not merely open. A thread that exists is not work; a thread that is
ready to be picked up again is. Without that distinction every quiet cycle
looks busy, because in any real system some deferred thread is technically
still open, and the engine would never be allowed an honest lazy day.

The gate never asks the model anything. That is the whole point: the model
cannot talk its way into being woken, and cannot talk its way out of a shift
it should take. The answer here is also what makes a claimed no-op checkable
(see planvalidate: a lazy day is legitimate, a missed shift is not).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Eligibility:
    """Why this tick should or should not wake the expensive part."""

    eligible: bool
    reason: str
    counts: dict[str, int] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.eligible


def assess_work(con, store, inbox=None, cfg=None, now: float | None = None) -> Eligibility:
    """Deterministic, cheap, model-free. Returns what is due right now.

    `cfg` keys consulted:
      thread_cooldown_minutes  how long after an attempt a thread may be
                               picked up again (default 0: always due once open)
    """
    cfg = cfg or {}
    cooldown = float(cfg.get("thread_cooldown_minutes", 0) or 0)
    counts: dict[str, int] = {}
    reasons: list[str] = []

    if inbox is not None:
        try:
            pending = len(inbox.pending())
        except Exception:
            pending = 0
        if pending:
            counts["inbox"] = pending
            reasons.append(f"{pending} inbox drop(s) waiting")

    try:
        due = store.due_threads(con, cooldown_minutes=cooldown, now=now, limit=50)
    except Exception:
        due = []
    if due:
        counts["due_threads"] = len(due)
        reasons.append(f"{len(due)} thread(s) due")

    if reasons:
        return Eligibility(True, "; ".join(reasons), counts)
    return Eligibility(False, "nothing due: no drops, no threads ready", counts)
