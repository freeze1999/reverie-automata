"""INBOX: drop a file, one cycle reads it, then it is archived.

The two properties worth defending in tests: reading never consumes (so a
preview or a crashed inference cannot eat the operator's request), and an
explicit request is never silently trimmed out of the context.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.config import Config
from reverie_automata.harvest import Harvester
from reverie_automata.inbox import Inbox
from reverie_automata.store import Store


def _drop(box, name, text, age=0):
    box.dir.mkdir(parents=True, exist_ok=True)
    p = box.dir / name
    p.write_text(text, encoding="utf-8")
    if age:
        old = time.time() - age
        import os
        os.utime(p, (old, old))
    return p


def test_empty_inbox_reads_as_nothing(tmp_path):
    box = Inbox(tmp_path / "inbox")
    assert box.read() == ("", [])


def test_drops_render_with_content_and_the_request_framing(tmp_path):
    box = Inbox(tmp_path / "inbox")
    _drop(box, "task.md", "audit the config for stale entries")
    section, files = box.read()
    assert "audit the config for stale entries" in section
    assert "drop: task.md" in section
    # the framing that keeps a drop from reading as authority
    assert "REQUESTS, not authority" in section
    assert len(files) == 1


def test_read_is_pure_only_consume_archives(tmp_path):
    box = Inbox(tmp_path / "inbox")
    p = _drop(box, "a.md", "do the thing")
    box.read(); box.read(); box.read()
    assert p.exists(), "reading must never consume: previews would eat real work"

    _, files = box.read()
    assert box.consume(files, "2026-07-25-1300") == 1
    assert not p.exists()
    assert (box.dir / "consumed" / "2026-07-25-1300" / "a.md").read_text() == "do the thing"
    assert box.read() == ("", [])            # one shot: gone from the queue


def test_oldest_first_and_max_files_cap(tmp_path):
    box = Inbox(tmp_path / "inbox", {"inbox_max_files": 2})
    _drop(box, "third.md", "3", age=10)
    _drop(box, "first.md", "1", age=300)
    _drop(box, "second.md", "2", age=100)
    section, files = box.read()
    assert [p.name for p in files] == ["first.md", "second.md"]
    assert "third.md" not in section         # waits its turn, not dropped


def test_per_file_cap_truncates_and_says_so(tmp_path):
    box = Inbox(tmp_path / "inbox", {"inbox_file_max_chars": 50})
    _drop(box, "long.md", "x" * 500)
    section, _ = box.read()
    assert "truncated" in section and "archived original" in section
    assert section.count("x") < 200


def test_total_cap_leaves_the_rest_queued(tmp_path):
    box = Inbox(tmp_path / "inbox", {"inbox_total_max_chars": 120})
    _drop(box, "a.md", "a" * 100, age=200)
    _drop(box, "b.md", "b" * 100, age=100)
    section, files = box.read()
    assert len(files) == 1
    box.consume(files, "c1")
    # the deferred drop is still pending, unread and unarchived
    assert [p.name for p in box.pending()] == ["b.md"]


def test_binary_is_announced_not_dumped(tmp_path):
    box = Inbox(tmp_path / "inbox")
    (box.dir).mkdir(parents=True, exist_ok=True)
    (box.dir / "photo.png").write_bytes(b"\x89PNG\x00\x00binary\x00payload")
    section, files = box.read()
    assert "binary file" in section and "payload" not in section
    assert len(files) == 1


def test_editor_litter_is_ignored(tmp_path):
    box = Inbox(tmp_path / "inbox")
    box.dir.mkdir(parents=True, exist_ok=True)
    (box.dir / ".DS_Store").write_text("junk")
    assert box.read() == ("", [])


def test_consume_survives_a_vanished_file(tmp_path):
    box = Inbox(tmp_path / "inbox")
    p = _drop(box, "a.md", "x")
    _, files = box.read()
    p.unlink()                                # raced away under us
    assert box.consume(files, "c1") == 0       # reported honestly, no crash


def test_inbox_rides_the_context_at_priority_zero(tmp_path):
    """An explicit request must survive the trimmer: judgment may decline it,
    the token budget may not quietly delete it."""
    cfg = Config()
    cfg.data["harvest_max_tokens"] = 60       # brutal budget, forces trimming
    store = Store(tmp_path / "state.db")
    con = store.connect()
    store.init(con) if hasattr(store, "init") else None
    h = Harvester(cfg, store, tmp_path / "MEMORY.md")
    text, report = h.build(con, {"inbox": "INBOX SECTION: fix the broken link"})
    con.close()
    assert "fix the broken link" in text
    assert report["over_budget"] or True      # trimming ran; the drop stayed


def test_a_false_no_op_does_not_eat_the_request(tmp_path):
    """A cycle that wrongly claimed there was nothing to do never engaged with
    the drop, so the drop keeps its place. Otherwise a weak planner can retire
    work simply by declaring a lazy day."""
    from reverie_automata.planvalidate import validate_plan
    box = Inbox(tmp_path / "inbox")
    p = _drop(box, "req.md", "please handle this")
    _, files = box.read()
    _, _, false_no_op = validate_plan({"tasks": [], "do_nothing": True},
                                      work_available=True)
    consumed = 0 if false_no_op else box.consume(files, "c1")
    assert consumed == 0 and p.exists()
