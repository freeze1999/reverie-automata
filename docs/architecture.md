# Architecture

reverie-automata is a small idea with a few load-bearing details. This is the
longer version of the README's diagram.

## The gate is a pure function

`gate.decide()` takes a timestamp, the last-input timestamp, an availability flag,
the gate state, config, a balance, and a kill flag, and returns `(fire, text_only,
reason)`. No model call, no I/O, no side effects. That's deliberate: the decision of
*whether* to spend money is the one place you cannot afford flakiness, so it is a pure
function with exhaustive unit tests and no clock or network in sight.

The load-bearing rule is **fire-once-per-idle-gap**. The gate is "armed" only when the
latest input is newer than the input present at its last fire. Its own cycles never
re-arm it. So: a human speaks, the gate arms, 60 min of silence pass, one cycle runs,
then silence until they speak again. Four idle hours is one cycle, not four.

The lock is **PID-stamped**. A cycle writes its PID into `.fire.lock`. The reaper
treats a lock as dead the instant its owning process is gone (a crash, a kill, a manual
run that exited), reaped on the next tick rather than after a fixed timeout, while a
genuinely long-running cycle (PID alive) is never reaped no matter how long it takes.
An age backstop covers reused-PID and legacy no-PID locks.

## The flywheel owns the writes, the agent owns the reasoning

Each phase is one call to a backend. The **engine** parses the structured envelope the
agent returns and performs every durable write itself, so a hallucinated file path or a
malformed line can never escape: the model never holds the file handle.

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

Plan-level risk labels are UX; a determined or confused agent can talk around them.
The real brake is `inspector.classify()`, which runs on each **concrete tool call**:
resolved-path writes to protected locations, privileged shell commands, raw network
egress to non-allowlisted hosts, mass deletion, and messages to unverified recipients
all become blocks. A block is filed as an **artifact-bound approval**, tied to the exact
diff or command rather than a vague intent, and delivered to a human whose identity is
verified before their decision counts. Everything else runs and is logged.

The inspector is pure classification, so you can wire it into whatever pre-tool hook
your agent backend exposes and unit-test it in isolation. It activates only for cycle
sessions (the engine sets a `REVERIE_CYCLE` marker in the session environment), so it
never interferes with the agent's normal interactive use.

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
