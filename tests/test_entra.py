"""Entra ID tokens for Azure OpenAI (#156; spec: operations.md §5, ADR-0115). No Azure, no network: a fake credential."""

from __future__ import annotations

import functools
import json
import logging
import threading
import time
from dataclasses import dataclass

import httpx
import pytest

from arcviz import server
from arcviz.envconfig import ConfigError
from arcviz.llm import entra, factory
from arcviz.llm.entra import DEFAULT_SCOPE, BearerTokenProvider
from arcviz.llm.factory import ProviderConfig, check_provider, create_provider
from arcviz.store.file import FileStore
from tests.test_optional_sdks import hide

AZURE_URL = "https://contoso.openai.azure.com/openai/v1/"


@dataclass
class AccessToken:  # the shape of azure.core.credentials.AccessToken
    token: str
    expires_on: int


class FakeCredential:
    """Hands out t1, t2, … valid for `lifetime` seconds of the fake clock; records the scopes it was asked for."""

    def __init__(self, clock, lifetime: int = 3600, fail: bool = False):
        self.clock = clock
        self.lifetime = lifetime
        self.fail = fail
        self.scopes: list[tuple[str, ...]] = []

    def get_token(self, *scopes: str, **kwargs) -> AccessToken:
        self.scopes.append(scopes)
        if self.fail:
            raise RuntimeError("ManagedIdentityCredential: no identity endpoint")
        return AccessToken(f"t{len(self.scopes)}", int(self.clock.now) + self.lifetime)


class Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


# ---- the token provider ---------------------------------------------------------------------------------------


def test_the_token_is_fetched_once_and_cached_until_shortly_before_it_expires():
    clock = Clock()
    credential = FakeCredential(clock, lifetime=3600)
    tokens = BearerTokenProvider(credential, DEFAULT_SCOPE, refresh_margin=300, clock=clock)
    assert tokens() == "t1"
    clock.now += 3000  # 600 s left: still good
    assert tokens() == "t1"
    clock.now += 301  # 299 s left: inside the refresh margin
    assert tokens() == "t2"
    assert credential.scopes == [(DEFAULT_SCOPE,), (DEFAULT_SCOPE,)]


def test_a_failed_refresh_keeps_a_token_that_is_still_valid(caplog):
    clock = Clock()
    credential = FakeCredential(clock, lifetime=3600)
    tokens = BearerTokenProvider(credential, DEFAULT_SCOPE, refresh_margin=300, clock=clock)
    assert tokens() == "t1"
    clock.now += 3400
    credential.fail = True
    with caplog.at_level(logging.WARNING, logger="arcviz.llm"):
        assert tokens() == "t1"
    assert "refresh" in caplog.text and "t1" not in caplog.text  # the token itself is never logged


def test_without_a_valid_token_a_failure_propagates():
    clock = Clock()
    tokens = BearerTokenProvider(FakeCredential(clock, fail=True), DEFAULT_SCOPE, clock=clock)
    with pytest.raises(RuntimeError, match="no identity endpoint"):
        tokens()


def test_concurrent_callers_share_one_fetch():
    clock = Clock()
    started = threading.Event()

    class Slow(FakeCredential):
        def get_token(self, *scopes, **kwargs):
            started.set()
            time.sleep(0.05)
            return super().get_token(*scopes, **kwargs)

    credential = Slow(clock)
    tokens = BearerTokenProvider(credential, DEFAULT_SCOPE, clock=clock)
    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(tokens())) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == ["t1"] * 8 and len(credential.scopes) == 1


def test_the_token_provider_has_no_token_in_its_repr():
    clock = Clock()
    tokens = BearerTokenProvider(FakeCredential(clock), DEFAULT_SCOPE, clock=clock)
    tokens()
    assert "t1" not in repr(tokens)


# ---- the provider sends it -------------------------------------------------------------------------------------


def reply(text: str = "ok") -> dict:
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-4o",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def test_the_openai_provider_sends_the_bearer_token_and_refreshes_it(monkeypatch):
    clock = Clock()
    credential = FakeCredential(clock, lifetime=3600)
    monkeypatch.setattr(entra, "default_credential", lambda: credential)
    monkeypatch.setattr(entra, "BearerTokenProvider", functools.partial(BearerTokenProvider, clock=clock))
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        assert str(request.url) == AZURE_URL + "chat/completions"
        return httpx.Response(200, json=reply())

    config = ProviderConfig(provider="openai", base_url=AZURE_URL, openai_auth="entra", stream=False)
    provider = create_provider(config)
    provider.client = provider.client.copy(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert provider.complete_text("system", "hello") == "ok"
    clock.now += 3400  # past the refresh point
    assert provider.complete_text("system", "again") == "ok"
    assert seen == ["Bearer t1", "Bearer t2"]
    assert credential.scopes == [(DEFAULT_SCOPE,), (DEFAULT_SCOPE,)]


def test_a_configured_scope_is_used(monkeypatch):
    clock = Clock()
    credential = FakeCredential(clock)
    monkeypatch.setattr(entra, "default_credential", lambda: credential)
    config = ProviderConfig.from_env(
        {
            "ARCVIZ_PROVIDER": "openai",
            "OPENAI_BASE_URL": AZURE_URL,
            "ARCVIZ_OPENAI_AUTH": "entra",
            "ARCVIZ_OPENAI_TOKEN_SCOPE": "api://custom/.default",
        }
    )
    provider = create_provider(config)
    provider.client = provider.client.copy(
        http_client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=reply())))
    )
    provider.complete_text("s", "u")
    assert credential.scopes == [("api://custom/.default",)]


def test_key_auth_is_unchanged():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json=reply())

    provider = create_provider(ProviderConfig(provider="openai", api_key="sk-test", base_url=AZURE_URL, stream=False))
    provider.client = provider.client.copy(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    provider.complete_text("s", "u")
    assert seen == ["Bearer sk-test"]


# ---- configuration ---------------------------------------------------------------------------------------------


def test_entra_configuration_from_the_environment():
    config = ProviderConfig.from_env(
        {
            "ARCVIZ_PROVIDER": "openai",
            "OPENAI_BASE_URL": AZURE_URL,
            "ARCVIZ_OPENAI_AUTH": "entra",
            "OPENAI_API_KEY": "k",
        }
    )
    assert config.openai_auth == "entra" and config.token_scope == DEFAULT_SCOPE
    assert config.api_key is None  # ignored with entra
    assert ProviderConfig.from_env({}).openai_auth == "key"


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        ({"ARCVIZ_OPENAI_AUTH": "magic"}, "ARCVIZ_OPENAI_AUTH must be one of key, entra, got 'magic'."),
        (
            {"ARCVIZ_PROVIDER": "openai", "ARCVIZ_OPENAI_AUTH": "entra"},
            "ARCVIZ_OPENAI_AUTH=entra needs OPENAI_BASE_URL (the Azure OpenAI endpoint, "
            "https://<account>.openai.azure.com/openai/v1/).",
        ),
        (
            {"ARCVIZ_PROVIDER": "anthropic", "ARCVIZ_OPENAI_AUTH": "entra"},
            "ARCVIZ_OPENAI_AUTH=entra works only with ARCVIZ_PROVIDER=openai, got 'anthropic'.",
        ),
    ],
)
def test_entra_configuration_problems(environ, message):
    with pytest.raises(ConfigError) as caught:
        ProviderConfig.from_env(environ)
    assert message in caught.value.problems


def test_entra_needs_azure_identity(monkeypatch):
    hide(monkeypatch, "azure.identity")
    config = ProviderConfig(provider="openai", base_url=AZURE_URL, openai_auth="entra")
    assert check_provider(config) == [
        'ARCVIZ_OPENAI_AUTH=entra needs azure-identity: pip install "arcviz-agent[azure-openai]".'
    ]
    with pytest.raises(ConfigError, match=r"arcviz-agent\[azure-openai\]"):
        entra.default_credential()


def test_a_start_with_entra_but_without_azure_identity_stops(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", AZURE_URL)
    monkeypatch.setenv("ARCVIZ_OPENAI_AUTH", "entra")
    hide(monkeypatch, "azure.identity")
    with pytest.raises(ConfigError, match=r"arcviz-agent\[azure-openai\]"):
        server.create_app(store=FileStore(tmp_path / "data"))


def test_the_azure_openai_extra_exists():
    import tomllib
    from pathlib import Path

    extras = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())["project"][
        "optional-dependencies"
    ]
    assert any(d.startswith("azure-identity") for d in extras["azure-openai"])
    assert any(d.startswith("openai>=1.106") for d in extras["azure-openai"])  # a callable api_key
    assert factory.OPENAI_AUTH_MODES == ("key", "entra")
    json.dumps(extras)  # plain data


# ---- single-flight refresh with backoff (review of #185) ----------------------------------------------------------


class SlowFailing(FakeCredential):
    def __init__(self, clock, delay: float = 0.3):
        super().__init__(clock, fail=True)
        self.delay = delay
        self.started = threading.Event()

    def get_token(self, *scopes, **kwargs):
        self.started.set()
        time.sleep(self.delay)
        return super().get_token(*scopes, **kwargs)


def test_a_slow_failing_refresh_blocks_nobody_who_has_a_valid_token(caplog):
    clock = Clock()
    tokens = BearerTokenProvider(FakeCredential(clock, lifetime=3600), DEFAULT_SCOPE, refresh_margin=300, clock=clock)
    assert tokens() == "t1"
    clock.now += 3400  # inside the refresh margin, still valid for 200 s
    failing = SlowFailing(clock)
    tokens.credential = failing
    refresher = threading.Thread(target=tokens)
    with caplog.at_level(logging.WARNING, logger="arcviz.llm"):
        refresher.start()
        assert failing.started.wait(1)
        started = time.perf_counter()
        results = []
        others = [threading.Thread(target=lambda: results.append(tokens())) for _ in range(6)]
        for thread in others:
            thread.start()
        for thread in others:
            thread.join()
        assert time.perf_counter() - started < failing.delay / 2  # nobody waited for the slow refresh
        refresher.join()
    assert results == ["t1"] * 6 and len(failing.scopes) == 1  # single flight
    assert len([r for r in caplog.records if "refresh failed" in r.getMessage()]) == 1


def test_after_a_failed_refresh_the_next_attempt_waits_for_the_backoff(caplog):
    clock = Clock()
    tokens = BearerTokenProvider(FakeCredential(clock, lifetime=3600), DEFAULT_SCOPE, refresh_margin=300, clock=clock)
    tokens()
    clock.now += 3400
    failing = FakeCredential(clock, fail=True)
    tokens.credential = failing
    with caplog.at_level(logging.WARNING, logger="arcviz.llm"):
        for _ in range(5):
            assert tokens() == "t1"
        assert len(failing.scopes) == 1  # one attempt, then the backoff
        clock.now += entra.REFRESH_BACKOFF + 1
        assert tokens() == "t1"
        assert len(failing.scopes) == 2
    assert len([r for r in caplog.records if "refresh failed" in r.getMessage()]) == 2  # one per backoff window


def test_callers_without_a_valid_token_wait_for_the_refresh():
    clock = Clock()

    class Slow(FakeCredential):
        def get_token(self, *scopes, **kwargs):
            time.sleep(0.1)
            return super().get_token(*scopes, **kwargs)

    credential = Slow(clock)
    tokens = BearerTokenProvider(credential, DEFAULT_SCOPE, clock=clock)
    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(tokens())) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == ["t1"] * 5 and len(credential.scopes) == 1


# ---- second review: no hang, failures handed to the waiters, backoff without a token ----------------------------


def test_a_base_exception_or_a_malformed_token_never_leaves_the_refresh_stuck():
    clock = Clock()

    class Interrupting(FakeCredential):
        def get_token(self, *scopes, **kwargs):
            raise KeyboardInterrupt

    tokens = BearerTokenProvider(Interrupting(clock), DEFAULT_SCOPE, clock=clock)
    with pytest.raises(KeyboardInterrupt):
        tokens()

    @dataclass
    class Malformed:
        token: str
        expires_on: object

    class Bad(FakeCredential):
        def get_token(self, *scopes, **kwargs):
            super().get_token(*scopes)
            return Malformed("t", None)

    tokens.credential = Bad(clock)
    with pytest.raises(ValueError, match="invalid access token"):
        tokens()
    clock.now += entra.REFRESH_BACKOFF + 1
    tokens.credential = FakeCredential(clock)
    assert tokens() == "t1"  # not stuck: the next caller fetches again


def test_waiters_get_the_failure_of_the_refresh_they_waited_for_and_the_backoff_fails_fast():
    clock = Clock()

    class SlowBroken(FakeCredential):
        def get_token(self, *scopes, **kwargs):
            self.scopes.append(scopes)
            time.sleep(0.2)
            raise RuntimeError("no identity endpoint")

    credential = SlowBroken(clock)
    tokens = BearerTokenProvider(credential, DEFAULT_SCOPE, clock=clock)
    errors: list[BaseException] = []

    def call():
        try:
            tokens()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(5)]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(credential.scopes) == 1  # one attempt, not one per waiter
    assert len(errors) == 5 and all(isinstance(e, RuntimeError) for e in errors)
    assert time.perf_counter() - started < 0.6
    with pytest.raises(RuntimeError):  # within the backoff: fail fast, no new attempt
        tokens()
    assert len(credential.scopes) == 1


def test_waiting_for_a_refresh_is_bounded(monkeypatch):
    clock = Clock()
    release = threading.Event()

    class Hanging(FakeCredential):
        def get_token(self, *scopes, **kwargs):
            release.wait(2)
            return super().get_token(*scopes, **kwargs)

    tokens = BearerTokenProvider(Hanging(clock), DEFAULT_SCOPE, clock=clock, refresh_timeout=0.1)
    first = threading.Thread(target=lambda: pytest.raises(TimeoutError, tokens))
    first.start()
    time.sleep(0.05)
    started = time.perf_counter()
    with pytest.raises((TimeoutError, entra.TokenRefreshError), match="Entra token"):
        tokens()  # the waiter ends with the refresh's deadline
    assert time.perf_counter() - started < 0.5
    release.set()
    first.join()
