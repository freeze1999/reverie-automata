"""Blast radius: changed, new, and DELETED watch-set files must all surface."""
from reverie_automata import blast


def test_diff_flags_changed_new_and_deleted(tmp_path):
    victim = tmp_path / "identity.md"
    victim.write_text("soul")
    touched = tmp_path / "config.yaml"
    touched.write_text("cfg")

    before = blast.snapshot([tmp_path])
    victim.unlink()                      # the change the old ledger missed
    touched.write_text("cfg edited")
    newcomer = tmp_path / "surprise.txt"
    newcomer.write_text("new")
    after = blast.snapshot([tmp_path])

    got = blast.diff(before, after)
    assert f"deleted: {victim}" in got
    assert str(touched) in got
    assert str(newcomer) in got


def test_quiet_cycle_has_empty_radius(tmp_path):
    (tmp_path / "a.md").write_text("x")
    snap = blast.snapshot([tmp_path])
    assert blast.diff(snap, blast.snapshot([tmp_path])) == []


def test_missing_watch_path_is_skipped_not_fatal(tmp_path):
    assert blast.snapshot([tmp_path / "does-not-exist"]) == {}
