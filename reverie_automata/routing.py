"""Who should do this task: the local brain, or someone better at it.

The temptation is to let the model decide. It knows its own limits, surely,
and a prompt saying "delegate when you cannot do it yourself" reads like it
should work. Measured, it does not: a small model handed a delegation tool and
a task it demonstrably could not do never once reached for the tool, across
every cycle of a live alpha. It did not refuse to ask for help. It never
noticed there was anything to ask about, which is a different and much harder
problem, because self-knowledge is exactly the capability that is missing.

So routing is deterministic and lives here, in the wrapper, next to the risk
classifier and the text/tool mode rule which work the same way. The model
proposes a task; code decides where the task goes. The rules are patterns over
the task's own words, which is crude, and crude is the point: it fires the same
way every time and a person can read it and predict it.

The line these defaults draw is not "coding versus not coding". It is
**whether the work must be faithful to data it was given**. That boundary was
measured rather than guessed: handed a matrix written out element by element in
its own context, the local brain wrote code defining a different matrix and
reported that one's properties. It could run the computation. It could not
transcribe under composition. Everything downstream of that finding is here.
"""
from __future__ import annotations

import json
import re

LOCAL = "local"
DELEGATE = "delegate"

# Authoring work: producing an artifact that has to match a specification.
# Deliberately multilingual, because the first version of the sibling rule in
# planvalidate matched English verbs only and a task written in Chinese walked
# straight past it.
AUTHOR = (r"\bwrite\b|\bimplement\b|\breproduce\b|\brewrite\b|\bport\b|"
          r"\brefactor\b|\bscript\b|\bcode\b|\bprogram\b|\bpatch\b|\bfix\b|"
          r"写|实现|复现|编写|重构|脚本|代码|修复")

# Faithfulness: the artifact must agree with something already given.
FIDELITY = (r"\bgiven\b|\bsupplied\b|\bprovided\b|\babove\b|\bthis matrix\b|"
            r"\bthe matrix\b|\bexactly\b|\bverbatim\b|\bfrom the (paper|drop|"
            r"citation|source)\b|\bas (stated|written|specified)\b|"
            r"给定|提供|上述|按照|原样|忠于")

DEFAULTS = {
    # Both halves must fire. "write a note about what we learned" is authoring
    # without fidelity and stays local; "check the rank of the matrix above" is
    # fidelity without authoring and stays local, because running a computation
    # is the thing the small brain is actually good at.
    "delegate_when_all": [AUTHOR, FIDELITY],
    # Any single hit here delegates on its own.
    "delegate_when_any": [],
    # Any hit here pins the task local no matter what else matched. An escape
    # for the operator, and the reason a bad pattern is a nuisance rather than
    # a wall.
    "keep_local_when_any": [r"\bdo not delegate\b|\blocal only\b|不要委托"],
}


def _hit(patterns, blob: str) -> str:
    for p in patterns or ():
        m = re.search(p, blob, re.I)
        if m:
            return m.group(0)
    return ""


def route(task: dict, cfg=None) -> tuple[str, str]:
    """Where this task should be done, and the words that decided it.

    Returns (LOCAL | DELEGATE, reason). The reason is quoted from the task
    itself on purpose: a routing decision a person cannot trace back to a word
    is a routing decision nobody will trust when it goes wrong.
    """
    cfg = cfg or {}
    rules = dict(DEFAULTS)
    rules.update(dict(cfg.get("routing") or {}))
    if not rules.get("enabled", True):
        return LOCAL, "routing disabled"

    blob = json.dumps(task, ensure_ascii=False)

    pin = _hit(rules.get("keep_local_when_any"), blob)
    if pin:
        return LOCAL, f"pinned local by {pin!r}"

    any_hit = _hit(rules.get("delegate_when_any"), blob)
    if any_hit:
        return DELEGATE, f"matched {any_hit!r}"

    alls = rules.get("delegate_when_all") or []
    hits = [_hit([p], blob) for p in alls]
    if alls and all(hits):
        return DELEGATE, "authoring against supplied data: " + ", ".join(
            repr(h) for h in hits)

    return LOCAL, "no routing rule matched"
