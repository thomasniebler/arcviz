# ADR-0113: The server generates every request id; a safe client `X-Request-ID` is logged apart; the trace id from `traceparent`

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/operations.md` (#83)

## Context

Container Apps' ingress (Envoy) sets `X-Request-ID`; Easy Auth and oauth2-proxy forward it. Clients and proxies
that trace send a W3C `traceparent`. A header value is attacker-controlled and ends up in every log record of the
request; a client could reuse another request's id and make records ambiguous.

## Options

1. **Always generate, ignore the header.** Unambiguous, but loses the correlation with the ingress and the proxy logs.
2. **Take a valid incoming value, else generate.** Correlates, but the client chooses the server's id.
3. **Always generate; log a valid incoming value as `client_request_id`** (`[A-Za-z0-9._:-]{1,128}`, else dropped).
   The response carries the server's id in `X-Request-ID`, replacing any the app set.

## Decision

Option 3; the trace id is parsed from a valid `traceparent` (and continued by the OpenTelemetry instrumentation).

## Consequences

Every request id is unique and the server's; ingress and proxy logs correlate through `client_request_id`, spans
through `trace_id`. Records written while serving a request carry all three.
