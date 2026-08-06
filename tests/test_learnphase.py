"""LEARN has to return what the model wrote.

Written after finding that it never had. The phase was routed through the
tool-running agent, and that adapter does not return the model's words: it
drives a loop under a forced step schema and returns a string it composes
itself from the loop verdict and the transcript, so the only blocks it can emit
are RESULT and VERIFY. The prompt asks for JOURNAL, REVIEW and LESSON. None of
them had a path into the return value, and the greps that look for them were
searching a string that structurally could not contain one.

One thousand three hundred cycles produced zero lessons, and the whole suite
stayed green throughout, because not one test asked whether the phase produced
anything. These do.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.adapters import agents
from reverie_automata.config import Config
from reverie_automata.runner import Runner

REVIEW = "the source list was stale and nobody told me"
LEARNED = ("<<JOURNAL>>read one paper and computed nothing<<END>>\n"
           f"<<REVIEW>>{REVIEW}<<END>>\n"
           "<<LESSON>>a stale source -> reread before planning -> the plan was wrong twice<<END>>")


class Scripted:
    """Mimics the real split: `complete` is text, `run_session` is a tool loop
    that returns its OWN envelope and never the model's prose."""
    name = "learnphase-test"
    seen: dict = {}

    def __init__(self, options=None):
        pass

    def complete(self, system, user, *, max_tokens=1000):
        if "<<JOURNAL>>" in user:            # the LEARN prompt's emit block
            Scripted.seen["learn_via"] = "complete"
            return LEARNED
        return '<<PLAN>>{"tasks": [], "do_nothing": true}<<END>>'

    def run_session(self, directive, **kw):
        if "<<JOURNAL>>" in directive:
            Scripted.seen["learn_via"] = "run_session"
        return "<<RESULT>>done<<END>>\n<<VERIFY>>a real receipt<<END>>"


def _runner(tmp_path):
    agents.REGISTRY["learnphase-test"] = Scripted
    Scripted.seen = {}
    cfg = Config()
    cfg.data.update({
        "home": str(tmp_path / "h"), "trigger": "work",
        "window": {"start": 0, "end": 0}, "idle_minutes": 0,
        "min_gap_minutes": 0, "max_cycles_per_day": 99,
        "planner": {"backend": "learnphase-test"},
        "agent": {"backend": "learnphase-test"},
    })
    r = Runner(cfg, last_input_ts=lambda: time.time() - 7200, is_available=lambda: True)
    d = Path(cfg["home"]) / "mandates"
    d.mkdir(parents=True, exist_ok=True)
    (d / "m.md").write_text("---\nid: p\nobjective: advance\n---\nbody\n")
    return r


def _cycle_dir(r):
    return sorted((Path(r.cfg["home"]) / "cycles").glob("*"))[-1]


def test_learn_is_not_routed_through_the_tool_loop(tmp_path):
    """The regression guard. `run_session` cannot carry a review, so a LEARN
    that goes through it has been deleted, whatever the model wrote."""
    r = _runner(tmp_path)
    r.tick()
    assert Scripted.seen.get("learn_via") == "complete", (
        "LEARN went through the tool loop, whose return value cannot contain "
        "a JOURNAL, a REVIEW or a LESSON")


def test_the_transcript_holds_what_the_model_actually_wrote(tmp_path):
    r = _runner(tmp_path)
    r.tick()
    txt = (_cycle_dir(r) / "learn.txt").read_text(encoding="utf-8")
    assert REVIEW in txt, "the model's own words did not reach the record"
    for tag in ("<<JOURNAL>>", "<<REVIEW>>", "<<LESSON>>"):
        assert tag in txt, f"{tag} was lost between the model and the disk"


def test_a_lesson_survives_into_the_outcome(tmp_path):
    """Lessons are the only part of this phase anything downstream reads. They
    were empty for the life of the engine and nothing said so."""
    r = _runner(tmp_path)
    r.tick()
    out = json.loads((_cycle_dir(r) / "outcome.json").read_text())
    assert len(out["lessons"]) == 1, f"the lesson was dropped: {out['lessons']}"
    got = out["lessons"][0]
    assert got["situation"] == "a stale source"
    assert got["action"] == "reread before planning"
    assert got["outcome"] == "the plan was wrong twice"


def test_a_phase_that_says_nothing_is_not_mistaken_for_one_with_nothing_to_say(tmp_path):
    """The failure that hid A24 for so long: an empty phase and a deleted phase
    look identical. A LEARN that returns no blocks must leave a transcript that
    shows what it did return, so the next person can see which it was."""
    class Mute(Scripted):
        name = "learnphase-mute"

        def complete(self, system, user, *, max_tokens=1000):
            if "<<JOURNAL>>" in user:
                return "[local server error: connection refused]"
            return '<<PLAN>>{"tasks": [], "do_nothing": true}<<END>>'

    agents.REGISTRY["learnphase-mute"] = Mute
    r = _runner(tmp_path)
    r.cfg.data["planner"] = {"backend": "learnphase-mute"}
    r.cfg.data["agent"] = {"backend": "learnphase-mute"}
    r = Runner(r.cfg, last_input_ts=lambda: time.time() - 7200, is_available=lambda: True)
    r.tick()
    txt = (_cycle_dir(r) / "learn.txt").read_text(encoding="utf-8")
    assert "connection refused" in txt, (
        "a phase that failed left a record indistinguishable from one that "
        "simply had nothing to report")
