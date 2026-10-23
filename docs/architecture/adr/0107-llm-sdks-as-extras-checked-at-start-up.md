# ADR-0107: LLM SDKs are extras; the configured provider's SDK is checked at start-up

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/operations.md` (#84)

## Context

`anthropic` and `openai` were core dependencies: every installation carried both SDKs and their dependency trees,
although one deployment uses one provider. The provider is created lazily on the first model call, and a failure
answers 503 (`/readyz` reports it). Without the SDKs in the core, a wheel installed without extras would only fail
on the first chat.

## Options

1. **Extras, errors stay lazy (503).** The app starts; `/readyz` stays unready; the reason is in `/api/config`.
2. **Extras, the SDK and the provider configuration are checked at start-up.** `importlib.util.find_spec` (no
   import), plus the provider name and its numeric variables; a missing key stays lazy because it is not a defect of
   the installation and readiness already reports it.
3. **Keep both SDKs in the core.** No change for anyone, but the dependency stays mandatory for everyone.

## Decision

Option 2: a missing SDK is an installation defect that should fail the rollout of a revision with a message naming
the extra, not surface as a 503 on the first chat.

## Consequences

`pip install arcviz-agent` needs an extra now (`[anthropic]` or `[openai]`); the message says which. An unknown
`ARCVIZ_PROVIDER` and malformed `ARCVIZ_LLM_*` values stop the start instead of answering 503 (only when the app
creates its provider itself; tests and embedders that inject one are unaffected). The lazy path explains a missing
SDK with the same message.
