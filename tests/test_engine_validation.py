"""Validation as the engine actually runs it, not in isolation.

The unit tests prove the rules are right. These prove the rules are WIRED:
that the engine asks its own eligibility question rather than believing the
plan, records what it objected to, and protects the request a bad plan
ignored. A rule that is correct and unreachable protects nothing.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.adapters import agents
from reverie_automata.config import Config
from reverie_automata.runner import Runner


class ScriptedPlanner:
    """Emits whatever plan the test hands it."""

    name = "scripted"
    plan = {"learned": "", "tasks": [], "do_nothing": True, "do_nothing_reason": "nothing"}

    def __init__(self, options=None):
        pass

    def complete(self, system, user, *, max_tokens=1000):
        return "<<PLAN>>" + json.dumps(ScriptedPlanner.plan) + "<<END>>"

    def run_session(self, directive, **kw):
        return "<<RESULT>>done<<END>>\n<<VERIFY>>a receipt<<END>>"


def _runner(tmp_path, plan, **over):
    agents.REGISTRY["scripted"] = ScriptedPlanner
    ScriptedPlanner.plan = plan
    cfg = Config()
    cfg.data.update({
        "home": str(tmp_path / "h"), "trigger": "work",
        "window": {"start": 0, "end": 0}, "idle_minutes": 0,
        "min_gap_minutes": 0, "max_cycles_per_day": 99,
        "planner": {"backend": "scripted"}, "agent": {"backend": "scripted"},
    })
    cfg.data.update(over)
    return Runner(cfg, last_input_ts=lambda: time.time() - 7200,
                  is_available=lambda: True)


def _drop(runner, text="please handle this"):
    box = runner.engine.inbox
    box.dir.mkdir(parents=True, exist_ok=True)
    (box.dir / "req.md").write_text(text)
    return box


def _outcome(runner):
    home = Path(runner.cfg["home"])
    latest = sorted((home / "cycles").glob("*"))[-1]
    return json.loads((latest / "outcome.json").read_text())


TASK = {"id": "t1", "what": "read the drop and answer the question in it",
        "why": "it was asked", "mode": "text", "risk": "SAFE"}


def test_a_false_no_op_is_recorded_and_the_request_survives(tmp_path):
    """The whole point: the engine knows work was pending, so it does not
    accept the model's claim that there was none, and it does not archive the
    request that claim ignored."""
    r = _runner(tmp_path, {"tasks": [], "do_nothing": True, "do_nothing_reason": "nope"})
    box = _drop(r)
    assert r.tick()["fired"] is True

    out = _outcome(r)
    assert out["false_no_op"] is True
    assert any("contradicts the eligibility check" in c for c in out["plan_complaints"])
    assert out["inbox_consumed"] == 0
    assert [p.name for p in box.pending()] == ["req.md"]


def test_the_ignored_request_is_simply_picked_up_next_tick(tmp_path):
    """Retry falls out of the gate: the drop is still pending, so the work is
    still due, so the next heartbeat takes it. No retry machinery exists."""
    r = _runner(tmp_path, {"tasks": [], "do_nothing": True})
    _drop(r)
    r.tick()
    ScriptedPlanner.plan = {"tasks": [dict(TASK)], "do_nothing": False}
    assert r.tick()["fired"] is True
    out = _outcome(r)
    assert out["false_no_op"] is False and out["inbox_consumed"] == 1
    assert r.engine.inbox.pending() == []


def test_an_honest_lazy_day_is_not_flagged(tmp_path):
    """Nothing pending, nothing claimed, no complaint. The virtue must survive
    the guard that protects it."""
    r = _runner(tmp_path, {"tasks": [], "do_nothing": True, "do_nothing_reason": "quiet"},
                trigger="idle")
    assert r.tick()["fired"] is True
    out = _outcome(r)
    assert out["false_no_op"] is False and out["plan_complaints"] == []


def test_content_free_work_never_reaches_the_ledger(tmp_path):
    r = _runner(tmp_path, {"tasks": [{"id": "t1", "what": "research"}], "do_nothing": False})
    _drop(r)
    r.tick()
    out = _outcome(r)
    assert out["ledger"] == []
    assert any("no content" in c for c in out["plan_complaints"])


def test_the_per_cycle_cap_is_enforced_by_the_engine(tmp_path):
    r = _runner(tmp_path,
                {"tasks": [dict(TASK, id=f"t{i}") for i in range(4)], "do_nothing": False},
                max_tasks_per_cycle=1)
    _drop(r)
    r.tick()
    out = _outcome(r)
    assert len(out["ledger"]) == 1
    assert any("cap is 1" in c for c in out["plan_complaints"])


def test_a_do_nothing_cycle_teaches_nothing(tmp_path):
    """Found live: an early no-op wrote "a lazy cycle ... summarise instead of
    forcing tool work ... legible and cheap", and that sentence then rode at
    the top of every context and taught the engine to decline real work. A
    defect became doctrine. Evidence gates done; it must gate learned too."""
    lesson = "<<LESSON>>idle was fine -> did nothing -> nothing broke<<END>>"

    class Teaching(ScriptedPlanner):
        def run_session(self, directive, **kw):
            return "<<RESULT>>done<<END>>\n<<VERIFY>>ok<<END>>\n" + lesson

        def complete(self, system, user, *, max_tokens=1000):
            if "<<PLAN>>" in user:
                return "<<PLAN>>" + json.dumps(ScriptedPlanner.plan) + "<<END>>"
            return lesson

    agents.REGISTRY["teaching"] = Teaching
    r = _runner(tmp_path, {"tasks": [], "do_nothing": True, "do_nothing_reason": "quiet"},
                trigger="idle", planner={"backend": "teaching"},
                agent={"backend": "teaching"})
    r.tick()

    import sqlite3
    con = sqlite3.connect(Path(r.cfg["home"]) / "state.db")
    assert con.execute("SELECT COUNT(*) FROM lessons").fetchone()[0] == 0
    con.close()
