# ADR-0114: OpenTelemetry and Azure Monitor export through one optional `telemetry` extra

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/operations.md` (#83)

## Context

Operators want traces and logs in Application Insights (Azure) or any OpenTelemetry backend. The OpenTelemetry SDK,
the instrumentations and the Azure Monitor distro are several megabytes of packages that most installations do not
need.

## Options

1. **Only OTLP.** Vendor-neutral; on Azure it needs a collector (or the Container Apps managed OpenTelemetry
   agent) in between.
2. **Only the Azure Monitor distro.** Direct to Application Insights, but Azure-specific.
3. **One extra with both**, chosen by `ARCVIZ_TELEMETRY=otlp|azure-monitor`; the FastAPI instrumentation is applied
   explicitly to the app, probes excluded, path attributes redacted.

## Decision

Option 3: the distro already brings the OpenTelemetry SDK and the FastAPI instrumentation; the OTLP exporter adds
little.

## Consequences

`pip install "arcviz-agent[telemetry]"` (the image includes it). A missing extra or a missing connection string is a
start-up problem. Metrics and Entra authentication of the Azure Monitor exporter are follow-ups.
