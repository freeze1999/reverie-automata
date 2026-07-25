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
from . import prompts as P
from .inbox import Inbox
from .planvalidate import validate_plan
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
    r"\bsecret\w*|\bprod\b|\bproduction\b", re.I)


class Engine:
    def __init__(self, cfg, store, harvester, inspector, agent, planner, approvals):
        self.cfg, self.store, self.harvest = cfg, store, harvester
        self.inspector, self.agent, self.planner, self.approvals = inspector, agent, planner, approvals
        self.home = cfg.home
        self.memory_path = self.home / "MEMORY.md"
        self.inbox = Inbox(self.home / "inbox", cfg)

    # -- risk (defense in depth: wrapper classifies too, and wins) ----------
    def _wrapper_risk(self, task: dict) -> tuple[str, str]:
        blob = json.dumps(task, ensure_ascii=False)
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
        context, _ = self.harvest.build(con, {"inbox": inbox_section})
        p1 = self.planner.complete("", P.PLAN.format(context=context),
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
            allow_text_tasks=bool(self.cfg.get("allow_text_tasks", True)))
        for c in plan_complaints:
            print(f"[plan] {c}")

        # A drop is spent by a cycle that ENGAGED with it. A cycle that
        # wrongly declared there was nothing to do did not engage, and
        # archiving the request anyway would let a weak planner quietly eat
        # work by claiming a lazy day. Leave it in the queue for the next one.
        n_inbox = 0 if false_no_op else self.inbox.consume(inbox_files, ts)

        ledger: list[dict] = []
        pre = blast.snapshot(self.cfg["protected_paths"])
        if not plan.get("do_nothing"):
            for task in plan.get("tasks", []):
                ledger.append(self._do_task(con, ts, cdir, task, text_only))

        # --- LEARN ---
        ledger_txt = "\n".join(f"- {e['id']} [{e['status']}] {e['what'][:80]}" for e in ledger) or "(nothing to do)"
        p3 = self.agent.run_session(P.LEARN.format(context=context, ledger=ledger_txt),
                                    cwd=str(self.home), env=self._cycle_env(ts),
                                    turn_cap=self.cfg["max_tool_turns"]["learn"])
        journal = _grab("JOURNAL", p3) or p3[:1200]
        review = _grab("REVIEW", p3)
        lessons = [Lesson(*[x.strip() for x in re.split(r"->", l, maxsplit=2)])
                   for l in re.findall(r"<<LESSON>>(.*?)<<END>>", p3, re.S)
                   if len(re.split(r"->", l, maxsplit=2)) == 3]

        # Tasks and the LEARN session both hold real tools, so the post
        # snapshot happens after both; anything in the watch set that changed,
        # appeared, or vanished during the cycle lands in the outcome.
        touched = blast.diff(pre, blast.snapshot(self.cfg["protected_paths"]))

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
            "lessons": [l.__dict__ for l in lessons]}, ensure_ascii=False, indent=2), encoding="utf-8")
        return outcome

    # -- one task -----------------------------------------------------------
    def _do_task(self, con, ts, cdir, task, text_only) -> dict:
        tid = str(task.get("id", "?"))
        what = task.get("what", "")
        wrisk, wpat = self._wrapper_risk(task)
        final = "RISKY" if "RISKY" in (wrisk, str(task.get("risk", "SAFE")).upper()) else "SAFE"
        if final == "RISKY":
            self._file_approval(con, ts, task, task.get("risk_reason") or wpat)
            self.store.add_thread(con, f"parked (awaiting approval): {what[:100]}",
                                  json.dumps(task, ensure_ascii=False), kind="approval",
                                  created_cycle=ts, defer=True, unique=True)
            return {"id": tid, "status": "parked", "what": what}
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
        if task.get("mode") == "text":
            raw = self.planner.complete("", P.EXECUTE_TEXT_ONLY.format(context="", what=what), max_tokens=1200)
        else:
            raw = self.agent.run_session(P.EXECUTE.format(context="", task_id=tid, what=what, why=task.get("why", "")),
                                         cwd=str(self.home), env=self._cycle_env(ts),
                                         turn_cap=self.cfg["max_tool_turns"]["execute"])
        (cdir / f"task_{tid}.txt").write_text(raw, encoding="utf-8")
        result = (_grab("RESULT", raw) or "failed").lower()
        verify = _grab("VERIFY", raw)
        status = result if result in ("done", "failed", "parked") else "failed"
        if status == "done" and not verify:
            status = "failed"  # no evidence, no done
        con.execute("UPDATE tasks SET status=?, ended_at=?, result=? WHERE cycle_ts=? AND task_id=? AND status='started'",
                    (status, time.time(), verify[:2000], ts, tid))
        con.commit()
        if status == "failed":
            self.store.add_thread(con, f"resume failed task: {what[:100]}", verify[:400],
                                  created_cycle=ts, defer=True, unique=True)
        return {"id": tid, "status": status, "what": what, "verify": verify[:200]}

    def _file_approval(self, con, ts, task, reason):
        con.execute("INSERT INTO approvals (cycle_ts, artifact, reasoning, status, filed_at, expires_at) "
                    "VALUES (?,?,?, 'pending', ?, ?)",
                    (ts, json.dumps(task, ensure_ascii=False), reason, time.time(), time.time() + 24 * 3600))
        con.commit()

    def _cycle_env(self, ts):
        import os
        return dict(os.environ, REVERIE_CYCLE=ts)  # marks a session so a pre-tool hook can gate it

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
