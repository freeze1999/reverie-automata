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
from typing import Any, Callable

from .local_server import LocalServer


def _step_schema(tool_names: list[str]) -> dict[str, Any]:
    return {
        "name": "step",
        "schema": {
            "type": "object",
            "properties": {
                "thought": {"type": "string"},
                "tool": {"enum": tool_names},
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
        self.server = LocalServer({
            "base_url": o.get("base_url", "http://127.0.0.1:8080"),
            "model": o.get("model", "local"),
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
            raw = self.server.complete("", prompt, max_tokens=700)
            step = self._parse(raw)
            if step is None:
                self.transcript.append(
                    f"[{turn + 1}] the model produced no usable step: {raw[:160]}")
                continue

            tool, arg = step.get("tool", ""), str(step.get("argument", ""))
            if tool == "done":
                outcome, evidence = "done", arg or "finished without saying what was established"
                self.transcript.append(f"[{turn + 1}] done: {arg[:200]}")
                break
            if tool == "give_up":
                outcome, evidence = "failed", arg or "gave up without saying why"
                self.transcript.append(f"[{turn + 1}] gave up: {arg[:200]}")
                break

            result = self._run_tool(tool, arg)
            self.transcript.append(
                f"[{turn + 1}] {tool}({arg[:120]}) -> {result[:self.max_result_chars]}")

            # A small model does not readily abandon a failing approach: it
            # will reissue the identical call until the cap. Repetition is
            # therefore treated as the loop's problem, not the model's, and
            # after a few identical failures the loop says so plainly and
            # stops rather than burning the remaining turns on it.
            signature = (tool, arg)
            if result.startswith("the tool raised") or result.startswith("no such tool"):
                repeats[signature] = repeats.get(signature, 0) + 1
                if repeats[signature] >= 3:
                    outcome = "failed"
                    evidence = (f"the same call failed {repeats[signature]} times "
                                f"({tool}): {result[:200]}")
                    self.transcript.append(f"[{turn + 1}] loop stopped: {evidence}")
                    break
        else:
            outcome = "failed"
            evidence = f"turn cap reached ({cap}) with no conclusion"

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
