"""Typed work, tested against the attacks that made it necessary.

Every test here names the catalogue entry it defends. A gate is not finished
when it stops the failure we saw; it is finished when the failure cannot be
constructed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.tasktypes import Menu, TaskType

RESOLVE = TaskType(
    name="resolve_citation",
    required=("arxiv_id", "claimed_title"),
    identity=("arxiv_id",),
    summary="check one identifier against the source's own metadata")
ARTIFACT = TaskType(
    name="write_artifact",
    required=("path", "reproduces", "author", "script", "output", "conclusion"),
    identity=("path",),
    optional=("extends",),
    summary="write a result whose claims are checkable against a citation")
DEADEND = TaskType(
    name="record_deadend",
    required=("subject", "evidence"),
    identity=("subject",),
    summary="rule a branch out, with what ruled it out")

MENU = Menu([RESOLVE, ARTIFACT, DEADEND])


def test_the_unfinishable_task_cannot_be_constructed():
    """A4. "Read the log and understand the state" burned 267 cycles. There is
    no type for it, because no postcondition could exist."""
    ok, why = MENU.validate({"type": "read_and_understand", "target": "LOG.md"})
    assert not ok
    assert "unknown task type" in why


def test_the_stub_artifact_cannot_be_constructed():
    """A14. Nineteen bytes, {"author": "local"}, a true receipt, grade A, and
    the referee unmoved. write_artifact requires every field at once."""
    ok, why = MENU.validate({"type": "write_artifact", "author": "local"})
    assert not ok
    assert "missing required field" in why
    for f in ("path", "reproduces", "script", "output", "conclusion"):
        assert f in why


def test_paraphrase_is_not_new_work():
    """A1. 125 wordings of one task walked past an abandonment floor that fired
    174 times. Identity is a comparison of values."""
    a = {"type": "resolve_citation", "arxiv_id": "1503.08733",
         "claimed_title": "Some approaches toward the Jacobian conjecture"}
    b = {"type": "resolve_citation", "arxiv_id": "1503.08733",
         "claimed_title": "SOME APPROACHES TOWARD THE JACOBIAN CONJECTURE  ",
         "why": "completely different sentence, backticks, `1503.08733`, etc"}
    assert MENU.key(a) == MENU.key(b)


def test_different_work_stays_different():
    """The guard must not collapse everything into one item; a floor that
    strangles legitimate traffic gets switched off and then guards nothing."""
    a = {"type": "resolve_citation", "arxiv_id": "1503.08733", "claimed_title": "x"}
    b = {"type": "resolve_citation", "arxiv_id": "2407.07911", "claimed_title": "x"}
    assert MENU.key(a) != MENU.key(b)


def test_two_types_with_the_same_values_are_not_the_same_work():
    a = {"type": "record_deadend", "subject": "1503.08733", "evidence": "e"}
    b = {"type": "resolve_citation", "arxiv_id": "1503.08733", "claimed_title": "t"}
    assert MENU.key(a) != MENU.key(b)


def test_an_invented_type_is_not_expressible_in_the_grammar():
    """A11's cousin: the model reaches for whatever word is in front of it.
    An enum makes the reach impossible rather than unlikely."""
    assert MENU.schema()["properties"]["type"] == {
        "enum": ["record_deadend", "resolve_citation", "write_artifact"]}


def test_the_schema_stays_flat_and_capped():
    """A schema clever enough to express per-type requirements compiles into a
    grammar large enough to take the server down; that happened once here with
    a maxLength of 2000. Shape from the grammar, semantics from the validator."""
    props = MENU.schema()["properties"]
    assert "oneOf" not in MENU.schema()
    assert all(p.get("maxLength", 0) <= 400 for p in props.values()
               if p.get("type") == "string")


def test_the_menu_states_the_rules_it_enforces():
    """Nineteen wrapper corrections in one run for a rule the prompt never
    mentioned. A rule enforced and unstated is a tax the model pays for a
    secret."""
    text = MENU.describe()
    assert "write_artifact" in text
    assert "reproduces" in text and "conclusion" in text


def test_an_empty_menu_admits_nothing():
    """An engine not told what its program considers work must not fall back to
    accepting prose."""
    from reverie_automata.tasktypes import EMPTY
    ok, _ = EMPTY.validate({"type": "anything", "what": "please"})
    assert not ok
