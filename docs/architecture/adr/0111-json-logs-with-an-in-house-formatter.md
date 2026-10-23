# ADR-0111: JSON logs are opt-in and written by an in-house formatter

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/operations.md` (#83)

## Context

Logs went to stderr unstructured; the app's INFO records were not even shown (no root handler). Container Apps
sends stdout/stderr to Log Analytics, where JSON lines can be queried field by field. Rule 5 forbids a new mandatory
runtime dependency without a reason.

## Options

1. **`structlog` or `python-json-logger`.** Mature, but a new runtime dependency for about 80 lines of code.
2. **An in-house `logging.Formatter` and `Filter`** over the standard library, opt-in through `ARCVIZ_LOG_FORMAT=json`;
   uvicorn's records go through the same handler, its access log is replaced by the app's request records.
3. **JSON by default.** Better for the deployment, worse for a developer reading a terminal.

## Decision

Option 2: no dependency, the format is ours to keep stable, and `text` stays the default for local use.

## Consequences

Operators set `ARCVIZ_LOG_FORMAT=json` in deployments. Started with plain `uvicorn`, uvicorn's own access log stays in
its format unless `--no-access-log` is passed (documented). The format is part of the spec (field names stable).
