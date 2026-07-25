"""Run one real cycle on a locally served small model.

This is the weak-brain profile in one file: a grammar instead of trust, no
deliberation phase, a spine sized to the window the server actually reports,
and one task per cycle. The plan comes from the real model; execution here is
the offline mock, because a brain this size is meant to plan and delegate,
not to run tool sessions itself.

    python examples/with_local_model.py [--base http://host:8080]
"""
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from reverie_automata.config import Config          # noqa: E402
from reverie_automata.runner import Runner          # noqa: E402
from reverie_automata.adapters.local_server import LocalServer  # noqa: E402

BASE = "http://127.0.0.1:8080"
if "--base" in sys.argv:
    BASE = sys.argv[sys.argv.index("--base") + 1].rstrip("/")

# The plan envelope as a schema: the model cannot emit malformed structure,
# which removes the entire class of failure a small brain actually has.
PLAN_SCHEMA = {
    "name": "plan",
    "schema": {
        "type": "object",
        "properties": {
            "learned": {"type": "string"},
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "what": {"type": "string"},
                        "why": {"type": "string"},
                        "evidence": {"type": "string"},
                        "mode": {"enum": ["tool", "text", "delegate"]},
                        "risk": {"enum": ["SAFE", "RISKY"]},
                        "risk_reason": {"type": "string"},
                    },
                    "required": ["id", "what", "why", "mode", "risk"],
                },
            },
            "do_nothing": {"type": "boolean"},
            "do_nothing_reason": {"type": "string"},
        },
        "required": ["learned", "tasks", "do_nothing"],
    },
}


def main():
    probe = LocalServer({"base_url": BASE})
    n_ctx = probe.server_context()
    if not n_ctx:
        print(f"no server at {BASE} (or /props unavailable); start it first")
        return 1
    # Trust the window the server reports, never the one we asked for: it
    # splits context across slots and silently shrinks past its fitting limit.
    spine = max(1200, int(n_ctx * 0.5))
    print(f"server window: {n_ctx} tokens -> spine budget {spine}")

    home = Path("local-alpha-data")
    shutil.rmtree(home, ignore_errors=True)
    (home / "inbox").mkdir(parents=True)
    (home / "inbox" / "start.md").write_text(
        "Begin the research program. First step is literature only: find one\n"
        "published result about cubic-linear polynomial maps that can be\n"
        "checked by exact computation. Do not attempt a proof.\n")

    cfg = Config()
    cfg.data.update({
        "home": str(home),
        "harvest_max_tokens": spine,
        "max_tasks_per_cycle": 1,
        "window": {"start": 0, "end": 0},        # always in window for a demo
        "idle_minutes": 0,
        "planner": {"backend": "local_server", "options": {
            "base_url": BASE, "model": "local", "thinking": False,
            "schema": PLAN_SCHEMA, "temperature": 0.6}},
        "agent": {"backend": "mock", "options": {}},
    })

    runner = Runner(cfg, last_input_ts=lambda: time.time() - 7200,
                    is_available=lambda: True)
    t0 = time.time()
    out = runner.tick()
    print(f"\ntick -> {out}   ({time.time() - t0:.0f}s)")

    latest = sorted((home / "cycles").glob("*")) if (home / "cycles").is_dir() else []
    if latest:
        plan = (latest[-1] / "plan.txt")
        if plan.exists():
            print("\n--- what the brain actually planned ---")
            print(plan.read_text()[:700])
    print("\ninbox after the cycle:",
          [p.name for p in (home / "inbox").glob("*") if p.is_file()] or "(consumed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
