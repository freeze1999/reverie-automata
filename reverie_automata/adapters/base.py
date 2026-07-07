"""The three extension points. Everything provider-specific lives behind one of these.

reverie-automata is deliberately not tied to a model, an agent runtime, a chat
app, or a data source. The core reasons; these interfaces let it *act*.

* ``AgentBackend``  — runs a phase as a real tool-using session (phase 2/3), or a
                      cheap text completion (phase 1 / text-only). This is where
                      Claude Code, Codex, Cursor, Devin, Windsurf, Cline, Pi, or a
                      raw OpenAI-compatible endpoint plug in.
* ``ApprovalTransport`` — carries a risky action out to a human and brings back
                      approve/deny (Telegram, Slack, email, a CLI prompt, a webhook).
* ``Source``        — contributes one block of harvested context each cycle
                      (a file, a shell probe, a log query, an inbox, an API).

Keep them tiny. A new integration is a subclass, never a fork.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class AgentBackend(Protocol):
    """Runs the agent's reasoning. Two calls, deliberately minimal."""

    name: str

    def complete(self, system: str, user: str, *, max_tokens: int = 1000) -> str:
        """A single non-tool completion (phase 1 planning, text-only phase 3)."""

    def run_session(self, directive: str, *, cwd: str = "", env: dict[str, str] | None = None,
                    turn_cap: int = 40, timeout_s: int = 2700) -> str:
        """A real tool-using agent session (phase 2/3 execution). Returns the final
        text. The engine wraps ``env`` with an inspection marker so the tool-layer
        guard is active only for cycle sessions."""


@runtime_checkable
class ApprovalTransport(Protocol):
    """Delivers a risky action to a human and returns their decision events."""

    def send(self, approval_id: int, title: str, action: str, reasoning: str) -> Optional[str]:
        """Present the approval. Return an opaque message ref (or None). The action
        is shown verbatim; reasoning is labelled untrusted."""

    def poll(self) -> list["ApprovalEvent"]:
        """Return newly-arrived decisions since the last poll (may be empty)."""


class ApprovalEvent:
    """One human decision. ``event_id`` must monotonically increase per transport so
    the store can dedupe replays."""

    def __init__(self, approval_id: int, decision: str, event_id: int, actor: str = ""):
        self.approval_id = approval_id
        self.decision = decision      # "approve" | "deny"
        self.event_id = event_id
        self.actor = actor            # verified identity of who decided


@runtime_checkable
class Source(Protocol):
    """Contributes one labelled block of context. Must never raise — degrade to a
    short '?' so a broken source can't crash a cycle."""

    label: str
    priority: int   # lower = trimmed later when the harvest is over budget

    def collect(self, ctx: dict[str, Any]) -> str: ...
