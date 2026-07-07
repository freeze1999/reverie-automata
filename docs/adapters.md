# Writing adapters

Everything provider-specific lives behind one of three tiny interfaces in
`reverie_automata/adapters/base.py`. A new integration is a subclass, registered in a
dict. Never a fork.

## Agent backend

Runs the agent's reasoning. Two methods:

```python
class MyAgent:
    name = "myagent"
    def __init__(self, options=None): self.opt = dict(options or {})
    def complete(self, system, user, *, max_tokens=1000) -> str:
        # a single non-tool completion (planning / text-only)
        ...
    def run_session(self, directive, *, cwd="", env=None, turn_cap=40, timeout_s=2700) -> str:
        # a real tool-using session (execute / learn). return the final text.
        ...
```

Most coding agents are a CLI with a non-interactive mode, so subclass `_CliAgent` in
`agents.py` and just declare `_argv()` (and `_extract()` if the output is JSON). Add
your class to `REGISTRY`. Then in config:

```yaml
agent: { backend: myagent, options: { bin: myagent, model: fast } }
```

## Approval transport

Carries a risky action to a human and brings back their decision. Two methods:
`send(approval_id, title, action, reasoning) -> ref` and `poll() -> [ApprovalEvent]`.

Contract:
- show the **action verbatim** (never truncate the thing that will run);
- label the model-authored reasoning **untrusted**;
- in `poll()`, **verify who decided**: only surface events from an authorized actor;
- give each event a **monotonic `event_id`** so replays are rejected by the store.

`stdout` (offline, file-backed) and `telegram` (inline buttons, verified sender) ship
as worked examples. Slack, email, and webhook transports are the same shape.

## Context source

Contributes one labelled block to the harvest. One method, `collect(ctx) -> str`, plus
a `label` and a `priority` (higher number = trimmed first when over budget). **Never
raise**: degrade to `"?"`.

```python
class InboxSource:
    label = "inbox"; priority = 3
    def collect(self, ctx):
        try:    return summarise_unread()
        except Exception: return "?"
```

Built-ins: `file` (inject files/globs), `shell` (read-only probes), `markers` (index
recently-changed or TODO/WIP-marked files under a root). Configure any number in
`sources:`; each entry is `{type, label, priority, ...type-specific...}`.
