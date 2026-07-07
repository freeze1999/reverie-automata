"""Context sources — what the agent gets to see each cycle.

A source contributes one labelled block. The harvester concatenates them under a
token budget, trimming lowest-priority blocks first. Sources must never raise;
they degrade to "?" so a broken probe can't take down a cycle.

Built-in sources cover the common cases; writing your own is a ~10-line class.
Configure them in ``sources:`` — each entry is ``{type, label, priority, ...}``.
"""
from __future__ import annotations

import glob
import os
import subprocess
from pathlib import Path
from typing import Any


class FileSource:
    """Injects the contents (or tail) of files/globs — memory, notes, a TODO inbox."""

    def __init__(self, label, priority=4, patterns=None, max_chars=4000, **_):
        self.label, self.priority = label, priority
        self.patterns = patterns or []
        self.max_chars = max_chars

    def collect(self, ctx):
        out = []
        for pat in self.patterns:
            for p in sorted(glob.glob(os.path.expanduser(pat), recursive=True))[:20]:
                try:
                    out.append(f"# {p}\n" + Path(p).read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    continue
        return ("\n\n".join(out) or "(none)")[: self.max_chars]


class ShellSource:
    """Runs read-only probes and injects their output — service health, git status,
    disk, a queue depth. Commands are yours; keep them side-effect free."""

    def __init__(self, label, priority=4, commands=None, timeout=15, **_):
        self.label, self.priority = label, priority
        self.commands = commands or []
        self.timeout = timeout

    def collect(self, ctx):
        lines = []
        for c in self.commands:
            try:
                r = subprocess.run(c, shell=True, capture_output=True, text=True, timeout=self.timeout)
                lines.append(f"$ {c}\n{(r.stdout or r.stderr).strip()[:400]}")
            except Exception:
                lines.append(f"$ {c}\n?")
        return "\n".join(lines) or "(none)"


class MarkerScanSource:
    """Indexes files under roots that were recently modified or carry work markers
    (#todo, TODO, WIP, FIXME, #idea). Great for pointing the agent at a vault/repo
    without dumping it — it opens what it needs in-session."""

    MARKERS = ("#todo", "TODO", "WIP", "FIXME", "#idea", "#bug")

    def __init__(self, label, priority=2, roots=None, exts=(".md", ".txt"),
                 fresh_days=7, cap=60, **_):
        self.label, self.priority = label, priority
        self.roots = roots or []
        self.exts, self.fresh_days, self.cap = tuple(exts), fresh_days, cap

    def collect(self, ctx):
        import time
        items, now = [], time.time()
        for root in self.roots:
            rp = Path(os.path.expanduser(root))
            if not rp.is_dir():
                continue
            for p in sorted(rp.rglob("*")):
                if len(items) >= self.cap or p.suffix not in self.exts or not p.is_file():
                    continue
                try:
                    st = p.stat()
                    head = p.read_text(encoding="utf-8", errors="replace")[:2000]
                    fresh = (now - st.st_mtime) < self.fresh_days * 86400
                    mark = next((m for m in self.MARKERS if m in head), "")
                    if not fresh and not mark:
                        continue
                    heading = next((ln.strip().lstrip("#").strip() for ln in head.splitlines() if ln.strip()), "")
                    items.append(f"{p.name} — {heading[:60]} [{mark or 'fresh'}]")
                except Exception:
                    continue
        return "\n".join(items) or "(nothing fresh/marked)"


REGISTRY = {"file": FileSource, "shell": ShellSource, "markers": MarkerScanSource}


def build_source(spec: dict[str, Any]):
    t = (spec or {}).get("type")
    if t not in REGISTRY:
        raise ValueError(f"unknown source type '{t}'. known: {sorted(REGISTRY)}")
    kw = {k: v for k, v in spec.items() if k != "type"}
    kw.setdefault("label", t)
    return REGISTRY[t](**kw)
