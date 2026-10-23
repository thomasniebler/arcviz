"""LLM SDKs are extras; the configured provider's SDK is checked at start-up (#84; spec: operations.md §2, ADR-0107)."""

from __future__ import annotations

import builtins
import importlib.util
import tomllib
from pathlib import Path

import pytest

from arcviz import server
from arcviz.envconfig import ConfigError
from arcviz.llm.factory import ProviderConfig, check_provider, create_provider
from arcviz.store.file import FileStore
from tests.conftest import ScriptedProvider

ROOT = Path(__file__).parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def app(tmp_path, provider=None):
    return server.create_app(store=FileStore(tmp_path / "data"), provider=provider)


def hide(monkeypatch, *modules: str) -> None:
    """Make `find_spec` and `import` behave as if the modules were not installed."""
    real_find_spec = importlib.util.find_spec
    real_import = builtins.__import__

    def find_spec(name, *args):
        return None if name.split(".")[0] in modules or name in modules else real_find_spec(name, *args)

    def fake_import(name, *args, **kwargs):
        if name in modules or name.split(".")[0] in modules:
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    for name in list(__import__("sys").modules):
        if name.startswith("arcviz.llm.anthropic_provider") or name.startswith("arcviz.llm.openai_provider"):
            monkeypatch.delitem(__import__("sys").modules, name)


# ---- packaging ------------------------------------------------------------------------------------------------


def test_the_sdks_are_extras_not_core_dependencies():
    core = " ".join(PYPROJECT["project"]["dependencies"])
    assert "anthropic" not in core and "openai" not in core
    extras = PYPROJECT["project"]["optional-dependencies"]
    assert any(d.startswith("anthropic") for d in extras["anthropic"])
    assert any(d.startswith("openai") for d in extras["openai"])
    dev = " ".join(extras["dev"])
    for name in ("anthropic", "openai"):
        assert name in dev, f"the tests need the {name} SDK"


def test_the_image_installs_every_runtime_extra():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    runtime = set(PYPROJECT["project"]["optional-dependencies"]) - {"dev"}
    line = next(line for line in dockerfile.splitlines() if line.startswith("ARG ARCVIZ_EXTRAS="))
    assert set(line.split("=", 1)[1].strip('"').split(",")) == runtime


# ---- the check ------------------------------------------------------------------------------------------------


def test_installed_sdks_pass_the_check():
    assert check_provider(ProviderConfig(provider="anthropic")) == []
    assert check_provider(ProviderConfig(provider="openai")) == []


@pytest.mark.parametrize(
    ("provider", "module", "message"),
    [
        (
            "anthropic",
            "anthropic",
            'ARCVIZ_PROVIDER=anthropic needs the Anthropic SDK: pip install "arcviz-agent[anthropic]".',
        ),
        ("openai", "openai", 'ARCVIZ_PROVIDER=openai needs the OpenAI SDK: pip install "arcviz-agent[openai]".'),
        ("ollama", "openai", 'ARCVIZ_PROVIDER=ollama needs the OpenAI SDK: pip install "arcviz-agent[openai]".'),
    ],
)
def test_a_missing_sdk_is_named_with_its_extra(monkeypatch, provider, module, message):
    hide(monkeypatch, module)
    assert check_provider(ProviderConfig(provider=provider)) == [message]


def test_an_unknown_provider_is_a_configuration_problem():
    with pytest.raises(ConfigError) as caught:
        ProviderConfig.from_env({"ARCVIZ_PROVIDER": "nonsense"})
    assert caught.value.problems == (
        "ARCVIZ_PROVIDER must be one of anthropic, openai, openai-compatible, ollama, got 'nonsense'.",
    )


@pytest.mark.parametrize(("provider", "module"), [("anthropic", "anthropic"), ("openai", "openai")])
def test_create_provider_explains_a_missing_sdk(monkeypatch, provider, module):
    hide(monkeypatch, module)
    with pytest.raises(ConfigError, match=rf'pip install "arcviz-agent\[{module}\]"'):
        create_provider(ProviderConfig(provider=provider, api_key="k"))


# ---- start-up -------------------------------------------------------------------------------------------------


def test_an_unknown_provider_stops_the_start(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_PROVIDER", "nonsense")
    with pytest.raises(ConfigError, match="ARCVIZ_PROVIDER must be one of anthropic, openai"):
        app(tmp_path)


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_a_missing_provider_sdk_stops_the_start_naming_the_extra(tmp_path, monkeypatch, provider):
    monkeypatch.setenv("ARCVIZ_PROVIDER", provider)
    hide(monkeypatch, provider)
    with pytest.raises(ConfigError) as caught:
        app(tmp_path)
    assert f'pip install "arcviz-agent[{provider}]"' in str(caught.value)
    assert f"ARCVIZ_PROVIDER={provider}" in str(caught.value)


def test_only_the_configured_providers_sdk_is_needed(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_PROVIDER", "openai")
    hide(monkeypatch, "anthropic")
    app(tmp_path)


def test_an_injected_provider_needs_no_sdk(tmp_path, monkeypatch):
    hide(monkeypatch, "anthropic", "openai")
    app(tmp_path, ScriptedProvider([]))


def test_a_missing_key_does_not_stop_the_start(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ARCVIZ_PROVIDER", "anthropic")
    app(tmp_path)  # the key is a readiness problem, not an installation defect (ADR-0107)
