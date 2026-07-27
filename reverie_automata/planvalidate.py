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

import re

# Words that are a category, not a task. A `what` consisting of one of these
# says nothing about what would actually be done.
_EMPTY_INTENTS = {
    "research", "work", "task", "todo", "none", "n/a", "nothing",
    "investigate", "review", "check", "look", "think", "plan", "continue",
}

MIN_WHAT_CHARS = 15

# Work that plainly touches the world cannot be done from memory. A planner
# that labels "read the file" as a text task is not lying, it has simply
# mis-sorted itself, and the engine will then hand it a prompt with no tools
# and get an honest "I cannot reach that" back. Upgrading is the safe
# direction: a task given tools it did not need loses nothing, while a task
# denied tools it needed is stranded.
_NEEDS_TOOLS = re.compile(
    r"\b(read|open|list|search|fetch|download|look\s?up|browse|query|"
    r"run|execute|compute|calculate|verify|check|test|"
    r"write|save|record|append|log|edit|create|file)\b", re.I)


def _is_content_free(what: str) -> bool:
    w = (what or "").strip().rstrip(".").strip()
    if not w:
        return True
    if w.lower() in _EMPTY_INTENTS:
        return True
    return len(w) < MIN_WHAT_CHARS


def validate_plan(plan: dict, *, work_available: bool, max_tasks: int = 1,
                  allow_text_tasks: bool = True, menu=None,
                  ruled_out: set[str] | None = None) -> tuple[dict, list[str], bool]:
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
        seen: set[str] = set()
        for t in tasks:
            # Typed admissibility comes first, because when a menu exists every
            # check below it is asking about prose that is no longer there. A
            # task that is not an admissible SHAPE of work is not a badly
            # written task, it is not a task.
            if menu is not None:
                ok, why = menu.validate(t)
                if not ok:
                    complaints.append(f"task {t.get('id', '?')!r} refused: {why}")
                    continue
                key = menu.key(t)
                if key in seen:
                    complaints.append(
                        f"task {t.get('id', '?')!r} is the same work as one already "
                        "in this plan; rewording does not make it new")
                    continue
                if ruled_out and key in ruled_out:
                    # A recorded dead end is a constraint, not a note in a log
                    # the planner may skim. Watched live: the machine wrote a
                    # correct diagnosis of its own repetition and then repeated.
                    complaints.append(
                        f"task {t.get('id', '?')!r} was already ruled out; a dead end "
                        "is a constraint, not a suggestion")
                    continue
                seen.add(key)
                kept.append(t)
                continue
            if _is_content_free(str(t.get("what", ""))):
                complaints.append(f"task {t.get('id', '?')!r} had no content in `what`; dropped")
                continue
            if str(t.get("mode", "")).lower() == "text" and not allow_text_tasks:
                # Observed live on a small brain: asked to work without tools,
                # it produced a fluent and entirely invented account of the
                # subject, opening with "I have reviewed the text". Nothing
                # grounds a text task, so for such a brain there is no text
                # task; everything goes through tools, where each claim traces
                # to something a tool actually returned.
                complaints.append(
                    f"task {t.get('id', '?')!r} filed as text, but this profile "
                    "grounds every claim in a tool result; upgraded")
                t = dict(t, mode="tool")
            elif (str(t.get("mode", "")).lower() == "text"
                    and _NEEDS_TOOLS.search(str(t.get("what", "")))):
                complaints.append(
                    f"task {t.get('id', '?')!r} needs tools but was filed as text; upgraded")
                t = dict(t, mode="tool")
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
