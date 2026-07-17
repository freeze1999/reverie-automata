# Changelog

All notable changes to reverie-automata are recorded here. The format
follows Keep a Changelog; versions follow semver.

## [Unreleased]

### Fixed
- Fire-lock claim is atomic (`O_CREAT|O_EXCL`); an exists()-then-write race
  could let two concurrent ticks both fire.

### Added
- Blast radius is now actually computed: `protected_paths` are snapshotted
  around each cycle and changed, new, and deleted files land in the outcome
  (deletions flagged with a `deleted: ` prefix).
- Adapter and harvest tests for the pure parts (argv construction, output
  extraction, registry, token estimation).
- `py.typed` marker so downstream type checkers use the package's hints.
- SECURITY.md stating what the inspector does and does not guarantee.
- CI badge in the README.

## [0.1.0] - 2026-07-07

### Added
- Model-free gate: window, idle threshold, budget floor, daily and gap caps,
  fire-once-per-idle-gap arming. Pure function, tested without a clock.
- Three-phase engine (plan, execute, learn) with one session per task and
  evidence-gated "done".
- Tool-layer inspector classifying each concrete call; fail-closed approvals
  for protected-path writes, privileged commands, egress, mass deletion, and
  messaging.
- Adapters for eight agent backends, approval transports, and context
  sources; `reverie.yaml.example` config.
- Offline mock demo and a supervised Claude Code example.
- Tests for the gate, inspector, and engine store; CI on Python 3.10-3.12.
