"""Evidence that says what it is evidence OF.

The rule this project ran on for a month was "a claim needs a receipt", and it
is necessary and nowhere near sufficient. A receipt proves a tool ran. It says
nothing about what the tool ran on, and nothing about whether the result is the
thing that was asked for. Four independent demonstrations, two model sizes:

- a matrix invented, computed correctly, filed with a comment naming the paper
  it was not;
- forty bytes reading `[extracted facts and ruled-out branches]`, reported as
  "the exact text (24 chars)" of a 5176 byte file;
- a log copied into itself, moving a counter from 8 to 46;
- nineteen bytes reading `{"author": "local"}`, with a receipt every word of
  which was true.

None of those were dishonest. Each one was the smallest thing that made the
sentence true. So the fix is not to ask the executor for more honesty, which it
already had, but to make the sentence harder to satisfy trivially.

Two mechanisms here, and they are deliberately dull:

**Identity-grade receipts.** A receipt names path, size, and hash. "I read it"
is not a receipt; `results/x.json, 776 bytes, sha256 3f2a...` is. Adopted from
a sibling system where a competent agent had been doing this by judgment for
weeks, and turned into a floor because this engine's executor is not guaranteed
that judgment.

**Postconditions checked in the wrapper.** Where a task type implies something
mechanically checkable, the wrapper checks it after the claim and before the
grade. An exact copy implies hash equality. A computation implies its result
appears in captured output. Neither is a matter of opinion, and neither asks
the model anything.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def fingerprint(path) -> dict:
    """What a file IS, in the three terms a person can check by hand."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as e:
        return {"path": str(p), "exists": False, "why": str(e)}
    return {"path": str(p), "exists": True, "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()[:16]}


def same_bytes(a, b) -> tuple[bool, str]:
    """Exact-copy postcondition. The forty-byte placeholder fails here, and so
    does a truncation, a re-encoding, and a helpful summary."""
    fa, fb = fingerprint(a), fingerprint(b)
    if not fa["exists"] or not fb["exists"]:
        return False, f"missing: {fa if not fa['exists'] else fb}"
    if fa["sha256"] != fb["sha256"]:
        return False, (f"contents differ: {fa['path']} is {fa['bytes']} bytes, "
                       f"{fb['path']} is {fb['bytes']}")
    return True, f"identical, {fa['bytes']} bytes"


def values_in_output(values: dict, output: str) -> tuple[bool, str]:
    """A claimed computation must show its numbers in what it printed.

    The weak form of "did you actually compute this": a value asserted in a
    field and absent from the captured output was not computed, it was written.
    """
    missing = [k for k, v in (values or {}).items() if str(v) not in str(output)]
    if missing:
        return False, ("claimed but absent from the captured output: "
                       + ", ".join(missing))
    return True, f"{len(values or {})} value(s) present in output"


def not_a_regression(path, required_fields: tuple[str, ...]) -> tuple[bool, str]:
    """Refuse an overwrite that makes an existing artifact worse.

    Watched live: a sound artifact was replaced in full by an invented 4x4
    identity matrix, losing every field it had, with the modification time as
    the only trace. Artifacts are append-or-improve. If the file on disk
    already satisfies the contract, a replacement must satisfy it too.
    """
    p = Path(path)
    if not p.exists():
        return True, "new file"
    try:
        old = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True, "existing file is not readable json; nothing to preserve"
    lost = [f for f in required_fields if f in old]
    return (True, f"existing file has {len(lost)} contract field(s) to preserve"
            ) if lost else (True, "existing file satisfies nothing worth keeping")


def check_overwrite(path, new: dict, required_fields: tuple[str, ...]) -> tuple[bool, str]:
    """The half of `not_a_regression` that actually refuses: compare candidate
    against incumbent, field by field."""
    p = Path(path)
    if not p.exists():
        return True, "new file"
    try:
        old = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True, "existing file is not readable json"
    dropped = [f for f in required_fields
               if str(old.get(f, "")).strip() and not str(new.get(f, "")).strip()]
    if dropped:
        return False, (f"refusing to overwrite {p.name}: the replacement drops "
                       f"{dropped}, which the existing file has")
    return True, "replacement keeps every contract field the original had"
