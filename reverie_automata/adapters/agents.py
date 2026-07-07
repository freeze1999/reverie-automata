"""Agent backends — point the flywheel at whatever coding agent you already run.

Every modern coding agent exposes a non-interactive "run this prompt, print the
result" mode. That is *exactly* the shape reverie-automata needs for a phase-2/3
session. Each adapter below is a thin wrapper over one such CLI (or API). The
reasoning is the agent's; reverie-automata owns the loop, the gate, the memory,
and the safety rail around it.

Implemented against the documented non-interactive invocation of each tool. CLIs
move fast — every adapter reads its binary/flags from config ``options`` so you
can pin the exact command without editing code:

    agent:
      backend: claude_code
      options: { bin: claude, model: sonnet, extra_args: ["--permission-mode", "acceptEdits"] }

``complete()`` (cheap, no tools) falls back to the same CLI in a single-shot mode
unless you set a separate ``planner:`` backend (e.g. a raw LLM endpoint) — which is
usually cheaper for phase 1.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any


class _CliAgent:
    """Shared machinery for CLI-driven agents. Subclasses declare how to build the
    argv and how to pull the final text out of the tool's output."""

    name = "cli"
    default_bin = ""

    def __init__(self, options: dict[str, Any] | None = None):
        self.opt = dict(options or {})
        self.bin = self.opt.get("bin", self.default_bin)

    # -- subclass hooks ----------------------------------------------------
    def _argv(self, prompt: str, *, session: bool) -> list[str]:
        raise NotImplementedError

    def _extract(self, stdout: str) -> str:
        return stdout.strip()

    # -- interface ---------------------------------------------------------
    def run_session(self, directive, *, cwd="", env=None, turn_cap=40, timeout_s=2700) -> str:
        return self._invoke(directive, session=True, cwd=cwd, env=env, timeout_s=timeout_s)

    def complete(self, system, user, *, max_tokens=1000) -> str:
        prompt = (system + "\n\n" + user) if system else user
        return self._invoke(prompt, session=False, timeout_s=600)

    def _invoke(self, prompt, *, session, cwd="", env=None, timeout_s=2700) -> str:
        try:
            r = subprocess.run(self._argv(prompt, session=session), input=prompt,
                               capture_output=True, text=True, timeout=timeout_s,
                               cwd=cwd or None, env=env)
            return self._extract(r.stdout or r.stderr or "")
        except subprocess.TimeoutExpired:
            return "[session timed out]"
        except FileNotFoundError:
            return f"[adapter error: '{self.bin}' not found on PATH]"
        except Exception as e:  # noqa: BLE001
            return f"[session error: {e}]"


class ClaudeCode(_CliAgent):
    """Anthropic Claude Code. Non-interactive: ``claude -p <prompt> --output-format json``."""

    name = "claude_code"
    default_bin = "claude"

    def _argv(self, prompt, *, session):
        a = [self.bin, "-p", prompt, "--output-format", "json"]
        if self.opt.get("model"):
            a += ["--model", self.opt["model"]]
        if session and self.opt.get("dangerously_skip_permissions", True):
            a += ["--dangerously-skip-permissions"]
        return a + list(self.opt.get("extra_args", []))

    def _extract(self, stdout):
        try:
            return json.loads(stdout).get("result", stdout).strip()
        except Exception:
            return stdout.strip()


class Codex(_CliAgent):
    """OpenAI Codex CLI. Non-interactive: ``codex exec <prompt>`` (a.k.a. ``codex -q``)."""

    name = "codex"
    default_bin = "codex"

    def _argv(self, prompt, *, session):
        sub = self.opt.get("subcommand", "exec")
        a = [self.bin, sub, prompt]
        if self.opt.get("model"):
            a += ["-m", self.opt["model"]]
        if session and self.opt.get("full_auto", True):
            a += ["--full-auto"]
        return a + list(self.opt.get("extra_args", []))


class Cursor(_CliAgent):
    """Cursor agent CLI. Non-interactive headless run: ``cursor-agent -p <prompt>``."""

    name = "cursor"
    default_bin = "cursor-agent"

    def _argv(self, prompt, *, session):
        a = [self.bin, "-p", prompt]
        if self.opt.get("model"):
            a += ["--model", self.opt["model"]]
        return a + list(self.opt.get("extra_args", []))


class Devin(_CliAgent):
    """Cognition Devin. Driven via its API through the official CLI shim; set
    ``options.bin`` to your ``devin`` wrapper and pass the session prompt as argv."""

    name = "devin"
    default_bin = "devin"

    def _argv(self, prompt, *, session):
        return [self.bin, self.opt.get("subcommand", "run"), prompt] + list(self.opt.get("extra_args", []))


class Windsurf(_CliAgent):
    """Codeium Windsurf headless agent. ``windsurf --headless -p <prompt>`` (adjust to your build)."""

    name = "windsurf"
    default_bin = "windsurf"

    def _argv(self, prompt, *, session):
        return [self.bin, "--headless", "-p", prompt] + list(self.opt.get("extra_args", []))


class Cline(_CliAgent):
    """Cline (VS Code agent) via its CLI companion. ``cline task <prompt>``."""

    name = "cline"
    default_bin = "cline"

    def _argv(self, prompt, *, session):
        return [self.bin, self.opt.get("subcommand", "task"), prompt] + list(self.opt.get("extra_args", []))


class Pi(_CliAgent):
    """A generic 'pi'-style agent CLI. Configure ``bin`` + ``subcommand`` to match
    your install; the prompt is passed as the final argument and on stdin."""

    name = "pi"
    default_bin = "pi"

    def _argv(self, prompt, *, session):
        sub = [self.opt["subcommand"]] if self.opt.get("subcommand") else []
        return [self.bin, *sub, prompt] + list(self.opt.get("extra_args", []))


class Mock:
    """Deterministic, phase-aware offline backend so the demo and tests need no keys
    or network. It recognises which phase it's in from the envelope the prompt asks
    for and answers in kind — so the demo produces a clean, realistic cycle."""

    name = "mock"

    def __init__(self, options=None):
        self.opt = dict(options or {})

    def _reply(self, prompt: str) -> str:
        if "<<PLAN>>" in prompt:
            return ('<<PLAN>>{"learned": "last cycle I tidied notes; the box stayed small", '
                    '"tasks": [{"id": "t1", "what": "summarise today\'s open threads", '
                    '"why": "keep the working set legible", "mode": "text", "risk": "SAFE"}], '
                    '"do_nothing": false}<<END>>')
        if "<<JOURNAL>>" in prompt:
            return ("<<JOURNAL>>Quiet cycle. I read the open threads, wrote a one-line summary, "
                    "and left the rest for later. Nothing needed a real tool.<<END>>"
                    "<<REVIEW>>Smooth. I wasn't missing context this time.<<END>>"
                    "<<LESSON>>a lazy cycle with one small text task -> summarise instead of "
                    "forcing tool work -> the working set stayed legible and cheap<<END>>")
        # EXECUTE / EXECUTE_TEXT_ONLY
        return ("<<RESULT>>done<<END>><<VERIFY>>mock: wrote a 1-line summary of the open "
                "threads (no real tools were run)<<END>><<NOTE>>swap in a real agent backend "
                "to do real work<<END>>")

    def complete(self, system, user, *, max_tokens=1000):
        return self._reply((system or "") + "\n" + user)

    def run_session(self, directive, *, cwd="", env=None, turn_cap=40, timeout_s=2700):
        return self._reply(directive)


REGISTRY = {a.name: a for a in [ClaudeCode, Codex, Cursor, Devin, Windsurf, Cline, Pi, Mock]}


def build_agent(spec: dict[str, Any]):
    """spec = {"backend": "<name>", "options": {...}}. Unknown names raise, so a typo
    in config fails loudly instead of silently no-op'ing."""
    name = (spec or {}).get("backend", "mock")
    if name not in REGISTRY:
        raise ValueError(f"unknown agent backend '{name}'. known: {sorted(REGISTRY)}")
    return REGISTRY[name](spec.get("options"))
