# ADR-0154: Cosmos DB request units as a metric by operation and document kind

- Status: Accepted
- Date: 2026-10-11
- Spec: `docs/architecture/specs/robustness.md`
- Refines: [ADR-0134](0134-own-spans-and-metrics-by-allow-list.md)

## Context

ADR-0134 added the app's own metrics and left out Cosmos DB request units. Operators cannot see the RU cost of the
locator, the presence relay or the writer heartbeat and reaper (#201). The SDK reports each response's charge in the
header `x-ms-request-charge` and offers a per-request `response_hook`.

## Options

1. **A `response_hook` per request through a metering wrapper of the container.** Exact per request, also for failed
   requests (their headers are on the error), no SDK internals.
2. **`client_connection.last_response_headers` after each call.** Shared by all threads: charges would be attributed
   to the wrong request.
3. **Azure Monitor's Cosmos DB metrics.** Per account and container only, not per kind of document.

## Decision

Option 1: the histogram `arcviz.store.request_units` with only `db.operation.name` and `arcviz.store.document`
(fixed value sets, added to `telemetry.METRIC_ATTRIBUTES`), a no-op without `start_telemetry`.

## Consequences

RU costs are visible per operation and document kind (diagram, project, presence, workspace, user, location, writer,
…), never per id. A query records one point per page. No new dependency.
