"""Routing: who does the task, decided by code rather than by self-knowledge.

The live finding these tests defend: a small brain handed a delegate tool and a
task it could not do never called the tool, not once. It did not decline to ask
for help; it never noticed there was anything to ask about. So the rule cannot
live in the prompt.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.routing import DELEGATE, LOCAL, route


def test_authoring_against_supplied_data_is_delegated():
    where, why = route({"what": "write a script that computes the rank of the "
                                "matrix given above", "mode": "tool"})
    assert where == DELEGATE
    assert "write" in why and "matrix" in why  # both halves quoted back


def test_authoring_alone_stays_local():
    """"Write a note about what we learned" is authoring with nothing to be
    faithful to. Delegating it would hand out the easy half of the job."""
    assert route({"what": "write a short note on what this cycle learned"})[0] == LOCAL


def test_fidelity_alone_stays_local():
    """Running a computation over given data is precisely what the small brain
    is good at. The wall is transcription under composition, not arithmetic."""
    assert route({"what": "compute the rank of the matrix given in the drop",
                  "mode": "tool"})[0] == LOCAL


def test_the_rule_is_not_english_only():
    """A sibling rule elsewhere matched English verbs only and a Chinese task
    walked straight past it. Same bug, pre-empted here."""
    where, _ = route({"what": "按照给定的矩阵写一个精确算术的脚本"})
    assert where == DELEGATE


def test_reproduction_delegates_on_its_own():
    """Found on the first live cycle after routing shipped. "Reproduce the
    published claims for the 4x4 Druzkowski matrix" carried no other fidelity
    marker, stayed local, and spent ten turns against the exact wall routing
    exists to avoid. There is no such thing as reproducing something loosely."""
    where, why = route({"what": "Resume task #7: Reproduce the published claims "
                                "for the 4x4 Druzkowski matrix over Z[i]"})
    assert where == DELEGATE and "eproduc" in why


def test_an_operator_can_pin_a_task_local():
    task = {"what": "write a script for the matrix given above (local only)"}
    where, why = route(task)
    assert where == LOCAL and "pinned local" in why


def test_routing_can_be_switched_off_whole():
    task = {"what": "write a script reproducing the given example"}
    assert route(task, {"routing": {"enabled": False}})[0] == LOCAL


def test_the_reason_quotes_the_task_so_a_person_can_argue_with_it():
    _, why = route({"what": "implement exactly the algorithm supplied"})
    assert "implement" in why and "supplied" in why
