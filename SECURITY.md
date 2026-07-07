# Security

## What the inspector guarantees, and what it does not

The safety boundary in reverie-automata is the tool-layer inspector: every
concrete call an agent session makes is classified, and the risky classes
(protected-path writes, privileged commands, raw egress, mass deletion,
messaging strangers) fail closed into approvals bound to the exact diff or
command, delivered to a verified human. Everything else runs and is logged
with a per-cycle blast radius.

Because the inspector is the boundary, the agent adapters run their CLIs
permissively by default (for example the Claude Code adapter passes
`--dangerously-skip-permissions` in session mode, configurable per backend
in `agent.options`). If you run the engine WITHOUT the inspector layer, or
point it at an agent whose tool calls the inspector cannot see, you have no
rail: configure the backend's own permission system instead.

The inspector classifies what it observes. It does not sandbox the process,
it cannot see side effects of a call it was told was safe, and it trusts the
transport that delivers approvals to actually reach the human named in
config. Approval identity is verified before a yes counts; protect that
channel like a credential.

## Reporting

Open a GitHub issue for anything that is safe to discuss publicly. For
anything exploitable, use GitHub's private vulnerability reporting on this
repository instead of a public issue.
