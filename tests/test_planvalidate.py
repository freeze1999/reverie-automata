"""Plan validation: the rules that catch what a schema cannot.

Every case here reproduces a defect observed from a real small-brain run.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.planvalidate import validate_plan

GOOD = {"id": "t1", "what": "search the literature for cubic-linear results",
        "why": "the thread asks for it", "mode": "tool", "risk": "SAFE"}


def test_a_sound_plan_passes_untouched():
    plan, complaints, replan = validate_plan(
        {"tasks": [dict(GOOD)], "do_nothing": False}, work_available=True)
    assert plan["tasks"] == [GOOD] and not complaints and not replan


def test_content_free_task_is_dropped():
    """Observed: `what` came back as the single word 'research'."""
    plan, complaints, _ = validate_plan(
        {"tasks": [{"id": "t1", "what": "research"}], "do_nothing": False},
        work_available=False)
    assert plan["tasks"] == []
    assert plan["do_nothing"] is True
    assert any("no content" in c for c in complaints)


def test_do_nothing_with_tasks_is_incoherent():
    """Observed: do_nothing true while also listing work."""
    plan, complaints, _ = validate_plan(
        {"tasks": [dict(GOOD)], "do_nothing": True}, work_available=False)
    assert plan["tasks"] == []
    assert any("while listing" in c for c in complaints)


def test_false_do_nothing_is_caught_by_the_gate_not_the_model():
    """The dangerous one: a no-op claimed while work was demonstrably pending.
    Indistinguishable from a legitimate lazy day without an outside witness."""
    plan, complaints, replan = validate_plan(
        {"tasks": [], "do_nothing": True}, work_available=True)
    assert replan is True
    assert any("contradicts the eligibility check" in c for c in complaints)


def test_an_honest_lazy_day_is_left_alone():
    """The virtue this must never punish: nothing pending, nothing done."""
    plan, complaints, replan = validate_plan(
        {"tasks": [], "do_nothing": True, "do_nothing_reason": "queue empty"},
        work_available=False)
    assert plan["do_nothing"] is True and replan is False and not complaints
    assert plan["do_nothing_reason"] == "queue empty"


def test_ambition_is_capped_not_half_run():
    """Observed: three tasks proposed by a brain with a one-task budget."""
    tasks = [dict(GOOD, id=f"t{i}") for i in range(3)]
    plan, complaints, _ = validate_plan(
        {"tasks": tasks, "do_nothing": False}, work_available=True, max_tasks=1)
    assert len(plan["tasks"]) == 1 and plan["tasks"][0]["id"] == "t0"
    assert any("cap is 1" in c for c in complaints)


def test_a_reason_is_always_recorded_for_a_no_op():
    plan, _, _ = validate_plan({"tasks": [], "do_nothing": True},
                               work_available=False)
    assert plan["do_nothing_reason"]


def test_junk_input_never_raises():
    for junk in (None, {}, {"tasks": "not a list"}, {"tasks": [None, 3, "x"]}):
        plan, _, _ = validate_plan(junk, work_available=False)
        assert isinstance(plan.get("tasks"), list)
