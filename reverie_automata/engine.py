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

from . import prompts as P
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


RISKY_HINTS = re.compile(r"sudo|systemctl|crontab|deploy|\bpush\b|install|delete|drop\s+table|"
                         r"restart|migrat|password|secret|prod", re.I)


class Engine:
    def __init__(self, cfg, store, harvester, inspector, agent, planner, approvals):
        self.cfg, self.store, self.harvest = cfg, store, harvester
        self.inspector, self.agent, self.planner, self.approvals = inspector, agent, planner, approvals
        self.home = cfg.home
        self.memory_path = self.home / "MEMORY.md"

    # -- risk (defense in depth: wrapper classifies too, and wins) ----------
    def _wrapper_risk(self, task: dict) -> tuple[str, str]:
        blob = json.dumps(task, ensure_ascii=False)
        m = RISKY_HINTS.search(blob)
        return ("RISKY", m.group(0)) if m else ("SAFE", "")

    def run_cycle(self, now: datetime | None = None, text_only: bool = False) -> Outcome:
        now = now or datetime.now()
        ts = now.strftime("%Y-%m-%d-%H%M%S")
        cdir = self.home / "cycles" / ts
        cdir.mkdir(parents=True, exist_ok=True)
        con = self.store.connect()

        orphan = self.store.orphaned_cycle(con)
        if orphan:
            con.execute("UPDATE cycles SET status='recovered' WHERE ts=?", (orphan[0],))
            if con.execute("SELECT COUNT(*) FROM tasks WHERE cycle_ts=?", (orphan[0],)).fetchone()[0]:
                self.store.add_thread(con, f"recovery: cycle {orphan[0]} crashed mid-run",
                                      "reconcile its half-done work", kind="recovery", created_cycle=ts)
        con.execute("INSERT INTO cycles (ts, started_at, status) VALUES (?,?,'running')", (ts, now.timestamp()))
        con.commit()

        # --- PLAN ---
        context, _ = self.harvest.build(con)
        p1 = self.planner.complete("", P.PLAN.format(context=context),
                                   max_tokens=self.cfg["max_tool_turns"]["plan"] * 80)
        (cdir / "plan.txt").write_text(p1, encoding="utf-8")
        plan = parse_plan(p1) or {"do_nothing": True, "do_nothing_reason": "unparseable plan", "tasks": []}

        ledger: list[dict] = []
        if not plan.get("do_nothing"):
            for task in plan.get("tasks", [])[:8]:
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

        grade = derive_grade(ledger)
        con.execute("INSERT OR REPLACE INTO journal (cycle_ts, body, created_at) VALUES (?,?,?)",
                    (ts, journal + (("\n\n[review]\n" + review) if review else ""), time.time()))
        for ls in lessons[:3]:
            if all([ls.situation, ls.action, ls.outcome]):
                con.execute("INSERT INTO lessons (cycle_ts, situation, action, outcome, created_at) VALUES (?,?,?,?,?)",
                            (ts, ls.situation, ls.action, ls.outcome, time.time()))
        self._append_memory(lessons)
        con.execute("UPDATE cycles SET finished_at=?, status=?, grade=?, plan_json=? WHERE ts=?",
                    (time.time(), "done" if not plan.get("do_nothing") else "do_nothing", grade,
                     json.dumps(plan, ensure_ascii=False), ts))
        con.commit()
        con.close()

        outcome = Outcome(when=now, action_class=ActionClass.NOTHING if plan.get("do_nothing") else ActionClass.NEEDS_TOOL,
                          grade=grade, phase1=p1, phase2=p3, ledger=ledger, lessons=lessons, journal=journal)
        (cdir / "outcome.json").write_text(json.dumps({
            "ts": ts, "grade": grade, "plan": plan, "ledger": ledger,
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
                                  json.dumps(task, ensure_ascii=False), kind="approval", created_cycle=ts)
            return {"id": tid, "status": "parked", "what": what}
        if text_only and task.get("mode") == "tool":
            self.store.add_thread(con, f"deferred (text-only budget): {what[:100]}", "", created_cycle=ts)
            return {"id": tid, "status": "skipped", "what": what}

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
            self.store.add_thread(con, f"resume failed task: {what[:100]}", verify[:400], created_cycle=ts)
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
