"""Read what the model wrote, not the punctuation it forgot.

The fixture below is the first LEARN output this engine ever collected, verbatim
off a real cycle, after the phase was fixed to return the model's own words. It
opened four blocks and closed one, at the very bottom, and wrote its lessons as
a labelled report rather than the arrow form the prompt asks for.

Under the strict parse that produced: one enormous journal that swallowed the
review and all three lessons, and zero lessons recorded. The content was
specific, correct and useful, and every word of it was discarded on formatting.

The same rule the rest of this engine runs on: the harness absorbs the
executor's shape rather than depending on it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.engine import _grab, _grab_all, _lesson_parts

# Verbatim, cycle 2026-08-06-225705. Four opening tags, one <<END>>.
REAL = """<<JOURNAL>>
I attempted to resolve the citation for arXiv:1503.08733 by verifying its metadata.

<<REVIEW>>
What worked: I identified the core issue.

What context were you missing this cycle?
I was missing the specific metadata fields required for citation verification, particularly the authorship information.

<<LESSON>>
Situation: Citation resolution failed due to missing authorship in the source metadata.
Action: Attempted to verify the metadata and compute a matrix quantity.
Observed outcome: The process stalled because the verification step could not be completed.

<<LESSON>>
Situation: Previous attempts were stuck in loops.
Action: Tried to record deadends for these attempts.
Observed outcome: The deadend recording tasks were stuck in loops.
<<END>>"""


def test_a_block_ends_at_the_next_tag_when_end_is_missing():
    j = _grab("JOURNAL", REAL)
    assert j.startswith("I attempted to resolve")
    assert "<<REVIEW>>" not in j, "the journal swallowed the rest of the document"
    assert "missing the specific metadata" not in j


def test_the_review_survives_and_carries_its_answer():
    """The whole point of the phase. This sentence is the amnesia signal."""
    r = _grab("REVIEW", REAL)
    assert "authorship information" in r
    assert "<<LESSON>>" not in r, "the review ran on into the lessons"


def test_every_lesson_is_found_not_just_the_last():
    assert len(_grab_all("LESSON", REAL)) == 2


def test_the_labelled_lesson_form_parses():
    """The prompt asks for `a -> b -> c`. A model reaching for a report format
    writes Situation/Action/Observed outcome, with the same three fields."""
    got = _lesson_parts(_grab_all("LESSON", REAL)[0])
    assert got is not None, "a well-formed lesson was dropped for its punctuation"
    situation, action, outcome = got
    assert situation.startswith("Citation resolution failed")
    assert action.startswith("Attempted to verify")
    assert outcome.startswith("The process stalled")


def test_the_arrow_form_still_parses():
    got = _lesson_parts("a stale source -> reread before planning -> the plan was wrong")
    assert got == ["a stale source", "reread before planning", "the plan was wrong"]


def test_a_closed_block_is_unaffected():
    """The forgiving terminator must not change well-formed output."""
    text = "<<JOURNAL>>one<<END>>\n<<REVIEW>>two<<END>>"
    assert _grab("JOURNAL", text) == "one"
    assert _grab("REVIEW", text) == "two"


def test_the_prompt_echoed_back_is_not_a_lesson():
    """Measured live on the first cycle after the parser was relaxed. A model
    with nothing to report copies the example instead of omitting the block,
    and the copy parses perfectly: three fields, all non-empty. A false lesson
    is worse than no lesson, because it reads as a finding."""
    assert _lesson_parts("situation -> action -> observed outcome") is None
    assert _lesson_parts("situation -> action -> the outcome you actually observed") is None
    assert _lesson_parts("Situation -> Action -> Outcome") is None
    assert _lesson_parts("None") is None
    assert _lesson_parts("(up to three; omit if none)") is None
    assert _lesson_parts("a stale source -> reread first -> none") is None


def test_prose_that_is_not_a_lesson_is_still_refused():
    """Forgiving about shape is not the same as accepting anything. A lesson
    missing a field is worse than no lesson: it reads as a finding."""
    assert _lesson_parts("I learned that the source was stale.") is None
    assert _lesson_parts("Situation: a stale source") is None
    assert _lesson_parts("Situation: x\nAction: y") is None
    assert _lesson_parts("a -> b") is None


def test_the_real_transcript_yields_exactly_what_it_says():
    """End to end on the fixture: two lessons, a review with the answer in it,
    and a journal that is only the journal."""
    lessons = [p for p in (_lesson_parts(l) for l in _grab_all("LESSON", REAL)) if p]
    assert len(lessons) == 2
    assert "authorship" in _grab("REVIEW", REAL)
    assert len(_grab("JOURNAL", REAL)) < 200
