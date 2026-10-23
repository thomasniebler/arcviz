# ADR-0134: The app's own spans and metrics, by allow-list, and keyless Azure Monitor export

- Status: Accepted
- Date: 2026-10-09
- Spec: `docs/architecture/specs/follow-ups.md` (#187)
- Refines: [ADR-0112](0112-logs-carry-pseudonymous-users-and-route-templates.md), [ADR-0114](0114-opentelemetry-as-an-optional-extra.md)

## Context

M9 exports request spans (FastAPI instrumentation, attributes cut to an allow-list, events scrubbed) and log records,
and switches the instrumentation's own HTTP metrics off because their attributes (raw target, host) cannot be
restricted. A chat turn is one opaque request span; there are no metrics; the Azure Monitor exporter authenticates
with the key in the connection string. Every privacy rule of M9 stays: no ids, raw paths, user ids or content.

## Options

1. **Own instrumentation through one small module** (`arcviz/telemetry.py`, no package imports): spans around model
   calls, tool executions and the turn's store writes, and four own instruments (request duration per route template,
   LLM duration and tokens, open live streams), each with a fixed attribute allow-list and value rules (route
   templates, offered tool names else `unknown`, configured provider/model names, outcomes, counts); a no-op until
   telemetry is configured. Entra ID through `DefaultAzureCredential` handed to `configure_azure_monitor`
   (azure-identity already comes with the Azure Monitor exporter: no new dependency).
2. **Instrumentation libraries** (OpenAI/Anthropic SDK instrumentations, the FastAPI instrumentation's metrics, the
   Azure SDK's tracing): broader coverage, but their attributes include prompts, URLs with document ids or raw
   targets, which ADR-0112 rules out, and they would need per-library scrubbing.
3. **Metrics derived from logs** (the `request` and `llm_turn` records): no code, but only in JSON mode and only in a
   log backend.

## Decision

Option 1: the smallest surface that answers the operational questions (latency per route, model cost and latency,
live load) with attributes we control; spans that cross a generator's `yield` (a streamed model call) are not made
current, so no context is attached on one worker thread and detached on another.

## Consequences

- `observability.ALLOWED_SPAN_ATTRIBUTES` = the request spans' list plus `telemetry.SPAN_ATTRIBUTES`; the end-of-span
  scrubber enforces it for every span. Metric points are filtered by `telemetry.METRIC_ATTRIBUTES`.
- The OTLP path installs a meter provider with a periodic OTLP metric reader; the Azure Monitor distro installs its own.
- Cosmos DB request units are not a metric yet (the SDK reports them per response header; recording them would mean
  wrapping every store call): a follow-up if needed.
- `ARCVIZ_TELEMETRY_AUTH=entra` (Azure Monitor only) needs the role *Monitoring Metrics Publisher*; the connection
  string stays required as the resource and endpoint name.
