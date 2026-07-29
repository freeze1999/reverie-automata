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


# ---- the menu, as the validator actually applies it -------------------------

from reverie_automata.planvalidate import validate_plan  # noqa: E402

GOOD = {"id": "t1", "type": "resolve_citation", "arxiv_id": "1503.08733",
        "claimed_title": "Some approaches toward the Jacobian conjecture",
        "why": "M1 needs a third source", "risk": "SAFE"}


def _v(tasks, **kw):
    kw.setdefault("work_available", True)
    kw.setdefault("max_tasks", 3)
    kw.setdefault("menu", MENU)
    return validate_plan({"tasks": tasks, "do_nothing": False}, **kw)


def test_an_admissible_task_survives_validation():
    plan, complaints, _ = _v([dict(GOOD)])
    assert len(plan["tasks"]) == 1 and complaints == []


def test_an_inadmissible_task_is_refused_by_the_validator():
    """A4 at the seam that matters: not merely unrepresentable in the type
    system, but actually dropped by the code the engine calls."""
    plan, complaints, _ = _v([{"id": "t1", "type": "read_and_understand",
                               "target": "LOG.md", "why": "x", "risk": "SAFE"}])
    assert plan["tasks"] == []
    assert any("refused" in c for c in complaints)


def test_two_paraphrases_in_one_plan_collapse_to_one():
    """A1, at the plan level: 125 wordings must not become 125 work items."""
    a = dict(GOOD)
    b = dict(GOOD, id="t2", claimed_title="  SOME APPROACHES TOWARD THE JACOBIAN CONJECTURE ")
    plan, complaints, _ = _v([a, b])
    assert len(plan["tasks"]) == 1
    assert any("same work" in c for c in complaints)


def test_a_recorded_dead_end_blocks_the_work_that_was_ruled_out():
    """A5: it diagnosed its own loop correctly and continued. A dead end has to
    have mechanical force, not prose force."""
    plan, complaints, _ = _v([dict(GOOD)], ruled_out={MENU.key(GOOD)})
    assert plan["tasks"] == []
    assert any("already ruled out" in c for c in complaints)


def test_the_stub_artifact_is_refused_at_the_seam_too():
    """A14, end to end: the nineteen-byte file never gets a task to be written
    by, because the task is missing five required fields."""
    plan, complaints, _ = _v([{"id": "t1", "type": "write_artifact",
                               "author": "local", "why": "x", "risk": "SAFE"}])
    assert plan["tasks"] == []
    assert any("missing required field" in c for c in complaints)


def test_without_a_menu_the_old_prose_path_still_runs():
    """Reverie ships for idle companions too, and they have no program menu.
    The typed path must be an addition, not a breaking change."""
    plan, _, _ = validate_plan(
        {"tasks": [{"id": "t1", "what": "sweep the vault", "mode": "tool"}],
         "do_nothing": False}, work_available=True, max_tasks=1)
    assert len(plan["tasks"]) == 1


def test_a_complete_supplied_task_is_filed_when_planning_produces_nothing(tmp_path):
    """Measured twice on the milestone run: the planner picks a plausible task
    TYPE and leaves the required fields empty, because filling them means
    transcribing specific values, which is the wall. Refusing is correct and
    produces nothing, so the harness supplies what the executor cannot author.
    """
    import json as _json
    import time as _time
    from reverie_automata.adapters import agents
    from reverie_automata.config import Config
    from reverie_automata.runner import Runner

    class Empty:
        name = "empty-planner"

        def __init__(self, options=None):
            pass

        def complete(self, system, user, *, max_tokens=1000):
            # exactly what the 27B did: right type, no fields
            return ('<<PLAN>>{"tasks": [{"id": "t1", "type": "resolve_citation",'
                    ' "why": "M1 needs a source", "risk": "SAFE"}],'
                    ' "do_nothing": false}<<END>>')

        def run_session(self, directive, **kw):
            return "<<RESULT>>done<<END>>\n<<VERIFY>>receipt<<END>>"

    agents.REGISTRY["empty-planner"] = Empty
    cfg = Config()
    cfg.data.update({
        "home": str(tmp_path / "h"), "trigger": "work", "window": {"start": 0, "end": 0},
        "idle_minutes": 0, "min_gap_minutes": 0, "max_cycles_per_day": 99,
        "planner": {"backend": "empty-planner"}, "agent": {"backend": "empty-planner"},
        "menu": MENU,
    })
    r = Runner(cfg, last_input_ts=lambda: _time.time() - 7200, is_available=lambda: True)

    con = r.store.connect()
    r.store.add_thread(con, "supplied: verify 2407.07911",
                       _json.dumps({"type": "resolve_citation",
                                    "arxiv_id": "2407.07911",
                                    "claimed_title": "Pluckerians"}),
                       kind="supplied")
    con.close()

    r.tick()
    latest = sorted((Path(cfg["home"]) / "cycles").glob("*"))[-1]
    out = _json.loads((latest / "outcome.json").read_text())
    assert out["ledger"], out["plan_complaints"]
    assert out["plan"]["tasks"][0]["arxiv_id"] == "2407.07911"
    assert any("supplied task was due" in c for c in out["plan_complaints"])


def test_the_risk_classifier_reads_intent_and_not_payload(tmp_path):
    """Found live: a task whose script field contained `np.prod(...)` was parked
    for approval, because a word boundary sits either side of a dot and `prod`
    was a risk token. Third time a token short enough to appear inside ordinary
    code has been used as a discriminator here."""
    import time as _time
    from reverie_automata.adapters import agents
    from reverie_automata.config import Config
    from reverie_automata.runner import Runner
    from reverie_automata.tasktypes import Menu, TaskType

    CODE = TaskType(name="compute", required=("path", "script"),
                    identity=("path",), payload=("script",),
                    summary="run something and write it down")

    class Noop:
        name = "risk-test"

        def __init__(self, options=None):
            pass

        def complete(self, s, u, *, max_tokens=1000):
            return '<<PLAN>>{"tasks": [], "do_nothing": true}<<END>>'

        def run_session(self, d, **kw):
            return "<<RESULT>>done<<END>>\n<<VERIFY>>r<<END>>"

    agents.REGISTRY["risk-test"] = Noop
    cfg = Config()
    cfg.data.update({"home": str(tmp_path / "h"), "menu": Menu([CODE]),
                     "planner": {"backend": "risk-test"}, "agent": {"backend": "risk-test"}})
    r = Runner(cfg, last_input_ts=lambda: _time.time() - 7200)

    task = {"type": "compute", "path": "results/x.json",
            "script": "import numpy as np\nprint(np.prod([1,2,3]))"}
    assert r.engine._wrapper_risk(task) == ("SAFE", "")

    # and intent that really is risky is still caught
    risky = {"type": "compute", "path": "results/x.json",
             "why": "deploy this to production", "script": "print(1)"}
    assert r.engine._wrapper_risk(risky)[0] == "RISKY"


def test_the_wrapper_cannot_force_an_untyped_task_under_a_menu(tmp_path):
    """The M0 verdict, as one test. Over 57 unattended cycles the wrapper filed
    21 tasks built from thread TITLES, with no type, so no postcondition ran and
    three of them graded done falsely. One answered a thread called "dead end:
    write_artifact" by computing the sum of a hundred rationals. The gates
    applied to what the planner proposed and not to what the wrapper forced."""
    import time as _time
    from reverie_automata.adapters import agents
    from reverie_automata.config import Config
    from reverie_automata.runner import Runner

    class Refuses:
        name = "refuser2"

        def __init__(self, options=None):
            pass

        def complete(self, s, u, *, max_tokens=1000):
            return '<<PLAN>>{"tasks": [], "do_nothing": true}<<END>>'

        def run_session(self, d, **kw):
            return "<<RESULT>>done<<END>>\n<<VERIFY>>anything at all<<END>>"

    agents.REGISTRY["refuser2"] = Refuses
    cfg = Config()
    cfg.data.update({
        "home": str(tmp_path / "h"), "trigger": "work", "window": {"start": 0, "end": 0},
        "idle_minutes": 0, "min_gap_minutes": 0, "max_cycles_per_day": 99,
        "planner": {"backend": "refuser2"}, "agent": {"backend": "refuser2"},
        "menu": MENU,
    })
    r = Runner(cfg, last_input_ts=lambda: _time.time() - 7200, is_available=lambda: True)

    con = r.store.connect()
    # a plain prose thread, exactly what used to get forced into a task
    r.store.add_thread(con, "advance the programme by one verifiable step", "",
                       kind="mandate")
    con.close()

    for _ in range(3):
        r.tick()

    import json as _json
    for d in sorted((Path(cfg["home"]) / "cycles").glob("*")):
        out = _json.loads((d / "outcome.json").read_text())
        assert all(e["status"] != "done" for e in out["ledger"]), out["ledger"]


def test_a_dead_end_is_never_picked_up_as_work(tmp_path):
    """A dead end records what was ruled out. Offering it back as a task asks
    the machine to redo what it just abandoned."""
    import json as _json
    import time as _time
    from reverie_automata.adapters import agents
    from reverie_automata.config import Config
    from reverie_automata.runner import Runner

    class Quiet:
        name = "quiet"

        def __init__(self, options=None):
            pass

        def complete(self, s, u, *, max_tokens=1000):
            return '<<PLAN>>{"tasks": [], "do_nothing": true}<<END>>'

        def run_session(self, d, **kw):
            return "<<RESULT>>done<<END>>\n<<VERIFY>>r<<END>>"

    agents.REGISTRY["quiet"] = Quiet
    cfg = Config()
    cfg.data.update({"home": str(tmp_path / "h"), "menu": MENU,
                     "planner": {"backend": "quiet"}, "agent": {"backend": "quiet"}})
    r = Runner(cfg, last_input_ts=lambda: _time.time() - 7200)
    con = r.store.connect()
    # a dead end whose body IS a valid typed task: still must not be worked
    r.store.add_thread(con, "dead end: resolve 1503.08733",
                       _json.dumps({"type": "resolve_citation", "arxiv_id": "1503.08733",
                                    "claimed_title": "t"}), kind="deadend")
    assert r.engine._typed_from_due_thread(con) is None
    con.close()


def test_a_type_no_tool_can_satisfy_is_reported_as_unreachable():
    """Found live: `record_deadend` required a structured row that no tool in
    the instance could write, so the machine failed it twice for a reason that
    had nothing to do with its ability. Admissible is not the same as
    reachable, and a menu entry with no route to its own postcondition is A4's
    unfinishable task wearing a type."""
    reach = Menu([
        TaskType(name="cite", required=("id",), identity=("id",), summary="s",
                 postcondition=lambda *a: (True, ""), satisfied_by=("verify_citation",)),
        TaskType(name="deadend", required=("subject",), identity=("subject",),
                 summary="s", postcondition=lambda *a: (True, "")),
    ])
    problems = reach.unreachable({"verify_citation", "read_file"})
    assert len(problems) == 1
    assert problems[0].startswith("deadend") and "which tool" in problems[0]

    missing = Menu([TaskType(name="cite", required=("id",), identity=("id",),
                             summary="s", postcondition=lambda *a: (True, ""),
                             satisfied_by=("record_deadend",))])
    assert "has none of them" in missing.unreachable({"read_file"})[0]


def test_a_task_whose_postcondition_already_holds_is_not_work(tmp_path):
    """A18. Over 140 cycles the only three completions were tasks asking for a
    state that already held, and one of them was a receipt describing its own
    failure, graded done because the check read the world as it is rather than
    what the cycle changed."""
    import json as _json
    import time as _time
    from reverie_automata.adapters import agents
    from reverie_automata.config import Config
    from reverie_automata.runner import Runner
    from reverie_automata.tasktypes import Menu, TaskType

    DONE_ALREADY = TaskType(
        name="ensure", required=("subject",), identity=("subject",),
        summary="make it so", satisfied_by=("tool",),
        postcondition=lambda t, r, h: (True, "it was already so"))

    class Eager:
        name = "eager"

        def __init__(self, options=None):
            pass

        def complete(self, s, u, *, max_tokens=1000):
            return ('<<PLAN>>{"tasks": [{"id": "t1", "type": "ensure",'
                    ' "subject": "x", "why": "w", "risk": "SAFE"}],'
                    ' "do_nothing": false}<<END>>')

        def run_session(self, d, **kw):
            # The learn phase legitimately runs a session; only the EXECUTE
            # directive must never appear, because the work was already done.
            assert "Do exactly this one task" not in d, "the executor was reached"
            return "<<JOURNAL>>nothing to do<<END>>"

    agents.REGISTRY["eager"] = Eager
    cfg = Config()
    cfg.data.update({"home": str(tmp_path / "h"), "trigger": "work",
                     "window": {"start": 0, "end": 0}, "idle_minutes": 0,
                     "min_gap_minutes": 0, "max_cycles_per_day": 9,
                     "planner": {"backend": "eager"}, "agent": {"backend": "eager"},
                     "menu": Menu([DONE_ALREADY])})
    r = Runner(cfg, last_input_ts=lambda: _time.time() - 7200, is_available=lambda: True)
    con = r.store.connect()
    r.store.add_thread(con, "something", "", kind="work")
    con.close()

    r.tick()
    out = _json.loads(
        (sorted((Path(cfg["home"]) / "cycles").glob("*"))[-1] / "outcome.json").read_text())
    assert out["ledger"][0]["status"] == "skipped"
    assert "already true before this cycle" in out["ledger"][0]["verify"]
