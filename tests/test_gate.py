import sys, time, os
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.config import Config
from reverie_automata import gate as G


def cfg():
    c = Config.load()
    c.data["window"] = {"start": 12, "end": 2}
    c.data["idle_minutes"] = 60
    c.data["min_gap_minutes"] = 90
    c.data["max_cycles_per_day"] = 3
    c.data["budget"] = {"floor": 1.5, "soft": 2.5, "max_per_cycle": 0.8}
    return c.data


BASE = datetime(2026, 7, 5, 14, 0)
def ago(m, ref=BASE): return ref.timestamp() - m * 60
def st(**kw):
    s = G.GateState(); [setattr(s, k, v) for k, v in kw.items()]; return s


def test_window_wraps_past_midnight():
    assert G.in_window(1, 12, 2) and G.in_window(23, 12, 2)
    assert not G.in_window(3, 12, 2) and not G.in_window(11, 12, 2)


def test_fires_when_armed_and_idle():
    f, to, _ = G.decide(BASE, ago(70), True, st(), cfg(), 5.0, False)
    assert f and not to


def test_kill_and_availability_block():
    assert not G.decide(BASE, ago(70), True, st(), cfg(), 5.0, True)[0]
    assert not G.decide(BASE, ago(70), False, st(), cfg(), 5.0, False)[0]


def test_fire_once_per_gap_then_rearm():
    assert not G.decide(BASE, ago(70), True, st(last_fired_input_ts=ago(70)), cfg(), 5.0, False)[0]
    assert G.decide(BASE, ago(70), True, st(last_fired_input_ts=ago(200)), cfg(), 5.0, False)[0]


def test_daily_cap_and_min_gap():
    day = BASE.strftime("%Y-%m-%d")
    full = st(fires=[f"{day}-0100", f"{day}-0230", f"{day}-1200"])
    assert not G.decide(BASE, ago(70), True, full, cfg(), 5.0, False)[0]
    assert not G.decide(BASE, ago(120), True, st(last_fired_input_ts=ago(300), last_fire_at=ago(60)), cfg(), 5.0, False)[0]


def test_budget_floor_and_soft():
    assert not G.decide(BASE, ago(70), True, st(), cfg(), 1.2, False)[0]
    f, to, _ = G.decide(BASE, ago(70), True, st(), cfg(), 2.0, False)
    assert f and to


def test_pid_aware_reap(tmp_path):
    c = cfg()
    # dead pid -> reaped immediately
    dead = tmp_path / "dead"; dead.write_text(str(2 ** 22))
    assert G.reap_lock(dead, c) and not dead.exists()
    # live pid, fresh -> kept
    live = tmp_path / "live"; live.write_text(str(os.getpid()))
    assert not G.reap_lock(live, c) and live.exists()
    # no-pid stale -> reaped
    old = tmp_path / "old"; old.touch()
    past = time.time() - (c["phase_timeout_minutes"] + 20) * 60
    os.utime(old, (past, past))
    assert G.reap_lock(old, c)
