"""Start-up stops with one message naming every malformed variable (#118, #84; spec: operations.md §1-2, ADR-0106/0107)."""

from __future__ import annotations

import pytest

from arcviz import server
from arcviz.envconfig import ConfigError
from arcviz.store.file import FileStore
from tests.conftest import ScriptedProvider

# Every numeric variable the server itself reads, with a malformed value and the format the message must name.
SERVER_VARIABLES = [
    ("ARCVIZ_MAX_ROUNDS", "twelve", "an integer of at least 1"),
    ("ARCVIZ_MAX_ROUNDS", "0", "an integer of at least 1"),
    ("ARCVIZ_CONTEXT_BUDGET", "12k", "a non-negative integer"),
    ("ARCVIZ_CONTEXT_BUDGET", "-5", "a non-negative integer"),
    ("ARCVIZ_HISTORY_TURNS", "30.5", "a non-negative integer"),
    ("ARCVIZ_CHAT_COMPACT_AFTER", "sixty", "a non-negative integer"),
    ("ARCVIZ_CHAT_KEEP", "0", "an integer of at least 1"),
    ("ARCVIZ_TURN_TOKEN_BUDGET", "1e6", "a non-negative integer"),
    ("ARCVIZ_LLM_TIMEOUT", "soon", "a non-negative number"),
    ("ARCVIZ_LLM_MAX_RETRIES", "-2", "a non-negative integer"),
    ("ARCVIZ_MAX_BODY_BYTES", "1MB", "a non-negative integer"),
    ("ARCVIZ_MAX_IMPORT_BYTES", "-1", "a non-negative integer"),
    ("ARCVIZ_MAX_CHAT_CHARS", "x", "a non-negative integer"),
    ("ARCVIZ_RATE_LIMIT_CHAT", "ten", "a non-negative integer"),
    ("ARCVIZ_RATE_LIMIT_REVIEW", "ten", "a non-negative integer"),
    ("ARCVIZ_RATE_LIMIT_OPS", "ten", "a non-negative integer"),
    ("ARCVIZ_RATE_LIMIT_SHARE", "ten", "a non-negative integer"),
    ("ARCVIZ_LIVE_MAX_STREAMS", "many", "a non-negative integer"),
    ("ARCVIZ_LIVE_MAX_STREAMS_PER_USER", "many", "a non-negative integer"),
    ("ARCVIZ_LIVE_POLL_SECONDS", "often", "a number of seconds of at least 0.1"),
    ("ARCVIZ_LIVE_MAX_SECONDS", "forever", "a number of seconds of at least 0.1"),
]


def app(tmp_path, provider=None):
    return server.create_app(store=FileStore(tmp_path / "data"), provider=provider)


@pytest.mark.parametrize(("name", "value", "expected"), SERVER_VARIABLES, ids=lambda v: str(v))
def test_each_malformed_variable_stops_the_start_naming_variable_format_and_value(
    tmp_path, monkeypatch, name, value, expected
):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError) as caught:
        app(tmp_path)  # no injected provider: the provider variables are checked too
    assert caught.value.problems == (f"{name} must be {expected}, got {value!r}.",)


def test_valid_values_start_and_are_used(tmp_path, monkeypatch):
    for name, value in [
        ("ARCVIZ_MAX_ROUNDS", "3"),
        ("ARCVIZ_CONTEXT_BUDGET", "0"),
        ("ARCVIZ_HISTORY_TURNS", "4"),
        ("ARCVIZ_CHAT_COMPACT_AFTER", "0"),
        ("ARCVIZ_CHAT_KEEP", "2"),
        ("ARCVIZ_TURN_TOKEN_BUDGET", "100"),
    ]:
        monkeypatch.setenv(name, value)
    settings = server.AgentSettings.from_env()
    assert settings == server.AgentSettings(
        max_rounds=3, context_budget=None, history_turns=4, compact_after=0, keep=2, token_budget=100
    )
    app(tmp_path, ScriptedProvider([]))


def test_agent_settings_defaults_are_unchanged():
    assert server.AgentSettings.from_env({}) == server.AgentSettings(
        max_rounds=12,
        context_budget=server.DEFAULT_CONTEXT_BUDGET,
        history_turns=30,
        compact_after=60,
        keep=30,
        token_budget=0,
    )


def test_problems_of_several_subsystems_are_reported_together(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_MAX_ROUNDS", "x")  # agent
    monkeypatch.setenv("ARCVIZ_MAX_BODY_BYTES", "y")  # limits
    monkeypatch.setenv("ARCVIZ_LIVE_POLL_SECONDS", "z")  # live
    monkeypatch.setenv("ARCVIZ_AUTH", "sometimes")  # authentication (a rule of its own)
    monkeypatch.setenv("ARCVIZ_LLM_TIMEOUT", "w")  # provider
    monkeypatch.setenv("ARCVIZ_LOG_FORMAT", "xml")  # logging
    with pytest.raises(ConfigError) as caught:
        app(tmp_path)
    text = str(caught.value)
    assert text.startswith("Invalid configuration (6 problems):")
    for name in (
        "ARCVIZ_MAX_ROUNDS",
        "ARCVIZ_MAX_BODY_BYTES",
        "ARCVIZ_LIVE_POLL_SECONDS",
        "ARCVIZ_AUTH",
        "ARCVIZ_LLM_TIMEOUT",
        "ARCVIZ_LOG_FORMAT",
    ):
        assert name in text


def test_an_injected_provider_skips_the_provider_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_PROVIDER", "nonsense")
    monkeypatch.setenv("ARCVIZ_LLM_TIMEOUT", "soon")
    app(tmp_path, ScriptedProvider([]))  # tests and embedders bring their own provider


def test_main_reports_every_problem_without_a_traceback_and_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ARCVIZ_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARCVIZ_PORT", "80a")
    monkeypatch.setenv("ARCVIZ_MAX_ROUNDS", "x")
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: pytest.fail("must not start"))
    with pytest.raises(SystemExit) as caught:
        server.main()
    assert caught.value.code == 2
    err = capsys.readouterr().err
    assert "Invalid configuration (2 problems):" in err
    assert "ARCVIZ_PORT must be an integer from 1 to 65535, got '80a'." in err
    assert "ARCVIZ_MAX_ROUNDS must be an integer of at least 1, got 'x'." in err
    assert "Traceback" not in err


@pytest.mark.parametrize("port", ["0", "65536", "-1"])
def test_main_refuses_a_port_out_of_range(tmp_path, monkeypatch, capsys, port):
    monkeypatch.setenv("ARCVIZ_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARCVIZ_PORT", port)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: pytest.fail("must not start"))
    with pytest.raises(SystemExit):
        server.main()
    assert "ARCVIZ_PORT" in capsys.readouterr().err
