"""The local-server adapter, exercised without a network.

What matters here is not that HTTP works, but that the three behaviours the
adapter exists for actually happen: the grammar is attached, deliberation is
off, and a reasoning build that ran out of budget mid-thought is reported as
that rather than as silence the engine would read as a refusal.
"""
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reverie_automata.adapters import local_server as LS
from reverie_automata.profiles import PLAN_SCHEMA, small_local, standing_operative


class _Captured:
    """Stands in for the server: records the request, returns a canned reply."""

    def __init__(self, reply):
        self.reply = reply
        self.body = None
        self.url = None

    def __call__(self, req, timeout=None):
        self.url = getattr(req, "full_url", str(req))
        data = getattr(req, "data", None)
        if data:
            self.body = json.loads(data)
        return io.BytesIO(json.dumps(self.reply).encode())


def _reply(content="", reasoning="", finish="stop"):
    return {"choices": [{"finish_reason": finish,
                         "message": {"role": "assistant", "content": content,
                                     "reasoning_content": reasoning}}]}


def _patch(monkeypatch, cap):
    monkeypatch.setattr(LS.urllib.request, "urlopen", cap)


def test_deliberation_is_off_by_default(monkeypatch):
    cap = _Captured(_reply(content="<<PLAN>>{}<<END>>"))
    _patch(monkeypatch, cap)
    LS.LocalServer({"base_url": "http://x"}).complete("", "plan please")
    assert cap.body["chat_template_kwargs"] == {"enable_thinking": False}


def test_thinking_can_be_asked_for_explicitly(monkeypatch):
    cap = _Captured(_reply(content="ok"))
    _patch(monkeypatch, cap)
    LS.LocalServer({"base_url": "http://x", "thinking": True}).complete("", "hi")
    assert "chat_template_kwargs" not in cap.body


def test_the_schema_is_attached_and_markers_are_restored(monkeypatch):
    """Under a grammar the model emits bare JSON; the harness still expects
    its envelope, so the adapter puts the markers back."""
    cap = _Captured(_reply(content='{"do_nothing": true}'))
    _patch(monkeypatch, cap)
    out = LS.LocalServer({"base_url": "http://x", "schema": PLAN_SCHEMA}).complete("", "go")
    assert cap.body["response_format"]["type"] == "json_schema"
    assert cap.body["response_format"]["json_schema"]["strict"] is True
    assert out.startswith("<<PLAN>>") and out.endswith("<<END>>")


def test_markers_are_not_doubled_if_the_model_supplied_them(monkeypatch):
    cap = _Captured(_reply(content="<<PLAN>>{}<<END>>"))
    _patch(monkeypatch, cap)
    out = LS.LocalServer({"base_url": "http://x", "schema": PLAN_SCHEMA}).complete("", "go")
    assert out.count("<<PLAN>>") == 1


def test_a_thought_that_never_finished_is_reported_not_hidden(monkeypatch):
    """The failure that reads as a dead model: a reasoning build spends the
    whole budget deliberating and returns empty content. Saying so plainly
    keeps the engine from recording a refusal that never happened."""
    cap = _Captured(_reply(content="", reasoning="thinking out loud...", finish="length"))
    _patch(monkeypatch, cap)
    out = LS.LocalServer({"base_url": "http://x"}).complete("", "go")
    assert "still deliberating" in out and "length" in out


def test_an_empty_reply_says_empty(monkeypatch):
    cap = _Captured(_reply(content="", reasoning=""))
    _patch(monkeypatch, cap)
    assert "empty response" in LS.LocalServer({"base_url": "http://x"}).complete("", "go")


def test_a_dead_server_returns_a_legible_string_not_an_exception(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("connection refused")
    _patch(monkeypatch, boom)
    out = LS.LocalServer({"base_url": "http://x"}).complete("", "go")
    assert out.startswith("[local server error:") and "refused" in out


def test_the_window_is_read_from_the_server(monkeypatch):
    class _Ctx(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(
        LS.urllib.request, "urlopen",
        lambda url, timeout=None: _Ctx(json.dumps(
            {"default_generation_settings": {"n_ctx": 16384}}).encode()))
    assert LS.LocalServer({"base_url": "http://x"}).server_context() == 16384


def test_an_unreachable_server_reports_no_window(monkeypatch):
    def boom(url, timeout=None):
        raise OSError("nope")
    monkeypatch.setattr(LS.urllib.request, "urlopen", boom)
    assert LS.LocalServer({"base_url": "http://x"}).server_context() is None


def test_tool_sessions_are_declined_in_words(monkeypatch):
    out = LS.LocalServer({"base_url": "http://x"}).run_session("do a thing")
    assert "no tool session" in out and "delegates" in out


# --- the profiles that bundle all of the above ---------------------------
def test_small_local_sizes_the_spine_from_the_reported_window(monkeypatch):
    monkeypatch.setattr(LS.LocalServer, "server_context", lambda self: 16384)
    cfg = small_local("http://x")
    assert cfg["harvest_max_tokens"] == 8192
    assert cfg["max_tasks_per_cycle"] == 1
    assert cfg["planner"]["options"]["thinking"] is False
    assert cfg["planner"]["options"]["schema"] is PLAN_SCHEMA


def test_small_local_falls_back_small_when_the_server_is_silent(monkeypatch):
    monkeypatch.setattr(LS.LocalServer, "server_context", lambda self: None)
    assert small_local("http://x")["harvest_max_tokens"] == 2048


def test_overrides_win_over_the_profile(monkeypatch):
    monkeypatch.setattr(LS.LocalServer, "server_context", lambda self: 4096)
    assert small_local("http://x", max_tasks_per_cycle=3)["max_tasks_per_cycle"] == 3


def test_standing_operative_arms_on_work():
    cfg = standing_operative()
    assert cfg["trigger"] == "work" and cfg["thread_cooldown_minutes"] > 0


def test_a_grammar_applies_only_to_the_shape_it_describes(monkeypatch):
    """One planner serves both planning and text-only execution. A schema
    bound to every call makes the second answer the first one's question."""
    cap = _Captured(_reply(content='{"do_nothing": true}'))
    _patch(monkeypatch, cap)
    srv = LS.LocalServer({"base_url": "http://x", "schema": PLAN_SCHEMA,
                          "schema_marker": "<<PLAN>>"})

    srv.complete("", "... emit one envelope <<PLAN>>{...}<<END>>")
    assert "response_format" in cap.body

    cap.body = None
    out = srv.complete("", "do the task, close with <<RESULT>>done<<END>>")
    assert "response_format" not in cap.body
    assert not out.startswith("<<PLAN>>")


def test_json_object_mode_puts_the_shape_in_the_prompt(monkeypatch):
    """DeepSeek's official API answers "this response_format type is
    unavailable now" to json_schema. The weaker mode guarantees valid json and
    not the keys, so the shape has to be stated where the model can read it."""
    from reverie_automata.adapters.local_server import LocalServer
    sent = {}

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return json.dumps({"choices": [{"message": {"content": '{"a": 1}'}}]}).encode()

    def fake_urlopen(req, timeout=0):
        sent["body"] = json.loads(req.data.decode())
        sent["headers"] = req.headers
        return R()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    s = LocalServer({"schema_mode": "json_object", "api_key": "k",
                     "schema": {"name": "step", "schema": {"type": "object"}}})
    s.complete("", "do the thing")
    assert sent["body"]["response_format"] == {"type": "json_object"}
    assert "ONE json object" in sent["body"]["messages"][-1]["content"]
    assert sent["headers"]["Authorization"] == "Bearer k"
