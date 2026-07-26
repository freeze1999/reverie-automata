"""Standing orders, and the failure they exist to prevent.

A work-gated engine that runs its queue dry stops forever, correctly and
silently: nothing due, so nothing fires, so nothing new is created, so nothing
is ever due again. A mandate is what keeps the objective in force.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata import mandate as M
from reverie_automata.store import Store

BODY = """---
id: program-a
objective: advance the program by one verifiable step
cadence_hours: 6
---
The unit of work is a computation, a literature check, or a written question.
"""


def _store(tmp_path):
    s = Store(tmp_path / "state.db")
    return s, s.connect()


def _dir(tmp_path, text=BODY, name="program-a.md"):
    d = tmp_path / "mandates"
    d.mkdir(exist_ok=True)
    (d / name).write_text(text)
    return d


def test_a_mandate_becomes_due_work(tmp_path):
    store, con = _store(tmp_path)
    filed = M.refresh(con, store, _dir(tmp_path))
    assert len(filed) == 1
    assert store.due_threads(con)


def test_it_files_once_and_not_once_per_tick(tmp_path):
    """The whole risk: an objective that refiles every tick is a spin at
    heartbeat speed, and the queue fills with copies of one standing order."""
    store, con = _store(tmp_path)
    d = _dir(tmp_path)
    for _ in range(20):
        M.refresh(con, store, d)
    assert len(store.open_threads(con)) == 1


def test_rewording_the_body_does_not_spawn_a_second_thread(tmp_path):
    store, con = _store(tmp_path)
    d = _dir(tmp_path)
    M.refresh(con, store, d)
    (d / "program-a.md").write_text(BODY.replace("The unit of work", "Each step"))
    M.refresh(con, store, d)
    assert len(store.open_threads(con)) == 1


def test_an_inactive_mandate_files_nothing(tmp_path):
    store, con = _store(tmp_path)
    d = _dir(tmp_path, BODY.replace("cadence_hours: 6", "cadence_hours: 6\nactive: false"))
    assert M.refresh(con, store, d) == []
    assert store.open_threads(con) == []


def test_a_closed_mandate_waits_out_its_cadence(tmp_path):
    """Finishing an objective must not reissue it on the very next tick."""
    store, con = _store(tmp_path)
    d = _dir(tmp_path)
    state = tmp_path / "mandate_state.json"
    M.refresh(con, store, d, state_path=state)
    con.execute("UPDATE threads SET status='done'")
    con.commit()
    assert M.refresh(con, store, d, state_path=state) == []
    later = time.time() + 7 * 3600
    assert M.refresh(con, store, d, now=later, state_path=state)


def test_a_mandate_is_context_not_authority(tmp_path):
    """It files a thread like any other work item. There is no field here that
    can widen the toolkit or unlock a path, and that is deliberate."""
    store, con = _store(tmp_path)
    M.refresh(con, store, _dir(tmp_path, BODY + "\nprotected_paths: []\nrisk: SAFE\n"))
    kinds = {row[1] for row in store.open_threads(con)}
    assert kinds == {"mandate"}


def test_a_malformed_mandate_is_skipped_not_fatal(tmp_path):
    store, con = _store(tmp_path)
    d = _dir(tmp_path)
    (d / "broken.md").write_text("")
    assert len(M.refresh(con, store, d)) == 1


def test_one_unreadable_file_does_not_cost_the_others(tmp_path):
    """Found moving a live instance between machines: a macOS AppleDouble
    sidecar rode along inside the archive, was binary, and raised on decode.
    The whole load failed, nothing was filed, and the engine then correctly did
    nothing forever, because with no standing order nothing is ever due. It
    looked exactly like an honest quiet night."""
    store, con = _store(tmp_path)
    d = _dir(tmp_path)
    (d / "._program-a.md").write_bytes(b"\x00\xa3\xff Mac metadata, not text")
    assert len(M.refresh(con, store, d)) == 1
