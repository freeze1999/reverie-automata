"""Standing orders: the organ that makes the engine perpetual.

A work-gated engine wakes when something is due and otherwise costs nothing.
That is exactly right, and it has one consequence nobody expects until they
watch it happen: **finish the queue and the machine correctly stops forever.**
It is not broken and it is not lazy. Nothing is due, so nothing fires, so
nothing new is ever created, so nothing is ever due again. A standing operative
built only from the inbox and its own follow-ups has a half-life.

A mandate is the fix, and it is deliberately not a schedule. Cron says "run
this at ten past". A mandate says "this objective is always in force", and the
engine decides when to serve it, may honestly do nothing on a given cycle, and
may fail without the objective going away. Standing orders, not a timer.

Two floors, both learned the hard way elsewhere in this system:

- **A mandate files work; it does not authorise work.** The body is context,
  identical in standing to an inbox drop. It cannot widen the toolkit, unlock a
  protected path, or grant an approval, because text carries no authority and
  the inspector on the action is the only brake.
- **One thread per mandate, ever.** An objective that refiles itself every tick
  is a spin at heartbeat speed, and the queue would fill with copies of the
  same standing order until real work never got picked. The guarantee here is
  existence, not accumulation.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

HEAD = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)


@dataclass
class Mandate:
    id: str
    objective: str
    body: str = ""
    cadence_hours: float = 0.0
    active: bool = True
    priority: str = "mandate"

    @property
    def title(self) -> str:
        # The thread title is the identity. Stable across edits to the body, so
        # rewording an objective does not silently spawn a second standing
        # thread beside the first.
        return f"mandate {self.id}: {self.objective}"


def parse(text: str, fallback_id: str) -> Mandate | None:
    """A mandate is a short yaml-ish header and a body. No yaml dependency:
    the header is five scalar keys and a parser you can read in one sitting is
    worth more here than one that handles anchors."""
    m = HEAD.match(text)
    head, body = (m.group(1), text[m.end():]) if m else ("", text)
    fields: dict[str, str] = {}
    for line in head.splitlines():
        if ":" in line and not line.strip().startswith("#"):
            k, v = line.split(":", 1)
            fields[k.strip().lower()] = v.strip().strip("\"'")

    objective = fields.get("objective") or body.strip().splitlines()[0][:200] if body.strip() else ""
    if not objective:
        return None
    active = fields.get("active", "true").lower() not in ("false", "no", "0", "off")
    try:
        cadence = float(fields.get("cadence_hours", 0) or 0)
    except ValueError:
        cadence = 0.0
    return Mandate(id=fields.get("id") or fallback_id, objective=objective,
                   body=body.strip(), cadence_hours=cadence, active=active)


def load(directory) -> list[Mandate]:
    """Every readable mandate. One unreadable file must never cost the rest.

    Found moving a live instance between machines: a macOS AppleDouble sidecar
    (`._program-a.md`) travelled inside the archive, is binary, and raised
    UnicodeDecodeError. The caller caught it, filed nothing, and the engine
    then correctly did nothing forever, because with no standing order nothing
    is ever due. A single stray file silently switched off the organ that makes
    the machine perpetual, and the failure looked exactly like an honest quiet
    night. So: dot-files are skipped, decode errors are per-file, and a bad
    mandate costs only itself.
    """
    d = Path(directory)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        if p.name.startswith("."):
            continue
        try:
            m = parse(p.read_text(encoding="utf-8"), fallback_id=p.stem)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if m:
            out.append(m)
    return out


def refresh(con, store, directory, *, now: float | None = None,
            state_path=None) -> list[str]:
    """Guarantee one open thread per active mandate. Returns what it filed.

    Deterministic and model-free, like the gate it feeds. This runs before the
    work gate is asked its question, so a standing objective becomes due work
    by the same path an inbox drop does, and the gate stays the only thing that
    decides whether to spend a model call.
    """
    now = time.time() if now is None else now
    filed: list[str] = []
    last = _load_last(state_path)
    for m in load(directory):
        if not m.active:
            continue
        if store.has_open_titled(con, m.title):
            continue
        # A mandate whose thread was closed does not come back instantly; the
        # cadence is the minimum gap between refilings, which is what keeps a
        # completed objective from being reissued on the very next tick.
        if m.cadence_hours and now - float(last.get(m.id, 0)) < m.cadence_hours * 3600:
            continue
        store.add_thread(con, m.title, m.body, kind=m.priority,
                         created_cycle=None, defer=False, unique=True)
        last[m.id] = now
        filed.append(m.title)
    if filed:
        _save_last(state_path, last)
    return filed


def _load_last(path) -> dict:
    if not path:
        return {}
    try:
        import json
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_last(path, data) -> None:
    if not path:
        return
    try:
        import json
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass
