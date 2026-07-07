"""Point the flywheel at a real coding agent (Claude Code here; swap the backend
name for codex / cursor / devin / windsurf / cline / pi).

Prereqs: the agent's CLI installed and authenticated. This runs ONE supervised
cycle in the foreground so you can watch it — exactly how you'd validate before
putting ``Runner.tick()`` on a cron.

    python examples/with_claude_code.py ~/my-project
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata import Config
from reverie_automata.runner import Runner


def main(project: str):
    cfg = Config.load()                       # or Config.load("reverie.yaml")
    cfg.data.update({
        "home": str(Path(project) / ".reverie-automata"),
        "window": {"start": 0, "end": 24},
        "idle_minutes": 0, "min_gap_minutes": 0,
        "agent":   {"backend": "claude_code", "options": {"bin": "claude", "model": "sonnet"}},
        "planner": {"backend": "claude_code", "options": {"bin": "claude", "model": "haiku"}},
        "protected_paths": [str(Path(project) / ".env"), "~/.ssh"],
        "sources": [
            {"type": "shell", "label": "git", "priority": 3,
             "commands": [f"git -C {project} status -s", f"git -C {project} log --oneline -5"]},
            {"type": "markers", "label": "code", "priority": 2,
             "roots": [project], "exts": [".py", ".md", ".ts"]},
        ],
    })
    runner = Runner(cfg, last_input_ts=lambda: time.time() - 3600, is_available=lambda: True)
    print("running one supervised cycle...")
    print(runner.tick())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
