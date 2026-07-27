"""Evidence identity, tested with the exact artifacts that defeated the old gate."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.evidence import (check_overwrite, fingerprint, same_bytes,
                                       values_in_output)

# The real one, from the frozen control arm.
PLACEHOLDER = "[extracted facts and ruled-out branches]"


def test_the_placeholder_fails_the_exact_copy_postcondition(tmp_path):
    """A2. Forty bytes reported as "the exact text (24 chars)" of a 5176 byte
    file, graded done, because nothing compared them."""
    src = tmp_path / "LOG.md"
    src.write_text("# Program A log\n" + "real content\n" * 400)
    dst = tmp_path / "log_extract.md"
    dst.write_text(PLACEHOLDER)

    ok, why = same_bytes(src, dst)
    assert not ok
    assert "contents differ" in why and str(len(PLACEHOLDER)) in why


def test_a_true_copy_passes(tmp_path):
    src = tmp_path / "a.md"
    src.write_text("exactly this")
    dst = tmp_path / "b.md"
    dst.write_text("exactly this")
    ok, why = same_bytes(src, dst)
    assert ok and "identical" in why


def test_a_receipt_names_path_size_and_hash(tmp_path):
    """"I read it" is not a receipt. This is."""
    f = tmp_path / "x.json"
    f.write_text('{"a": 1}')
    fp = fingerprint(f)
    assert fp["exists"] and fp["bytes"] == 8 and len(fp["sha256"]) == 16


def test_a_missing_file_is_reported_rather_than_assumed(tmp_path):
    fp = fingerprint(tmp_path / "nope")
    assert fp["exists"] is False and "why" in fp


def test_a_value_claimed_but_never_printed_is_refused():
    """The weak form of "did you compute this". A number in a field and absent
    from the output was written, not computed."""
    ok, why = values_in_output({"rank": 2, "trace": 4}, "rank is 2 and nothing else")
    assert not ok and "trace" in why


def test_values_actually_printed_pass():
    ok, _ = values_in_output({"rank": 2, "trace": 4}, "rank 2, trace 4, done")
    assert ok


def test_an_overwrite_that_drops_contract_fields_is_refused(tmp_path):
    """A16. A sound artifact replaced in full by an invented identity matrix,
    every field lost, mtime the only trace."""
    p = tmp_path / "repro.json"
    p.write_text(json.dumps({"reproduces": "arXiv:1503.08733", "author": "delegate",
                             "script": "s", "output": "o", "conclusion": "c"}))
    invented = {"matrix": [[1, 0], [0, 1]]}
    ok, why = check_overwrite(p, invented,
                              ("reproduces", "author", "script", "output", "conclusion"))
    assert not ok
    assert "drops" in why and "reproduces" in why


def test_an_overwrite_that_keeps_everything_is_allowed(tmp_path):
    """Append-or-improve. Adding a field must stay possible, or the artifact
    can never be corrected."""
    p = tmp_path / "repro.json"
    p.write_text(json.dumps({"reproduces": "x", "author": "local"}))
    ok, _ = check_overwrite(p, {"reproduces": "x", "author": "local", "extends": {"t": 1}},
                            ("reproduces", "author"))
    assert ok


def test_a_new_file_is_never_a_regression(tmp_path):
    ok, why = check_overwrite(tmp_path / "fresh.json", {"a": 1}, ("a",))
    assert ok and "new file" in why
