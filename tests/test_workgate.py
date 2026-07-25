"""The work gate: what makes a continuous heartbeat affordable.

The claim under test is economic, not cosmetic. A work-gated operative may
tick as often as you like because a tick with nothing due never reaches the
model. If that property breaks, the whole premise of a fast heartbeat breaks
with it, so it is asserted directly by counting model calls.
"""
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata import gate as G
from reverie_automata.config import Config
from reverie_automata.inbox import Inbox
from reverie_automata.runner import Runner
from reverie_automata.store import Store
from reverie_automata.workgate import assess_work


class CountingPlanner:
    """A planner that refuses to be woken quietly: every call is recorded."""

    name = "counting"
    calls = 0

    def __init__(self, options=None):
        pass

    def complete(self, system, user, *, max_tokens=1000):
        CountingPlanner.calls += 1
        return '<<PLAN>>{"learned": "", "tasks": [], "do_nothing": true, "do_nothing_reason": "x"}<<END>>'

    def run_session(self, directive, **kw):
        CountingPlanner.calls += 1
        return "<<RESULT>>done<<END>>"


def _cfg(home, **over):
    cfg = Config()
    cfg.data.update({
        "home": str(home), "trigger": "work",
        "window": {"start": 0, "end": 0}, "idle_minutes": 0,
        "min_gap_minutes": 0, "max_cycles_per_day": 99,
        "planner": {"backend": "counting"}, "agent": {"backend": "counting"},
    })
    cfg.data.update(over)
    return cfg


def _runner(cfg):
    from reverie_automata.adapters import agents
    agents.REGISTRY["counting"] = CountingPlanner
    CountingPlanner.calls = 0
    return Runner(cfg, last_input_ts=lambda: time.time() - 7200,
                  is_available=lambda: True)


# --- the eligibility question itself ------------------------------------
def test_nothing_due_is_not_eligible(tmp_path):
    store = Store(tmp_path / "s.db")
    con = store.connect()
    e = assess_work(con, store, Inbox(tmp_path / "inbox"), {})
    con.close()
    assert not e and "nothing due" in e.reason


def test_a_waiting_drop_is_work(tmp_path):
    store = Store(tmp_path / "s.db")
    box = Inbox(tmp_path / "inbox")
    box.dir.mkdir(parents=True)
    (box.dir / "a.md").write_text("do the thing")
    con = store.connect()
    e = assess_work(con, store, box, {})
    con.close()
    assert e and e.counts["inbox"] == 1


def test_open_is_not_the_same_as_due(tmp_path):
    """The distinction the gate turns on: a thread that was just attempted is
    still open, but it is not work again until its cooldown elapses. Without
    this, any deferred thread makes every cycle look busy forever."""
    store = Store(tmp_path / "s.db")
    con = store.connect()
    store.add_thread(con, "a deferred piece of work")
    tid = con.execute("SELECT id FROM threads").fetchone()[0]

    assert assess_work(con, store, None, {"thread_cooldown_minutes": 60})
    store.mark_thread_attempted(con, tid)
    assert not assess_work(con, store, None, {"thread_cooldown_minutes": 60})

    # ... and it becomes work again once the cooldown has passed
    later = time.time() + 61 * 60
    assert assess_work(con, store, None, {"thread_cooldown_minutes": 60}, now=later)
    con.close()


# --- the economic claim ---------------------------------------------------
def test_an_idle_heartbeat_costs_no_model_calls(tmp_path):
    """Twenty ticks with nothing due: the expensive part is never woken."""
    r = _runner(_cfg(tmp_path))
    for _ in range(20):
        out = r.tick()
        assert out["fired"] is False and "nothing due" in out["reason"]
    assert CountingPlanner.calls == 0


def test_a_drop_wakes_the_next_tick(tmp_path):
    r = _runner(_cfg(tmp_path))
    assert r.tick()["fired"] is False
    box = r.engine.inbox
    box.dir.mkdir(parents=True, exist_ok=True)
    (box.dir / "a.md").write_text("please look at the thing")
    assert r.tick()["fired"] is True
    assert CountingPlanner.calls >= 1


def test_work_mode_does_not_wait_for_the_principal_to_leave(tmp_path):
    """A standing operative works its queue whether or not anyone is at the
    desk; that is the difference from the companion rhythm."""
    cfg = _cfg(tmp_path)
    from reverie_automata.adapters import agents
    agents.REGISTRY["counting"] = CountingPlanner
    CountingPlanner.calls = 0
    r = Runner(cfg, last_input_ts=lambda: time.time(),      # spoke just now
               is_available=lambda: False)                   # and is right here
    r.engine.inbox.dir.mkdir(parents=True, exist_ok=True)
    (r.engine.inbox.dir / "a.md").write_text("a request that should not wait")
    assert r.tick()["fired"] is True


def test_idle_mode_is_unchanged_and_ignores_the_queue(tmp_path):
    """Backwards compatibility: the companion engine still fires on presence."""
    state = G.GateState()
    cfg = Config().data
    cfg.update({"window": {"start": 0, "end": 0}, "idle_minutes": 90,
                "min_gap_minutes": 0, "max_cycles_per_day": 99})
    now = datetime.now()
    fire, _, reason = G.decide(now, (now - timedelta(minutes=120)).timestamp(),
                               True, state, cfg, None, False)
    assert fire and "idle" in reason


def test_work_mode_without_an_assessment_refuses_to_fire(tmp_path):
    """Fail closed: a work-gated engine that was never told what is due must
    not guess that something is."""
    cfg = Config().data
    cfg.update({"trigger": "work", "window": {"start": 0, "end": 0}})
    fire, _, reason = G.decide(datetime.now(), time.time() - 7200, True,
                               G.GateState(), cfg, None, False, work=None)
    assert not fire and "no eligibility" in reason


def test_a_failing_task_cannot_spin_the_heartbeat(tmp_path):
    """Found live: a failed task files a follow-up thread, and if that thread
    is due at once the next tick fires on it, fails again, and files another,
    spinning at heartbeat speed until the daily cap. Work the engine makes for
    itself starts inside its cooldown; work from outside does not."""
    store = Store(tmp_path / "s.db")
    con = store.connect()
    store.add_thread(con, "resume failed task: something", defer=True)
    assert not assess_work(con, store, None, {"thread_cooldown_minutes": 60})

    store.add_thread(con, "a request from a person")      # defer defaults off
    assert assess_work(con, store, None, {"thread_cooldown_minutes": 60})
    con.close()


def test_two_cycles_in_one_second_do_not_collide(tmp_path):
    """A fast heartbeat fires again the moment work remains due, and a no-op
    cycle costs milliseconds, so two cycles inside one second is ordinary
    rather than exotic. Second-resolution ids must not collide."""
    r = _runner(_cfg(tmp_path))
    box = r.engine.inbox
    box.dir.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (box.dir / f"d{i}.md").write_text("please look at the thing")
        assert r.tick()["fired"] is True
    ids = sorted(p.name for p in (Path(r.cfg["home"]) / "cycles").glob("*"))
    assert len(ids) == 3 and len(set(ids)) == 3
