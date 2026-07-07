"""Tests for the pure parts of the agent adapters: argv construction, output
extraction, registry lookup, and the offline Mock's phase awareness. No agent
CLIs are executed except one FileNotFoundError probe with a fake binary name."""

from reverie_automata.adapters.agents import (
    REGISTRY,
    ClaudeCode,
    Codex,
    Mock,
    build_agent,
)
from reverie_automata.harvest import estimate_tokens


def test_claude_code_argv_defaults():
    a = ClaudeCode()._argv("do the thing", session=True)
    assert a[:3] == ["claude", "-p", "do the thing"]
    assert "--output-format" in a
    assert "--dangerously-skip-permissions" in a  # documented default, see SECURITY.md


def test_claude_code_argv_can_disable_skip_permissions():
    a = ClaudeCode({"dangerously_skip_permissions": False})._argv("x", session=True)
    assert "--dangerously-skip-permissions" not in a


def test_claude_code_argv_completion_mode_never_skips():
    a = ClaudeCode()._argv("x", session=False)
    assert "--dangerously-skip-permissions" not in a


def test_claude_code_extract_json_and_fallback():
    c = ClaudeCode()
    assert c._extract('{"result": "hi there"}') == "hi there"
    assert c._extract("not json at all") == "not json at all"


def test_codex_argv_options():
    a = Codex({"model": "o3", "subcommand": "exec"})._argv("task", session=True)
    assert a[:3] == ["codex", "exec", "task"]
    assert "-m" in a and "o3" in a
    assert "--full-auto" in a


def test_extra_args_are_appended():
    a = ClaudeCode({"extra_args": ["--verbose"]})._argv("x", session=False)
    assert a[-1] == "--verbose"


def test_registry_has_all_eight_backends():
    assert set(REGISTRY) == {"claude_code", "codex", "cursor", "devin",
                             "windsurf", "cline", "pi", "mock"}


def test_build_agent_unknown_backend_fails_loudly():
    try:
        build_agent({"backend": "typo"})
    except ValueError as e:
        assert "typo" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_build_agent_defaults_to_mock():
    assert build_agent({}).name == "mock"


def test_missing_binary_reports_instead_of_raising():
    agent = ClaudeCode({"bin": "definitely-not-a-real-binary-xyz"})
    out = agent.run_session("hello")
    assert "not found on PATH" in out


def test_mock_is_phase_aware():
    m = Mock()
    assert "<<PLAN>>" in m.complete("", "please answer with <<PLAN>>...")
    assert "<<JOURNAL>>" in m.run_session("write <<JOURNAL>> then stop")
    assert "<<RESULT>>" in m.run_session("execute the task")


def test_estimate_tokens_scales_with_text():
    assert estimate_tokens("") == 0
    small, big = estimate_tokens("word " * 10), estimate_tokens("word " * 1000)
    assert 0 < small < big
