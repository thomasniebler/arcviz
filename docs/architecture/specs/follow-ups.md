# Follow-ups from M9, M22 and M23 (M24)

Status: implemented in M24 (#195, #186, #196, #197, #189, #187). ADRs: see each section.

## Summary

M24 closes what M9 (operations), M22 (scale and robustness) and M23 (hardening) left open: duplicated logic that had
already drifted (#195), deletes from the UI that ignored the conditional deletes of M22 (#186), an end-to-end test
that only worked with readable ids (#196), Mermaid's SVG as the last server-derived markup outside the allow-list
sanitizer (#197), locator tokens of crashed writers that were never cleared (#189), and the telemetry follow-ups of
M9: keyless export, metrics and spans inside chat turns (#187).

The rules of M20–M23 stay: authorization only in the services, a failed store read is never "absent" (fail closed),
no cross-partition query on a per-request path, every write conditional on what it read, no content, no raw paths and
no user ids in telemetry.

## Goals

- One implementation of the link take-back and of the bounded id-attempt loop; the project agent handles a lost role
  like the diagram agent (#195).
- The UI deletes diagrams and projects conditionally on the revision the user saw and asks again after a 412 (#186).
- The Playwright smoke test passes with readable and with real (random-suffix) ids; CI runs both (#196).
- Every server-rendered SVG, Mermaid's included, goes through the allow-list sanitizer (#197).
- A pending locator token of a writer that provably died can be cleared without clocks and without weakening the
  M22 invariant; an operator command does it in a maintenance window (#189).
- Keyless (Entra ID) Azure Monitor export, privacy-safe metrics and spans inside chat turns, all optional (#187).

## Non-goals

- New features beyond the issues; a Content-Security-Policy rollout; Cosmos request-unit metrics (the SDK reports
  them per response header only, see #187 below).

## Shared link take-back and id attempts (#195)

Behaviour-preserving refactor, apart from the drift fix.

- `agent.take_back_invalid_link(diagrams, model_tools, diagram_id, diagram, revision, call) -> LinkTakeBack | None`:
  verifies a written `link_element` (`ModelTools.verify_link`), writes the take-back through the turn's
  `write_through` and maps the outcome once: `written`/`rejected` → the plain problem, nothing recorded; `busy`,
  `deleted`, `forbidden` → the "could not be taken back" problem, the call recorded as applied (`kept`), plus
  `deleted`/`lost` for the turn. Both agents call it and only apply the flags to their own turn state.
- The project agent now handles `forbidden` like the diagram agent, per diagram: a refused write or take-back stops
  the writes to that diagram (`_DiagramTurn.lost`), a refused `create_diagram` stops further creates, and
  `final.error` is the role-lost turn error (before, it tried every later call again and only noticed at the
  transcript). Other diagrams of the turn the caller may still edit stay editable (coordinator review of #199: a
  refusal can be about one diagram that left for a project the caller only views).
- `ports.CREATE_ATTEMPTS` (8) and `ports.id_attempts(base, what)`: at most that many candidates of `ports.new_ids(base)`
  (looked up at call time, so it stays the one seam that the tests and the e2e server pin), then `StoreError("No free
  id for a new <what> after 8 attempts; try again.")`. `DiagramService._create_free` and `ProjectService._create_free`
  iterate it. The issue suggested `ports.new_ids(base, attempts)`; a separate helper keeps the generator's signature,
  which `tests/conftest.py` and `tests/e2e/server.py` replace.

Testing: `tests/test_shared_helpers.py` runs every take-back outcome (`written`, `rejected`, `busy`, `deleted`,
`forbidden`) and a `forbidden` write through both agents with the same assertions, and pins the bounded loop for both
services.

## An id-agnostic smoke test and a real-ids run (#196)

- `tests/e2e/smoke.mjs` never derives an id from a title: `idOf(page, title)` / `waitForTitle` read the id of a
  diagram row in the navigator by its title, created projects are read from the project select, and diagrams created
  through `fetch` take the id from the response. `isNewId(slug, id)` accepts a readable id (`slug`, `slug-<n>`) or a
  random suffix (`slug-<10 base32>`), and with `ARCVIZ_E2E_REAL_IDS=1` requires the suffix, so the real-ids run also
  checks that every create path (UI, import, project import, project chat, REST) hands out a new random id. The
  seeded legacy diagram `demo` and the reserved projects (`default`, `home_<hash>`) keep their fixed ids.
- CI: a second step *Browser smoke test with real ids* in the one CI job, after the first run (it reuses the
  installed browser), gated on the new scope `e2e` (`arcviz/static/`, `tests/e2e/`, `arcviz/server.py`,
  `arcviz/ports.py`, `arcviz/services/`, `arcviz/store/`, `arcviz/projects.py` and the agents), which
  `scripts/ci_scope.py` computes like the others. About one billed
  minute on pull requests that touch those paths, nothing otherwise.
- Observed while doing it (not changed here): importing a document whose id already carries a random suffix appends a
  second one (`zahlungen-<s1>-<s2>`), because the import uses the document's id as the slug (ADR-0131). The id stays
  valid (the base is cut to fit), only longer. Fixed in M26 (#200, ADR-0150): a generated suffix is replaced.

Testing: both modes locally (`make e2e`, `ARCVIZ_E2E_REAL_IDS=1 make e2e`); `tests/test_ci_scope.py` pins the scope
and that the variant is a step of the single job.

## Conditional deletes from the UI (#186)

M22 made `DELETE /api/diagrams/{id}` and `DELETE /api/projects/{id}` conditional on `If-Match` (ADR-0123); the UI did
not send it, so a delete confirmed for one state removed whatever was there by then.

- Pure `conflicts.confirmedDelete({current, reload, ask, remove, question, changed})`: ask, delete with the revision
  the question was about, and on a 412 only: `reload()` (null stops), ask again with `changed(author)`. Every retry
  needs a new confirmation, so the loop ends with the user; any other failure propagates to the caller's `alert`.
- Diagram rows and **Löschen**: the shown diagram uses `state.revision` (kept current by the live stream); another
  row reads `GET /api/diagrams/{id}` before asking (the user confirms the state that is current then). The author
  named after a 412 is the live stream's last change for that revision, else the newest version's author, else
  *jemand anderem* (as the inspector does, ADR-0099); an unversioned change in flight can be attributed to the last
  versioned author, which is the inspector's existing approximation.
- Project: `nav.view.revision`; after a 412 the project view is shown again; a project that is not empty any more is
  not asked about again (the button is disabled by the view).
- After the coordinator review of #199: the author named after a 412 comes from `conflicts.changeAuthor` (the live
  stream's author of that revision, else the newest version's author unless that is the caller, else *jemand
  anderem*), and a project that is not empty any more after the reload is reported (`{refused}` → `alert`) instead of
  stopping silently; a failing reload rejects and is shown by `guarded`.
- No server change.

Testing: `tests/js/conflicts.test.mjs` (asked revision, decline, 412 → reload → ask again, reload that stops, other
errors not retried); e2e step 14b changes a diagram and a project while the confirmation is open and checks the 412,
the second question naming the change, and the delete.

## Mermaid's SVG through the allow-list (#197)

Decision: [ADR-0132](../adr/0132-mermaid-output-through-the-svg-allow-list.md).

- `canvas.parseMermaidSvg(text)`: `template.innerHTML = text` (HTML parser: Mermaid's labels are HTML, `<br>` inside
  `<p>` is not well-formed XML; a template's content is inert), exactly one `<svg>` root or an error,
  `sanitizeSvg(root, {htmlLabels: true})`, `document.importNode`. `renderView` inserts the result with
  `replaceChildren`; `renderOffscreen` (SVG/PNG export) returns it, so `export.js` has no markup sink left.
- `svgsafe.sanitizeSvg(root, {htmlLabels})`: with the option, an SVG `foreignObject` keeps its allowed attributes
  (`x`, `y`, `width`, `height`) and only `LABEL_ELEMENTS` (`div`, `span`, `p`, `br`) in the XHTML namespace, each with
  only `LABEL_ATTRIBUTES` (`class`, a safe `style`, `xmlns` equal to XHTML); any other element (including SVG inside
  the label, `img`, `a`, `style`, forms, Markdown's `b`/`strong`) goes with its subtree; text stays text. HTML outside
  a `foreignObject` still goes. Without the option (the own renderers) a `foreignObject` still goes.
- `isSafeCss` allows `@keyframes` (Mermaid's edge animations) besides `@media`, and only the functions of
  `CSS_FUNCTIONS` (url, var, calc, min/max/clamp, rgb/rgba/hsl/hsla, drop-shadow, transforms, cubic-bezier, steps and
  selector pseudo-classes): `image-set("…")`, `-webkit-image-set`, `cross-fade`, `element()`, `src()` and any other
  function go (the names are read from the text itself, as CSS tokenizes it). Every other rule is unchanged (no `url()`
  but `url(#id)`, no escapes, comments, schemes or `expression(`).
- `mask` (element) and the `mask`, `maskUnits`, `maskContentUnits` attributes are allowed, references only as
  `url(#id)` like `clip-path`: the vendored GCP icons use a mask (`gcp:cloud-functions`).
- `tests/js/sinks.test.mjs`: `TRUSTED` is empty; `INERT = {"canvas.js": 1}`, and an inert parse must assign
  `template.innerHTML` and call `sanitizeSvg(` before the function returns.

Measured (Mermaid 11.17.2, Chromium, every deployment template, the activity template, icon kinds and hostile names;
medians of 20 runs): parse + sanitize + insert 2.5–2.9 ms against 0.9–1.1 ms for `innerHTML`, i.e. about +1.5–2 ms per
render next to 60–160 ms of Mermaid layout (about 3 %). The allow-list removed only Mermaid's unused drop-shadow
`filter`/`feDropShadow` definition and no attribute; hostile names stayed text.

Testing: `tests/js/svgsafe.test.mjs` (label markup kept exactly, hostile label content removed, label markup only
inside a `foreignObject` and only with the option, label attributes, `@keyframes`); `tests/js/sinks.test.mjs` (no
trusted sink, the inert-parse rule and its self-test); e2e step 2 (labels contain only `div/span/p/br`, no `filter`
survives) plus the unchanged hostile-name checks of steps 2, 12, 13 and 14, the exports of step 6.

## Pending locator tokens of crashed writers (#189)

Decision: [ADR-0133](../adr/0133-pending-tokens-settled-on-proof.md). As implemented:
[Projects](../../spec/projects.md#pending-tokens-of-writers-that-died).

The M22 invariant stays: the entry lists every project that holds the id; a project with a pending token is never
dropped. What changes is who may clear a token.

- **Tokens name their writer**: `<boot>.<12 hex>`, `boot` = `locations.Writer.boot` (16 hex, new per process start;
  one `Writer` per store, `writer_of`). M22 tokens (16 hex) name none (`boot_of` → None).
- **Heartbeat** (`services/writers.py`, `Heartbeat`, started and closed by the server's lifespan): keyed document of
  kind `writer` per incarnation, renewed every `ARCVIZ_WRITER_HEARTBEAT_SECONDS` (30) by a write conditional on its
  own last revision; `stopped` on a clean shutdown once nothing is in flight (up to 5 s), else it stays `alive`.
- **Proof** (`Reaper`, one pass per heartbeat in every process; the operator command): `stopped`/`dead` → settle; an
  `alive` document whose revision this process saw unchanged for a lease (10 heartbeats) *on its own monotonic clock*
  → write `dead` conditional on that revision (the fence) → settle. No timestamps of two processes are compared.
- **Fencing**: a renewal that conflicts (the document was fenced) or finds it deleted marks the writer `fenced`: no
  more tokens (placements fail with a StoreError before writing), `/readyz` fails. A writer whose last successful
  renewal is older than half a lease on its own clock issues no tokens until it renews.
- **Settling** (`settle_tokens`): remove the incarnation's tokens from every entry (conditional writes), then drop each
  such project only if it does not hold the diagram (`remove_location`, read again). Failed listings, reads or writes
  settle nothing and keep the writer document for the next pass; the document is deleted a lease after its tokens
  were first all settled (so a placement paused before the fence still publishes a known token; Codex review), and a
  heartbeat whose create answer was lost adopts its document on the next registration attempt.
- **Writer side**: a placement's settle lists the target again when it is missing (`relist=True`, after the membership
  is written); after a failed placement the token is cleared once a re-read shows whether the membership was written
  (M22 cleared it only when the re-read showed none, leaving a redundant token when the write had happened); a token
  that cannot be cleared now goes to `Writer.abandoned` and the heartbeat retries it.
- **Operator**: `arcviz-migrate --settle-pending STORE [--wait S] [--all]` (`settle_pending`): watch the writer
  documents for `--wait` (default and minimum a lease), settle stopped, dead and still-standing writers; `--all`
  (maintenance window, at least two heartbeats) refuses if any heartbeat moved or a new writer appeared, otherwise
  also clears M22 tokens and tokens of unknown incarnations.

Coordinator review of #199 (one more round):

- **Outages**: `Reaper.observe(healthy)` judges others only while this process's own heartbeat is fresh
  (`Heartbeat.reap`: registered, not fenced, renewed within half a lease) and clears its timers after its own failed
  listing, an unhealthy pass or a gap of more than half a lease between passes: a store outage that stopped every
  heartbeat (the reaper's included) fences nobody; every writer gets a whole lease again.
- **Liveness**: a fenced process fails `/healthz` as well as `/readyz` (503 `{"status": "fenced"}`), so the
  orchestrator's liveness probe restarts it; every lifespan is a new incarnation (`Heartbeat.start` replaces a writer a
  previous `close()` fenced with `locations.new_incarnation`), so a second lifespan in one process places again.
- **The membership write**: `Writer.check()` (not fenced, renewed within half a lease) runs inside the conditional
  membership write of `place` and of the repair's adoption, right before the store write, not only when the token is
  taken.
- **Cost**: beats and reaper passes run on separate threads; a pass lists the locator entries only for writers that
  newly stopped or died, and once more a lease later (the final pass before the document is dropped; a late token found
  then gives another lease), all due writers in one listing.

Residual risk: a membership write that lands more than half a lease (2.5 minutes by default) after the writer's last
check inside it, while another process fenced the writer and dropped its target from the entry, leaves a membership
unlisted until the writer's settle lists it again.

Testing: `tests/test_pending_tokens.py` (both local backends): token format; self-fencing after half a lease; a crashed
writer settled only after a whole lease on the observer's clock, the project dropped when it does not hold the
diagram and `vacate` usable again; a live writer never settled over three leases; a holding project stays listed; the
fenced writer stops placing and its settle re-lists; a clean stop settled at once, not with a placement in flight;
abandoned tokens retried by the heartbeat; failed listings settle nothing; M22 tokens never settled automatically; the
placement paths; `settle_pending` (a lease, never before, `--all`, refusal while a heartbeat moves) and the CLI; the
server registers a writer, writes `stopped` on shutdown and reports a fenced writer in `/readyz`.

## Telemetry: keyless export, metrics, spans inside chat turns (#187)

Decision: [ADR-0134](../adr/0134-own-spans-and-metrics-by-allow-list.md). As implemented:
[LLM layer](../../spec/llm-layer.md#spans-and-metrics-187-adr-0134), [HTTP server](../../spec/http-server.md),
[configuration](../../reference/configuration.md#logs-and-telemetry).

- **Module** `arcviz/telemetry.py` (no package imports): `enable()`/`enabled()` (set by
  `observability.start_telemetry`), `span(name, attributes)` (current, for synchronous blocks), `start(name,
  attributes, parent)` + `Span.end()` (not current, for work across a generator's yields), `context()`,
  `record_request`, `record_llm`, `observe_live_streams(hub)`, `span_attributes`/`metric_attributes` (filters),
  `tool_name(name, offered)`. While telemetry is off every function returns before importing OpenTelemetry.
- **Spans**: `arcviz.llm.call` per model call (round, provider, model, outcome, token counts), `arcviz.tool` per tool
  execution (offered tool name else `unknown`, outcome), `arcviz.store.write` per `write_through`, `commit_turn` and
  `append_chat` of both agents (operation, outcome), nested under the request span. Attributes:
  `telemetry.SPAN_ATTRIBUTES`; the end-of-span scrubber now keeps `observability.ALLOWED_SPAN_ATTRIBUTES` (request
  attributes plus these) and, as before, events with `exception.type` only and status descriptions with the type only.
- **Metrics** (meter `arcviz`): `arcviz.http.server.request.duration` (s; `http.route` = the route template from
  `route_of`, `-` for unmatched paths, never the raw target; `http.request.method`, non-standard methods as `_OTHER`;
  `http.response.status_code`; probes not measured), `arcviz.llm.duration` (s) and `arcviz.llm.tokens` (by
  `gen_ai.token.type`), `arcviz.live.streams` (observable, the open streams of every live hub of the process).
  Attributes: `telemetry.METRIC_ATTRIBUTES`. The FastAPI instrumentation's own HTTP metrics stay off (M9 review).
  OTLP: a `MeterProvider` with a `PeriodicExportingMetricReader(OTLPMetricExporter())`; Azure Monitor: the distro's.
- **Entra ID**: `ARCVIZ_TELEMETRY_AUTH=connection-string|entra` (`LogConfig.telemetry_auth`); with `entra` and
  `ARCVIZ_TELEMETRY=azure-monitor`, `configure_azure_monitor(credential=DefaultAzureCredential())`. `entra` with another
  mode, or without azure-identity, is a start-up problem; the connection string stays required (resource and endpoint).
  azure-identity is already a dependency of the Azure Monitor exporter: no new dependency.
- Not done: Cosmos DB request units as a metric (see ADR-0134). Added in M26 (#201, ADR-0154): the histogram
  `arcviz.store.request_units`.

Testing: `tests/test_telemetry_m24.py` with in-memory span exporter and metric reader: the spans of a diagram turn
(names, attributes, parent chain under the request span, a made-up tool name exported as `unknown`), a project turn,
a failing model call (type only), nothing touched while telemetry is off; metric names, attributes (route templates,
`_OTHER`, no probes, unmatched as `-`), token sums, the live-stream gauge, the allow-list filters; Entra and connection
string modes of the Azure Monitor wiring (a fake distro and credential) and the start-up checks. Every exported span
and metric point is searched for the secret id, node name and message. `tests/conftest.py` keeps the app's own
telemetry off and gives every test an in-memory metric reader and log exporter, so no test exports over the network.
