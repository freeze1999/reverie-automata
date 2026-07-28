"""The referee: what counts as progress, measured where the machine cannot reach.

The engine used to grade a cycle on its own ledger: did the task report done,
and was there a receipt. Measured across roughly four hundred cycles and two
model sizes, that number does not track progress at all. A weak model earned an
A by fabricating a matrix and computing it correctly. A frontier model earned an
A by competently summarising a file. A concrete task earned an A for nineteen
bytes reading `{"author": "local"}`, with a receipt every word of which was
true. In none of those cycles did the program learn anything.

So the grade moves here, and the rule is:

    a cycle's grade is the DELTA of the program's state vector,
    never the ledger's opinion of itself.

**The component test, which is the part that was nearly missed.** A verifier is
only a referee if moving a component requires producing something whose
validity is checked against a fact the machine does not control. Our first
version counted dead ends by regex over a log the machine may write, and the
machine moved it from 8 to 46 by copying the log into itself. It found nothing,
ruled nothing out, and the number went up by 38. **A count of pattern matches in
a document the author controls is a word counter wearing a referee's coat.**

Every component declares what it is checked against, and `audit()` refuses a
vector containing one that cannot answer. That refusal is not decoration: it is
the only thing standing between this design and the previous one, which also
looked rigorous.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# What a component's count is checked against. The first two are admissible;
# the third is not, and is named so that writing one is a deliberate act rather
# than an accident.
EXTERNAL = "external"      # a fact outside the machine (a source's metadata)
DERIVED = "derived"        # computed from artifacts that are themselves checked
SELF_REPORTED = "self"     # counted from text the machine may write. NOT a referee.


@dataclass(frozen=True)
class Component:
    """One number in the state vector, and the two questions it must answer.

    `checked_against` says what outside fact validates it. `counts_distinct`
    says what makes two of the things it counts the SAME thing, and it exists
    because leaving it implicit cost a false positive on the first milestone
    run: a citations component, genuinely checked against arXiv's own metadata,
    counted `arXiv:2407.07911`, `2407.07911` and `arXiv:2407.07911v5` as three
    citations. It rose from three to five while establishing nothing.

    **An external check does not imply an idempotent count.** A component needs
    both: an outside fact to answer to, and an answer that does not change when
    the same question is asked twice in different words. Writing the second
    sentence is what catches the second failure, so the field is required
    rather than optional and the audit refuses a component without one.
    """

    name: str
    count: Callable[[], int]
    checked_against: str
    why: str
    counts_distinct: str = ""

    @property
    def admissible(self) -> bool:
        return (self.checked_against in (EXTERNAL, DERIVED)
                and bool(self.counts_distinct.strip()))


class Referee:
    """A program's state vector, and the delta between two readings of it."""

    def __init__(self, components: list[Component]):
        self.components = list(components)

    def audit(self) -> list[str]:
        """Every component that cannot say what checks it. Called before the
        referee is allowed to grade anything, because a vector is only as
        trustworthy as its softest entry."""
        problems = []
        for c in self.components:
            if c.checked_against not in (EXTERNAL, DERIVED):
                problems.append(
                    f"{c.name}: counted from {c.checked_against}, which the "
                    "machine can write; this cannot grade anything")
            elif not c.counts_distinct.strip():
                problems.append(
                    f"{c.name}: does not say what makes two of these the same "
                    "thing, so nothing stops the count rising on a duplicate")
        return problems

    def state(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.components:
            if not c.admissible:
                continue
            try:
                out[c.name] = int(c.count())
            except Exception:  # noqa: BLE001
                # A component that cannot be read is not zero, it is unknown,
                # and calling it zero would manufacture a delta on the next
                # reading. Absence is the honest answer.
                continue
        return out

    @staticmethod
    def delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
        keys = set(before) & set(after)
        return {k: after[k] - before[k] for k in sorted(keys)
                if after[k] != before[k]}


def grade(delta: dict[str, int], *, attempted: bool, honest_no_op: bool) -> str:
    """The grade of one cycle.

    Deliberately blunt, and deliberately not a ratio of tasks. What matters is
    whether the world changed, so:

    - anything moved forward: A, whatever the ledger says about how many tasks
      were attempted, because one real result is a good night;
    - a component went BACKWARDS: F, and loudly. Nothing legitimate reduces an
      established count, so this is the signature of an artifact being
      overwritten or a ledger being damaged (see the identity matrix written
      over a good file);
    - work attempted, nothing moved: F. This is the case the old grader called
      an A four separate times;
    - nothing due and nothing claimed: N, an honest lazy day, which stays a
      first-class outcome and is never punished.
    """
    if any(v < 0 for v in delta.values()):
        return "F"
    if delta:
        return "A"
    if honest_no_op and not attempted:
        return "N"
    return "F"
