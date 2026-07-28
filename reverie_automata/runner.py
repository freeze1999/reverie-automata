"""Runner — the cron entrypoint that glues the gate to the engine.

You wire two callbacks — "when did the principal last act?" and "is the principal
available?" — and schedule ``Runner.tick()`` on a timer (cron every ~10 min). The
gate decides; the engine only runs when it should. A PID-stamped lock prevents
overlap and self-heals if an owner dies.
"""
from __future__ import annotations

import os
import socket
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import events
from . import gate as G
from . import mandate as M
from .config import Config
from .engine import Engine
from .harvest import Harvester
from .inspector import Inspector
from .store import Store
from .workgate import assess_work
from .adapters.agents import build_agent
from .adapters.delegates import build_delegate
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
        # pid AND host: a lock that travels with a copied instance must not be
        # mistaken for a live cycle on the machine it landed on.
        f.write(f"{os.getpid()} {socket.gethostname()}")
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
        self.delegate = build_delegate(cfg.get("delegation"))
        self.mandates = self.home / str(cfg.get("mandates_dir", "mandates"))
        self.engine = Engine(
            cfg, self.store,
            Harvester(cfg, self.store, self.home / "MEMORY.md"),
            Inspector(cfg),
            build_agent(cfg["agent"]),
            build_agent(cfg["planner"]),
            build_transport(cfg["approval"]),
            delegate=self.delegate,
        )

    def preflight(self) -> list[str]:
        """Everything wrong with this instance before it is allowed to run.

        The referee's audit and the menu's reachability check, together. Both
        exist because a configuration can be internally consistent and still
        unable to produce anything: a component nothing outside validates, or
        a task type no tool can complete. Both were found by running, and both
        are cheap to ask at startup.
        """
        problems = []
        ref = self.cfg.get("referee")
        if ref is not None:
            problems += [f"referee: {p}" for p in ref.audit()]
        menu = self.cfg.get("menu")
        if menu is not None:
            tools = (self.cfg.get("agent", {}).get("options", {}) or {}).get("tools") or {}
            problems += [f"menu: {p}" for p in menu.unreachable(tools)]
        return problems

    # -- housekeeping, outside the cycle -------------------------------------
    def collect(self) -> list[str]:
        """Take in whatever the delegate answered, and close what it closed.

        Deliberately not a tool the agent can call. A result nobody collected
        is a job that silently never finished, and the one thing you cannot
        trust to remember a chore is the thing that had a bad night.
        """
        notes = []
        try:
            for res in self.delegate.collect():
                notes.append(str(res.get("note", res))[:200])
                events.emit(self.home, "collect", **{
                    k: v for k, v in res.items() if k != "raw"})
        except Exception as e:  # noqa: BLE001
            events.emit(self.home, "collect_error", error=f"{type(e).__name__}: {e}")
        return notes

    def standing_orders(self) -> list[str]:
        """Refile any standing objective that has no open thread. This is what
        keeps a work-gated engine from running its queue dry and stopping
        forever, and it happens before the gate is asked anything."""
        con = self.store.connect()
        try:
            filed = M.refresh(con, self.store, self.mandates,
                              state_path=self.home / "mandate_state.json")
        except Exception as e:  # noqa: BLE001
            events.emit(self.home, "mandate_error", error=f"{type(e).__name__}: {e}")
            filed = []
        finally:
            con.close()
        for t in filed:
            events.emit(self.home, "mandate", title=t)
        return filed

    def tick(self) -> Optional[dict]:
        now = datetime.now()
        G.reap_lock(self.lock, self.cfg)
        state = G.load_state(self.state_file)
        self.collect()
        self.standing_orders()

        # The cheap question first: one indexed query and a directory scan.
        # A heartbeat tick with nothing due stops here, having spent no model
        # call, which is what lets the heartbeat run as fast as you please.
        work = None
        if str(self.cfg.get("trigger", "idle")).lower() in ("work", "both"):
            con = self.store.connect()
            try:
                work = assess_work(con, self.store, self.engine.inbox, self.cfg,
                                   now.timestamp())
            finally:
                con.close()

        fire, text_only, reason = G.decide(now, self.last_input_ts(), self.is_available(),
                                           state, self.cfg, self.balance(),
                                           self.kill.exists(), work=work)
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
