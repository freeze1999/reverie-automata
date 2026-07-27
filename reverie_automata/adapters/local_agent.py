"""A tool-using loop for a small locally served brain.

Giving a small model tools is not the same problem as giving a large one
tools. A large model can be handed a rich function-calling schema and be
trusted to pick sensibly among a dozen options with structured arguments. A
small one, on the evidence of this project, understands the task and then
fails at the mechanics: it invents a tool that does not exist, nests its
arguments wrongly, or writes something that is nearly JSON.

So the shape here is deliberately impoverished:

- **One call per turn.** No parallel calls, no chains proposed in advance.
- **A closed enum of tools**, enforced by the same grammar that fixed the
  planning envelope. An invented tool name is not possible rather than
  unlikely.
- **One string argument, always.** Tools that need two values take a
  separator. This is uglier than a typed signature and far more reliable,
  which is the correct trade at this size.
- **Every result goes back verbatim** as the next turn's evidence, including
  errors. A tool that failed is information, not a reason to stop.

The loop owns the transcript and the turn cap; the tools own their own
safety. Nothing here decides what is allowed, because a loop that both wields
the tools and judges them is not a boundary.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from .local_server import LocalServer


def _live(kind: str, **fields) -> None:
    """Emit one line about a step WHILE it happens.

    The transcript is assembled and returned when the session ends, which is
    right for the record and useless for watching: a cycle on a small local
    brain takes minutes, and for those minutes an observer sees nothing at all
    and cannot tell thinking from hung. So each step is appended as it occurs,
    to a file named by the environment the engine already stamps.

    Failure here is silence, never an exception. Watching the work must never
    be able to break the work.
    """
    try:
        home, cycle = os.environ.get("REVERIE_HOME"), os.environ.get("REVERIE_CYCLE")
        if not home or not cycle:
            return
        p = Path(home) / "cycles" / cycle / "live.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {"at": time.time(), "kind": kind}
        rec.update(fields)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            f.flush()
    except Exception:
        pass


def _step_schema(tool_names: list[str]) -> dict[str, Any]:
    return {
        "name": "step",
        "schema": {
            "type": "object",
            "properties": {
                # Capped for the same reason the plan envelope is: a long
                # thought runs out of budget before the step closes, and a
                # truncated step parses as nothing at all.
                "thought": {"type": "string", "maxLength": 200},
                "tool": {"enum": tool_names},
                # No cap here, deliberately. A grammar expands a length limit
                # into explicit repetitions, and a large one fails to compile
                # at all: asking for at most two thousand characters took the
                # server from working to returning 500 on every call. The
                # argument carries source code, so it must stay unbounded.
                "argument": {"type": "string"},
            },
            "required": ["thought", "tool", "argument"],
        },
    }


class LocalAgent:
    """Runs one task to completion (or to the turn cap) using a toolkit.

    `tools` maps a name to (description, callable). Each callable takes one
    string and returns a string; raising is fine, the loop reports it back to
    the model as the result of that step.
    """

    name = "local_agent"

    def __init__(self, options: dict[str, Any] | None = None):
        o = dict(options or {})
        self.tools: dict[str, tuple[str, Callable[[str], str]]] = o.get("tools") or {}
        self.max_turns = int(o.get("max_turns", 12))
        self.max_result_chars = int(o.get("max_result_chars", 1500))
        # A step that carries source code in its argument needs room. Too
        # small a budget truncates the json mid-string, which parses as no
        # step at all and looks exactly like a model that refused to answer.
        self.step_tokens = int(o.get("step_tokens", 1600))
        # Everything the server needs travels with it. The credential and the
        # constraint mode were missing here while the planner had both, so the
        # plan phase reached a hosted brain and the execute phase got 401 from
        # the same endpoint in the same cycle. A loop that builds its own
        # dependency has to be handed the whole configuration, not the part
        # somebody remembered.
        self.server = LocalServer({
            "base_url": o.get("base_url", "http://127.0.0.1:8080"),
            "model": o.get("model", "local"),
            "api_key": o.get("api_key", ""),
            "schema_mode": o.get("schema_mode", "json_schema"),
            "thinking": bool(o.get("thinking", False)),
            "temperature": float(o.get("temperature", 0.4)),
            "timeout_s": int(o.get("timeout_s", 600)),
        })
        self.transcript: list[str] = []

    # -- interface ---------------------------------------------------------
    def complete(self, system, user, *, max_tokens=1000) -> str:
        return self.server.complete(system, user, max_tokens=max_tokens)

    def run_session(self, directive, *, cwd="", env=None, turn_cap=None,
                    timeout_s=2700) -> str:
        names = sorted(self.tools) + ["done", "give_up"]
        schema = _step_schema(names)
        catalogue = "\n".join(f"  {n}: {self.tools[n][0]}" for n in sorted(self.tools))
        catalogue += ("\n  done: finish, argument is what you established"
                      "\n  give_up: stop, argument is what blocked you")
        # The engine passes its configured cap; a small brain has its own,
        # lower one, and the tighter of the two wins. Forty turns of a model
        # that repeats itself is forty turns of nothing.
        cap = min(int(turn_cap or self.max_turns), self.max_turns)
        self.transcript = []
        repeats: dict[tuple[str, str], int] = {}
        dud = 0
        outcome, evidence = "failed", "the loop ended without a verdict"

        for turn in range(cap):
            prompt = (
                f"{directive}\n\n"
                f"Tools, one per step:\n{catalogue}\n\n"
                + ("Steps so far:\n" + "\n".join(self.transcript) + "\n\n"
                   if self.transcript else "")
                + f"Step {turn + 1} of {cap}. Choose one tool. Ground every claim "
                  "in something a tool actually returned; if a tool failed, say so "
                  "and try a different approach rather than asserting the result."
            )
            self.server.schema = schema
            _live("thinking", turn=turn + 1, cap=cap)
            raw = self.server.complete("", prompt, max_tokens=self.step_tokens)
            step = self._parse(raw)
            if step is None:
                self.transcript.append(
                    f"[{turn + 1}] the model produced no usable step: {raw[:160]}")
                # A brain that cannot answer at all is not a brain that needs
                # more turns. Ten attempts against a server returning 500 is
                # ten times the wait for the same nothing, so unusable replies
                # in a row end the session with the reason visible.
                dud += 1
                _live("dud", turn=turn + 1, raw=raw[:200])
                if dud >= 3:
                    outcome = "failed"
                    evidence = (f"the model returned nothing usable {dud} times "
                                f"in a row: {raw[:200]}")
                    self.transcript.append(f"[{turn + 1}] loop stopped: {evidence}")
                    break
                continue
            dud = 0

            tool, arg = step.get("tool", ""), str(step.get("argument", ""))
            _live("step", turn=turn + 1, thought=str(step.get("thought", ""))[:200],
                  tool=tool, arg=arg[:200])
            if tool == "done":
                outcome, evidence = "done", arg or "finished without saying what was established"
                self.transcript.append(f"[{turn + 1}] done: {arg[:200]}")
                break
            if tool == "give_up":
                outcome, evidence = "failed", arg or "gave up without saying why"
                self.transcript.append(f"[{turn + 1}] gave up: {arg[:200]}")
                break

            result = self._run_tool(tool, arg)
            _live("result", turn=turn + 1, tool=tool, result=result[:400])

            signature = (tool, arg)
            repeats[signature] = repeats.get(signature, 0) + 1

            # Watching a wall being hit is not supervision. Measured: given
            # `rank_A_squared = A**2.rank()`, the interpreter answered with the
            # file, the line, a caret under the exact character and the words
            # "invalid decimal literal", and the model submitted the identical
            # code twice more. It does not update on evidence, not across
            # cycles, not within a session, not when the evidence is a compiler
            # pointing at the character.
            #
            # So on the SECOND identical call the loop stops asking politely
            # and changes something itself. The perturbation must happen BEFORE
            # the transcript append, because the transcript is what the next
            # prompt is built from; an instruction written after it is an
            # instruction the model never sees, which is the same mistake as
            # detecting a livelock and doing nothing about it.
            if repeats[signature] == 2:
                result = (
                    f"[harness] you have now made this exact call twice: "
                    f"{tool}({arg[:120]}). An identical call cannot return "
                    f"anything new. Do not make it a third time. Either change "
                    f"the argument, use a different tool, or call give_up with "
                    f"what blocked you. The last result was: {result[:600]}")
                _live("perturbed", turn=turn + 1, tool=tool)

            self.transcript.append(
                f"[{turn + 1}] {tool}({arg[:120]}) -> {result[:self.max_result_chars]}")

            # A small model does not readily abandon an approach that is not
            # working: it reissues the identical call until the cap. The guard
            # is on REPETITION, not on failure, because a search that keeps
            # returning "no results" is a successful call and the same dead
            # end, and an identical call with an identical argument cannot
            # produce new information whatever it returns.
            if repeats[signature] >= 3:
                outcome = "failed"
                evidence = (f"the same call was made {repeats[signature]} times "
                            f"with the same argument ({tool}), which cannot "
                            f"produce anything new: {result[:150]}")
                self.transcript.append(f"[{turn + 1}] loop stopped: {evidence}")
                break
        else:
            outcome = "failed"
            evidence = f"turn cap reached ({cap}) with no conclusion"

        _live("verdict", outcome=outcome, evidence=evidence[:300])
        # The engine reads these; the transcript is the receipt behind them.
        log = "\n".join(self.transcript)
        return (f"<<RESULT>>{outcome}<<END>>\n"
                f"<<VERIFY>>{evidence}\n\n--- steps ---\n{log}<<END>>")

    # -- internals ---------------------------------------------------------
    def _parse(self, raw: str) -> dict | None:
        text = (raw or "").strip()
        if text.startswith("[local server"):
            return None
        for candidate in (text, text.replace("<<PLAN>>", "").replace("<<END>>", "")):
            try:
                d = json.loads(candidate.strip())
                if isinstance(d, dict) and "tool" in d:
                    return d
            except Exception:  # noqa: BLE001
                continue
        return None

    def _run_tool(self, tool: str, arg: str) -> str:
        entry = self.tools.get(tool)
        if entry is None:
            return f"no such tool: {tool!r}"
        try:
            return str(entry[1](arg))
        except Exception as e:  # noqa: BLE001
            return f"the tool raised: {type(e).__name__}: {e}"
