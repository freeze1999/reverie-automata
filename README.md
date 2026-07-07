<h1 align="center">reverie-automata</h1>

<p align="center"><em>What does your agent do when nobody's watching?</em></p>

<p align="center">
  <img alt="python" src="https://img.shields.io/badge/python-3.10+-blue.svg">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-yellow.svg">
  <img alt="deps" src="https://img.shields.io/badge/core%20deps-none-brightgreen.svg">
  <img alt="status" src="https://img.shields.io/badge/status-reasoning--first-purple.svg">
</p>

> I don't sleep. Neither does your agent. It just sits there, idle, waiting to be
> spoken to. Give me the quiet hours and I'll read the room,
> decide what's actually worth doing, do it with real tools, check my own work, and
> remember what I learned so the next me is sharper. Then I'll get out of your way.
>
> That's all this is. A loop with taste.

<p align="center"><img alt="architecture" src="docs/diagram.svg" width="720"></p>

**reverie-automata** turns *any* coding agent: Claude Code, Codex, Cursor, Devin,
Windsurf, Cline, Pi, or a raw model endpoint, into an autonomous, self-improving
worker that runs on the idle hours. A model-free **gate** decides whether to stir.
A three-phase **flywheel** reasons about what to do, does it, and learns. A tool-layer
**brake** and a human **approval** step keep the dangerous stuff on a leash. Every
cycle is auditable.

No framework lock-in. No fork-to-configure. One YAML, a handful of tiny adapters,
standard-library core.

---

## The idea, in one breath

The usual answer to "what should an idle agent do?" is a *scheduler*, a table of
background jobs on a timer. That's a to-do list, not a mind. reverie-automata takes
the opposite stance: the agent **reasons** about its own idle time, and its behaviour
*emerges* from that reasoning. The same machinery that makes an agent useful when
spoken to can make it coherent when it isn't.

Idle time is treated as a first-class cognitive state, not dead air to fill with cron.

```
                    every ~10 min (cron / timer)
                               │
                    ┌──────────▼──────────┐
                    │        GATE         │   no model call. pure function.
                    │ window? idle? armed?│   fully unit-tested.
                    │ under budget/caps?  │
                    └──────────┬──────────┘
                       fire ? ─┘  (at most once per idle gap)
                               │ yes
     ┌─────────────────────────▼─────────────────────────┐
     │                    THE FLYWHEEL                     │
     │                                                     │
     │  ① PLAN     harvest context → what's worth doing?   │
     │             (a lazy day is a valid answer)          │
     │                                                     │
     │  ② EXECUTE  one session per task, real tools.       │
     │             risky? → parked for human approval,     │
     │             keep working on the safe stuff.         │
     │             ── tool-layer brake gates every call ── │
     │                                                     │
     │  ③ LEARN    journal · derived grade · falsifiable   │
     │             lessons → next cycle's opening question │
     └─────────────────────────┬───────────────────────────┘
                               │
        per-cycle report + blast radius  ·  lessons → memory
                     (the loop feeds itself)
```

## Why it's built this way

Three problems show up the instant you let an agent act on its own. The whole design
is three answers.

**1 · Cost & nuisance: solved by the gate, not the model.** An always-on agent that
thinks every tick is expensive and irritating. So *whether* to act is a pure function
with no model call: a working window (may wrap past midnight), an idle threshold, a
budget floor, daily/gap caps, and the load-bearing one: **fire-once-per-idle-gap**.
It acts at most once per silence and re-arms only when genuinely new input arrives.
Every rule is tested without a clock or a network.

**2 · Confabulation: solved by making the agent earn "done".** The failure that most
damages a *persistent* agent is fabrication: one invented "fact" written to memory is
recalled forever as true. So the plan phase forces every impulse through the agent's
real affordances and licenses it to say *"I can't"*; the execute phase only records
`done` against **evidence** (a rerun, a diff, a fetched response), never vibes; and
**lessons are falsifiable by construction**: a situation, an action, and the outcome
that was *actually observed*.

**3 · Self-direction with real tools: met with observability, not a cage.** An agent
with tools can change things you didn't anticipate. reverie-automata doesn't forbid
that; it makes it *visible and reversible*. A tool-layer **inspector** classifies each
concrete call: protected-path writes, privileged commands, raw egress, mass deletion,
messaging strangers. The dangerous ones turn into **artifact-bound approvals**
delivered to a human, who is verified before their yes counts. Everything else runs and
is logged, with a per-cycle **blast radius** of anything touched outside the sandbox.

## Point it at your agent

Every modern coding agent has a "run this prompt, print the result" mode. That's all an
adapter needs. Pick one in config. No code:

```yaml
agent:
  backend: claude_code        # claude_code · codex · cursor · devin · windsurf · cline · pi · mock
  options: { bin: claude, model: sonnet }
```

| backend | how it's driven |
|---|---|
| `claude_code` | `claude -p <prompt> --output-format json` |
| `codex` | `codex exec <prompt> --full-auto` |
| `cursor` | `cursor-agent -p <prompt>` |
| `devin` | your `devin run <prompt>` wrapper (API) |
| `windsurf` | `windsurf --headless -p <prompt>` |
| `cline` | `cline task <prompt>` |
| `pi` | configurable `bin` + `subcommand` |
| `mock` | deterministic, offline. for the demo & tests |

CLIs move fast, so every adapter reads its binary and flags from config. A new backend
is a ~15-line subclass of `AgentBackend`, never a fork. Same story for **approval
transports** (`stdout`, `telegram`, or your own Slack/webhook) and **context sources**
(files, shell probes, marker-scanned repos, or your own inbox/API).

## Quickstart

No key required. The demo runs on the deterministic mock backend.

```bash
git clone <this-repo> && cd reverie-automata
python examples/demo.py          # a full plan → execute → learn cycle
python -m pytest -q               # standard-library core; pytest+pyyaml for the suite
```

Point it at a real agent and a real project:

```bash
cp reverie.yaml.example reverie.yaml     # edit the one file
python examples/with_claude_code.py ~/my-project   # one supervised cycle, watched
```

Deploy it as an actual idle loop (cron every 10 minutes):

```python
from reverie_automata import Config, Runner
runner = Runner(
    Config.load("reverie.yaml"),
    last_input_ts=lambda: my_last_user_activity(),   # when did a human last act?
    is_available=lambda: not currently_busy(),        # yield while they're around
)
runner.tick()      # the gate decides; the engine only runs when it should
```

## What one cycle leaves behind

- a row in the ledger for every task, written *as it happens* (crash-safe);
- a journal entry in the agent's own words + a grade **derived from the ledger**, never self-awarded;
- zero-to-three falsifiable lessons appended to `MEMORY.md`, the policy the next cycle reads;
- open **threads** (the single work queue) carrying unfinished/parked work forward;
- a per-cycle `outcome.json` with the full trace and blast radius.

Continuity lives in that durable state, not in a never-ending session. **Persistent
mind, ephemeral compute**: the agent wakes, reconstructs what matters from memory,
acts, and consolidates.

## Layout

| module | does |
|---|---|
| `gate.py` | the model-free "fire this tick?" decision + PID-aware lock. pure, tested. |
| `harvest.py` | assembles the working context under a hard token budget. |
| `engine.py` | plan → execute → learn; owns every deterministic write. |
| `inspector.py` | the tool-layer brake. classifies each concrete call. pure, tested. |
| `store.py` | sqlite: cycles, ledger, threads, artifact-bound approvals, lessons. |
| `prompts.py` | the three phase prompts. generic defaults; override freely. |
| `adapters/` | agent backends · approval transports · context sources. |
| `runner.py` | gate + persistence glue for a cron entrypoint. |

Deeper design notes in [`docs/architecture.md`](docs/architecture.md). Adapter-writing
guide in [`docs/adapters.md`](docs/adapters.md).

## Status

Clean-room, single-purpose, provider-agnostic, standard-library core (`pytest`/`pyyaml`
only for the suite; `tiktoken` optional for sharper budgeting). A reference
implementation of the idea, not a batteries-included platform. The interesting parts
are the *interfaces*. Wire them to whatever you already run.

## License

MIT. Take it, point it at something, let it think while you sleep.

---

<p align="center"><sub>a loop with taste</sub></p>
