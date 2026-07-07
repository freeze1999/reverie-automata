"""The gate — a model-free decision made before any money is spent.

An always-on agent that thinks on every tick is expensive and annoying. So the
decision of *whether* to stir is a pure function with no model call: a working
window, an idle threshold, a fire-once-per-idle-gap arm flag, daily/gap caps, and
a budget floor. Because it's pure, every rule is unit-tested without a clock or a
network. A PID-stamped lock prevents overlap and self-heals when an owner dies.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class GateState:
    last_fired_input_ts: float = 0.0
    last_fire_at: float = 0.0
    fires: list[str] = None  # type: ignore

    def __post_init__(self):
        if self.fires is None:
            self.fires = []


def in_window(hour: int, start: int, end: int) -> bool:
    if start == end:
        return True
    return (start <= hour < end) if start < end else (hour >= start or hour < end)


def decide(now, last_input_ts, available, state, cfg, balance, killed) -> tuple[bool, bool, str]:
    """Pure. Returns (fire, text_only, reason). ``now`` is a datetime; ``cfg`` a dict."""
    if killed:
        return False, False, "kill switch present"
    if not available:
        return False, False, "principal is available / busy"
    if not in_window(now.hour, cfg["window"]["start"], cfg["window"]["end"]):
        return False, False, f"outside window ({now.hour:02d}h)"
    if last_input_ts <= 0:
        return False, False, "no input on record"
    idle = (now.timestamp() - last_input_ts) / 60.0
    if idle < cfg["idle_minutes"]:
        return False, False, f"not idle enough ({idle:.0f} < {cfg['idle_minutes']}m)"
    if last_input_ts <= state.last_fired_input_ts:
        return False, False, "already fired this idle gap"
    day = now.strftime("%Y-%m-%d")
    if sum(1 for f in state.fires if f.startswith(day)) >= cfg["max_cycles_per_day"]:
        return False, False, f"daily cap reached ({cfg['max_cycles_per_day']})"
    gap = (now.timestamp() - state.last_fire_at) / 60.0
    if gap < cfg["min_gap_minutes"]:
        return False, False, f"min gap not met ({gap:.0f} < {cfg['min_gap_minutes']}m)"
    floor = cfg["budget"]["floor"]
    if balance is not None and floor and balance < floor:
        return False, False, f"budget floor (${balance:.2f} < ${floor})"
    soft = cfg["budget"]["soft"]
    text_only = balance is not None and bool(soft) and balance < soft
    return True, text_only, f"idle {idle:.0f}m, armed" + (" [text-only: soft budget]" if text_only else "")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True


def reap_lock(lock: Path, cfg, now_ts: float | None = None) -> bool:
    """A lock is dead iff its owning PID is gone (crash / kill / manual run that
    exited). Reaped on the next tick regardless of age; a live long cycle (PID
    alive) is never reaped. An age backstop covers reused-PID / no-PID edge cases."""
    if not lock.exists():
        return False
    now_ts = now_ts or time.time()
    age_min = (now_ts - lock.stat().st_mtime) / 60.0
    raw = ""
    try:
        raw = lock.read_text().strip()
    except Exception:
        pass
    pid = int(raw) if raw.isdigit() else None
    dead = pid is not None and not _pid_alive(pid)
    one_phase = cfg["phase_timeout_minutes"] + 15
    hard = cfg["phase_timeout_minutes"] * 12
    if dead or (pid is None and age_min > one_phase) or age_min > hard:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass
        return True
    return False


def load_state(path: Path) -> GateState:
    try:
        d = json.loads(path.read_text())
        return GateState(d.get("last_fired_input_ts", 0.0), d.get("last_fire_at", 0.0), d.get("fires", []))
    except Exception:
        return GateState()


def save_state(path: Path, s: GateState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_fired_input_ts": s.last_fired_input_ts,
                                "last_fire_at": s.last_fire_at, "fires": s.fires[-50:]},
                               indent=2))
