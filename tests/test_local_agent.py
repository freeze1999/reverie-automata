"""The tool loop, driven by a scripted brain rather than a real one."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.adapters.local_agent import LocalAgent


class _Brain:
    """Returns a canned sequence of steps, then repeats the last one."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = 0

    def __call__(self, system, user, *, max_tokens=1000):
        self.calls += 1
        i = min(self.calls - 1, len(self.steps) - 1)
        return self.steps[i]


def _agent(steps, tools, **over):
    a = LocalAgent({"tools": tools, "max_turns": 10, **over})
    a.server.complete = _Brain(steps)
    return a


def _step(tool, arg="x"):
    return '{"thought": "t", "tool": "%s", "argument": "%s"}' % (tool, arg)


def test_a_tool_result_becomes_the_next_turn_evidence():
    seen = {}

    def probe(arg):
        seen["arg"] = arg
        return "42"

    a = _agent([_step("probe", "the question"), _step("done", "the answer is 42")],
               {"probe": ("a probe", probe)})
    out = a.run_session("find the answer")
    assert seen["arg"] == "the question"
    assert "<<RESULT>>done<<END>>" in out and "42" in out


def test_an_invented_tool_is_reported_not_executed():
    a = _agent([_step("teleport"), _step("done", "fine")], {"real": ("a real one", str)})
    a.run_session("go")
    assert "no such tool" in a.transcript[0]


def test_a_repeated_call_stops_the_loop_instead_of_burning_the_cap():
    """A small model reissues a call that is not working until the cap; the
    loop is what has to notice."""
    def always_raises(arg):
        raise PermissionError("nope")

    a = _agent([_step("bad")], {"bad": ("fails", always_raises)})
    out = a.run_session("go")
    assert "the same call was made 3 times" in out
    assert len(a.transcript) <= 4        # stopped early, did not run ten turns


def test_repetition_is_caught_even_when_the_call_succeeds():
    """Found live: the model searched for the same words six times. Each call
    returned "no results" perfectly successfully, so a guard that only watches
    for errors watched the whole thing happen."""
    a = _agent([_step("search", "same words")],
               {"search": ("searches", lambda q: "no results; try different words")})
    out = a.run_session("go")
    assert "cannot produce anything new" in out


def test_the_tighter_turn_cap_wins():
    a = _agent([_step("noop")], {"noop": ("does nothing", lambda x: "ok")},
               max_turns=2)
    a.run_session("go", turn_cap=40)
    assert len([t for t in a.transcript if t.startswith("[")]) <= 2


def test_giving_up_is_reported_as_a_failure_with_a_reason():
    a = _agent([_step("give_up", "the source is offline")], {})
    out = a.run_session("go")
    assert "<<RESULT>>failed<<END>>" in out and "offline" in out


def test_unparseable_steps_do_not_crash_the_loop():
    a = _agent(["not json at all", _step("done", "recovered")], {})
    out = a.run_session("go")
    assert "<<RESULT>>done<<END>>" in out
    assert "no usable step" in a.transcript[0]


def test_a_brain_that_answers_nothing_ends_the_session():
    """Found live: the server returned 500 on every call and the loop spent
    all ten turns asking again. A brain that cannot answer does not need more
    turns, it needs the reason surfaced."""
    a = _agent(["[local server error: HTTP Error 500: Internal Server Error]"], {})
    out = a.run_session("go")
    assert "nothing usable 3 times" in out
    assert len(a.transcript) <= 4


def test_the_loop_hands_its_server_the_whole_configuration():
    """The credential and the constraint mode were missing here while the
    planner had both, so in one cycle the plan phase reached a hosted brain and
    the execute phase got 401 from the same endpoint."""
    from reverie_automata.adapters.local_agent import LocalAgent
    a = LocalAgent({"base_url": "https://api.example.com", "api_key": "k",
                    "schema_mode": "json_object", "model": "m"})
    assert a.server.api_key == "k"
    assert a.server.schema_mode == "json_object"
    assert a.server.model == "m"
