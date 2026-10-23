"""Build the configured provider from environment variables."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from ..envconfig import ConfigError, Env, installed
from .base import LLMProvider
from .entra import DEFAULT_SCOPE, MISSING_EXTRA

PROVIDERS = ("anthropic", "openai", "openai-compatible", "ollama")
OPENAI_AUTH_MODES = ("key", "entra")  # entra: a bearer token from azure-identity (ADR-0115)
OPENAI_ALIASES = ("openai", "openai-compatible", "ollama")
# Per provider: the module its SDK installs, the SDK's name and the extra that provides it (ADR-0107).
SDKS = {
    "anthropic": ("anthropic", "Anthropic SDK", "anthropic"),
    **dict.fromkeys(OPENAI_ALIASES, ("openai", "OpenAI SDK", "openai")),
}


@dataclass
class ProviderConfig:
    provider: str = "anthropic"  # "anthropic" | "openai"
    model: str | None = None
    api_key: str | None = field(default=None, repr=False)
    base_url: str | None = None
    effort: str | None = None
    use_fallbacks: bool = True
    timeout: float | None = None  # seconds per request; None = SDK default
    max_retries: int | None = None  # retries with backoff; None = SDK default
    stream: bool = True  # stream reply text deltas
    openai_auth: str = "key"  # "key" | "entra" (OpenAI-compatible provider only)
    token_scope: str = DEFAULT_SCOPE  # the scope of the Entra token

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ProviderConfig:
        """The provider configuration; ConfigError (a ValueError) naming every malformed variable."""
        env = Env(environ)
        provider = env.choice("ARCVIZ_PROVIDER", PROVIDERS, "anthropic")
        cfg = cls(provider=provider, model=env.text("ARCVIZ_MODEL") or None)
        cfg.effort = env.text("ARCVIZ_EFFORT") or None
        cfg.use_fallbacks = env.raw("ARCVIZ_ANTHROPIC_FALLBACKS").lower() not in ("0", "false", "no")
        cfg.stream = env.raw("ARCVIZ_STREAM").lower() not in ("0", "false", "no")
        cfg.timeout = env.optional_number("ARCVIZ_LLM_TIMEOUT")
        cfg.max_retries = env.optional_integer("ARCVIZ_LLM_MAX_RETRIES")
        if provider == "anthropic":
            cfg.api_key = env.text("ANTHROPIC_API_KEY") or None
            cfg.base_url = env.text("ANTHROPIC_BASE_URL") or None
        else:
            cfg.api_key = env.text("OPENAI_API_KEY") or None
            cfg.base_url = env.text("OPENAI_BASE_URL") or None
        cfg.openai_auth = env.choice("ARCVIZ_OPENAI_AUTH", OPENAI_AUTH_MODES, "key")
        cfg.token_scope = env.text("ARCVIZ_OPENAI_TOKEN_SCOPE", DEFAULT_SCOPE)
        if cfg.openai_auth == "entra":
            cfg.api_key = None  # the token replaces the key
            if provider not in OPENAI_ALIASES:
                env.problem(f"ARCVIZ_OPENAI_AUTH=entra works only with ARCVIZ_PROVIDER=openai, got {provider!r}.")
            elif cfg.base_url is None:
                env.problem(
                    "ARCVIZ_OPENAI_AUTH=entra needs OPENAI_BASE_URL (the Azure OpenAI endpoint, "
                    "https://<account>.openai.azure.com/openai/v1/)."
                )
        env.check()
        return cfg


def check_provider(config: ProviderConfig) -> list[str]:
    """Problems that would stop `create_provider`: an SDK that is not installed (checked without importing it)."""
    sdk = SDKS.get(config.provider)
    if sdk is None:
        return []  # from_env reports an unknown provider
    module, name, extra = sdk
    problems = [] if installed(module) else [_missing(config.provider, name, extra)]
    if config.openai_auth == "entra" and not installed("azure.identity"):
        problems.append(MISSING_EXTRA)
    return problems


def _missing(provider: str, name: str, extra: str) -> str:
    return f'ARCVIZ_PROVIDER={provider} needs the {name}: pip install "arcviz-agent[{extra}]".'


def create_provider(config: ProviderConfig | None = None) -> LLMProvider:
    config = config or ProviderConfig.from_env()
    try:
        return _create(config)
    except ModuleNotFoundError as exc:
        sdk = SDKS.get(config.provider)
        if sdk is None or (exc.name or "").split(".")[0] != sdk[0]:
            raise
        raise ConfigError([_missing(config.provider, sdk[1], sdk[2])]) from exc


def _create(config: ProviderConfig) -> LLMProvider:
    if config.provider == "anthropic":
        from .anthropic_provider import DEFAULT_MODEL, AnthropicProvider

        return AnthropicProvider(
            model=config.model or DEFAULT_MODEL,
            api_key=config.api_key,
            base_url=config.base_url,
            effort=config.effort,
            use_fallbacks=config.use_fallbacks,
            timeout=config.timeout,
            max_retries=config.max_retries,
            stream=config.stream,
        )
    if config.provider in OPENAI_ALIASES:
        from .openai_provider import DEFAULT_MODEL, OpenAICompatibleProvider

        api_key: str | Callable[[], str] | None = config.api_key
        if config.openai_auth == "entra":
            from . import entra

            api_key = entra.BearerTokenProvider(entra.default_credential(), config.token_scope)
        return OpenAICompatibleProvider(
            model=config.model or DEFAULT_MODEL,
            api_key=api_key,
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=config.max_retries,
            stream=config.stream,
        )
    raise ValueError(f"Unknown provider '{config.provider}'. Use 'anthropic' or 'openai'.")
