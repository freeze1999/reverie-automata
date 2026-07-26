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

# A standing post is not an idle one. PLAN above opens by telling the agent
# nobody is asking anything of it, which is true for an idle companion and
# false for a work-gated operative with a queue. Watched live, an engine with a
# standing order open and due read that opening line and wrote back "I am truly
# idle". Blaming the model for that is blaming it for believing us. When the
# gate fires on WORK rather than on absence, the engine uses PLAN_STANDING.

PLAN_STANDING = """{context}

You are on duty. The queue above is real: it is what the engine knows is due
right now, and it was computed without asking you. Read it before anything else.

Answer honestly from the material above: what did the last cycle establish?
Then pick the ONE thing that most advances the standing orders above. Prefer
the oldest unfinished thread over a fresh idea, because unfinished work is the
only kind that goes stale.

You have no body, only the tools you hold. Say plainly what you cannot do
rather than describing what you would do if you could. A task is a thing that
leaves a receipt: a file, a computed value, a resolved identifier. "Review",
"consider" and "identify" are not tasks unless they end in one of those.

{constraints}

Reason as briefly as you can, then emit exactly one envelope:
<<PLAN>>{{"learned": "...", "tasks": [{{"id": "t1", "what": "...", "why": "...",
"mode": "tool", "risk": "SAFE|RISKY", "risk_reason": "", "thread": ""}}],
"do_nothing": false, "do_nothing_reason": ""}}<<END>>"""


def constraints(cfg) -> str:
    """The rules the validator actually enforces, rendered for the planner.

    Built from the same config keys the validator reads, so the two cannot
    drift apart. They had drifted: across one live run the wrapper corrected
    "filed as text but this profile needs a tool result" nineteen times and
    "three tasks proposed, cap is one" thirteen times, while the prompt
    mentioned neither rule and offered a mode the profile forbade. A rule that
    is only enforced and never stated turns the guard into a permanent tax.
    """
    lines = [f"- Emit at most {int(cfg.get('max_tasks_per_cycle', 8))} task(s). "
             "Extras are discarded, so the one you care about may not survive."]
    if not cfg.get("allow_text_tasks", True):
        lines.append('- Every task must be mode "tool". Text tasks are rejected '
                     "here: a claim this engine keeps has to come from a tool "
                     "result rather than from recall.")
    lines.append("- do_nothing is legitimate ONLY if the queue above is empty. "
                 "With something due, saying there is nothing to do is not a "
                 "lazy day, it is a missed shift, and the engine checks.")
    return "\n".join(lines)


EXECUTE = """{context}

Do exactly this one task, then stop:

  {task_id}: {what}
  why: {why}

Rules:
- You have at most {turn_cap} tool calls. Do not spend two of them on the same
  call with the same argument: that cannot tell you anything you do not have.
- The moment you have the result, STOP and emit the envelope. Running out of
  turns without one is the most expensive way to fail and it is always
  avoidable: an honest `failed` naming what you tried is worth more than
  silence, and it is what lets the next cycle start somewhere new.
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
