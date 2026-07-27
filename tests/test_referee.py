"""The referee, tested against the attack that nearly went unnoticed.

A12 is the reason this module exists in this shape: the one component that
moved in a live run moved from 8 to 46 because the machine copied a log into
itself. Everything here defends the distinction between a fact and a word
count.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.referee import (DERIVED, EXTERNAL, SELF_REPORTED,
                                      Component, Referee, grade)


def _c(name, value, kind=DERIVED):
    return Component(name, lambda: value, kind, "test")


def test_a_self_reported_component_is_refused_by_the_audit():
    """A12. Counting regex matches in a file the machine writes is not a
    referee, and the audit must say so before any grading happens."""
    r = Referee([_c("citations", 3), _c("deadends", 46, SELF_REPORTED)])
    problems = r.audit()
    assert len(problems) == 1
    assert "deadends" in problems[0] and "cannot grade" in problems[0]


def test_a_self_reported_component_never_reaches_the_state_vector():
    """Refusing it in the audit and then counting it anyway would be worse than
    not auditing: it would look checked."""
    r = Referee([_c("citations", 3), _c("deadends", 46, SELF_REPORTED)])
    assert "deadends" not in r.state()
    assert r.state() == {"citations": 3}


def test_an_unreadable_component_is_absent_rather_than_zero():
    """Calling it zero manufactures a delta on the next reading, which is a
    false A waiting to happen."""
    def boom():
        raise OSError("gone")
    r = Referee([Component("artifacts", boom, EXTERNAL, "t"), _c("citations", 3)])
    assert r.state() == {"citations": 3}


def test_movement_is_an_A_however_small():
    d = Referee.delta({"citations": 3, "artifacts": 0}, {"citations": 3, "artifacts": 1})
    assert d == {"artifacts": 1}
    assert grade(d, attempted=True, honest_no_op=False) == "A"


def test_activity_without_movement_is_an_F():
    """The four false A grades of the alpha, in one assertion. A cycle that
    attempted work and moved nothing did not do research, whatever its ledger
    says."""
    assert grade({}, attempted=True, honest_no_op=False) == "F"


def test_a_component_going_backwards_is_an_F():
    """A16: a sound artifact overwritten by an invented identity matrix. Nothing
    legitimate reduces an established count."""
    d = Referee.delta({"artifacts": 2}, {"artifacts": 1})
    assert d == {"artifacts": -1}
    assert grade(d, attempted=True, honest_no_op=False) == "F"


def test_an_honest_lazy_day_survives():
    """do_nothing stays a first-class outcome. The virtue must survive the
    guard that protects against its counterfeit."""
    assert grade({}, attempted=False, honest_no_op=True) == "N"


def test_a_claimed_lazy_day_with_work_attempted_is_not_one():
    assert grade({}, attempted=True, honest_no_op=True) == "F"


def test_the_delta_ignores_components_that_appeared_or_vanished():
    """A referee whose shape changed between readings cannot report movement;
    comparing across a schema change would invent one."""
    assert Referee.delta({"a": 1}, {"a": 1, "b": 5}) == {}
    assert Referee.delta({"a": 1, "b": 5}, {"a": 1}) == {}
