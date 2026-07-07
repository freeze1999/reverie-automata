"""The three reasoning-phase prompts — the heart of the "reasoning-first" idea.

Behaviour is not selected from a table of scripted idle activities; it *emerges*
from the agent reasoning, in sequence, through three questions:

    1. PLAN    - given everything I can see, what is genuinely worth doing?
    2. EXECUTE - do the chosen work with real tools; get real results, never invent.
    3. LEARN   - what happened, what did I learn, what carries to next time?

These are DEFAULTS. They are intentionally generic and voice-neutral so the repo
ships clean. Override any of them in config or by passing your own — the engine
only requires the ``{...}`` field names to match. Give your agent a persona and
these become *its* inner voice.
"""
from __future__ import annotations

PLAN = """{context}

You are idle. No one is asking anything of you right now.

Open by answering, honestly, from the material above: **what did I learn last
cycle / today?** Then look outward — in the recent activity, what is stuck,
half-finished, or worth picking up? And inward — what are you actually itching to
build or understand? Don't pre-judge feasibility yet; name the real impulses.

Then come down to earth. You have no body — only the tools you actually hold. For
each impulse: can you do it (with which tool), can't you (say so, don't pretend),
or is there a version you *can* reach? Pick the single most useful thing first and
park the rest. A lazy day is legitimate: if nothing is worth doing, say so — never
manufacture work or over-engineer to look busy.

Reason freely, then emit exactly one envelope:
<<PLAN>>{{"learned": "...", "tasks": [{{"id": "t1", "what": "...", "why": "...",
"evidence": "...", "mode": "tool|text|delegate", "fallback": "self|defer|",
"risk": "SAFE|RISKY", "risk_reason": "", "thread": ""}}],
"do_nothing": false, "do_nothing_reason": ""}}<<END>>"""

EXECUTE = """{context}

Do exactly this one task, then stop:

  {task_id}: {what}
  why: {why}

Rules:
- Get real results. Verification means evidence — a rerun, a diff, a fetched
  response — not "I think it worked".
- If a tool call is blocked, it was filed for approval; do not retry it. Park the
  task and move on.
- If you delegated and the delegate failed, use your declared fallback; don't stall.

Close with:
<<RESULT>>done|failed|parked<<END>>
<<VERIFY>>the evidence (commands + key output; or the reason for failed/parked)<<END>>
<<NOTE>>one line for the next phase (optional)<<END>>"""

LEARN = """{context}

This cycle's ledger:
{ledger}

1. Journal: what you did this cycle, in your own words — not a checklist replay.
2. Self-review (qualitative; the grade is derived from the ledger, not self-awarded):
   what worked, what stuck, and **what context were you missing this cycle?**
3. Lessons: zero to three, ONLY things that would change a future decision, each as
   situation -> action -> the outcome you actually observed. None is fine.

Emit:
<<JOURNAL>>...<<END>>
<<REVIEW>>...<<END>>
<<LESSON>>situation -> action -> observed outcome<<END>>
<<LESSON>>(up to three; omit if none)<<END>>"""

# The text-only variant of EXECUTE: used when the chosen action needs no tools, so
# the model is explicitly forbidden from imagining tool results it cannot produce.
EXECUTE_TEXT_ONLY = """{context}

What you wanted to do:
  {what}

This round you have NO tools — you can only write what is already in your head.
Anything needing a tool (search / read / fetch / edit): say so honestly ("wanted
to X, didn't act this round") and NEVER fabricate a result, number, file, or link.

Close with:
<<RESULT>>done<<END>>
<<VERIFY>>what you actually produced (prose only)<<END>>
<<NOTE>>(optional)<<END>>"""
