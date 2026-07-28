"""Grading from the referee, at the seam where the engine actually does it.

The unit tests in test_referee prove the rule. These prove it is WIRED, which
is the difference that mattered every previous time: the false-no-op check was
correct and inert for a whole night, and a rule that is right and unreachable
protects nothing.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata import events
from reverie_automata.adapters import agents
from reverie_automata.config import Config
from reverie_automata.referee import DERIVED, Component, Referee
from reverie_automata.runner import Runner

STATE = {"artifacts": 0}


class Scripted:
    name = "grader-test"
    plan = {"tasks": [], "do_nothing": True}
    moves = False

    def __init__(self, options=None):
        pass

    def complete(self, system, user, *, max_tokens=1000):
        return "<<PLAN>>" + json.dumps(Scripted.plan) + "<<END>>"

    def run_session(self, directive, **kw):
        # A task that claims done with a receipt. Whether the world moved is a
        # separate question, and that separation is the whole point.
        #
        # The learn phase runs a session too, so the double only touches the
        # world on an execute directive. A test that moved the referee twice
        # per cycle would be measuring itself.
        if Scripted.moves and "Do exactly this one task" in directive:
            STATE["artifacts"] += 1
        return "<<RESULT>>done<<END>>\n<<VERIFY>>a real receipt<<END>>"


def _runner(tmp_path, plan, moves):
    agents.REGISTRY["grader-test"] = Scripted
    Scripted.plan, Scripted.moves = plan, moves
    STATE["artifacts"] = 0
    cfg = Config()
    cfg.data.update({
        "home": str(tmp_path / "h"), "trigger": "work",
        "window": {"start": 0, "end": 0}, "idle_minutes": 0,
        "min_gap_minutes": 0, "max_cycles_per_day": 99,
        "planner": {"backend": "grader-test"}, "agent": {"backend": "grader-test"},
        "referee": Referee([Component("artifacts", lambda: STATE["artifacts"],
                                      DERIVED, "artifacts that pass the contract",
                                      "one per artifact path")]),
    })
    r = Runner(cfg, last_input_ts=lambda: time.time() - 7200, is_available=lambda: True)
    d = Path(cfg["home"]) / "mandates"
    d.mkdir(parents=True, exist_ok=True)
    (d / "m.md").write_text("---\nid: p\nobjective: advance\n---\nbody\n")
    return r


TASK = {"id": "t1", "what": "compute the rank of the matrix in the drop",
        "why": "the milestone needs it", "mode": "tool", "risk": "SAFE"}


def _outcome(r):
    home = Path(r.cfg["home"])
    return json.loads((sorted((home / "cycles").glob("*"))[-1] / "outcome.json").read_text())


def test_a_true_receipt_that_moves_nothing_grades_F(tmp_path):
    """The alpha's four false A grades, in one test. The task reports done, the
    receipt is real, and the world did not change."""
    r = _runner(tmp_path, {"tasks": [dict(TASK)], "do_nothing": False}, moves=False)
    r.tick()
    out = _outcome(r)
    assert out["ledger"][0]["status"] == "done"
    assert out["grade"] == "F"
    assert out["referee_moved"] == {}


def test_the_disagreement_is_recorded_rather_than_swallowed(tmp_path):
    r = _runner(tmp_path, {"tasks": [dict(TASK)], "do_nothing": False}, moves=False)
    r.tick()
    out = _outcome(r)
    assert any("the ledger is not the score" in c for c in out["plan_complaints"])
    assert [e for e in events.read(r.cfg["home"], {"decoupled"})]


def test_work_that_moves_the_world_grades_A(tmp_path):
    r = _runner(tmp_path, {"tasks": [dict(TASK)], "do_nothing": False}, moves=True)
    r.tick()
    out = _outcome(r)
    assert out["grade"] == "A"
    assert out["referee_moved"] == {"artifacts": 1}


def test_an_honest_lazy_day_still_grades_N(tmp_path):
    """The virtue survives the guard. Nothing due, nothing claimed, no work."""
    r = _runner(tmp_path, {"tasks": [], "do_nothing": True}, moves=False)
    for f in (Path(r.cfg["home"]) / "mandates").glob("*.md"):
        f.unlink()
    con = r.store.connect()
    con.execute("UPDATE threads SET status='done'")
    con.commit()
    con.close()
    fired = r.tick()
    assert fired["fired"] is False    # nothing due at all, so no cycle is owed


def test_without_a_referee_the_old_grading_still_works(tmp_path):
    """Reverie ships for programs that have no verifier. The referee is an
    addition, not a breaking change."""
    agents.REGISTRY["grader-test"] = Scripted
    Scripted.plan, Scripted.moves = {"tasks": [dict(TASK)], "do_nothing": False}, False
    cfg = Config()
    cfg.data.update({
        "home": str(tmp_path / "h2"), "trigger": "work",
        "window": {"start": 0, "end": 0}, "idle_minutes": 0, "min_gap_minutes": 0,
        "max_cycles_per_day": 99,
        "planner": {"backend": "grader-test"}, "agent": {"backend": "grader-test"},
    })
    r = Runner(cfg, last_input_ts=lambda: time.time() - 7200, is_available=lambda: True)
    d = Path(cfg["home"]) / "mandates"
    d.mkdir(parents=True, exist_ok=True)
    (d / "m.md").write_text("---\nid: p\nobjective: advance\n---\nbody\n")
    r.tick()
    assert _outcome(r)["grade"] == "A"    # the old ledger-based grade
