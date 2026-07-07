"""reverie-automata — a reasoning-first idle flywheel for any coding agent.

    from reverie_automata import Config, Runner
    cfg = Config.load("reverie.yaml")
    Runner(cfg, last_input_ts=my_last_activity).tick()

See the README for the full picture. Public entry points below; everything else is
an implementation detail you can still reach if you want to.
"""
from .config import Config
from .engine import Engine
from .gate import decide, reap_lock
from .harvest import Harvester
from .inspector import Inspector
from .runner import Runner
from .store import Store
from .types import ActionClass, Lesson, Outcome, Plan, Risk, Task

__all__ = ["Config", "Runner", "Engine", "Store", "Harvester", "Inspector",
           "decide", "reap_lock", "ActionClass", "Risk", "Task", "Plan", "Lesson", "Outcome"]
__version__ = "0.1.0"
