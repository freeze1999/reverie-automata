"""The typed task, on disk, before the session that has to satisfy it.

Written after watching an executor compute the right answer and then lose it on
the way into a tool call: the source filename came back with a letter missing
and three of the nine contract fields were gone. The fields had been in the
task the whole time. Nothing could read them, so the model was asked to retype
them, which is the one operation this class of model has been measured unable
to perform.

A tool that can read its own task can ask the model only for what the harness
does not already know. These tests hold the seam that makes that possible.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.adapters import agents
from reverie_automata.config import Config
from reverie_automata.runner import Runner

TASK = {"id": "t1", "type": "write_artifact", "mode": "tool", "risk": "SAFE",
        "what": "compute the rank", "why": "the milestone needs it",
        "path": "program/results/x.json", "input_from": "program/sources/a.py",
        "script": "print('trace =', 2)"}


class Scripted:
    name = "taskrecord-test"
    plan = {"tasks": [], "do_nothing": True}

    def __init__(self, options=None):
        pass

    def complete(self, system, user, *, max_tokens=1000):
        return "<<PLAN>>" + json.dumps(Scripted.plan) + "<<END>>"

    def run_session(self, directive, **kw):
        return "<<RESULT>>done<<END>>\n<<VERIFY>>a real receipt<<END>>"


def _runner(tmp_path, plan):
    agents.REGISTRY["taskrecord-test"] = Scripted
    Scripted.plan = plan
    cfg = Config()
    cfg.data.update({
        "home": str(tmp_path / "h"), "trigger": "work",
        "window": {"start": 0, "end": 0}, "idle_minutes": 0,
        "min_gap_minutes": 0, "max_cycles_per_day": 99,
        "planner": {"backend": "taskrecord-test"},
        "agent": {"backend": "taskrecord-test"},
    })
    r = Runner(cfg, last_input_ts=lambda: time.time() - 7200, is_available=lambda: True)
    d = Path(cfg["home"]) / "mandates"
    d.mkdir(parents=True, exist_ok=True)
    (d / "m.md").write_text("---\nid: p\nobjective: advance\n---\nbody\n")
    return r


def _cycle_dir(r):
    return sorted((Path(r.cfg["home"]) / "cycles").glob("*"))[-1]


def test_the_task_is_written_beside_the_transcript(tmp_path):
    r = _runner(tmp_path, {"tasks": [dict(TASK)], "do_nothing": False})
    r.tick()
    p = _cycle_dir(r) / "task_t1.json"
    assert p.exists(), "a task that ran left no record of what it was"
    assert json.loads(p.read_text())["id"] == "t1"


def test_every_field_survives_verbatim(tmp_path):
    """The point of the file. A value that changes on the way to disk is worth
    less than no file at all, because a tool would read it and be confident."""
    r = _runner(tmp_path, {"tasks": [dict(TASK)], "do_nothing": False})
    r.tick()
    rec = json.loads((_cycle_dir(r) / "task_t1.json").read_text())
    for k, v in TASK.items():
        assert rec[k] == v, f"{k} did not survive"


def test_it_is_written_before_the_session_not_after(tmp_path):
    """A tool called during the session must be able to read it. Written after
    the run_session call it would be useless to the only caller that wants it."""
    seen = {}

    class Peeking(Scripted):
        def run_session(self, directive, **kw):
            import os
            home, cycle = os.environ.get("REVERIE_HOME"), os.environ.get("REVERIE_CYCLE")
            p = Path(home) / "cycles" / cycle / "task_t1.json"
            seen["found"] = p.exists()
            seen["path"] = json.loads(p.read_text())["path"] if p.exists() else None
            return "<<RESULT>>done<<END>>\n<<VERIFY>>a receipt<<END>>"

    agents.REGISTRY["taskrecord-test"] = Peeking
    Peeking.plan = {"tasks": [dict(TASK)], "do_nothing": False}
    cfg = Config()
    cfg.data.update({
        "home": str(tmp_path / "h"), "trigger": "work",
        "window": {"start": 0, "end": 0}, "idle_minutes": 0,
        "min_gap_minutes": 0, "max_cycles_per_day": 99,
        "planner": {"backend": "taskrecord-test"},
        "agent": {"backend": "taskrecord-test"},
    })
    r = Runner(cfg, last_input_ts=lambda: time.time() - 7200, is_available=lambda: True)
    d = Path(cfg["home"]) / "mandates"
    d.mkdir(parents=True, exist_ok=True)
    (d / "m.md").write_text("---\nid: p\nobjective: advance\n---\nbody\n")
    r.tick()
    assert seen.get("found"), "the task was not on disk while the session ran"
    assert seen["path"] == TASK["path"]


def test_a_cycle_survives_a_directory_it_cannot_write(tmp_path, monkeypatch):
    """The record is worth having and never worth a crash.

    There is no test for an unserialisable task, because there cannot be one:
    a task arrives as json from the planner, so anything that reaches here has
    already round-tripped. The reachable failure is the disk, and this is it.
    """
    real = Path.write_text

    def refuse(self, *a, **kw):
        if self.name.startswith("task_") and self.name.endswith(".json"):
            raise OSError("read-only file system")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", refuse)
    r = _runner(tmp_path, {"tasks": [dict(TASK)], "do_nothing": False})
    r.tick()
    assert not (_cycle_dir(r) / "task_t1.json").exists()
    assert (_cycle_dir(r) / "task_t1.txt").exists(), "the cycle itself carried on"
