"""Deterministic validation of a plan before anything acts on it.

A schema guarantees shape. It cannot guarantee sense, and the defects a small
brain actually produces are all sense defects. Each rule below exists because
the failure was observed in a real run, not imagined:

- a task whose `what` was the single word "research";
- a plan that declared `do_nothing` while the queue held an inbox drop and an
  open thread;
- a plan that declared `do_nothing` and then listed tasks anyway.

The second one is the dangerous one, and it is the reason this module exists
at all. A false no-op is indistinguishable from the virtue the engine is
built to protect (a lazy day is legitimate; work is never manufactured) unless
something outside the model independently knows whether work existed. The
gate knows. So the gate's answer, not the model's mood, decides whether
"nothing to do" is an honest conclusion or a missed shift.

Nothing here judges whether a plan is GOOD. It rejects plans that are
internally incoherent or empty of content, which is a smaller and much more
reliable claim.
"""
from __future__ import annotations

# Words that are a category, not a task. A `what` consisting of one of these
# says nothing about what would actually be done.
_EMPTY_INTENTS = {
    "research", "work", "task", "todo", "none", "n/a", "nothing",
    "investigate", "review", "check", "look", "think", "plan", "continue",
}

MIN_WHAT_CHARS = 15


def _is_content_free(what: str) -> bool:
    w = (what or "").strip().rstrip(".").strip()
    if not w:
        return True
    if w.lower() in _EMPTY_INTENTS:
        return True
    return len(w) < MIN_WHAT_CHARS


def validate_plan(plan: dict, *, work_available: bool, max_tasks: int = 1
                  ) -> tuple[dict, list[str], bool]:
    """(repaired_plan, complaints, needs_replan).

    `work_available` comes from the engine's own eligibility check (a pending
    inbox drop, a due thread, an unmet mandate condition), never from the
    model. `max_tasks` caps ambition per cycle; extra tasks are dropped rather
    than half-run, because a small brain that lists five tasks has usually
    lost the thread rather than found four more.
    """
    plan = dict(plan or {})
    complaints: list[str] = []
    tasks = [t for t in (plan.get("tasks") or []) if isinstance(t, dict)]
    do_nothing = bool(plan.get("do_nothing"))

    if do_nothing and tasks:
        # Incoherent: it cannot both be a lazy day and a work day. Trust the
        # quieter half; acting on a plan that contradicts itself is worse than
        # skipping a cycle.
        complaints.append(f"declared do_nothing while listing {len(tasks)} task(s); tasks dropped")
        tasks = []

    if not do_nothing:
        kept = []
        for t in tasks:
            if _is_content_free(str(t.get("what", ""))):
                complaints.append(f"task {t.get('id', '?')!r} had no content in `what`; dropped")
                continue
            kept.append(t)
        if len(kept) > max_tasks:
            complaints.append(f"{len(kept)} tasks proposed, cap is {max_tasks}; kept the first")
            kept = kept[:max_tasks]
        tasks = kept
        if not tasks:
            complaints.append("no usable task survived validation")
            do_nothing = True

    needs_replan = False
    if do_nothing and work_available:
        # The defect this module was written for.
        complaints.append(
            "do_nothing contradicts the eligibility check: work was pending "
            "(inbox, due thread, or unmet condition). Not an honest lazy day")
        needs_replan = True

    plan["tasks"] = tasks
    plan["do_nothing"] = do_nothing
    if do_nothing and not plan.get("do_nothing_reason"):
        plan["do_nothing_reason"] = "; ".join(complaints) or "nothing worth doing"
    return plan, complaints, needs_replan
