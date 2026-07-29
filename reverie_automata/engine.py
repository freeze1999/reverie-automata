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


def _grab(tag, text):
    m = re.search(r"<<%s>>(.*?)<<END>>" % tag, text, re.S)
    return m.group(1).strip() if m else ""


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

    def run_cycle(self, now: datetime | None = None, text_only: bool = False) -> Outcome:
        now = now or datetime.now()
        con = self.store.connect()
        # A work-gated heartbeat can fire twice inside one second when cycles
        # are short (a no-op cycle costs milliseconds), and a second-resolution
        # id then collides on the primary key and kills the second cycle. The
        # idle engine could never do this; the standing one does it routinely.
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

        # --- PLAN ---
        # Reading the inbox is pure; the drops are archived only once a plan
        # exists, so a failed inference leaves them for the next cycle.
        inbox_section, inbox_files = self.inbox.read()
        context, _ = self.harvest.build(con, {"inbox": inbox_section,
                                              "mandates": self._standing()})
        # Which opening the planner gets is a fact about the post, not a
        # preference: an engine woken because work is due must not be told that
        # nobody is asking anything of it.
        if str(self.cfg.get("trigger", "idle")).lower() in ("work", "both"):
            typed = self.menu is not None
            prompt = P.PLAN_STANDING.format(
                context=context, constraints=P.constraints(self.cfg),
                menu=(P.TYPED_MENU.format(menu=self.menu.describe()) if typed else ""),
                envelope=(P.TYPED_ENVELOPE if typed else P.PROSE_ENVELOPE))
        else:
            prompt = P.PLAN.format(context=context)
        import os
        os.environ["REVERIE_CYCLE"], os.environ["REVERIE_HOME"] = ts, str(self.home)
        p1 = self.planner.complete("", prompt,
                                   max_tokens=self.cfg["max_tool_turns"]["plan"] * 80)
        (cdir / "plan.txt").write_text(p1, encoding="utf-8")
        plan = parse_plan(p1) or {"do_nothing": True, "do_nothing_reason": "unparseable plan", "tasks": []}
        # A schema can guarantee the plan's shape; only the engine can tell
        # whether "nothing to do" is an honest lazy day or a missed shift, so
        # the eligibility answer comes from here and never from the model.
        work_available = bool(inbox_files) or bool(self.store.due_threads(
            con, cooldown_minutes=float(self.cfg.get("thread_cooldown_minutes", 0) or 0),
            limit=1))
        plan, plan_complaints, false_no_op = validate_plan(
            plan, work_available=work_available,
            max_tasks=int(self.cfg.get("max_tasks_per_cycle", 8)),
            allow_text_tasks=bool(self.cfg.get("allow_text_tasks", True)),
            menu=self.menu, ruled_out=self._ruled_out(con))
        # A guard that only objects is not a floor. Watched live: with a
        # standing order open and due, the planner declared the program "at a
        # stalemate with no actionable path forward", the false-no-op check
        # caught it, nothing changed, and the next cycle declared the same
        # thing. The work stayed due, so the engine kept firing and kept
        # refusing, which is a livelock at heartbeat speed dressed as an honest
        # lazy day.
        #
        # So after a second consecutive refusal the wrapper stops asking. It
        # takes the top due thread and files it as the task, verbatim. This is
        # not the engine overruling judgment about WHETHER to work, which was
        # never the model's to make (the gate decides that); it is the engine
        # declining to accept "there is nothing to do" from a party that has
        # already been shown there is.
        # Supplied typed work outranks whatever the planner invented, not only
        # a refusal. Watched live: once the grammar was fixed the planner
        # happily authored its own tasks every cycle, so a fallback that fired
        # only on an empty plan never fired at all, and three complete supplied
        # tasks sat untouched for nine cycles while the machine invented and
        # failed at its own versions of the same work.
        #
        # The queue is authority and the plan is a proposal. Work that came
        # from outside, complete and due, is not competing with a guess.
        if not plan.get("tasks") or self._typed_from_due_thread(con) is not None:
            supplied = self._typed_from_due_thread(con)
            if supplied is not None:
                plan["tasks"] = [supplied]
                plan["do_nothing"] = False
                false_no_op = False
                plan_complaints.append(
                    "no task survived planning; a complete supplied task was due "
                    "and was filed by the wrapper")

        if false_no_op and self._consecutive_no_ops(con) >= 1:
            forced = self._task_from_due_thread(con)
            if forced:
                plan["tasks"] = [forced]
                plan["do_nothing"] = False
                false_no_op = False
                plan_complaints.append(
                    "second refusal in a row with work due: the top due thread "
                    "was filed as the task by the wrapper")
        for c in plan_complaints:
            print(f"[plan] {c}")
        # The reasoning, not the log line: what it saw as due, what it decided
        # to do about that, and every objection the wrapper raised to the
        # decision. Read across a run, this is where drift becomes visible.
        events.emit(self.home, "plan", cycle=ts, work_available=work_available,
                    inbox=len(inbox_files), do_nothing=bool(plan.get("do_nothing")),
                    do_nothing_reason=str(plan.get("do_nothing_reason", ""))[:300],
                    learned=str(plan.get("learned", ""))[:400],
                    tasks=[{"id": t.get("id"), "what": str(t.get("what", ""))[:200],
                            "why": str(t.get("why", ""))[:200], "mode": t.get("mode"),
                            "risk": t.get("risk")} for t in plan.get("tasks", [])],
                    complaints=plan_complaints, false_no_op=false_no_op)

        # A drop is spent by a cycle that ENGAGED with it. A cycle that
        # wrongly declared there was nothing to do did not engage, and
        # archiving the request anyway would let a weak planner quietly eat
        # work by claiming a lazy day. Leave it in the queue for the next one.
        n_inbox = 0 if false_no_op else self.inbox.consume(inbox_files, ts)

        ledger: list[dict] = []
        pre = blast.snapshot(self.cfg["protected_paths"])
        # The referee is read BEFORE the work and again after. What a cycle
        # achieved is the difference between two readings of the world, and it
        # is not available from anything the cycle says about itself.
        before = self.referee.state() if self.referee else {}
        if not plan.get("do_nothing"):
            for task in plan.get("tasks", []):
                ledger.append(self._do_task(con, ts, cdir, task, text_only))

        # --- LEARN ---
        ledger_txt = "\n".join(f"- {e['id']} [{e['status']}] {e['what'][:80]}" for e in ledger) or "(nothing to do)"
        p3 = self.agent.run_session(P.LEARN.format(context=context, ledger=ledger_txt),
                                    cwd=str(self.home), env=self._cycle_env(ts),
                                    turn_cap=self.cfg["max_tool_turns"]["learn"])
        # The LEARN phase holds real tools and its transcript was the one thing
        # a cycle never wrote down. That gap was found the hard way: a file
        # appeared in the working tree during a cycle, and the only phase whose
        # tool calls were not on disk was this one, so the question of what
        # created it could not be answered from the record at all. Every phase
        # that can touch the world leaves its transcript.
        (cdir / "learn.txt").write_text(p3, encoding="utf-8")
        journal = _grab("JOURNAL", p3) or p3[:1200]
        review = _grab("REVIEW", p3)
        lessons = [Lesson(*[x.strip() for x in re.split(r"->", l, maxsplit=2)])
                   for l in re.findall(r"<<LESSON>>(.*?)<<END>>", p3, re.S)
                   if len(re.split(r"->", l, maxsplit=2)) == 3]

        # Tasks and the LEARN session both hold real tools, so the post
        # snapshot happens after both; anything in the watch set that changed,
        # appeared, or vanished during the cycle lands in the outcome.
        touched = blast.diff(pre, blast.snapshot(self.cfg["protected_paths"]))

        if self.referee is not None:
            after = self.referee.state()
            moved = R.Referee.delta(before, after)
            grade = R.grade(moved,
                            attempted=bool([e for e in ledger
                                            if e["status"] in ("done", "failed")]),
                            honest_no_op=bool(plan.get("do_nothing")) and not false_no_op)
            # A ledger that says done while the world did not move is the exact
            # shape of four separate A grades in the alpha. Recording the
            # disagreement is what turns it from a silent lie into a signal.
            claimed = [e for e in ledger if e["status"] == "done"]
            if claimed and not moved:
                plan_complaints.append(
                    f"{len(claimed)} task(s) reported done and the referee did not "
                    "move; the ledger is not the score")
                events.emit(self.home, "decoupled", cycle=ts,
                            claimed=[e["id"] for e in claimed], state=after)
        else:
            moved = {}
            grade = derive_grade(ledger)
        con.execute("INSERT OR REPLACE INTO journal (cycle_ts, body, created_at) VALUES (?,?,?)",
                    (ts, journal + (("\n\n[review]\n" + review) if review else ""), time.time()))
        # A cycle that did nothing has nothing to teach. Evidence gates "done";
        # it must also gate "learned", because a lesson is the one artifact
        # that becomes permanent context at the highest priority and steers
        # every later decision.
        #
        # Observed live, and the reason this exists: an early no-op cycle
        # wrote "a lazy cycle with one small text task -> summarise instead of
        # forcing tool work -> the working set stayed legible and cheap". That
        # sentence then rode in every context and taught the engine to decline
        # real work, so a defect became doctrine. Note the asymmetry: a FAILED
        # cycle may still teach, because a failure is an event with content and
        # a recorded dead end is worth more than a fresh idea. A no-op is not
        # an event; "doing nothing was cheap" is a rationalisation wearing a
        # lesson's clothes.
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

        outcome = Outcome(when=now, action_class=ActionClass.NOTHING if plan.get("do_nothing") else ActionClass.NEEDS_TOOL,
                          grade=grade, phase1=p1, phase2=p3, ledger=ledger, lessons=lessons, journal=journal,
                          blast_radius=touched)
        (cdir / "outcome.json").write_text(json.dumps({
            "ts": ts, "grade": grade, "plan": plan, "ledger": ledger,
            "blast_radius": touched, "inbox_consumed": n_inbox,
            "plan_complaints": plan_complaints, "false_no_op": false_no_op,
            "referee_before": before, "referee_moved": moved,
            "lessons": [l.__dict__ for l in lessons]}, ensure_ascii=False, indent=2), encoding="utf-8")
        events.emit(self.home, "cycle", cycle=ts, grade=grade, moved=moved,
                    statuses=[e["status"] for e in ledger],
                    blast=len(touched), inbox_consumed=n_inbox,
                    lessons=[f"{l.situation} -> {l.action} -> {l.outcome}" for l in lessons],
                    journal=journal[:600])
        return outcome

    # -- one task -----------------------------------------------------------
    def _do_task(self, con, ts, cdir, task, text_only) -> dict:
        tid = str(task.get("id", "?"))
        # A typed task has no prose to run on, and that is the point: the
        # instruction the executor sees is generated from the fields rather
        # than written by the planner, so there is no free text for a tool name
        # or a mangled value to hide in.
        what = (self.menu.render(task) if self.menu and self.menu.get(task)
                else task.get("what", ""))
        wrisk, wpat = self._wrapper_risk(task)
        final = "RISKY" if "RISKY" in (wrisk, str(task.get("risk", "SAFE")).upper()) else "SAFE"
        if final == "RISKY":
            self._file_approval(con, ts, task, task.get("risk_reason") or wpat)
            self.store.add_thread(con, f"parked (awaiting approval): {what[:100]}",
                                  json.dumps(task, ensure_ascii=False), kind="approval",
                                  created_cycle=ts, defer=True, unique=True)
            return {"id": tid, "status": "parked", "what": what}
        # Routing, before any work is attempted. The model has already had its
        # say (it wrote the task); this is the wrapper deciding who does it,
        # and the reason is quoted from the task's own words so the decision
        # can be argued with later.
        # Already true before we started? Then there is no work here, and
        # spending a cycle to discover that is how three false completions and
        # fifty wasted attempts happened in one run.
        if self.menu is not None:
            done_already, why_already = self.menu.already_done(task, self.home)
            if done_already:
                if task.get("thread"):
                    self.store.close_thread(con, task["thread"],
                                            f"already satisfied: {why_already}")
                events.emit(self.home, "already_done", cycle=ts, task=tid,
                            why=why_already[:200], what=what[:150])
                return {"id": tid, "status": "skipped", "what": what,
                        "verify": f"already true before this cycle: {why_already}"}

        where, why = route(task, self.cfg)
        if where == DELEGATE and self.delegate is not None:
            job_id, note = self.delegate.file(task, cycle=ts)
            events.emit(self.home, "route", cycle=ts, task=tid, where=where,
                        reason=why, job=job_id, note=note, what=what[:200])
            if job_id:
                # Deferred, so the next tick does not immediately re-plan work
                # that is already out for answer. The thread is the obligation:
                # while it is open the job is not forgotten, and when the answer
                # lands the cycle that reads it has the context to use it.
                self.store.add_thread(con, f"awaiting job {job_id}: {what[:80]}",
                                      json.dumps({"job": job_id, "task": task},
                                                 ensure_ascii=False),
                                      kind="delegated", created_cycle=ts,
                                      defer=True, unique=True)
                return {"id": tid, "status": "delegated", "what": what,
                        "verify": f"job {job_id} filed: {why}"}
            # Two different failures wear the same empty job id, and treating
            # them alike is wrong in one direction or the other. A delegate
            # that is DOWN (unconfigured, unreachable) should not stop the
            # engine: doing the work badly here beats not doing it at all. A
            # delegate that is merely BUSY should not make the engine attempt
            # the exact thing it delegated the task to avoid; the work waits,
            # because the reason it was routed out has not changed.
            # Already answered. Not a failure and not work: the question has
            # been asked, accepted and written into the record, so the only
            # correct action is to stop carrying it. Ten identical jobs went
            # to a human collaborator before this existed.
            if str(note).startswith("solved:"):
                if task.get("thread"):
                    self.store.close_thread(con, task["thread"], note)
                events.emit(self.home, "solved", cycle=ts, task=tid,
                            note=note, what=what[:200])
                return {"id": tid, "status": "skipped", "what": what,
                        "verify": note}
            if str(note).startswith("defer:") or "concurrency cap" in str(note):
                self.store.add_thread(con, f"waiting on a free worker: {what[:80]}",
                                      json.dumps(task, ensure_ascii=False),
                                      kind="delegated", created_cycle=ts,
                                      defer=True, unique=True)
                return {"id": tid, "status": "deferred", "what": what,
                        "verify": f"not attempted locally: {note}"}
            print(f"[route] delegation unavailable ({note}); running locally")
        elif where == DELEGATE:
            events.emit(self.home, "route", cycle=ts, task=tid, where="local",
                        reason=f"would delegate ({why}) but no delegate is configured",
                        what=what[:200])

        if text_only and task.get("mode") == "tool":
            self.store.add_thread(con, f"deferred (text-only budget): {what[:100]}", "",
                                  created_cycle=ts, defer=True, unique=True)
            return {"id": tid, "status": "skipped", "what": what}

        if task.get("thread"):
            try:
                self.store.mark_thread_attempted(con, int(task["thread"]))
            except (TypeError, ValueError):
                pass  # a malformed thread id is the planner's problem, not a crash
        con.execute("INSERT INTO tasks (cycle_ts, task_id, what, mode, risk, status, started_at) "
                    "VALUES (?,?,?,?,?,'started',?)", (ts, tid, what, task.get("mode"), final, time.time()))
        con.commit()
        # The typed task itself, on disk, before the session that must satisfy
        # it. Two reasons, and the second is the load-bearing one.
        #
        # For the record: the fields were only ever recoverable from the plan
        # blob, so reading what a cycle was actually asked to do meant parsing
        # the planner's output rather than reading the task.
        #
        # For the tools: a task type's fields are known to the harness and were
        # nonetheless required to travel through the executor, which had to
        # retype them into a tool call. That is the one operation this class of
        # model has been measured unable to do. Watched live, an executor that
        # had just computed the right answer wrote the source filename back
        # with a letter missing and dropped three fields on the way. A tool can
        # now read the task it is being used to satisfy, and ask the model only
        # for what the harness cannot know.
        try:
            (cdir / f"task_{tid}.json").write_text(
                json.dumps(task, ensure_ascii=False, indent=1), encoding="utf-8")
        except (OSError, TypeError):
            pass  # the record is worth having and never worth a crash
        if task.get("mode") == "text":
            raw = self.planner.complete("", P.EXECUTE_TEXT_ONLY.format(context="", what=what), max_tokens=1200)
        else:
            raw = self.agent.run_session(P.EXECUTE.format(context="", task_id=tid, what=what, why=task.get("why", ""),
                                                          turn_cap=self.cfg["max_tool_turns"]["execute"]),
                                         cwd=str(self.home), env=self._cycle_env(ts),
                                         turn_cap=self.cfg["max_tool_turns"]["execute"])
        (cdir / f"task_{tid}.txt").write_text(raw, encoding="utf-8")
        result = (_grab("RESULT", raw) or "failed").lower()
        verify = _grab("VERIFY", raw)
        status = result if result in ("done", "failed", "parked") else "failed"
        if status == "done" and not verify:
            status = "failed"  # no evidence, no done
        if status == "done" and self.menu is not None:
            # Evidence gates existence; this gates identity. The claim is now
            # examined against the world rather than against itself, which is
            # the difference between a receipt saying a file was written and a
            # check that the file is the thing that was asked for.
            ok, why = self.menu.check(task, verify, self.home)
            if not ok:
                status = "failed"
                verify = f"postcondition failed: {why}\n\n{verify}"
                events.emit(self.home, "postcondition", cycle=ts, task=tid,
                            passed=False, why=why[:300])
        con.execute("UPDATE tasks SET status=?, ended_at=?, result=? WHERE cycle_ts=? AND task_id=? AND status='started'",
                    (status, time.time(), verify[:2000], ts, tid))
        con.commit()
        if status == "failed":
            # A retry is a bet that something has changed. Nothing has, when
            # the same task fails the same way, and this engine will happily
            # take that bet forever: watched live, one unfinishable task was
            # re-planned twenty-three times in two hours, each failure filing
            # the follow-up that produced the next attempt.
            #
            # So attempts are counted, and past the limit the work stops being
            # work and becomes a recorded dead end. That is not giving up: a
            # ruled-out branch written down is worth more than a fresh idea,
            # because only one of the two prevents the same two hours happening
            # again tomorrow.
            title = f"resume failed task: {what[:100]}"
            limit = int(self.cfg.get("max_task_attempts", 3))
            tried = self.store.attempts(con, title)
            if tried + 1 >= limit:
                self.store.add_thread(
                    con, f"dead end: {what[:100]}",
                    f"abandoned after {tried + 1} attempts. Last failure: {verify[:400]}",
                    kind="deadend", created_cycle=ts, defer=True, unique=True)
                for row in con.execute("SELECT id FROM threads WHERE title=? AND status='open'",
                                       (title,)).fetchall():
                    self.store.close_thread(con, row[0], f"abandoned after {tried + 1} attempts")
                events.emit(self.home, "abandoned", cycle=ts, task=tid,
                            attempts=tried + 1, what=what[:200], last=verify[:300])
            else:
                self.store.add_thread(con, title, verify[:400],
                                      created_cycle=ts, defer=True, unique=True)
                self.store.bump_attempt(con, title)
        events.emit(self.home, "task", cycle=ts, task=tid, status=status,
                    mode=task.get("mode"), what=what[:200], verify=verify[:400],
                    steps=raw.count("\n[") or None)
        return {"id": tid, "status": status, "what": what, "verify": verify[:200]}

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
        if not lessons:
            return
        try:
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.memory_path, "a", encoding="utf-8") as f:
                for l in lessons[:3]:
                    line = f"- {l.situation} -> {l.action} -> {l.outcome}"
                    if line not in (self.memory_path.read_text() if self.memory_path.exists() else ""):
                        f.write(line + "\n")
        except Exception:
            pass
