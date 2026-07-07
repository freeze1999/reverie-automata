"""Plain value types for the reverie-automata flywheel.

Deliberately behaviourless dataclasses: easy to serialize into fire reports and
easy to reason about in tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class ActionClass(str, Enum):
    """How the engine routes phase 3 after the agent has decided what to do."""

    NOTHING = "NOTHING"        # a lazy day is a valid, first-class outcome
    NEEDS_TOOL = "NEEDS_TOOL"  # the action reads or changes the world -> a real agent session
    TEXT_ONLY = "TEXT_ONLY"    # pure reflection from the agent's own head


class Risk(str, Enum):
    SAFE = "SAFE"
    RISKY = "RISKY"            # permanent / system-changing -> approval gate


@dataclass
class Task:
    """One unit of work the agent decided to attempt this cycle."""

    id: str
    what: str
    why: str = ""
    evidence: str = ""
    mode: str = "tool"                    # tool | text | delegate
    fallback: str = ""                    # for delegate tasks: "self" | "defer"
    risk: Risk = Risk.SAFE
    risk_reason: str = ""
    thread: str = ""


@dataclass
class Plan:
    learned: str = ""
    tasks: list[Task] = field(default_factory=list)
    do_nothing: bool = False
    do_nothing_reason: str = ""


@dataclass
class Lesson:
    """The unit of the textual-gradient flywheel. Falsifiable by construction:
    a lesson is a situation, an action, and the OUTCOME that was actually observed."""

    situation: str
    action: str
    outcome: str


@dataclass
class Outcome:
    """The full result of one cycle — everything needed to audit it later."""

    when: datetime
    action_class: ActionClass
    grade: str = "N"
    cost: float = 0.0
    phase1: str = ""
    phase2: str = ""
    ledger: list[dict[str, Any]] = field(default_factory=list)
    lessons: list[Lesson] = field(default_factory=list)
    blast_radius: list[str] = field(default_factory=list)
    journal: str = ""
