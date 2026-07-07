import sys, tempfile, time
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.config import Config
from reverie_automata.store import Store
from reverie_automata.engine import Engine, parse_plan, derive_grade
from reverie_automata.harvest import Harvester
from reverie_automata.inspector import Inspector
from reverie_automata.adapters.agents import build_agent
from reverie_automata.adapters.transports import build_transport


def test_parse_plan_tolerant():
    raw = 'blah blah <<PLAN>>{"tasks": [{"id":"t1","what":"x"}], "do_nothing": false}<<END>> trailing'
    p = parse_plan(raw)
    assert p and p["tasks"][0]["id"] == "t1"
    assert parse_plan("no envelope here") is None


def test_derive_grade():
    assert derive_grade([]) == "N"
    assert derive_grade([{"status": "done"}, {"status": "done"}]) == "A"
    assert derive_grade([{"status": "done"}, {"status": "failed"}, {"status": "failed"}]) == "C"
    assert derive_grade([{"status": "parked"}]) == "N"     # parked isn't a failure


def test_approval_transition_constraints():
    with tempfile.TemporaryDirectory() as td:
        s = Store(Path(td) / "a.db")
        con = s.connect()
        con.execute("INSERT INTO approvals (artifact, filed_at, status) VALUES ('{}', 0, 'pending')")
        con.commit()
        assert s.approval_transition(con, 1, "approved", event_id=10)
        assert not s.approval_transition(con, 1, "denied")          # illegal from approved
        assert not s.approval_transition(con, 1, "executed", event_id=10)  # replayed event
        assert s.approval_transition(con, 1, "executed", event_id=11)
        con.close()


def test_full_cycle_mock():
    with tempfile.TemporaryDirectory() as td:
        cfg = Config.load(); cfg.data["home"] = td
        store = Store(Path(td) / "state.db")
        eng = Engine(cfg, store, Harvester(cfg, store, Path(td) / "MEMORY.md"),
                     Inspector(cfg), build_agent({"backend": "mock"}),
                     build_agent({"backend": "mock"}), build_transport({"transport": "stdout"}))
        out = eng.run_cycle(now=datetime(2026, 7, 7, 13, 0))
        assert out.grade in ("A", "B", "C", "D", "F", "N")
        con = store.connect()
        assert con.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 1
        con.close()
