<h1 align="center">✦ reverie-automata ✦</h1>

<p align="center">
  <em>Let your coding agent do one useful thing while you sleep. ♡</em>
</p>

<p align="center">
  <img src="https://img.shields.io/github/actions/workflow/status/freeze1999/reverie-automata/ci.yml?style=flat-square&color=111111&label=ci" alt="CI">
  <img src="https://img.shields.io/badge/python-3.10+-111111?style=flat-square" alt="Python 3.10+">
  <img src="https://img.shields.io/github/v/tag/freeze1999/reverie-automata?style=flat-square&color=111111&label=release" alt="Release">
  <img src="https://img.shields.io/github/stars/freeze1999/reverie-automata?style=flat-square&color=111111&label=stars" alt="Stars">
  <img src="https://img.shields.io/badge/works%20with-7%20agents-111111?style=flat-square" alt="Works with 7 agents">
  <img src="https://img.shields.io/badge/core%20deps-none-111111?style=flat-square" alt="Zero core dependencies">
  <img src="https://img.shields.io/badge/license-MIT-111111?style=flat-square" alt="MIT license">
</p>

<p align="center">
  <picture>
    <source media="(prefers-reduced-motion: no-preference)" srcset="docs/diagram.gif">
    <img src="docs/diagram.png" width="920" alt="A model-free gate wakes the agent for idle time, due work, or both. A context queue flows through look, do, check, action-level safety, and durable state.">
  </picture>
</p>

Your coding agent usually waits for you to type something.

reverie-automata gives it a small bedtime routine. It wakes when your rules allow it. Then it looks at the project, picks one task, checks its work, and leaves you a note.

If there is nothing useful to do (trust it, it will judge if things are worth doing), it produces nothing. If a task looks risky, it waits for you. Like your lazy intern.

## how it works

```text
wait → gate → look → do → check → remember
```

Each cycle has three parts:

1. **Look:** Read the context queue and choose useful work (or an honest nothing).
2. **Do:** Give each task one session with real tools.
3. **Check:** Ask for evidence, record what happened, and leave lessons for next time.

A small model-free gate runs first. It can wake for an idle gap, due work, or both. It also checks the allowed window, budget, cooldown, daily limit, and kill switch before spending a model call.

## try the demo

You need Python 3.10 or newer. The demo is local and does not need an API key.

```bash
git clone https://github.com/freeze1999/reverie-automata
cd reverie-automata
python3 examples/demo.py
```

The demo runs one complete cycle with a pretend agent. You can watch each step and read its notes.

## try a real agent

The Claude Code example runs one supervised cycle. Install and sign in to Claude Code first.

```bash
python3 examples/with_claude_code.py ~/my-project
```

Built-in adapters support Claude Code, Codex, Cursor, Devin, Windsurf, Cline, Pi, and local models. Copy `reverie.yaml.example` when you are ready to change the schedule or safety rules. See [the adapter guide](docs/adapters.md) for setup.

## what you get in the morning

- A list of every task and its result
- A short journal from the cycle
- Lessons for the next run
- A record of files changed outside the sandbox
- Pending approval records for risky work

The agent may only mark a task as done when it has evidence. Evidence can be a test result, diff, or fetched response.

## safety

- The gate limits when and how often a cycle can run.
- Risky tasks are parked as pending approval records while safe work continues.
- The pure inspector can sit in a backend's pre-tool hook and check concrete calls before they run.
- With that hook connected, protected writes, deletion, privileged commands, raw uploads, and unverified messages can be blocked.
- Each cycle writes a ledger as it works, so a crash still leaves a useful record.

The field report [When the safety layer became the work](docs/field-reports/2026-08-approval-classifier.md)
documents how a running Reverie Automata instance found, reproduced, and tested
an approval-classifier false-positive loop, including the complete public
timeline and aggregate results from 89 approval records.

Start with the demo. Read the output. Point it at a test project before you trust it with anything important.

## status

This is a reference project for developers. It is not a hosted service or a one-click app.

Think of it as a cute janitor. It cleans up or patches your stuff when you are idle. Idle time is only the default. Customize it, reverse engineer it, and tinker with it. The loop can live wherever it is useful.

The core uses the Python standard library. Tests need `pytest` and `pyyaml`.

For the full design, read [the architecture notes](docs/architecture.md). For agent setup, read [the adapter guide](docs/adapters.md).

## license

MIT. Give your agent a quiet little job, then ask for receipts in the morning. ♡

<p align="center"><sub>when the machine gets bored, it makes something · or nothing</sub></p>
