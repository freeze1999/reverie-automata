"""A planner backend for a locally served model (llama.cpp server or any
OpenAI-shaped endpoint), built for brains that are small enough to be worth
constraining rather than trusting.

Three behaviours here exist because they were measured, not assumed:

**Grammar over discipline.** A small quantized model understands a planning
task perfectly well and then fails at hand-serializing JSON: control bytes
inside strings, invalid escapes, a dropped closing marker. Prompting harder
does not fix that; a schema does. When `schema` is supplied the request is
sent with `response_format: json_schema`, which makes malformed output
impossible rather than unlikely, and the envelope markers are re-attached
locally so the rest of the harness sees its usual shape.

**Deliberation off for structured phases.** Reasoning builds put their
thinking in `reasoning_content` and leave `content` empty until they finish.
On a small local window a deliberation pass can burn the entire budget and
never reach the envelope at all, which reads to the harness as a dead model.
`enable_thinking` is therefore off by default here, and both fields are read
back so a server that ignores the switch still degrades to something legible.

**Read the window back, never trust it.** `llama.cpp` splits its context
across parallel slots unless told otherwise, and above its fitting limit it
silently falls back to a small default instead of the next workable size. The
configured number is a request; `/props` is the truth.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any


class LocalServer:
    """OpenAI-shaped local endpoint. Planner-side only: it completes prompts,
    it does not run tool sessions (a small brain delegates that work)."""

    name = "local_server"

    def __init__(self, options: dict[str, Any] | None = None):
        o = dict(options or {})
        self.base = str(o.get("base_url", "http://127.0.0.1:8080")).rstrip("/")
        self.model = o.get("model", "local")
        self.temperature = float(o.get("temperature", 0.6))
        self.thinking = bool(o.get("thinking", False))
        self.timeout_s = int(o.get("timeout_s", 600))
        # {"name": ..., "schema": {...}} to force valid structure
        self.schema = o.get("schema")
        # A grammar describes ONE expected shape, so it must not be applied to
        # every prompt this backend serves. The harness reuses one planner for
        # both planning and text-only execution; forcing the plan schema on the
        # second makes the model answer a question it was not asked. When set,
        # the schema applies only to prompts that actually request that shape.
        self.schema_marker = o.get("schema_marker")
        # markers re-attached around schema output so the harness parses it
        self.open_tag = o.get("open_tag", "<<PLAN>>")
        self.close_tag = o.get("close_tag", "<<END>>")

    # -- introspection -----------------------------------------------------
    def server_context(self) -> int | None:
        """The window the server ACTUALLY holds, read back from /props."""
        try:
            with urllib.request.urlopen(self.base + "/props", timeout=15) as r:
                d = json.loads(r.read())
            return (d.get("default_generation_settings") or {}).get("n_ctx")
        except Exception:
            return None

    # -- interface ---------------------------------------------------------
    def complete(self, system, user, *, max_tokens=1000) -> str:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": user})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
            "max_tokens": max_tokens,
            "temperature": self.temperature,
        }
        if not self.thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        use_schema = bool(self.schema) and (
            self.schema_marker is None or self.schema_marker in (user or ""))
        if use_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": self.schema.get("name", "envelope"),
                                "schema": self.schema["schema"], "strict": True},
            }
        try:
            req = urllib.request.Request(
                self.base + "/v1/chat/completions",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                d = json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            return f"[local server error: {e}]"

        choice = (d.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = (msg.get("content") or "").strip()
        if not text:
            # a reasoning build that never finished thinking: say so plainly
            # rather than returning silence the harness would read as a refusal
            thought = (msg.get("reasoning_content") or "").strip()
            if thought:
                return ("[local server: the model was still deliberating when the "
                        f"token budget ran out ({choice.get('finish_reason')}); "
                        "no envelope was produced]")
            return "[local server: empty response]"
        if use_schema and not text.startswith(self.open_tag):
            text = f"{self.open_tag}{text}{self.close_tag}"
        return text

    def run_session(self, directive, *, cwd="", env=None, turn_cap=40, timeout_s=2700) -> str:
        return ("[local_server has no tool session: this brain plans and "
                "delegates; give the engine a tool-capable agent backend]")
