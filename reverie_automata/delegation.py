"""Handing work to another agent, as jobs rather than as conversation.

Two agents that talk to each other will loop. Not through malice or bad
prompting: an acknowledgement invites an acknowledgement, and any depth cap
you impose cuts good long exchanges at N while permitting bad short ones
under it. The loop is a property of conversation itself.

So machines here do not converse. A job is an immutable specification filed
once, worked once, and answered once, and every follow-up is a state
transition on the same job id rather than a new message. There is no reply
reflex to trigger, so there is nothing to spiral.

What makes that safe rather than merely tidy:

**The acceptance contract is written before the work is sent.** Not after,
and not by the party doing the work. A job says what must be true of the
result, in checks a machine can run, and the requester never has to form an
opinion about the quality of what came back.

**The worker is an untrusted tool, not a colleague.** Its result is input to
be checked, not a report to be believed. It cannot amend the specification it
was given, and it cannot mark its own work accepted.

**A terminal state is a machine state.** SUCCEEDED, FAILED, BLOCKED,
CANCELLED, EXPIRED. There is no "done, thanks" and no "one more thing"; the
social forms that keep a human thread alive are exactly the ones that keep a
machine thread spinning.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

OPEN = "open"           # filed, not yet claimed by anyone
WORKING = "working"     # the assignee has it
SUCCEEDED = "succeeded"
FAILED = "failed"
BLOCKED = "blocked"     # the worker needs something only a human can give
CANCELLED = "cancelled"
EXPIRED = "expired"

TERMINAL = {SUCCEEDED, FAILED, BLOCKED, CANCELLED, EXPIRED}

# What a state may become. A worker cannot resurrect a cancelled job, and
# nothing may leave a terminal state, which is what makes replay harmless.
ALLOWED = {
    OPEN: {WORKING, CANCELLED, EXPIRED},
    WORKING: {SUCCEEDED, FAILED, BLOCKED, CANCELLED, EXPIRED},
}


@dataclass
class Job:
    """An immutable request plus the mutable record of what happened to it."""

    spec: str                              # what to do, in words, for the worker
    contract: dict[str, Any]               # what must be true of the result
    requester: str = "engine"
    assignee: str = "delegate"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    filed_at: float = field(default_factory=time.time)
    deadline_at: float = 0.0
    status: str = OPEN
    history: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    verdict: dict[str, Any] | None = None

    @property
    def spec_hash(self) -> str:
        """Binds a result to the exact words that were sent. A worker that
        answers a different question fails the binding, not the reviewer's
        judgement."""
        payload = json.dumps({"spec": self.spec, "contract": self.contract},
                             sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


class JobQueue:
    """One directory, one file per job. No daemon, no broker, no sockets.

    A file-backed queue is not a limitation here: the point is that a handoff
    survives every process involved dying, and that the whole state of the
    exchange can be read by a person with `cat`.
    """

    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def _path(self, job_id: str) -> Path:
        return self.dir / f"{job_id}.json"

    # -- filing ------------------------------------------------------------
    def file(self, spec: str, contract: dict, *, requester: str = "engine",
             assignee: str = "delegate", deadline_hours: float = 24.0) -> Job:
        if not str(spec).strip():
            raise ValueError("a job needs a specification")
        if not contract:
            raise ValueError(
                "a job needs an acceptance contract: what must be true of the "
                "result, decided before the work is sent")
        job = Job(spec=str(spec), contract=dict(contract), requester=requester,
                  assignee=assignee,
                  deadline_at=time.time() + deadline_hours * 3600)
        job.history.append({"at": job.filed_at, "to": OPEN, "by": requester,
                            "note": f"filed, spec {job.spec_hash}"})
        self._write(job)
        return job

    def _write(self, job: Job) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self._path(job.id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(job), indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(p)

    def load(self, job_id: str) -> Job | None:
        try:
            d = json.loads(self._path(job_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return Job(**d)

    def all(self) -> list[Job]:
        if not self.dir.is_dir():
            return []
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                out.append(Job(**json.loads(p.read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError):
                continue
        return out

    def open_jobs(self) -> list[Job]:
        return [j for j in self.all() if j.status not in TERMINAL]

    # -- transitions -------------------------------------------------------
    def transition(self, job: Job, to: str, *, by: str, note: str = "") -> bool:
        """The only way a job changes. Refuses anything the state machine does
        not allow, which is what makes a redelivered message harmless: the
        second attempt to move an already-finished job simply fails."""
        if to not in ALLOWED.get(job.status, set()):
            return False
        job.status = to
        job.history.append({"at": time.time(), "to": to, "by": by, "note": note})
        self._write(job)
        return True

    def expire_overdue(self, now: float | None = None) -> list[Job]:
        """A job nobody answered does not linger as a live obligation. It
        expires loudly, in its own record, where a person can find it."""
        now = now or time.time()
        gone = []
        for j in self.open_jobs():
            if j.deadline_at and now > j.deadline_at:
                if self.transition(j, EXPIRED, by="clock",
                                   note=f"no result within the deadline"):
                    gone.append(j)
        return gone

    # -- results -----------------------------------------------------------
    def accept_result(self, job_id: str, result: dict, *, checks) -> tuple[bool, str]:
        """Take an answer from the worker and judge it against the contract.

        `checks` maps a contract key to a callable (expected, result) -> bool.
        The worker's own claim of success is data, never a verdict: nothing it
        says can move the job to SUCCEEDED except the contract passing.
        """
        job = self.load(job_id)
        if job is None:
            return False, f"no such job: {job_id}"
        if job.status in TERMINAL:
            return False, f"job is already {job.status}; results are not reopened"
        if result.get("spec_hash") and result["spec_hash"] != job.spec_hash:
            self.transition(job, FAILED, by="contract",
                            note="the result answers a different specification")
            return False, "spec hash mismatch: this answers a different question"

        job.result = dict(result)
        failures = []
        for key, expected in job.contract.items():
            check = checks.get(key)
            if check is None:
                failures.append(f"{key}: no check is defined for this clause")
                continue
            try:
                if not check(expected, result):
                    failures.append(f"{key}: not satisfied (wanted {expected!r})")
            except Exception as e:  # noqa: BLE001
                failures.append(f"{key}: the check raised {type(e).__name__}: {e}")

        job.verdict = {"at": time.time(), "failures": failures,
                       "passed": not failures}
        self._write(job)
        if failures:
            self.transition(job, FAILED, by="contract",
                            note="; ".join(failures)[:400])
            return False, "; ".join(failures)
        self.transition(job, SUCCEEDED, by="contract", note="every clause satisfied")
        return True, "accepted: the contract holds"
