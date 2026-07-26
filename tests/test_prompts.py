"""The prompt must state the rules the validator enforces.

Across one live run the wrapper corrected "filed as text, but this profile
grounds every claim in a tool result" nineteen times and "three tasks proposed,
cap is one" thirteen times, while the prompt mentioned neither rule and offered
a mode the profile forbade. A rule that is enforced and never stated is not a
guard, it is a tax the model pays every cycle for a secret.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata import prompts as P


def test_the_standing_opening_does_not_tell_a_working_engine_it_is_idle():
    """The line that caused it: an engine with a standing order open and due
    read "no one is asking anything of you" and wrote back "I am truly idle"."""
    assert "You are idle" in P.PLAN            # correct for the idle engine
    assert "You are idle" not in P.PLAN_STANDING
    assert "on duty" in P.PLAN_STANDING


def test_the_cap_the_validator_enforces_is_stated():
    assert "at most 3" in P.constraints({"max_tasks_per_cycle": 3})


def test_the_text_ban_is_stated_only_when_it_applies():
    assert 'mode "tool"' in P.constraints({"allow_text_tasks": False})
    assert 'mode "tool"' not in P.constraints({"allow_text_tasks": True})


def test_the_lazy_day_rule_is_stated_with_its_condition():
    """do_nothing stays legitimate. What the planner was never told is the
    condition under which it stops being legitimate."""
    text = P.constraints({})
    assert "do_nothing is legitimate ONLY if the queue above is empty" in text
    assert "missed shift" in text


def test_execute_states_the_turn_cap_it_will_be_cut_off_at():
    """Seventeen tasks in one run died at "turn cap reached with no
    conclusion", a limit the prompt never mentioned."""
    body = P.EXECUTE.format(context="", task_id="t1", what="x", why="y", turn_cap=6)
    assert "at most 6 tool calls" in body
    assert "STOP and emit the envelope" in body


def test_the_standing_plan_offers_only_the_mode_the_engine_accepts():
    """Routing is the wrapper's decision. Leaving "delegate" on the planner's
    menu invites it to choose something the engine will not read."""
    assert '"mode": "tool"' in P.PLAN_STANDING
    assert "tool|text|delegate" not in P.PLAN_STANDING
