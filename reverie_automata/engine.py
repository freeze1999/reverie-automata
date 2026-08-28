"""The flywheel: plan -> execute -> learn.

``Engine.run_cycle`` performs one full cycle. The gate (``gate.py``) has already
decided a cycle *should* happen; the engine decides *what* happens, by reasoning:

  1. PLAN     one planning session over the harvested context -> a structured Plan.
  2. EXECUTE  one session PER task (a live ledger row as each starts/ends), with
              the inspector as the tool-layer brake and risky tasks parked for
              approval; the agent keeps working on safe tasks meanwhile.
  3. LEARN    one wrap session -> journal, a derived grade, and falsifiable lessons;
              the outcome is written to durable memory and the next opening ritual.

The engine owns every deterministic write. Prompts are just strings; swap them.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import blast
from . import events
from . import mandate
from . import prompts as P
from . import referee as R
from .inbox import Inbox
from .planvalidate import validate_plan
from .routing import DELEGATE, route
from .types import ActionClass, Lesson, Outcome, Risk


def _transport_failed(raw: str) -> bool:
    """Did the model never answer, as opposed to answering badly?

    The adapters return their failures as text beginning with a bracketed
    marker, because a backend that raises kills a cycle and a cycle that dies
    leaves no record. The cost of that choice is that silence looks like speech
    to everything downstream, so it has to be recognised explicitly exactly
    once, here.
    """
    s = (raw or "").lstrip()
    return s.startswith("[local server error") or s.startswith("[transport error")


def _grab(tag, text):
    """One tagged block, ending at <<END>>, the next tag, or the end of text.

    A closing tag the model forgot is not a block the model did not write. The
    first LEARN output this engine ever collected opened four blocks and closed
    one, at the very bottom, and the strict form read that as a single enormous
    journal and zero lessons. The content was correct and specific and all of it
    was thrown away on punctuation.

    The rule everywhere else in this project applies to parsing too: the harness
    absorbs the executor's formatting instead of depending on it.
    """
    m = re.search(r"<<%s>>(.*?)(?:<<END>>|(?=<<[A-Z_]+>>)|\Z)" % tag, text, re.S)
    return m.group(1).strip() if m else ""


def _grab_all(tag, text):
    """Every block with this tag, same forgiving terminator."""
    return [m.strip() for m in re.findall(
        r"<<%s>>(.*?)(?:<<END>>|(?=<<[A-Z_]+>>)|\Z)" % tag, text, re.S) if m.strip()]


# situation -> action -> outcome, however the model chose to write it. The
# arrow form is what the prompt asks for; the labelled form is what a model
# reaching for a report format produces instead, and it carries exactly the
# same three fields.
_LABELLED = re.compile(
    r"situation\s*:\s*(.+?)\s*(?:^|\n)\s*action\s*:\s*(.+?)\s*(?:^|\n)\s*"
    r"(?:observed\s+outcome|outcome|observed)\s*:\s*(.+)",
    re.S | re.I)


# The prompt's own placeholder words. A model with nothing to report copies the
# example back rather than omitting the block, and `situation -> action ->
# observed outcome` parses perfectly: three fields, all non-empty, structurally
# indistinguishable from a real lesson. It was recorded as one on the first
# cycle after the parser was relaxed. An echo of the question is not an answer,
# and a false lesson is worse than no lesson because it reads as a finding.
_ECHO = {"situation", "action", "outcome", "observed", "observed outcome",
         "the outcome you actually observed", "none", "n/a", "na", "nothing",
         "up to three", "omit if none", "up to three; omit if none", "..."}


def _lesson_parts(body: str) -> list[str] | None:
    def clean(xs):
        out = [x.strip().rstrip(".").strip() for x in xs]
        if not all(out):
            return None
        if any(x.lower().strip("()") in _ECHO for x in out):
            return None
        return out

    parts = clean(re.split(r"->", body, maxsplit=2)) if body.count("->") >= 2 else None
    if parts:
        return parts
    m = _LABELLED.search(body)
    return clean(m.groups()) if m else None


def parse_plan(raw: str) -> dict | None:
    m = re.search(r"<<PLAN>>(.*?)<<END>>", raw, re.S)
    cand = m.group(1) if m else raw
    jm = re.search(r"\{.*\}", cand, re.S)
    if not jm:
        return None
    try:
        plan = json.loads(jm.group(0))
        if isinstance(plan, dict):
            plan.setdefault("tasks", [])
            return plan
    except Exception:
        return None
    return None


def derive_grade(ledger: list[dict]) -> str:
    attempted = [t for t in ledger if t["status"] in ("done", "failed")]
    if not attempted:
        return "N"
    done = sum(1 for t in attempted if t["status"] == "done")
    ratio = done / len(attempted)
    return "A" if ratio >= 0.8 else "B" if ratio >= 0.6 else "C" if ratio >= 0.3 else ("D" if done else "F")


# Word boundaries are not decoration here. Without them a guard meant for
# "production" fired on "re-prod-uce" and parked an exact-arithmetic
# computation, and "sudo" would have caught every "pseudo-inverse" in a
# mathematics workload. A guard that strangles legitimate traffic does not
# get called cautious; it gets switched off, and then it guards nothing.
RISKY_HINTS = re.compile(
    r"\bsudo\b|\bsystemctl\b|\bcrontab\b|\bdeploy\w*|\bpush\b|\binstall\w*|"
    r"\bdelete\w*|\bdrop\s+table\b|\brestart\w*|\bmigrat\w*|\bpassword\w*|"
    r"\bsecret\w*|\bproduction\b", re.I)
# `prod` was here beside `production` and had to go: a word boundary sits either
# side of a dot, so it matched `np.prod`, `math.prod` and `sympy.prod`, and
# parked a legitimate exact-arithmetic task for approval. This is the third
# time a token short enough to appear inside ordinary code has been used as a
# discriminator here, after re-prod-uce and after `program` in the routing
# rules. The lesson keeps arriving in the same envelope: a pattern that fires
# on a substring of normal work is not cautious, it is broken.


@dataclass
class _PlanPhase:
    raw: str
    plan: dict
    complaints: list[str]
    false_no_op: bool
    work_available: bool
    unreachable: bool
    context: str
    inbox_files: list[Path]


@dataclass
class _ExecutionPhase:
    ledger: list[dict]
    pre: dict
    before: dict
    inbox_consumed: int


@dataclass
class _LearnPhase:
    raw: str
    journal: str
    review: str
    lessons: list[Lesson]


@dataclass
class _GradePhase:
    grade: str
    moved: dict
    touched: list


@dataclass
class _TaskContext:
    task: dict
    tid: str
    what: str
    risk: str
    risk_pattern: str


class Engine:
    def __init__(self, cfg, store, harvester, inspector, agent, planner, approvals,
                 delegate=None):
        self.cfg, self.store, self.harvest = cfg, store, harvester
        self.inspector, self.agent, self.planner, self.approvals = inspector, agent, planner, approvals
        self.delegate = delegate
        # The program supplies both: what counts as work, and what counts as
        # progress. An engine with neither still runs the prose path, because
        # reverie also ships for idle companions who have no program.
        self.menu = cfg.get("menu")
        self.referee = cfg.get("referee")
        self.home = cfg.home
        self.memory_path = self.home / "MEMORY.md"
        self.inbox = Inbox(self.home / "inbox", cfg)

    def _standing(self) -> str:
        """The active standing orders, in full, for the planning context.

        Text, not authority: identical in standing to an inbox drop. It says
        what the post is for; it cannot widen the toolkit or unlock a path.
        """
        try:
            ms = [m for m in mandate.load(self.home / str(self.cfg.get("mandates_dir", "mandates")))
                  if m.active]
        except Exception:  # noqa: BLE001
            return ""
        if not ms:
            return ""
        return "\n\n".join(f"[{m.id}] {m.objective}\n{m.body}"[:4000] for m in ms)

    def _ruled_out(self, con) -> set[str]:
        """Work this program has already ruled out, as identity keys.

        A dead end has to be a constraint the planner cannot argue with. It was
        prose in a log before, competing for attention with everything else in
        the context, and the machine duly wrote a correct diagnosis of its own
        repetition loop and then repeated. Text does not bind; a set does.
        """
        if self.menu is None:
            return set()
        try:
            rows = con.execute(
                "SELECT body FROM threads WHERE kind='deadend'").fetchall()
        except Exception:  # noqa: BLE001
            return set()
        out: set[str] = set()
        for (body,) in rows:
            try:
                out.add(self.menu.key(json.loads(body or "{}")))
            except Exception:  # noqa: BLE001
                continue
        return out

    def _consecutive_no_ops(self, con) -> int:
        """How many cycles in a row ended in do_nothing, most recent first."""
        n = 0
        for (status,) in con.execute(
                "SELECT status FROM cycles WHERE finished_at IS NOT NULL "
                "ORDER BY started_at DESC LIMIT 10"):
            if status != "do_nothing":
                break
            n += 1
        return n

    def _typed_from_due_thread(self, con) -> dict | None:
        """A due thread whose body IS a typed task, handed over unchanged.

        The supply path. Measured twice: the executor picks a plausible task
        TYPE and leaves its required fields empty, because filling them means
        transcribing specific values, which is the one thing this class of
        model cannot do. Refusing the incomplete task is correct and produces
        nothing; the harness has to supply what the executor cannot author.
        That is the same rule as perturbing a repeated call, applied one level
        up: nothing is left to the executor noticing.
        """
        if self.menu is None:
            return None
        rows = self.store.due_threads(
            con, cooldown_minutes=float(self.cfg.get("thread_cooldown_minutes", 0) or 0),
            limit=20)
        for tid, kind, title in rows:
            # A dead end is a record of what was ruled out. Picking one up as
            # work is asking the machine to redo the thing it just abandoned,
            # and it did: a thread titled "dead end: write_artifact ..." was
            # forced into a task and answered with an unrelated computation.
            if kind == "deadend":
                continue
            row = con.execute("SELECT body FROM threads WHERE id=?", (tid,)).fetchone()
            if not row or not row[0]:
                continue
            try:
                task = json.loads(row[0])
            except ValueError:
                continue
            if not isinstance(task, dict) or not self.menu.get(task):
                continue
            ok, _ = self.menu.validate(task)
            if ok:
                return dict(task, id="t1", thread=tid,
                            why=f"supplied work, thread #{tid}",
                            risk=task.get("risk", "SAFE"))
        return None

    def _task_from_due_thread(self, con) -> dict | None:
        """The top due thread, as a task, unedited.

        Deliberately dumb. The wrapper is not trying to plan better than the
        model; it is refusing to let "nothing to do" stand when something is
        demonstrably due, and the thread's own title is the most honest
        statement of that something available without asking anyone.
        """
        # Under a typed menu this path must produce a typed task or nothing.
        # It used to build one out of a thread TITLE, with no type, and an
        # untyped task has no postcondition, so it passed unexamined. Measured
        # over 57 unattended cycles: twenty one tasks filed this way, three of
        # them graded done, all three false. One computed the sum of a hundred
        # rationals in answer to a thread titled "dead end: write_artifact".
        #
        # The gates applied to what the planner PROPOSED and not to what the
        # wrapper FORCED, which is the same blind spot as every other entry
        # where the operator turned out to be inside the threat model.
        if self.menu is not None:
            return None

        rows = self.store.due_threads(
            con, cooldown_minutes=float(self.cfg.get("thread_cooldown_minutes", 0) or 0),
            limit=1)
        if not rows:
            return None
        tid, kind, title = rows[0][0], rows[0][1], rows[0][2]
        body = con.execute("SELECT body FROM threads WHERE id=?", (tid,)).fetchone()
        return {"id": "t1", "what": title, "thread": tid, "mode": "tool",
                "risk": "SAFE",
                "why": (f"filed by the wrapper: this {kind} thread is due and the "
                        f"planner declined twice in a row. "
                        + (str(body[0])[:400] if body and body[0] else ""))}

    # -- risk (defense in depth: wrapper classifies too, and wins) ----------
    def _wrapper_risk(self, task: dict) -> tuple[str, str]:
        """Scan what the task INTENDS, never what it carries.

        A typed task can hold source code in a field, and a risk pattern
        written for a 240-character prose description then runs over a program.
        Every code-shaped token becomes a false positive: `prod`, `install`,
        `push` and `delete` all appear in ordinary source. So payload fields
        are excluded, and the classifier reads the fields that say what the
        task is for.
        """
        payload = ()
        if self.menu is not None:
            t = self.menu.get(task)
            payload = getattr(t, "payload", ()) if t else ()
        scanned = {k: v for k, v in task.items() if k not in payload}
        blob = json.dumps(scanned, ensure_ascii=False)
        m = RISKY_HINTS.search(blob)
        return ("RISKY", m.group(0)) if m else ("SAFE", "")

    def _open_cycle(self, now: datetime):
        con = self.store.connect()
        ts = base = now.strftime("%Y-%m-%d-%H%M%S")
        n = 1
        while con.execute("SELECT 1 FROM cycles WHERE ts=?", (ts,)).fetchone():
            ts = f"{base}-{n}"
            n += 1
        cdir = self.home / "cycles" / ts
        cdir.mkdir(parents=True, exist_ok=True)

        orphan = self.store.orphaned_cycle(con)
        if orphan:
            con.execute("UPDATE cycles SET status='recovered' WHERE ts=?", (orphan[0],))
            if con.execute("SELECT COUNT(*) FROM tasks WHERE cycle_ts=?", (orphan[0],)).fetchone()[0]:
                self.store.add_thread(con, f"recovery: cycle {orphan[0]} crashed mid-run",
                                      "reconcile its half-done work", kind="recovery", created_cycle=ts)
        con.execute("INSERT INTO cycles (ts, started_at, status) VALUES (?,?,'running')", (ts, now.timestamp()))
        con.commit()
        return con, ts, cdir

    def _planning_prompt(self, context: str) -> str:
        if str(self.cfg.get("trigger", "idle")).lower() not in ("work", "both"):
            return P.PLAN.format(context=context)
        typed = self.menu is not None
        return P.PLAN_STANDING.format(
            context=context, constraints=P.constraints(self.cfg),
            menu=(P.TYPED_MENU.format(menu=self.menu.describe()) if typed else ""),
            envelope=(P.TYPED_ENVELOPE if typed else P.PROSE_ENVELOPE))

    def _parse_and_validate_plan(self, con, raw: str, inbox_files: list[Path]):
        unreachable = _transport_failed(raw)
        if unreachable:
            plan = {"do_nothing": True, "tasks": [],
                    "do_nothing_reason": f"THE PLANNER NEVER ANSWERED: {raw[:200]}"}
        else:
            plan = parse_plan(raw) or {"do_nothing": True, "tasks": [],
                                       "do_nothing_reason": "unparseable plan"}
        work_available = bool(inbox_files) or bool(self.store.due_threads(
            con, cooldown_minutes=float(self.cfg.get("thread_cooldown_minutes", 0) or 0),
            limit=1))
        plan, complaints, false_no_op = validate_plan(
            plan, work_available=work_available,
            max_tasks=int(self.cfg.get("max_tasks_per_cycle", 8)),
            allow_text_tasks=bool(self.cfg.get("allow_text_tasks", True)),
            menu=self.menu, ruled_out=self._ruled_out(con))
        if unreachable:
            complaints = [f"the planner never answered: {raw[:200]}"]
            false_no_op = False
        return plan, complaints, false_no_op, work_available, unreachable

    def _apply_work_floors(self, con, plan, complaints, false_no_op):
        supplied = self._typed_from_due_thread(con)
        if supplied is not None:
            displaced = len(plan.get("tasks") or [])
            plan["tasks"] = [supplied]
            plan["do_nothing"] = False
            false_no_op = False
            message = (f"{displaced} planner task(s) displaced by complete supplied work"
                       if displaced else
                       "no task survived planning; a complete supplied task was due "
                       "and was filed by the wrapper")
            complaints.append(message)
        if false_no_op and self._consecutive_no_ops(con) >= 1:
            forced = self._task_from_due_thread(con)
            if forced:
                plan["tasks"] = [forced]
                plan["do_nothing"] = False
                false_no_op = False
                complaints.append(
                    "second refusal in a row with work due: the top due thread "
                    "was filed as the task by the wrapper")
        return plan, complaints, false_no_op

    def _emit_plan(self, ts, phase: _PlanPhase) -> None:
        for complaint in phase.complaints:
            print(f"[plan] {complaint}")
        plan = phase.plan
        events.emit(
            self.home, "plan", cycle=ts, work_available=phase.work_available,
            inbox=len(phase.inbox_files), do_nothing=bool(plan.get("do_nothing")),
            do_nothing_reason=str(plan.get("do_nothing_reason", ""))[:300],
            learned=str(plan.get("learned", ""))[:400],
            tasks=[{"id": task.get("id"), "what": str(task.get("what", ""))[:200],
                    "why": str(task.get("why", ""))[:200], "mode": task.get("mode"),
                    "risk": task.get("risk")} for task in plan.get("tasks", [])],
            complaints=phase.complaints, false_no_op=phase.false_no_op)

    def _plan_phase(self, con, ts, cdir) -> _PlanPhase:
        inbox_section, inbox_files = self.inbox.read()
        context, _ = self.harvest.build(con, {"inbox": inbox_section,
                                              "mandates": self._standing()})
        import os
        os.environ["REVERIE_CYCLE"], os.environ["REVERIE_HOME"] = ts, str(self.home)
        raw = self.planner.complete(
            "", self._planning_prompt(context),
            max_tokens=self.cfg["max_tool_turns"]["plan"] * 80)
        (cdir / "plan.txt").write_text(raw, encoding="utf-8")
        plan, complaints, false_no_op, work_available, unreachable = \
            self._parse_and_validate_plan(con, raw, inbox_files)
        if unreachable:
            events.emit(self.home, "planner_unreachable", cycle=ts, detail=raw[:300])
        plan, complaints, false_no_op = self._apply_work_floors(
            con, plan, complaints, false_no_op)
        phase = _PlanPhase(raw, plan, complaints, false_no_op, work_available,
                           unreachable, context, inbox_files)
        self._emit_plan(ts, phase)
        return phase

    def _execute_phase(self, con, ts, cdir, phase: _PlanPhase, text_only: bool):
        ledger: list[dict] = []
        pre = blast.snapshot(self.cfg["protected_paths"])
        before = self.referee.state() if self.referee else {}
        if not phase.plan.get("do_nothing"):
            for task in phase.plan.get("tasks", []):
                ledger.append(self._do_task(con, ts, cdir, task, text_only))
        n_inbox = 0
        if not phase.false_no_op and not phase.unreachable and ledger:
            n_inbox = self.inbox.consume(phase.inbox_files, ts)
        return _ExecutionPhase(ledger, pre, before, n_inbox)

    def _learn_phase(self, cdir, context, ledger):
        ledger_txt = "\n".join(f"- {e['id']} [{e['status']}] {e['what'][:80]}" for e in ledger) or "(nothing to do)"
        p3 = self.planner.complete("", P.LEARN.format(context=context, ledger=ledger_txt),
                                   max_tokens=self.cfg["learn_max_tokens"])
        (cdir / "learn.txt").write_text(p3, encoding="utf-8")
        journal = _grab("JOURNAL", p3) or p3[:1200]
        review = _grab("REVIEW", p3)
        lessons = [Lesson(*parts) for parts in
                   (_lesson_parts(l) for l in _grab_all("LESSON", p3)) if parts]
        return _LearnPhase(p3, journal, review, lessons)

    def _grade_phase(self, ts, phase: _PlanPhase, ledger, pre, before):
        touched = blast.diff(pre, blast.snapshot(self.cfg["protected_paths"]))
        if self.referee is not None:
            after = self.referee.state()
            moved = R.Referee.delta(before, after)
            grade = R.grade(moved,
                            attempted=bool([e for e in ledger
                                            if e["status"] in ("done", "failed")]),
                            honest_no_op=bool(phase.plan.get("do_nothing")) and
                            not phase.false_no_op)
            # A ledger that says done while the world did not move is the exact
            # shape of four separate A grades in the alpha. Recording the
            # disagreement is what turns it from a silent lie into a signal.
            claimed = [e for e in ledger if e["status"] == "done"]
            if claimed and not moved:
                phase.complaints.append(
                    f"{len(claimed)} task(s) reported done and the referee did not "
                    "move; the ledger is not the score")
                events.emit(self.home, "decoupled", cycle=ts,
                            claimed=[e["id"] for e in claimed], state=after)
        else:
            moved = {}
            grade = derive_grade(ledger)
        return _GradePhase(grade, moved, touched)

    def _persist_cycle(self, con, ts, plan, journal, review, lessons, grade):
        con.execute("INSERT OR REPLACE INTO journal (cycle_ts, body, created_at) VALUES (?,?,?)",
                    (ts, journal + (("\n\n[review]\n" + review) if review else ""), time.time()))
        if not plan.get("do_nothing"):
            for ls in lessons[:3]:
                if all([ls.situation, ls.action, ls.outcome]):
                    con.execute("INSERT INTO lessons (cycle_ts, situation, action, outcome, created_at) VALUES (?,?,?,?,?)",
                                (ts, ls.situation, ls.action, ls.outcome, time.time()))
            self._append_memory(lessons)
        elif lessons:
            print(f"[learn] {len(lessons)} lesson(s) discarded: a cycle that did "
                  "nothing has nothing to teach")
        con.execute("UPDATE cycles SET finished_at=?, status=?, grade=?, plan_json=? WHERE ts=?",
                    (time.time(), "done" if not plan.get("do_nothing") else "do_nothing", grade,
                     json.dumps(plan, ensure_ascii=False), ts))
        con.commit()
        con.close()

    def _write_outcome(self, now, ts, cdir, phase, execution, learned, graded):
        ledger = execution.ledger
        lessons = learned.lessons
        outcome = Outcome(when=now, action_class=ActionClass.NOTHING if phase.plan.get("do_nothing") else ActionClass.NEEDS_TOOL,
                          grade=graded.grade, phase1=phase.raw, phase2=learned.raw,
                          ledger=ledger, lessons=lessons, journal=learned.journal,
                          blast_radius=graded.touched)
        (cdir / "outcome.json").write_text(json.dumps({
            "ts": ts, "grade": graded.grade, "plan": phase.plan, "ledger": ledger,
            "brain": self._brain(),
            "blast_radius": graded.touched,
            "inbox_consumed": execution.inbox_consumed,
            "plan_complaints": phase.complaints, "false_no_op": phase.false_no_op,
            "referee_before": execution.before, "referee_moved": graded.moved,
            "lessons": [l.__dict__ for l in lessons]}, ensure_ascii=False, indent=2), encoding="utf-8")
        events.emit(self.home, "cycle", cycle=ts, grade=graded.grade,
                    moved=graded.moved,
                    statuses=[e["status"] for e in ledger],
                    blast=len(graded.touched),
                    inbox_consumed=execution.inbox_consumed,
                    lessons=[f"{l.situation} -> {l.action} -> {l.outcome}" for l in lessons],
                    journal=learned.journal[:600])
        return outcome

    def run_cycle(self, now: datetime | None = None, text_only: bool = False) -> Outcome:
        now = now or datetime.now()
        con, ts, cdir = self._open_cycle(now)
        phase = self._plan_phase(con, ts, cdir)
        execution = self._execute_phase(con, ts, cdir, phase, text_only)
        learned = self._learn_phase(cdir, phase.context, execution.ledger)
        graded = self._grade_phase(
            ts, phase, execution.ledger, execution.pre, execution.before)
        self._persist_cycle(
            con, ts, phase.plan, learned.journal, learned.review,
            learned.lessons, graded.grade)
        return self._write_outcome(now, ts, cdir, phase, execution, learned,
                                   graded)

    # -- which brain answered ------------------------------------------------
    def _brain(self) -> dict:
        """The executor's own identity, stamped into every cycle record.

        Every gate in this engine grades what the machine DID. None of them
        record what the machine WAS, so two runs of the same instance were
        never comparable and nobody could tell. It cost eight days: a unit at
        boot replaced the model and cut the window to a quarter, the cycles
        after it were read against the reports of the cycles before it, and the
        swap was only found by reading a systemd file weeks later.

        A changed brain is announced loudly and does not stop the cycle. A
        harness that refuses to run is a harness that stops recording, and the
        record is the thing being protected. Introspection is best effort:
        a backend that cannot say who it is says nothing, and never raises.
        """
        for src in (self.agent, self.planner):
            probe = getattr(src, "server_identity", None)
            if probe is None:
                continue
            try:
                now = probe()
            except Exception:
                continue
            if not now:
                continue
            seen = self.home / "brain.json"
            try:
                was = json.loads(seen.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                was = None
            if was and was != now:
                events.emit(self.home, "brain_changed", was=was, now=now)
            if was != now:
                try:
                    seen.write_text(json.dumps(now, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
                except OSError:
                    pass  # the stamp in the cycle record is the durable copy
            return now
        return {}

    def _note_task(self, con, ts, tid, what, status, why) -> None:
        """Put a task that never ran into the ledger anyway, with its reason.

        Parked and skipped tasks used to return before the ledger row was
        written, so from the record's point of view they had not happened at
        all. Nothing downstream could report them and nothing could carry them
        into the next cycle. A refusal that leaves no trace is the same shape
        as the work simply not existing, and the machine cannot tell those
        apart any more than we could.
        """
        try:
            con.execute("INSERT INTO tasks (cycle_ts, task_id, what, mode, risk, "
                        "status, started_at, ended_at, result) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (ts, tid, what, "", "", status, time.time(), time.time(),
                         str(why)[:500]))
            con.commit()
        except Exception:  # noqa: BLE001
            pass  # the ledger entry is worth having and never worth a crash

    # -- one task -----------------------------------------------------------
    def _task_context(self, ts, task) -> _TaskContext:
        tid = str(task.get("id", "?"))
        # A typed task has no prose to run on, and that is the point: the
        # instruction the executor sees is generated from the fields rather
        # than written by the planner, so there is no free text for a tool name
        # or a mangled value to hide in.
        what = (self.menu.render(task) if self.menu and self.menu.get(task)
                else task.get("what", ""))
        wrapper_risk, pattern = self._wrapper_risk(task)
        # Who is allowed to call this risky. For TYPED work: the menu, which
        # decided once for the whole kind, and the wrapper, which reads the
        # task's stated intent. Not the planner. It filled that field with
        # RISKY 218 times running for a read-only lookup, and a self-declared
        # RISKY parks the task, so the machine spent a night filing approvals
        # against itself into a queue nobody opens, completed nothing, and
        # blamed something else in every review because nothing told it why.
        #
        # Untyped work keeps the old rule: with no menu entry, the planner's
        # word is the only signal there is, and an unclassified task should
        # stop rather than run.
        declared = str(task.get("risk", "SAFE")).upper()
        t = self.menu.get(task) if self.menu is not None else None
        if t is not None:
            typed_risk = str(getattr(t, "risk", "SAFE")).upper()
            risk = "RISKY" if "RISKY" in (wrapper_risk, typed_risk) else "SAFE"
            if declared == "RISKY" and risk == "SAFE":
                # Recorded rather than obeyed, so the habit stays measurable.
                events.emit(self.home, "risk_overridden", cycle=ts, task=tid,
                            type=t.name, declared=declared, applied=risk)
        else:
            risk = "RISKY" if "RISKY" in (wrapper_risk, declared) else "SAFE"
        return _TaskContext(task, tid, what, risk, pattern)

    def _park_risky(self, con, ts, ctx: _TaskContext) -> dict | None:
        if ctx.risk != "RISKY":
            return None
        task, tid, what = ctx.task, ctx.tid, ctx.what
        reason = task.get("risk_reason") or ctx.risk_pattern
        self._file_approval(con, ts, task, reason)
        self.store.add_thread(con, f"parked (awaiting approval): {what[:100]}",
                              json.dumps(task, ensure_ascii=False), kind="approval",
                              created_cycle=ts, defer=True, unique=True)
        why = (f"parked awaiting approval, because this task was classified "
               f"RISKY ({reason or 'no reason given'}). It was never run. "
               "Approvals are opened by a person and nothing happens until one is.")
        self._note_task(con, ts, tid, what, "parked", why)
        return {"id": tid, "status": "parked", "what": what, "why": why}

    def _skip_already_done(self, con, ts, ctx: _TaskContext) -> dict | None:
        if self.menu is None:
            return None
        task, tid, what = ctx.task, ctx.tid, ctx.what
        done_already, why_already = self.menu.already_done(task, self.home)
        if not done_already:
            return None
        if task.get("thread"):
            self.store.close_thread(con, task["thread"],
                                    f"already satisfied: {why_already}")
        events.emit(self.home, "already_done", cycle=ts, task=tid,
                    why=why_already[:200], what=what[:150])
        why = f"already true before this cycle: {why_already}"
        self._note_task(con, ts, tid, what, "skipped", why)
        return {"id": tid, "status": "skipped", "what": what,
                "verify": why, "why": why}

    def _delegate_task(self, con, ts, ctx: _TaskContext, why: str) -> dict | None:
        task, tid, what = ctx.task, ctx.tid, ctx.what
        job_id, note = self.delegate.file(task, cycle=ts)
        events.emit(self.home, "route", cycle=ts, task=tid, where=DELEGATE,
                    reason=why, job=job_id, note=note, what=what[:200])
        if job_id:
            self.store.add_thread(con, f"awaiting job {job_id}: {what[:80]}",
                                  json.dumps({"job": job_id, "task": task},
                                             ensure_ascii=False),
                                  kind="delegated", created_cycle=ts,
                                  defer=True, unique=True)
            receipt = f"job {job_id} filed: {why}"
            self._note_task(con, ts, tid, what, "delegated", receipt)
            return {"id": tid, "status": "delegated", "what": what,
                    "verify": receipt, "why": receipt}
        note = str(note)
        if note.startswith("solved:"):
            if task.get("thread"):
                self.store.close_thread(con, task["thread"], note)
            events.emit(self.home, "solved", cycle=ts, task=tid,
                        note=note, what=what[:200])
            self._note_task(con, ts, tid, what, "skipped", note)
            return {"id": tid, "status": "skipped", "what": what,
                    "verify": note, "why": note}
        if note.startswith("defer:") or "concurrency cap" in note:
            self.store.add_thread(con, f"waiting on a free worker: {what[:80]}",
                                  json.dumps(task, ensure_ascii=False),
                                  kind="delegated", created_cycle=ts,
                                  defer=True, unique=True)
            receipt = f"not attempted locally: {note}"
            self._note_task(con, ts, tid, what, "deferred", receipt)
            return {"id": tid, "status": "deferred", "what": what,
                    "verify": receipt, "why": receipt}
        print(f"[route] delegation unavailable ({note}); running locally")
        return None

    def _route_task(self, con, ts, ctx: _TaskContext) -> dict | None:
        where, why = route(ctx.task, self.cfg)
        if where == DELEGATE and self.delegate is not None:
            return self._delegate_task(con, ts, ctx, why)
        if where == DELEGATE:
            events.emit(self.home, "route", cycle=ts, task=ctx.tid,
                        where="local",
                        reason=f"would delegate ({why}) but no delegate is configured",
                        what=ctx.what[:200])
        return None

    def _skip_text_only(self, con, ts, ctx: _TaskContext,
                        text_only: bool) -> dict | None:
        if not text_only or ctx.task.get("mode") != "tool":
            return None
        self.store.add_thread(
            con, f"deferred (text-only budget): {ctx.what[:100]}", "",
            created_cycle=ts, defer=True, unique=True)
        why = "not attempted locally: text-only budget"
        self._note_task(con, ts, ctx.tid, ctx.what, "skipped", why)
        return {"id": ctx.tid, "status": "skipped", "what": ctx.what,
                "verify": why, "why": why}

    def _start_task(self, con, ts, cdir, ctx: _TaskContext) -> None:
        task = ctx.task
        if task.get("thread"):
            try:
                self.store.mark_thread_attempted(con, int(task["thread"]))
            except (TypeError, ValueError):
                pass
        con.execute(
            "INSERT INTO tasks (cycle_ts, task_id, what, mode, risk, status, started_at) "
            "VALUES (?,?,?,?,?,'started',?)",
            (ts, ctx.tid, ctx.what, task.get("mode"), ctx.risk, time.time()))
        con.commit()
        try:
            (cdir / f"task_{ctx.tid}.json").write_text(
                json.dumps(task, ensure_ascii=False, indent=1), encoding="utf-8")
        except (OSError, TypeError):
            pass

    def _run_task(self, ts, ctx: _TaskContext) -> str:
        task = ctx.task
        if task.get("mode") == "text":
            return self.planner.complete(
                "", P.EXECUTE_TEXT_ONLY.format(context="", what=ctx.what),
                max_tokens=1200)
        return self.agent.run_session(
            P.EXECUTE.format(context="", task_id=ctx.tid, what=ctx.what,
                             why=task.get("why", ""),
                             turn_cap=self.cfg["max_tool_turns"]["execute"]),
            cwd=str(self.home), env=self._cycle_env(ts),
            turn_cap=self.cfg["max_tool_turns"]["execute"])

    def _handle_failure(self, con, ts, ctx: _TaskContext, verify: str) -> None:
        title = f"resume failed task: {ctx.what[:100]}"
        limit = int(self.cfg.get("max_task_attempts", 3))
        tried = self.store.attempts(con, title)
        if tried + 1 < limit:
            self.store.add_thread(con, title, verify[:400], created_cycle=ts,
                                  defer=True, unique=True)
            self.store.bump_attempt(con, title)
            return
        self.store.add_thread(
            con, f"dead end: {ctx.what[:100]}",
            f"abandoned after {tried + 1} attempts. Last failure: {verify[:400]}",
            kind="deadend", created_cycle=ts, defer=True, unique=True)
        for row in con.execute(
                "SELECT id FROM threads WHERE title=? AND status='open'",
                (title,)).fetchall():
            self.store.close_thread(con, row[0],
                                    f"abandoned after {tried + 1} attempts")
        events.emit(self.home, "abandoned", cycle=ts, task=ctx.tid,
                    attempts=tried + 1, what=ctx.what[:200], last=verify[:300])

    def _finish_task(self, con, ts, ctx: _TaskContext, raw: str) -> dict:
        result = (_grab("RESULT", raw) or "failed").lower()
        verify = _grab("VERIFY", raw)
        status = result if result in ("done", "failed", "parked") else "failed"
        if status == "done" and not verify:
            status = "failed"
        if status == "done" and self.menu is not None:
            ok, why = self.menu.check(ctx.task, verify, self.home)
            if not ok:
                status = "failed"
                verify = f"postcondition failed: {why}\n\n{verify}"
                events.emit(self.home, "postcondition", cycle=ts, task=ctx.tid,
                            passed=False, why=why[:300])
        con.execute(
            "UPDATE tasks SET status=?, ended_at=?, result=? "
            "WHERE cycle_ts=? AND task_id=? AND status='started'",
            (status, time.time(), verify[:2000], ts, ctx.tid))
        con.commit()
        if status == "failed":
            self._handle_failure(con, ts, ctx, verify)
        events.emit(self.home, "task", cycle=ts, task=ctx.tid, status=status,
                    mode=ctx.task.get("mode"), what=ctx.what[:200],
                    verify=verify[:400], steps=raw.count("\n[") or None)
        return {"id": ctx.tid, "status": status, "what": ctx.what,
                "verify": verify[:200]}

    def _do_task(self, con, ts, cdir, task, text_only) -> dict:
        ctx = self._task_context(ts, task)
        decision = self._park_risky(con, ts, ctx)
        if decision is not None:
            return decision
        decision = self._skip_already_done(con, ts, ctx)
        if decision is not None:
            return decision
        decision = self._route_task(con, ts, ctx)
        if decision is not None:
            return decision
        decision = self._skip_text_only(con, ts, ctx, text_only)
        if decision is not None:
            return decision
        self._start_task(con, ts, cdir, ctx)
        raw = self._run_task(ts, ctx)
        (cdir / f"task_{ctx.tid}.txt").write_text(raw, encoding="utf-8")
        return self._finish_task(con, ts, ctx, raw)

    def _file_approval(self, con, ts, task, reason):
        con.execute("INSERT INTO approvals (cycle_ts, artifact, reasoning, status, filed_at, expires_at) "
                    "VALUES (?,?,?, 'pending', ?, ?)",
                    (ts, json.dumps(task, ensure_ascii=False), reason, time.time(), time.time() + 24 * 3600))
        con.commit()

    def _cycle_env(self, ts):
        import os
        # CYCLE marks a session so a pre-tool hook can gate it; HOME lets a
        # tool loop write its steps where an observer is already looking.
        return dict(os.environ, REVERIE_CYCLE=ts, REVERIE_HOME=str(self.home))

    def _append_memory(self, lessons):
        """Append what is new, where new means more than not byte-identical.

        The exact-match guard here was the whole defence, and a model that
        restates one observation three ways defeats it without trying. Measured
        over one night: 522 recorded lessons, three a cycle, every one of them
        the same sentence about the same blocked citation with the clauses
        reordered. Nothing was learned twice; it was written down 522 times.

        So the comparison is on the shape of the situation rather than its
        wording. Cheap, and it does not need to be clever: an exact restatement
        with a synonym swapped is still worth one line, not three.
        """
        if not lessons:
            return

        def key(text: str) -> str:
            return " ".join(sorted(set(
                w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split()
                if len(w) > 3)))[:400]

        try:
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            have = (self.memory_path.read_text(encoding="utf-8")
                    if self.memory_path.exists() else "")
            seen = {key(l) for l in have.splitlines() if l.strip()}
            with open(self.memory_path, "a", encoding="utf-8") as f:
                for l in lessons[:3]:
                    line = f"- {l.situation} -> {l.action} -> {l.outcome}"
                    k = key(line)
                    if k in seen:
                        continue
                    seen.add(k)
                    f.write(line + "\n")
        except Exception:
            pass
