"""Typed work: what this program considers a task, and what proves one happened.

The engine used to accept a task as a 240-character string and then run five
separate mechanisms to guess what the string meant: routing regexes, a
content-free check, a text-versus-tool upgrade, a subject deduplicator, and an
attempt counter keyed on the thread title. Every one of them was defeated by
paraphrase, because a paraphrase is free and the guesses were not. One live run
produced **125 distinct wordings of a single task** and walked past an
abandonment floor that fired 174 times.

The fix is not a better guess. It is to stop asking. A task is an object with a
declared type from a menu the PROGRAM supplies, and the type carries three
things the guessing was trying to recover:

- **required fields**, so an incomplete task is not a task;
- **identity fields**, so "the same work" is a comparison of values rather than
  of prose, and rewording changes nothing;
- **a postcondition**, so whether it happened is a question about the world
  rather than about the model's report of the world.

The menu belongs to the program because admissibility is a property of the
work, not of the engine. A research program admits citations and computations;
an operations instance admits tickets and deployments. Same engine, different
menu, no code edits, which is also the second-instance test this project uses
to decide whether something has been generalized or merely moved.

**What this makes impossible rather than discouraged.** "Read the log and
understand the current state" cannot be constructed: there is no type for it,
because there is no postcondition anyone could check. A stub artifact carrying
only `{"author": "local"}` cannot be written: `write_artifact` requires every
field at once. Two requests for the same citation are the same task whatever
the sentence around them. None of these are refusals. They are absences in the
grammar, which is a stronger thing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class TaskType:
    """One admissible shape of work.

    `postcondition` receives (task, receipt, home) and returns (ok, why). It is
    asked AFTER the executor claims completion, and it is the only thing that
    can turn a claim into a done. It should read the world, not the receipt:
    the receipt is the claim under examination.
    """

    name: str
    required: tuple[str, ...]
    identity: tuple[str, ...]
    summary: str
    postcondition: Callable[[dict, str, Any], tuple[bool, str]] | None = None
    optional: tuple[str, ...] = ()
    instruction: Callable[[dict], str] | None = None
    # Fields that carry DATA rather than intent: source code, captured output,
    # quoted text. The risk classifier must not read them, because a pattern
    # written for a prose description reads a program as a threat and parks
    # legitimate work. Intent is what a guard should judge.
    payload: tuple[str, ...] = ()
    # The tools that can actually satisfy this type's postcondition. Declared
    # rather than inferred, because a menu entry with no route to its own
    # postcondition is a trap: the work is admissible, the executor tries, and
    # nothing it can do will ever pass. Found live, twice in one run, on a
    # `record_deadend` type whose check wanted a structured row that no tool
    # could write. That is A4's unfinishable task wearing a type, and the fix
    # is the same shape as `counts_distinct`: make somebody write the sentence.
    satisfied_by: tuple[str, ...] = ()

    def render(self, task: dict) -> str:
        """The instruction the executor receives, generated from the fields.

        Not written by the planner, which matters more than it looks. Two of
        this project's defects came from prose in a task being read as an
        instruction by something else: the word "delegate" in a task caused the
        model to call the tool of that name, and an attempt to work around that
        by asking for a value spelled as two fragments produced "degate". A
        rendered instruction has no free text for either failure to live in.
        """
        if self.instruction is not None:
            return self.instruction(task)
        fields = ", ".join(f"{f}={task[f]!r}" for f in self.required
                           if str(task.get(f, "")).strip())
        return f"{self.name}: {self.summary}. Fields: {fields}"

    def key(self, task: dict) -> str:
        """What makes two of these the same work. Values, never prose."""
        return self.name + "|" + "|".join(
            str(task.get(f, "")).strip().lower() for f in self.identity)

    def missing(self, task: dict) -> list[str]:
        return [f for f in self.required
                if not str(task.get(f, "")).strip()]


class Menu:
    """The set of task types one program admits, and the grammar for them."""

    def __init__(self, types: list[TaskType]):
        self.types = {t.name: t for t in types}

    def get(self, task: dict) -> TaskType | None:
        return self.types.get(str(task.get("type", "")).strip())

    def key(self, task: dict) -> str:
        t = self.get(task)
        # An untyped task has no identity, so it is never "the same" as
        # anything and can never be deduplicated. That is the correct answer
        # and also the reason untyped tasks are refused before they get here.
        return t.key(task) if t else "untyped|" + json.dumps(task, sort_keys=True)

    def validate(self, task: dict) -> tuple[bool, str]:
        t = self.get(task)
        if t is None:
            return False, (f"unknown task type {task.get('type')!r}; "
                           f"this program admits: {sorted(self.types)}")
        gaps = t.missing(task)
        if gaps:
            return False, f"{t.name} is missing required field(s): {gaps}"
        return True, ""

    def render(self, task: dict) -> str:
        t = self.get(task)
        return t.render(task) if t else str(task.get("what", ""))

    def check(self, task: dict, receipt: str, home) -> tuple[bool, str]:
        """The postcondition, asked of the world after the claim.

        No type means no check, and that is a gap rather than a pass: it is
        recorded as such so a menu with unchecked types cannot be mistaken for
        one that verifies everything.
        """
        t = self.get(task)
        if t is None or t.postcondition is None:
            return True, "no postcondition defined for this type"
        try:
            return t.postcondition(task, receipt, home)
        except Exception as e:  # noqa: BLE001
            # A check that crashed did not pass. The opposite default would
            # turn every bug in a postcondition into a free done.
            return False, f"the postcondition raised {type(e).__name__}: {e}"

    def unreachable(self, tool_names) -> list[str]:
        """Types this executor cannot possibly complete, given its toolkit.

        Admissibility is not enough: work must also be REACHABLE. A type whose
        postcondition no available tool can produce invites the machine to
        fail forever for a reason that has nothing to do with its ability.
        """
        have = set(tool_names or ())
        out = []
        for name, t in sorted(self.types.items()):
            if t.postcondition is None:
                continue
            if not t.satisfied_by:
                out.append(f"{name}: does not say which tool can satisfy its "
                           "postcondition, so nothing checks that one exists")
            elif not (set(t.satisfied_by) & have):
                out.append(f"{name}: needs one of {sorted(t.satisfied_by)} and "
                           f"this instance has none of them")
        return out

    def schema(self) -> dict[str, Any]:
        """A json schema for one task.

        The type is an enum, so an invented type is not expressible. The fields
        are a flat union of everything any type uses, because a `oneOf` per
        type compiles into a large grammar and this project has already taken a
        server down once with a schema that was too clever. Shape comes from
        the grammar; which fields are REQUIRED for a given type comes from
        `validate`, deterministically, in code. Both are model-free, and the
        weaker guarantee is the one that also works on endpoints with no
        grammar support at all.
        """
        fields: dict[str, Any] = {}
        for t in self.types.values():
            for f in t.required + t.optional:
                fields.setdefault(f, {"type": "string", "maxLength": 400})
        return {
            "type": "object",
            "properties": {
                "type": {"enum": sorted(self.types)},
                **fields,
                "why": {"type": "string", "maxLength": 300},
                "risk": {"enum": ["SAFE", "RISKY"]},
            },
            "required": ["type", "why", "risk"],
        }

    def describe(self) -> str:
        """The menu as the planner sees it. Stating the required fields is the
        whole point: a rule that is enforced and never stated is a tax."""
        out = []
        for name in sorted(self.types):
            t = self.types[name]
            out.append(f"  {name}: {t.summary}\n"
                       f"    required: {', '.join(t.required)}")
        return "\n".join(out)


# A menu with nothing in it admits nothing, which is the correct behaviour for
# an engine that has not been told what its program considers work. It is not a
# fallback to free prose.
EMPTY = Menu([])
