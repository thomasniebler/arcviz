"""Provider construction without network: credential checks that readiness relies on."""

from __future__ import annotations

import pytest

from arcviz.llm.anthropic_provider import AnthropicProvider
from tests.conftest import ScriptedProvider


@pytest.fixture
def no_anthropic_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


def test_anthropic_without_credentials_reports_a_config_error(no_anthropic_env):
    assert AnthropicProvider().config_error() == "ANTHROPIC_API_KEY is not set."


def test_anthropic_with_a_key_or_token_is_configured(no_anthropic_env, monkeypatch):
    assert AnthropicProvider(api_key="sk-ant-test").config_error() is None
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
    assert AnthropicProvider().config_error() is None


def test_providers_are_configured_by_default():
    assert ScriptedProvider([]).config_error() is None
