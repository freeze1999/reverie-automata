"""Configuration — one YAML, every knob.

The whole point of reverie-automata is that behaviour is *configured*, not
forked. ``Config.load`` reads a YAML file, overlays it on the defaults, and hands
back a plain object every other module reads. Unknown keys are preserved (so your
own adapters can read their own settings from the same file).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    # where all state lives (db, memory, cycle artifacts). One root, no hardcoded paths.
    "home": "~/.reverie-automata",

    # --- the gate: when may a cycle run at all? ---
    "window": {"start": 0, "end": 24},   # local hours [start, end); wraps past midnight
    "idle_minutes": 60,                    # silence before the agent may stir
    "max_cycles_per_day": 3,
    "min_gap_minutes": 90,
    "phase_timeout_minutes": 45,

    # --- budget: heavy by design, but bounded ---
    "budget": {"floor": 0.0, "soft": 0.0, "max_per_cycle": 1.0},
    "max_tool_turns": {"plan": 25, "execute": 40, "learn": 15},

    # --- what the agent gets to see each cycle (harvest) ---
    "sources": [],                         # list of source specs; see adapters/sources
    "harvest_max_tokens": 10000,

    # --- memory / learning ---
    "memory_max_lines": 150,               # lessons file cap; over it, a prune task is forced
    "retention_days": 60,

    # --- safety ---
    "protected_paths": [],                 # resolved-prefix write deny-list
    "egress_allowlist": [],                # domains raw network egress may reach
    "allowed_recipients": [],              # ids the agent may message without approval

    # --- backends (pluggable; see adapters/) ---
    "agent": {"backend": "mock", "options": {}},       # who runs phase 2/3 sessions
    "planner": {"backend": "mock", "options": {}},     # who runs phase 1 + text-only
    "approval": {"transport": "stdout", "options": {}},
}


@dataclass
class Config:
    data: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Config":
        merged = _deep_merge(dict(DEFAULTS), _read_yaml(path) if path else {})
        return cls(merged)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def home(self) -> Path:
        return Path(os.path.expanduser(self.data["home"])).resolve()

    def in_window(self, hour: int) -> bool:
        s, e = self.data["window"]["start"], self.data["window"]["end"]
        if s == e:
            return True
        return (s <= hour < e) if s < e else (hour >= s or hour < e)


def _read_yaml(path: str | os.PathLike) -> dict[str, Any]:
    try:
        import yaml
        return yaml.safe_load(Path(os.path.expanduser(path)).read_text()) or {}
    except Exception:
        return {}


def _deep_merge(base: dict, over: dict) -> dict:
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = _deep_merge(dict(base[k]), v)
        else:
            base[k] = v
    return base
