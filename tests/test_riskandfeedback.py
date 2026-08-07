"""Who decides a task is risky, and does the machine ever find out it was.

Both of these come from one night. The planner filled the required `risk` field
with RISKY 218 consecutive times for a read-only identifier lookup. A
self-declared RISKY is enough to park a task, so every one of them was filed as
an approval into a queue nobody opens, and none was ever run. 314 pending rows.

Then the second half: a parked task returned before its ledger row was written,
so nothing recorded that it had been refused, and nothing carried the refusal
into the next cycle. The machine could see only that nothing had happened. Its
reviews that night were specific, confident, well argued, and about a cause
that was not the cause, in 177 distinct formulations.

It handcuffed itself, and then was never told which hand held the key.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.adapters import agents
from reverie_automata.config import Config
from reverie_automata.runner import Runner
from reverie_automata.tasktypes import Menu, TaskType

SAFE_TASK = {"id": "t1", "type": "look_up", "subject": "1503.08733",
             "why": "the milestone needs it", "risk": "RISKY"}


def _menu(risk="SAFE"):
    return Menu([TaskType(name="look_up", required=("subject",),
                          identity=("subject",), risk=risk,
                          summary="read one identifier's metadata",
                          satisfied_by=("read_meta",),
                          postcondition=lambda t, r, h: (False, "not checked here"))])


class Scripted:
    name = "riskfeedback-test"
    plan = {"tasks": [], "do_nothing": True}

    def __init__(self, options=None):
        pass

    def complete(self, system, user, *, max_tokens=1000):
        Scripted.last_prompt = user
        if "<<JOURNAL>>" in user:
            return "<<JOURNAL>>nothing<<END>>\n<<REVIEW>>nothing<<END>>"
        return "<<PLAN>>" + json.dumps(Scripted.plan) + "<<END>>"

    def run_session(self, directive, **kw):
        return "<<RESULT>>done<<END>>\n<<VERIFY>>a receipt<<END>>"


def _runner(tmp_path, plan, risk="SAFE"):
    agents.REGISTRY["riskfeedback-test"] = Scripted
    Scripted.plan, Scripted.last_prompt = plan, ""
    cfg = Config()
    cfg.data.update({
        "home": str(tmp_path / "h"), "trigger": "work",
        "window": {"start": 0, "end": 0}, "idle_minutes": 0,
        "min_gap_minutes": 0, "max_cycles_per_day": 99,
        "menu": _menu(risk),
        "planner": {"backend": "riskfeedback-test"},
        "agent": {"backend": "riskfeedback-test"},
    })
    r = Runner(cfg, last_input_ts=lambda: time.time() - 7200, is_available=lambda: True)
    d = Path(cfg["home"]) / "mandates"
    d.mkdir(parents=True, exist_ok=True)
    (d / "m.md").write_text("---\nid: p\nobjective: advance\n---\nbody\n")
    return r


def _outcome(r):
    c = sorted((Path(r.cfg["home"]) / "cycles").glob("*"))[-1]
    return json.loads((c / "outcome.json").read_text())


def _events(r, kind):
    p = Path(r.cfg["home"]) / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in
            p.read_text(encoding="utf-8").replace("\x00", "").splitlines()
            if l.strip() and json.loads(l).get("kind") == kind]


def test_the_planner_cannot_call_its_own_safe_work_risky(tmp_path):
    """The 218-times bug. The type says the work is safe; the planner's field
    is not competent to overrule it."""
    r = _runner(tmp_path, {"tasks": [dict(SAFE_TASK)], "do_nothing": False}, risk="SAFE")
    r.tick()
    statuses = [e["status"] for e in _outcome(r)["ledger"]]
    assert "parked" not in statuses, f"safe work was parked on the model's say-so: {statuses}"


def test_the_override_is_recorded_so_the_habit_stays_measurable(tmp_path):
    r = _runner(tmp_path, {"tasks": [dict(SAFE_TASK)], "do_nothing": False}, risk="SAFE")
    r.tick()
    got = _events(r, "risk_overridden")
    assert len(got) == 1, "the model's declaration was dropped silently"
    assert got[0]["declared"] == "RISKY" and got[0]["applied"] == "SAFE"


def test_a_type_that_really_is_risky_still_parks(tmp_path):
    """Relaxing who decides is not relaxing the decision."""
    task = dict(SAFE_TASK, risk="SAFE")     # the model says safe; the type does not
    r = _runner(tmp_path, {"tasks": [task], "do_nothing": False}, risk="RISKY")
    r.tick()
    assert [e["status"] for e in _outcome(r)["ledger"]] == ["parked"]


def test_a_parked_task_is_written_into_the_ledger(tmp_path):
    """It used to return before the row was written, so from the record's point
    of view the refusal had not happened."""
    r = _runner(tmp_path, {"tasks": [dict(SAFE_TASK, risk="SAFE")], "do_nothing": False},
                risk="RISKY")
    r.tick()
    con = r.store.connect()
    try:
        rows = r.store.last_task_outcomes(con, 5)
    finally:
        con.close()
    assert rows, "a parked task left no trace at all"
    assert rows[0][3] == "parked"
    assert "never run" in str(rows[0][4]), rows[0][4]


def test_the_next_cycle_is_told_what_became_of_the_last_one(tmp_path):
    """The F7 wire in its smallest form: the reason has to reach the planner."""
    r = _runner(tmp_path, {"tasks": [dict(SAFE_TASK, risk="SAFE")], "do_nothing": False},
                risk="RISKY")
    r.tick()
    r.cfg.data["menu"] = _menu("RISKY")
    Scripted.plan = {"tasks": [], "do_nothing": True}
    r.tick()
    prompt = Scripted.last_prompt
    assert "what became of your last tasks" in prompt, "the block never reached the planner"
    assert "parked" in prompt
    assert "never run" in prompt, "the planner was told it happened, not why"


def test_the_reason_names_the_classification_not_just_the_refusal(tmp_path):
    r = _runner(tmp_path, {"tasks": [dict(SAFE_TASK, risk="SAFE")], "do_nothing": False},
                risk="RISKY")
    r.tick()
    why = _outcome(r)["ledger"][0].get("why", "")
    assert "RISKY" in why and "approval" in why, why
