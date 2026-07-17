"""Runner — the cron entrypoint that glues the gate to the engine.

You wire two callbacks — "when did the principal last act?" and "is the principal
available?" — and schedule ``Runner.tick()`` on a timer (cron every ~10 min). The
gate decides; the engine only runs when it should. A PID-stamped lock prevents
overlap and self-heals if an owner dies.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import gate as G
from .config import Config
from .engine import Engine
from .harvest import Harvester
from .inspector import Inspector
from .store import Store
from .adapters.agents import build_agent
from .adapters.transports import build_transport


def claim_lock(lock: Path) -> bool:
    """Atomically claim the fire lock, stamping this PID for ``gate.reap_lock``.

    Create-if-absent must be ONE operation (O_CREAT|O_EXCL): a separate
    exists()-then-write leaves a window where two ticks both see no lock and
    both fire. The OS guarantees exactly one winner; the loser returns False.
    """
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))
    return True


class Runner:
    def __init__(self, cfg: Config, *, last_input_ts: Callable[[], float],
                 is_available: Callable[[], bool] = lambda: True,
                 balance: Callable[[], Optional[float]] = lambda: None):
        self.cfg = cfg
        self.last_input_ts = last_input_ts
        self.is_available = is_available
        self.balance = balance
        self.home = cfg.home
        self.home.mkdir(parents=True, exist_ok=True)
        self.state_file = self.home / "gate_state.json"
        self.lock = self.home / ".fire.lock"
        self.kill = self.home / "KILL"
        self.store = Store(self.home / "state.db")
        self.engine = Engine(
            cfg, self.store,
            Harvester(cfg, self.store, self.home / "MEMORY.md"),
            Inspector(cfg),
            build_agent(cfg["agent"]),
            build_agent(cfg["planner"]),
            build_transport(cfg["approval"]),
        )

    def tick(self) -> Optional[dict]:
        now = datetime.now()
        G.reap_lock(self.lock, self.cfg)
        state = G.load_state(self.state_file)
        fire, text_only, reason = G.decide(now, self.last_input_ts(), self.is_available(),
                                           state, self.cfg, self.balance(), self.kill.exists())
        if not fire:
            return {"fired": False, "reason": reason}
        if not claim_lock(self.lock):
            return {"fired": False, "reason": "another cycle holds the lock"}
        try:
            state.last_fired_input_ts = self.last_input_ts()
            state.last_fire_at = now.timestamp()
            state.fires.append(now.strftime("%Y-%m-%d-%H%M"))
            G.save_state(self.state_file, state)  # consume the gap BEFORE running: a crash can't re-fire
            outcome = self.engine.run_cycle(now=now, text_only=text_only)
            return {"fired": True, "grade": outcome.grade, "ledger": len(outcome.ledger)}
        finally:
            self.lock.unlink(missing_ok=True)
