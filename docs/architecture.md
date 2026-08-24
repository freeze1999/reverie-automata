# Architecture

reverie-automata is a small idea with a few load-bearing details. This is the
longer version of the README's diagram.

## Reading the diagram

The friendly labels in the diagram map directly to the implementation:

- **Wake gate** is `Runner.tick()` asking the pure `gate.decide()` whether this
  heartbeat is allowed to spend a model call.
- **Context queue** is the harvester's bounded view of lessons, recent outcomes,
  open and due threads, one-shot inbox drops, standing orders, and configured
  sources. Harvested text is context, never authority: it cannot approve an action
  or weaken a safety rule.
- **Look** is PLAN: choose typed or prose work, or record an honest no-op.
- **Do** is EXECUTE: one session per task, with one live ledger row per task.
- **Check** is LEARN plus verification: keep receipts, derive the grade from the
  ledger or referee, write the journal, and retain only falsifiable lessons.
- **Safety lives on the action** means task-level risk is parked before execution,
  while concrete tool calls can be classified by the pure inspector at a backend's
  pre-tool hook.
- **Durable desk** is `state.db` plus cycle artifacts such as `MEMORY.md`,
  `outcome.json`, transcripts, and the event log. The process may disappear; this
  record is what lets the next one continue.

## The gate is a pure function

`gate.decide()` takes a timestamp, the last-input timestamp, an availability flag,
the gate state, config, a balance, and a kill flag, and returns `(fire, text_only,
reason)`. No model call, no I/O, no side effects. That's deliberate: the decision of
*whether* to spend money is the one place you cannot afford flakiness, so it is a pure
function with exhaustive unit tests and no clock or network in sight.

The trigger has three modes:

- **`idle`** wakes in an available person's idle gap. Its load-bearing rule is
  **fire-once-per-idle-gap**: a human action arms the gate, one cycle consumes that
  arm, and the engine cannot re-arm itself. Four idle hours is one cycle, not four.
- **`work`** wakes only when an indexed queue check or inbox scan says work is due.
  An empty heartbeat stops before any model call.
- **`both`** requires due work and an idle gap.

Window, minimum gap, daily cap, budget floor, and kill switch apply in every mode.
The work trigger also honours thread cooldowns, so a failed or deferred task does not
turn a fast heartbeat into a retry storm.

The lock is **PID-and-host stamped**. A cycle writes both into `.fire.lock`. The
reaper treats a lock as dead when its owning process is gone or the lock arrived from
another machine, while a genuinely live local cycle is not reaped merely for taking a
long time. An age backstop covers reused-PID and legacy no-PID locks.

## The flywheel owns the writes, the agent owns the reasoning

PLAN and LEARN are one text completion each; EXECUTE is one session per task. The
**engine** parses their structured envelopes and owns every write to its durable state.
The execution session may change the project with real tools, but it never gets to
invent where the ledger, journal, approvals, or lessons are stored.

- **Plan:** one planning call over the harvested context produces a `Plan` (tasks, or an
  explicit `do_nothing`). The plan opens with a ritual question, *what did I learn last
  cycle?*, read straight from memory, so continuity re-enters every time.
- **Execute:** one session *per task*, with a live ledger row written as each task
  starts and ends, so a crash mid-cycle leaves a truthful record. Risky tasks are
  filed for approval and parked; the agent keeps working on the safe ones.
- **Learn:** one wrap session produces a journal plus a grade **derived from the
  ledger**, never self-awarded, because self-grading inflates, plus falsifiable lessons
  appended to memory.

## Risk is enforced on the action, not the plan

Plan-level risk labels are only one layer; a determined or confused agent can talk
around them. The engine first applies typed-task and wrapper risk rules, parking risky
tasks as approvals before they execute. For backends that expose a pre-tool hook, the
deeper brake is `inspector.classify()` on each **concrete tool call**:
resolved-path writes to protected locations, privileged shell commands, raw network
egress to non-allowlisted hosts, mass deletion, and messages to unverified recipients
all return a block with a reason. The hook that calls the inspector is responsible for
stopping the action, logging it, and attaching any approval to the exact artifact or
command rather than to a vague intent.

The inspector is pure classification, so it can be wired into whatever pre-tool hook
an agent backend exposes and unit-tested in isolation. The engine marks cycle sessions
with `REVERIE_CYCLE`, allowing the hook to stay out of normal interactive use. A CLI
adapter with no pre-tool integration still gets the engine's task-level risk gate, but
must not be described as having concrete-call enforcement. In the core engine, a risky
task becomes a pending approval row and an approval thread; it is not executed merely
because the planner asked for it.

## Continuity is durable state, not a long session

A never-ending "always-on" session looks like the way to give an agent continuity, but
it loses on every count: its context compacts (destroying the very timeline you wanted),
a crash loses everything, and "what changed and when" becomes a forensic exercise.
reverie-automata instead reconstructs a curated timeline from disk every cycle, drawing
on lessons, open threads, and configured sources, then consolidates back to disk at the
end. Cycles are wake periods; the store is the continuity organ. Persistent mind,
ephemeral compute.

If cycles ever feel amnesiac, the fix is a richer harvest, never a longer session.
The learn phase even asks the agent *what context was I missing?* and files the answer
as a lesson, so the amnesia is measured, not guessed at.

## The learning loop is honest about what it is

Most agent backends are closed models you cannot train. So the flywheel is an
**in-context** one: a graded, falsifiable lesson from cycle N is injected into cycle
N+1's opening question. Memory is the policy; behaviour improves as the lessons
accumulate and get pruned. The raw per-cycle traces are also kept as a clean, growing
dataset, useful later if you ever *do* control the weights, though no training is
claimed or implied here.

## Maintaining the diagram

`docs/diagram.html` is the editable source of truth. `docs/diagram.png` is the static
and reduced-motion fallback; `docs/diagram.gif` is the animated README version. Both
images must be regenerated from the HTML after a visual or wording change rather than
edited independently. Keep the GIF below 10 MB, check a first and middle frame for
disposal artifacts, and verify the HTML at desktop and mobile widths before committing.
