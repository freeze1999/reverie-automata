"""Which brain answered, recorded in the cycle it answered.

Written after eight days of cycles were graded against the wrong executor. A
unit at boot replaced the model with a larger quantisation of the same weights
and cut the context window to a quarter. Every gate held, every receipt was
true, and none of them said what produced it, so runs before the swap and runs
after it were read as one series. The swap was eventually reconstructed from a
systemd file and the size of a graphics card, weeks later.

The engine records what the machine did. These tests hold the line that it also
records what the machine was.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.adapters import agents
from reverie_automata.config import Config
from reverie_automata.runner import Runner

BRAIN = {"model_path": "/m/small.gguf", "alias": "small", "n_ctx": 16384,
         "slots": 1, "build": "b1-abc"}


class Scripted:
    name = "brainstamp-test"
    identity = dict(BRAIN)
    raises = False

    def __init__(self, options=None):
        pass

    def complete(self, system, user, *, max_tokens=1000):
        return '<<PLAN>>{"tasks": [], "do_nothing": true}<<END>>'

    def run_session(self, directive, **kw):
        return "<<RESULT>>done<<END>>\n<<VERIFY>>a real receipt<<END>>"

    def server_identity(self):
        if Scripted.raises:
            raise RuntimeError("the server is not answering")
        return dict(Scripted.identity)


class Mute(Scripted):
    """A backend with no introspection at all, which is most of them."""
    name = "brainstamp-mute"
    server_identity = None


def _runner(tmp_path, backend="brainstamp-test"):
    agents.REGISTRY["brainstamp-test"] = Scripted
    agents.REGISTRY["brainstamp-mute"] = Mute
    Scripted.identity, Scripted.raises = dict(BRAIN), False
    cfg = Config()
    cfg.data.update({
        "home": str(tmp_path / "h"), "trigger": "work",
        "window": {"start": 0, "end": 0}, "idle_minutes": 0,
        "min_gap_minutes": 0, "max_cycles_per_day": 99,
        "planner": {"backend": backend},
        "agent": {"backend": backend},
    })
    r = Runner(cfg, last_input_ts=lambda: time.time() - 7200, is_available=lambda: True)
    d = Path(cfg["home"]) / "mandates"
    d.mkdir(parents=True, exist_ok=True)
    (d / "m.md").write_text("---\nid: p\nobjective: advance\n---\nbody\n")
    return r


def _outcomes(r):
    return [json.loads((c / "outcome.json").read_text())
            for c in sorted((Path(r.cfg["home"]) / "cycles").glob("*"))
            if (c / "outcome.json").exists()]


def _events(r, kind):
    p = Path(r.cfg["home"]) / "events.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").replace("\x00", "").splitlines():
        if line.strip():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") == kind or e.get("event") == kind:
                out.append(e)
    return out


def test_the_cycle_records_which_brain_answered(tmp_path):
    r = _runner(tmp_path)
    r.tick()
    got = _outcomes(r)[-1]["brain"]
    assert got == BRAIN, f"the cycle did not say what produced it: {got}"


def test_a_silent_swap_is_announced(tmp_path):
    """The whole reason this exists. The second cycle must not be readable as
    a continuation of the first when the executor underneath changed."""
    r = _runner(tmp_path)
    r.tick()
    Scripted.identity = dict(BRAIN, model_path="/m/big.gguf", alias="big", n_ctx=4096)
    r.cfg.data["min_gap_minutes"] = 0
    r.tick()

    changed = _events(r, "brain_changed")
    assert len(changed) == 1, f"a model and window swap went unannounced: {changed}"
    assert changed[0]["was"]["n_ctx"] == 16384
    assert changed[0]["now"]["n_ctx"] == 4096

    stamps = [o["brain"]["alias"] for o in _outcomes(r)]
    assert stamps == ["small", "big"], f"the stamps did not follow the swap: {stamps}"


def test_an_unchanged_brain_says_nothing(tmp_path):
    """A warning that fires every cycle is not a warning."""
    r = _runner(tmp_path)
    r.tick()
    r.tick()
    assert _events(r, "brain_changed") == []


def test_a_backend_that_cannot_introspect_does_not_stop_the_cycle(tmp_path):
    r = _runner(tmp_path, backend="brainstamp-mute")
    r.tick()
    out = _outcomes(r)
    assert out, "a backend without introspection lost the whole cycle"
    assert out[-1]["brain"] == {}, "silence must be recorded as silence"


def test_a_server_that_refuses_to_answer_does_not_stop_the_cycle(tmp_path):
    """The brain going away is exactly when the record matters most."""
    r = _runner(tmp_path)
    Scripted.raises = True
    r.tick()
    out = _outcomes(r)
    assert out, "an unreachable server lost the whole cycle"
    assert out[-1]["brain"] == {}


def test_the_stamp_is_never_inferred_from_a_stale_file(tmp_path):
    """brain.json is a comparison baseline, not a source. If the server has
    nothing to say, the cycle says nothing, rather than repeating yesterday."""
    r = _runner(tmp_path)
    r.tick()
    assert (Path(r.cfg["home"]) / "brain.json").exists()
    Scripted.raises = True
    r.tick()
    assert _outcomes(r)[-1]["brain"] == {}, "a stale file was passed off as a reading"


def test_a_hosted_endpoint_still_names_itself(tmp_path):
    """A remote API has no /props. Returning nothing there would put the record
    back where it was before the stamp existed: unable to say what produced a
    cycle, which is the defect the stamp was written for."""
    from reverie_automata.adapters.local_server import LocalServer

    s = LocalServer({"base_url": "https://api.example.com", "model": "big-model",
                     "n_ctx": 65536, "api_key": "x"})
    s._props = lambda: {}          # no introspection, as with a hosted API
    got = s.server_identity()
    assert got["alias"] == "big-model"
    assert got["model_path"] == "https://api.example.com"
    assert got["n_ctx"] == 65536
    assert got["build"] == "configured", "an assumed value must not read as observed"


def test_a_local_server_still_prefers_what_it_observed(tmp_path):
    from reverie_automata.adapters.local_server import LocalServer

    s = LocalServer({"base_url": "http://127.0.0.1:8080", "model": "local", "n_ctx": 99})
    s._props = lambda: {"model_path": "/m/a.gguf", "model_alias": "a",
                        "total_slots": 1, "build_info": "b1",
                        "default_generation_settings": {"n_ctx": 16384}}
    got = s.server_identity()
    assert got["n_ctx"] == 16384, "the declared value overrode the observed one"
    assert got["build"] == "b1"
