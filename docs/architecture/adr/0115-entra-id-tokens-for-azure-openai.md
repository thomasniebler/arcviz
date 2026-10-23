# ADR-0115: Entra ID tokens for Azure OpenAI through an in-house token provider over `azure-identity`

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/operations.md` (#156)

## Context

The Azure OpenAI v1 endpoint accepts an Entra bearer token for the scope `https://cognitiveservices.azure.com/.default`.
Keys are long-lived shared secrets; the Cosmos store already uses `DefaultAzureCredential`. The OpenAI SDK (≥ 1.106)
accepts a callable as `api_key` and calls it before every request.

## Options

1. **`azure.identity.get_bearer_token_provider`.** Ready-made, but its refresh behaviour is azure-identity's and
   testing it needs the package and its internals.
2. **An in-house `BearerTokenProvider(credential, scope)`** that caches the `AccessToken` and refreshes it five
   minutes before `expires_on` (thread-safe, a single refresh in flight that never blocks holders of a valid token;
   a failed refresh keeps a still-valid token and backs off 30 s, without one the waiters share the failure, the
   backoff fails fast; the fetch has a 30 s deadline after which a hanging credential is abandoned as a failure,
   and the waiters get a new exception caused by it), over any object with
   `get_token(scope)`. Testable with a fake credential and a fake clock.
3. **`AzureOpenAI` client with `azure_ad_token_provider`.** The deployment-based API instead of the v1 endpoint the
   deployment uses.

## Decision

Option 2, configured with `ARCVIZ_OPENAI_AUTH=entra` (default `key`); `azure-identity` comes with the
`azure-openai` extra.

## Consequences

The container app runs without an OpenAI key (role *Cognitive Services OpenAI User*, then `disableLocalAuth` on the
account). A credential failure surfaces as a chat `error` event like any provider error; the readiness probe does
not fetch a token (no network call in `/readyz` for the provider).
