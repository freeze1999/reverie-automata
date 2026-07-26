"""The refusal livelock, and the floor that ends it.

Watched live on the alpha: with a standing order open and due, the planner
declared the program "at a stalemate with no actionable path forward". The
false-no-op check caught it and objected. Nothing else changed, so the work
stayed due, so the engine fired again, so the planner declared the same thing.
A guard that only objects is not a floor.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.adapters import agents
from reverie_automata.config import Config
from reverie_automata.runner import Runner

REFUSE = {"tasks": [], "do_nothing": True,
          "do_nothing_reason": "the program is at a stalemate"}


class Refuser:
    name = "refuser"

    def __init__(self, options=None):
        pass

    def complete(self, system, user, *, max_tokens=1000):
        return "<<PLAN>>" + json.dumps(REFUSE) + "<<END>>"

    def run_session(self, directive, **kw):
        return "<<RESULT>>done<<END>>\n<<VERIFY>>a receipt<<END>>"


def _runner(tmp_path):
    agents.REGISTRY["refuser"] = Refuser
    cfg = Config()
    cfg.data.update({
        "home": str(tmp_path / "h"), "trigger": "work",
        "window": {"start": 0, "end": 0}, "idle_minutes": 0,
        "min_gap_minutes": 0, "max_cycles_per_day": 99,
        "planner": {"backend": "refuser"}, "agent": {"backend": "refuser"},
    })
    r = Runner(cfg, last_input_ts=lambda: time.time() - 7200,
               is_available=lambda: True)
    d = Path(cfg["home"]) / "mandates"
    d.mkdir(parents=True, exist_ok=True)
    (d / "m.md").write_text("---\nid: p\nobjective: advance the program\n---\nbody\n")
    return r


def _outcome(r, n=-1):
    home = Path(r.cfg["home"])
    return json.loads((sorted((home / "cycles").glob("*"))[n] / "outcome.json").read_text())


def test_the_first_refusal_is_only_objected_to(tmp_path):
    """One bad night is not a pattern, and the guard should not seize the
    plan on the strength of a single disagreement."""
    r = _runner(tmp_path)
    r.tick()
    out = _outcome(r)
    assert out["false_no_op"] is True and out["ledger"] == []


def test_the_second_refusal_is_overruled(tmp_path):
    r = _runner(tmp_path)
    r.tick()
    r.tick()
    out = _outcome(r)
    assert out["ledger"], "the wrapper should have filed the due thread itself"
    assert out["ledger"][0]["what"].startswith("mandate p:")
    assert any("declined twice" in c or "filed as the task by the wrapper" in c
               for c in out["plan_complaints"])


def test_it_does_not_seize_the_plan_when_nothing_is_due(tmp_path):
    """An honest lazy day survives the floor that protects against a dishonest
    one. Without work due there is nothing to overrule with."""
    r = _runner(tmp_path)
    for f in (Path(r.cfg["home"]) / "mandates").glob("*.md"):
        f.unlink()
    assert r.tick()["fired"] is False


def test_the_forced_task_is_still_risk_classified(tmp_path):
    """The wrapper files work; it does not grant permission. A due thread whose
    title is dangerous parks for approval exactly as a planned one would."""
    r = _runner(tmp_path)
    con = r.store.connect()
    r.store.add_thread(con, "deploy the thing to production", "", kind="approval")
    con.close()
    r.tick()
    r.tick()
    out = _outcome(r)
    assert out["ledger"][0]["status"] == "parked"
