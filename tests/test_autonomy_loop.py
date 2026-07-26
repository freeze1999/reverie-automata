"""The loop the whole apparatus exists for, tested end to end.

Not "can a job be filed" (test_delegation covers that) but: does the ENGINE
file one, by itself, when a task crosses the line, without a person noticing
the line for it. That is the gap the alpha ran into: three delegated jobs, all
three filed by a human, the tool untouched by the model across every cycle.

And the other half: does a standing objective keep the queue from running dry,
so there is a next cycle at all.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata import events
from reverie_automata.adapters import agents, delegates
from reverie_automata.config import Config
from reverie_automata.runner import Runner


class ScriptedPlanner:
    name = "scripted2"
    plan = {"tasks": [], "do_nothing": True}

    def __init__(self, options=None):
        pass

    def complete(self, system, user, *, max_tokens=1000):
        return "<<PLAN>>" + json.dumps(ScriptedPlanner.plan) + "<<END>>"

    def run_session(self, directive, **kw):
        return "<<RESULT>>done<<END>>\n<<VERIFY>>a receipt<<END>>"


class SpyDelegate:
    """Records what it was handed. Files nothing over any wire."""

    name = "spy"
    filed: list = []
    available = True

    def __init__(self, options=None):
        pass

    def file(self, task, *, cycle=""):
        if not SpyDelegate.available:
            return "", "worker unreachable"
        SpyDelegate.filed.append(task)
        return f"job{len(SpyDelegate.filed)}", "filed"

    def collect(self):
        return []


def _runner(tmp_path, plan, **over):
    agents.REGISTRY["scripted2"] = ScriptedPlanner
    delegates.REGISTRY["spy"] = SpyDelegate
    SpyDelegate.filed = []
    SpyDelegate.available = True
    ScriptedPlanner.plan = plan
    cfg = Config()
    cfg.data.update({
        "home": str(tmp_path / "h"), "trigger": "work",
        "window": {"start": 0, "end": 0}, "idle_minutes": 0,
        "min_gap_minutes": 0, "max_cycles_per_day": 99,
        "planner": {"backend": "scripted2"}, "agent": {"backend": "scripted2"},
        "delegation": {"backend": "spy"},
    })
    cfg.data.update(over)
    return Runner(cfg, last_input_ts=lambda: time.time() - 7200,
                  is_available=lambda: True)


AUTHORING = {"id": "t1", "what": "write a script reproducing the matrix given "
                                 "in the drop", "why": "M0", "mode": "tool",
             "risk": "SAFE"}
LOCAL_TASK = {"id": "t2", "what": "compute the rank of the matrix in the drop",
              "why": "M0", "mode": "tool", "risk": "SAFE"}


def _drop(runner, text="please handle this"):
    box = runner.engine.inbox
    box.dir.mkdir(parents=True, exist_ok=True)
    (box.dir / "req.md").write_text(text)


def _ledger(runner):
    home = Path(runner.cfg["home"])
    latest = sorted((home / "cycles").glob("*"))[-1]
    return json.loads((latest / "outcome.json").read_text())["ledger"]


def test_the_engine_delegates_without_being_told_to(tmp_path):
    r = _runner(tmp_path, {"tasks": [dict(AUTHORING)], "do_nothing": False})
    _drop(r)
    r.tick()
    assert len(SpyDelegate.filed) == 1
    assert _ledger(r)[0]["status"] == "delegated"


def test_work_it_can_do_is_not_handed_away(tmp_path):
    r = _runner(tmp_path, {"tasks": [dict(LOCAL_TASK)], "do_nothing": False})
    _drop(r)
    r.tick()
    assert SpyDelegate.filed == []
    assert _ledger(r)[0]["status"] == "done"


def test_a_delegated_task_leaves_an_obligation_behind(tmp_path):
    """The open thread IS the memory of the job. Without it the answer comes
    back to a machine that has forgotten it asked."""
    r = _runner(tmp_path, {"tasks": [dict(AUTHORING)], "do_nothing": False})
    _drop(r)
    r.tick()
    con = r.store.connect()
    titles = [row[2] for row in con.execute("SELECT id, kind, title FROM threads")]
    con.close()
    assert any(t.startswith("awaiting job job1") for t in titles)


def test_a_down_worker_does_not_stop_the_engine(tmp_path):
    """Refusing to work because the helper is missing turns an inconvenience
    into an outage. It runs the task locally and records the intent."""
    r = _runner(tmp_path, {"tasks": [dict(AUTHORING)], "do_nothing": False})
    SpyDelegate.available = False
    _drop(r)
    r.tick()
    assert _ledger(r)[0]["status"] == "done"
    routed = events.read(r.cfg["home"], {"route"})
    assert routed and routed[-1]["note"] == "worker unreachable"


def test_a_standing_order_keeps_the_queue_from_running_dry(tmp_path):
    """With no mandate an empty queue means the engine never fires again."""
    r = _runner(tmp_path, {"tasks": [], "do_nothing": True})
    assert r.tick()["fired"] is False

    d = Path(r.cfg["home"]) / "mandates"
    d.mkdir(parents=True, exist_ok=True)
    (d / "program.md").write_text(
        "---\nid: p\nobjective: advance the program\n---\none step per cycle\n")
    assert r.tick()["fired"] is True


def test_the_run_leaves_a_readable_trace_of_its_decisions(tmp_path):
    r = _runner(tmp_path, {"tasks": [dict(AUTHORING)], "do_nothing": False})
    _drop(r)
    r.tick()
    kinds = [e["kind"] for e in events.read(r.cfg["home"])]
    assert {"plan", "route", "cycle"} <= set(kinds)
