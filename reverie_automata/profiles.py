"""Named configuration bundles.

A profile is the difference between "here are some settings that worked for
me" and something another operator can apply without reading a worked
example. Each one below is a config overlay: merge it over your own config,
and it sets the handful of values that have to move together for a given kind
of brain.

`small_local` exists because a small locally-served model is not a big model
with fewer parameters; it is a different animal with a different failure
profile, and the settings that make it usable are not obvious individually:

- output is constrained by a grammar rather than trusted, because such a
  model understands the task and then fails at hand-serializing JSON;
- deliberation is off, because on a small window a thinking pass can consume
  the whole budget before it ever reaches the envelope;
- the spine is sized from the window the SERVER reports, never from the
  number in a config file, because the server splits its context across slots
  and shrinks silently past its fitting limit;
- one task per cycle, because a small brain that proposes five has usually
  lost the thread rather than found four more.
"""
from __future__ import annotations

from typing import Any

# The plan envelope expressed as a schema. Given this, malformed structure is
# impossible rather than unlikely, which removes the entire failure class a
# small brain actually has.
#
# The length caps are load-bearing, not tidiness. Observed live: the model
# wrote a rambling risk_reason, ran out of budget before closing the envelope,
# and the truncated json parsed as nothing, which the engine correctly read as
# "no plan" and the validator then flagged as a false no-op. Two cycles were
# lost to a sentence that had no value at any length. A cap is the same trick
# as the grammar itself: make the failure impossible rather than discourage it.
PLAN_SCHEMA: dict[str, Any] = {
    "name": "plan",
    "schema": {
        "type": "object",
        "properties": {
            "learned": {"type": "string", "maxLength": 400},
            "tasks": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "maxLength": 8},
                        "what": {"type": "string", "maxLength": 240},
                        "why": {"type": "string", "maxLength": 300},
                        "evidence": {"type": "string", "maxLength": 240},
                        "mode": {"enum": ["tool", "text", "delegate"]},
                        "risk": {"enum": ["SAFE", "RISKY"]},
                        "risk_reason": {"type": "string", "maxLength": 200},
                    },
                    "required": ["id", "what", "why", "mode", "risk"],
                },
            },
            "do_nothing": {"type": "boolean"},
            "do_nothing_reason": {"type": "string", "maxLength": 300},
        },
        "required": ["learned", "tasks", "do_nothing"],
    },
}


def small_local(base_url: str, *, model: str = "local", n_ctx: int | None = None,
                spine_fraction: float = 0.5, **over: Any) -> dict[str, Any]:
    """Config overlay for a small brain on a local OpenAI-shaped server.

    `n_ctx` is read from the server when not supplied. If the server cannot be
    reached the spine falls back to a deliberately small budget rather than a
    hopeful one: under-filling a window wastes a little context, over-filling
    it truncates the agent's own memory without saying so.
    """
    from .adapters.local_server import LocalServer

    if n_ctx is None:
        n_ctx = LocalServer({"base_url": base_url}).server_context()
    spine = max(1200, int((n_ctx or 4096) * spine_fraction))

    cfg: dict[str, Any] = {
        "harvest_max_tokens": spine,
        "max_tasks_per_cycle": 1,
        "thread_cooldown_minutes": 60,
        # No ungrounded work. A brain this size, asked to produce from memory,
        # writes confident fiction; asked to produce from a tool result, it
        # reports what the tool said. The difference is not its honesty, it is
        # whether anything was holding the other end.
        "allow_text_tasks": False,
        "planner": {"backend": "local_server", "options": {
            "base_url": base_url, "model": model, "thinking": False,
            "schema": PLAN_SCHEMA, "schema_marker": "<<PLAN>>"}},
    }
    cfg.update(over)
    return cfg


def standing_operative(**over: Any) -> dict[str, Any]:
    """Config overlay for continuous operation: wake on work, not on absence.

    The heartbeat interval is the scheduler's business, not this file's. What
    this sets is the arming rule that makes a fast heartbeat affordable, plus
    a cooldown so a thread that was just attempted does not re-arm instantly.
    """
    cfg: dict[str, Any] = {
        "trigger": "work",
        "thread_cooldown_minutes": 60,
        "idle_minutes": 0,
    }
    cfg.update(over)
    return cfg
