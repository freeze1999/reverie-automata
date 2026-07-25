"""Delegation as jobs rather than conversation.

The properties worth defending: a worker cannot mark its own work accepted, a
finished job cannot be reopened by a redelivered message, an answer to a
different question fails on the binding rather than on someone's judgement,
and a job nobody answers expires in its own record instead of lingering.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.delegation import (BLOCKED, CANCELLED, EXPIRED, FAILED,
                                         OPEN, SUCCEEDED, WORKING, JobQueue)

CONTRACT = {"exit_zero": True, "mentions": "determinant"}
CHECKS = {
    "exit_zero": lambda want, res: bool(res.get("exit_code") == 0) == want,
    "mentions": lambda want, res: want in str(res.get("output", "")),
}


def _q(tmp_path):
    return JobQueue(tmp_path / "jobs")


def _good_result(job):
    return {"spec_hash": job.spec_hash, "exit_code": 0,
            "output": "the determinant is 1", "claimed": "success"}


def test_a_job_needs_a_contract_before_it_can_be_sent(tmp_path):
    q = _q(tmp_path)
    try:
        q.file("do a thing", {})
    except ValueError as e:
        assert "acceptance contract" in str(e)
    else:
        raise AssertionError("a job without a contract must not be filable")


def test_the_contract_decides_not_the_worker(tmp_path):
    """The worker claims success and is wrong; the contract is what counts."""
    q = _q(tmp_path)
    job = q.file("compute the determinant", CONTRACT)
    q.transition(job, WORKING, by="worker")
    bad = {"spec_hash": job.spec_hash, "exit_code": 1,
           "output": "I could not run it", "claimed": "success"}
    ok, why = q.accept_result(job.id, bad, checks=CHECKS)
    assert not ok and "exit_zero" in why
    assert q.load(job.id).status == FAILED


def test_a_result_that_satisfies_every_clause_is_accepted(tmp_path):
    q = _q(tmp_path)
    job = q.file("compute the determinant", CONTRACT)
    q.transition(job, WORKING, by="worker")
    ok, why = q.accept_result(job.id, _good_result(job), checks=CHECKS)
    assert ok, why
    done = q.load(job.id)
    assert done.status == SUCCEEDED and done.verdict["passed"] is True


def test_an_answer_to_a_different_question_fails_on_the_binding(tmp_path):
    """A worker that quietly reinterprets the task is caught by the hash, not
    by anyone reading the result carefully."""
    q = _q(tmp_path)
    job = q.file("compute the determinant", CONTRACT)
    q.transition(job, WORKING, by="worker")
    strayed = dict(_good_result(job), spec_hash="0" * 16)
    ok, why = q.accept_result(job.id, strayed, checks=CHECKS)
    assert not ok and "different question" in why
    assert q.load(job.id).status == FAILED


def test_a_finished_job_cannot_be_reopened_by_a_redelivery(tmp_path):
    """Chat reconnects and retried transports replay old messages; a terminal
    state has to make that harmless rather than exciting."""
    q = _q(tmp_path)
    job = q.file("compute the determinant", CONTRACT)
    q.transition(job, WORKING, by="worker")
    q.accept_result(job.id, _good_result(job), checks=CHECKS)

    ok, why = q.accept_result(job.id, _good_result(job), checks=CHECKS)
    assert not ok and "already succeeded" in why
    assert q.load(job.id).status == SUCCEEDED


def test_the_state_machine_refuses_impossible_moves(tmp_path):
    q = _q(tmp_path)
    job = q.file("x", CONTRACT)
    assert q.transition(job, SUCCEEDED, by="worker") is False   # must work first
    assert q.transition(job, WORKING, by="worker") is True
    assert q.transition(job, SUCCEEDED, by="contract") is True
    assert q.transition(job, WORKING, by="worker") is False     # terminal is final


def test_an_unanswered_job_expires_in_its_own_record(tmp_path):
    q = _q(tmp_path)
    job = q.file("x", CONTRACT, deadline_hours=0.001)
    gone = q.expire_overdue(now=time.time() + 60)
    assert [j.id for j in gone] == [job.id]
    dead = q.load(job.id)
    assert dead.status == EXPIRED
    assert dead.history[-1]["by"] == "clock"


def test_a_missing_check_is_a_failure_not_a_pass(tmp_path):
    """An unenforceable clause must never be waved through: a contract nobody
    can check is worse than no contract, because it looks like one."""
    q = _q(tmp_path)
    job = q.file("x", {"unmeasurable": "vibes"})
    q.transition(job, WORKING, by="worker")
    ok, why = q.accept_result(job.id, {"exit_code": 0}, checks=CHECKS)
    assert not ok and "no check is defined" in why


def test_a_check_that_raises_fails_the_clause(tmp_path):
    q = _q(tmp_path)
    job = q.file("x", {"boom": 1})
    q.transition(job, WORKING, by="worker")
    ok, why = q.accept_result(
        job.id, {}, checks={"boom": lambda w, r: 1 / 0})
    assert not ok and "ZeroDivisionError" in why


def test_the_history_is_the_whole_story(tmp_path):
    q = _q(tmp_path)
    job = q.file("x", CONTRACT)
    q.transition(job, WORKING, by="worker", note="picked up")
    q.transition(job, BLOCKED, by="worker", note="needs a credential")
    story = [h["to"] for h in q.load(job.id).history]
    assert story == [OPEN, WORKING, BLOCKED]


def test_open_jobs_excludes_the_finished_ones(tmp_path):
    q = _q(tmp_path)
    a = q.file("a", CONTRACT)
    b = q.file("b", CONTRACT)
    q.transition(b, CANCELLED, by="operator")
    assert [j.id for j in q.open_jobs()] == [a.id]


def test_the_evidence_a_check_produced_is_kept(tmp_path):
    """A check that runs the delivered code produces the receipt; storing the
    result before the checks keeps the claim and throws away the proof."""
    def running_check(expected, result):
        result["_ran"] = {"exit_code": 0, "output": "the determinant is 1"}
        return True

    q = _q(tmp_path)
    job = q.file("compute it", {"runs": True})
    q.transition(job, WORKING, by="worker")
    ok, _ = q.accept_result(job.id, {"spec_hash": job.spec_hash, "script": "x"},
                            checks={"runs": running_check})
    assert ok
    assert q.load(job.id).result["_ran"]["output"] == "the determinant is 1"
