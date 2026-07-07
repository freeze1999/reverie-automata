"""Run the full plan -> execute -> learn loop on the deterministic mock backend.

No API key, no network. Shows the shape of a cycle end to end.

    python examples/demo.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata import Config
from reverie_automata.runner import Runner


def main():
    with tempfile.TemporaryDirectory() as td:
        cfg = Config.load()
        cfg.data["home"] = td
        cfg.data["window"] = {"start": 0, "end": 24}     # always in-window for the demo
        cfg.data["idle_minutes"] = 0
        cfg.data["min_gap_minutes"] = 0
        cfg.data["agent"] = {"backend": "mock", "options": {}}
        cfg.data["planner"] = {"backend": "mock", "options": {}}

        # pretend the principal last acted long ago and is away
        runner = Runner(cfg, last_input_ts=lambda: 0.0 + 1.0, is_available=lambda: True)
        # decide() needs a non-zero, sufficiently-old input ts:
        import time
        runner.last_input_ts = lambda: time.time() - 3600

        result = runner.tick()
        print("cycle result:", result)

        con = runner.store.connect()
        grade = con.execute("SELECT grade FROM cycles ORDER BY started_at DESC LIMIT 1").fetchone()
        journal = con.execute("SELECT body FROM journal ORDER BY created_at DESC LIMIT 1").fetchone()
        con.close()
        print("grade:", grade[0] if grade else "-")
        print("journal:\n ", (journal[0][:400] if journal else "(none)"))


if __name__ == "__main__":
    main()
