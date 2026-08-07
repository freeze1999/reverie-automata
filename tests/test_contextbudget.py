"""The context has to fit, and a brain that never answered has to say so.

All three of these come from one outage on 2026-08-07, and the chain is worth
keeping because every link looked like an improvement.

The LEARN phase was repaired, so lessons began recording for the first time.
The model restated one observation three ways per cycle, so they recorded three
at a time. Lessons ride into the planning context at priority 0, where the
trimmer will not touch them, so MEMORY.md grew to 64,758 characters against a
16,384-token window. Every planning call then returned HTTP 400. The error
string was written into plan.txt, failed to parse, and became `do_nothing` with
the reason "unparseable plan".

So for hours the engine reported a machine that had nothing to do, while the
machine was not being asked anything at all.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.adapters import agents
from reverie_automata.config import Config
from reverie_automata.engine import _transport_failed
from reverie_automata.runner import Runner
from reverie_automata.types import Lesson


class Dead:
    """A backend that cannot be reached. It returns its failure as text,
    because raising would kill the cycle and leave no record at all."""
    name = "budget-dead"

    def __init__(self, options=None):
        pass

    def complete(self, system, user, *, max_tokens=1000):
        return ("[local server error: HTTP Error 400: Bad Request] "
                "request (20010 tokens) exceeds the available context size")

    def run_session(self, directive, **kw):
        return "<<RESULT>>failed<<END>>\n<<VERIFY>>never reached<<END>>"


def _runner(tmp_path, backend="budget-dead"):
    agents.REGISTRY["budget-dead"] = Dead
    cfg = Config()
    cfg.data.update({
        "home": str(tmp_path / "h"), "trigger": "work",
        "window": {"start": 0, "end": 0}, "idle_minutes": 0,
        "min_gap_minutes": 0, "max_cycles_per_day": 99,
        "planner": {"backend": backend}, "agent": {"backend": backend},
    })
    r = Runner(cfg, last_input_ts=lambda: time.time() - 7200, is_available=lambda: True)
    d = Path(cfg["home"]) / "mandates"
    d.mkdir(parents=True, exist_ok=True)
    (d / "m.md").write_text("---\nid: p\nobjective: advance\n---\nbody\n")
    return r


def _outcome(r):
    c = sorted((Path(r.cfg["home"]) / "cycles").glob("*"))[-1]
    return json.loads((c / "outcome.json").read_text())


def test_a_transport_failure_is_recognised():
    assert _transport_failed("[local server error: HTTP Error 400: Bad Request]")
    assert _transport_failed("  [transport error: timed out]")
    assert not _transport_failed('<<PLAN>>{"do_nothing": true}<<END>>')
    assert not _transport_failed("")
    assert not _transport_failed("the local server was mentioned in prose")


def test_an_unreachable_planner_is_not_recorded_as_an_idle_cycle(tmp_path):
    """The whole outage in one assertion. `do_nothing` was true either way; the
    record has to say which kind of nothing it was."""
    r = _runner(tmp_path)
    r.tick()
    o = _outcome(r)
    joined = " ".join(str(c) for c in o["plan_complaints"])
    assert "never answered" in joined, f"the silence was not reported: {joined}"
    assert "NEVER ANSWERED" in str(o["plan"].get("do_nothing_reason", ""))


def test_an_unreachable_planner_is_not_accused_of_a_false_lazy_day(tmp_path):
    """It did not decline the work. It was never shown the work."""
    r = _runner(tmp_path)
    r.tick()
    assert _outcome(r)["false_no_op"] is False


def test_the_silence_is_announced_as_an_event(tmp_path):
    r = _runner(tmp_path)
    r.tick()
    p = Path(r.cfg["home"]) / "events.jsonl"
    kinds = [json.loads(l).get("kind") for l in
             p.read_text(encoding="utf-8").replace("\x00", "").splitlines() if l.strip()]
    assert "planner_unreachable" in kinds, kinds


def test_the_lesson_file_cannot_grow_past_the_budget(tmp_path):
    """Priority 0 means the trimmer will not shrink it, so it must not grow."""
    r = _runner(tmp_path)
    mem = Path(r.cfg["home"]) / "MEMORY.md"
    mem.parent.mkdir(parents=True, exist_ok=True)
    mem.write_text("".join(f"- lesson number {i} about something\n" for i in range(4000)))
    assert len(mem.read_text()) > 60000
    con = r.store.connect()
    try:
        block = dict((lab, txt) for _, lab, txt in r.engine.harvest._spine(con))
    finally:
        con.close()
    got = block["memory (lessons)"]
    assert len(got) <= 6200, f"the memory block was {len(got)} chars"
    assert "lesson number 3999" in got, "it kept the oldest instead of the newest"


def test_a_restated_lesson_is_not_written_down_again(tmp_path):
    """522 rows in one night were one observation with its clauses reordered.
    Byte equality was the only guard and it does not survive a paraphrase."""
    r = _runner(tmp_path)
    mem = Path(r.cfg["home"]) / "MEMORY.md"
    a = Lesson("the citation is blocked by missing authorship",
               "I initiated resolve_citation", "the task stalled and was parked")
    b = Lesson("blocked by missing authorship, the citation is",
               "resolve_citation I initiated", "parked and stalled, the task was")
    r.engine._append_memory([a])
    r.engine._append_memory([b])
    assert len([l for l in mem.read_text().splitlines() if l.strip()]) == 1

    c = Lesson("the source file was not where the task said",
               "listed the directory", "found it under another name")
    r.engine._append_memory([c])
    assert len([l for l in mem.read_text().splitlines() if l.strip()]) == 2
