# Store, mirror and runtime robustness (M26)

Status: implemented in M26 (#117, #113, #213, #212, #200, #201, #115). ADRs:
[ADR-0150](../adr/0150-generated-id-suffixes-are-replaced.md),
[ADR-0151](../adr/0151-git-pushes-on-a-background-worker.md),
[ADR-0152](../adr/0152-the-summary-cuts-edges-then-leaves.md),
[ADR-0153](../adr/0153-element-texts-bounded-on-write-not-by-integrity.md),
[ADR-0154](../adr/0154-cosmos-request-units-as-a-metric.md),
[ADR-0155](../adr/0155-damaged-version-files-are-reported.md).

## Summary

M26 closes risks of the arc42 assessment (R5, R8, R13, R22) and follow-ups of M23–M25. Each one either corrupted
something silently, stalled the app, or made a cost unbounded: a file store that ordered versions as strings and
answered a damaged version file with a 500 (#117), a Git mirror whose pushes blocked every version-creating request
(#113), a live-stream test that raced a write against the stream's lifetime (#213), element texts without a bound in
the project item (#212), ids that grew by one suffix per export round trip (#200), Cosmos DB request units that
nobody could see (#201), and a context budget that flat diagrams ignored (#115).

## Goals

- File store: every replaced file is synced with its directory; versions are ordered by number; an empty or damaged
  version file is a `StoreError`, never a decoding error; `purge` raises only `StoreError` (#117).
- Git mirror: commits stay ordered on the request path; pushes never delay a request; a push is bounded; shutdown waits
  a bounded time for a pending push (#113).
- Live streams: tests wait for conditions, not time windows; the stream cannot lose a change for its client (#213).
- Elements: names and descriptions of new, changed and imported elements are bounded so the project item stays
  below Cosmos DB's 2 MB; stored oversize projects stay readable and writable (#212).
- Ids: a re-imported or requested id with a generated suffix gets a new suffix instead of a second one (#200).
- Telemetry: the request charge of every Cosmos DB request as a histogram with allow-listed attributes (#201).
- Agent: `len(summary(budget)) <= budget + SUMMARY_SLACK` for every diagram shape (#115).

## Non-goals

- Renaming stored version files, or any other migration of stored data.
- A transaction across the Git mirror and the store (the mirror stays best effort).
- Pricing, alerting or RU budgets (operators build those on the metric).
- A default token cap per turn (`ARCVIZ_TURN_TOKEN_BUDGET` stays 0 by default).

## Design

### File store durability and ordering (#117, ADR-0155)

- `_atomic_write` already synced the file before `os.replace` and the directory after it (M22, #168). The two
  plain-text files that bypassed it, the transcript generation and the purge tombstone, now go through
  `_atomic_write_text` with the same syncs.
- `_version_files` sorts by `int(stem)` (`_version_number`); `10000.json` follows `9999.json`. File names are unchanged
  (`f"{n:04d}.json"` already writes five digits beyond 9999), so no migration.
- `load_version`: an empty or whitespace-only file is "incomplete", undecodable, cut-off or invalid content is
  "damaged"; both are `StoreError` (HTTP 404 through the existing handlers), with the cause chained. `list_versions`
  skips an empty file (a claim, as before) and a damaged snapshot without a sidecar (logged, without path or id), so the
  rest of the history stays usable; a damaged sidecar (cut off, invalid, not UTF-8; it is only a cache) is rebuilt from
  its snapshot, and the version is skipped only when the snapshot is damaged too. The next version takes the next free
  number; a damaged file is never overwritten.
- `purge`: a `.tmp-*` file that a write outside the diagram lock (a listing that writes a sidecar) renames away is
  tolerated (`unlink(missing_ok=True)`), so is a subdirectory already gone, one it creates meanwhile is removed in a
  further pass (at most three);
  any other `OSError` becomes `StoreError("… could not be deleted completely")`, with the tombstone left, so the next
  create of the id moves the debris aside as before.

### Git pushes off the request path (#113, ADR-0151)

`GitMirror.record` and `remove` still commit synchronously under the mirror's lock, so commits keep the order of the
versions. `_commit` then only calls `_request_push()`: it sets a pending flag and wakes a daemon worker thread
(`arcviz-git-push`, started lazily, again after `close`). The worker clears the flag and runs one
`git push -q -u <remote> HEAD` with `timeout=PUSH_TIMEOUT_SECONDS` (60 s); requests made meanwhile set the flag again,
so any number of commits during a push cost one more push. The worker does not take the commit lock (git's ref
locking keeps a concurrent commit and push apart). Errors are kept apart under a lock: `commit_error` (cleared by a
successful commit) and `push_error` (cleared only by a successful push), so a push never clears a commit's failure that
happened while it ran; `last_error` joins them. The worker never dies on an exception. `drain(timeout)` waits until nothing is pending or
running; `close(timeout=DRAIN_SECONDS)` (10 s) drains, stops the worker and logs a warning when a push was still
pending. A push may take longer than the drain (60 s against 10 s), so `GitMirror.start()`, called by the server's
lifespan (`ServiceRoot.start_mirror()`, never by the constructor: the CLI builds stores too), asks for one push when push
is on, the remote is configured and the repository has a commit: commits a previous process left unpushed are sent
without waiting for a new commit (on ephemeral storage the mirror directory needs a volume for that). `status()` adds
`push_pending`, `commit_error` and `push_error`.

Shutdown: the app's lifespan calls `ServiceRoot.drain_mirror()` → `DiagramService.drain_mirror` →
`DiagramRepository.drain_mirror` (port `DiagramRepositoryPort.drain_mirror`), after the hub and the heartbeat, in the
threadpool. Other git commands keep `GIT_TIMEOUT_SECONDS` (120 s).

### Deterministic live-stream tests (#213)

Root cause: `test_a_stream_reports_a_concurrent_change_and_ends` ran a stream with `ARCVIZ_LIVE_MAX_SECONDS=1.0` and
expected the `changed` event of a write made after the stream started. A write slower than the rest of that second
(a loaded worker; reproduced deterministically by delaying the write by 1 s) lands after `bye`, and the test fails. The
attribution check had a second window (poll before bus event, then the deadline before the late author).

The stream itself does not lose a change for its client: a change it did not report before `bye` (written after its
last re-check, or a re-check cut short by the end of its lifetime) is in the `revision` of the next stream's `hello`,
and `live.js` compares that with the revision it shows and refreshes. No code change in `live.py`.

Tests: `recorded_streams` wraps `live.run_stream` and records every event the server writes; the change test runs a
stream with a 600 s lifetime, waits until an attributed `changed` with the new revision and a keep-alive were recorded,
then closes the hub (`bye shutdown`). The lifetime is tested alone (`bye expired`, nothing races it), and a new test pins
the guarantee above (a change after a stream ended is in the next `hello`).

### Bounded element texts (#212, ADR-0153)

`project_model.MAX_ELEMENT_NAME = 100`, `MAX_ELEMENT_DESCRIPTION = 300`, counted on what is stored (operations store the stripped value, an import the value as
it is, padding included). `projects.add_element` and
`update_element` check the values they write before changing anything (`ProjectError`, words the model can act on);
`update_element` checks only the fields it sets. `exchange.parse_project` never refuses a bundle for them: names and
descriptions are stripped (as the operations do) and those over a bound are cut to it, ending in `…`
(`project_model.shorten_element_texts`); a cut name that equals another name (case-insensitive) becomes `… (2)`,
`… (3)`, …; names that were equal already are refused (`duplicate_element_names`, as `add_element` does). Sections
standing for a cut element take its name, and so do its linked nodes and lifelines in the bundle's diagrams, through
the `sync_element` operation (so no view starts out of sync); `ProjectBundle.warnings` says how many were cut; `POST /api/projects/import` returns them as
`warnings` (`ProjectExchangeService.import_with_warnings`). A project stored with longer texts therefore survives its own
export and re-import (a backup). Nothing goes into `Project._model_errors`: a stored project with
longer texts loads, is written by every other operation, and its element takes any change that does not set an
oversize value. The agent's element tools say the bounds in their parameter descriptions.

Size budget (spec [model relations](model-relations.md#size-budget-adr-0135)): every element and relation at its bounds in
three-byte characters plus 1 000 memberships is 1.67 MB of UTF-8 (`tests/test_m25_review.py`).

**Size guard.** The per-field bounds cover realistic text; the guard covers the rest, whichever encoding Cosmos DB counts
the 2 MB on. `projects.document_size(project)` measures the document as the Cosmos SDK sends it (compact JSON,
`ensure_ascii=True`: six bytes for `€`); `projects.check_document_size(project, before)` raises `ProjectError` (HTTP 400,
"The project document would be too large …: shorten element or relation descriptions, or remove elements or
relations") when the result exceeds `MAX_PROJECT_BYTES` (1 900 000, room for the item's own fields) **and** is larger
than `before`. `ProjectService._update` calls it before every conditional write, `create_with_sections` (bundle imports)
with `before=None` (a refused import is a 400 `Invalid bundle: …`); every backend, so the behaviour is the same
everywhere. A project is never locked: a write that shrinks an oversize document, or keeps its size, passes. Membership
writes (`place`: creating, moving or adopting a diagram; the repair's adoption) only add a membership (~150 bytes) and
may go up to `MAX_MEMBERSHIP_BYTES` (2 000 000, a bounded overshoot), so a create never leaves a diagram outside its
project and the repair does not stop at a full project; above that, `reconcile` treats the refusal like a store failure:
the orphan is skipped (logged, reported, its placement record kept) and the repair goes on. When all orphans of a target
together do not fit, they are adopted one by one, so those that fit are placed and only the rest waits. The guard serialises the
project once per write (about 6 ms at the worst case, 0.05 ms for a typical project); a cheaper upper bound from
counts and field bounds would be unsound, because section titles, the project description and texts stored before M26
have no bound. Every element and relation at its bounds in ASCII is below
75 % of the budget, typical German text (an umlaut or `ß` in about one character in twenty) below 90 %; the
three-byte worst case (about 2.9 MB escaped) is refused cleanly (`tests/test_element_bounds.py`).

### Generated id suffixes are replaced (#200, ADR-0150)

`ports.id_suffix()` draws until the 10 base32 characters contain a digit (2–7): about 49.8 instead of 50 bits.
`ports.id_base(requested)` removes one trailing `-<10 base32 characters with a digit>` (or the whole id when it is only
such a suffix); `new_ids(base)` starts from `id_base(base)`. Every create path goes through `new_ids` (requested ids,
title slugs, diagram imports, the diagrams of project bundles), so a re-imported export gets a new suffix instead of a
second one. Ten letters without a digit (`processing`, `deployment`, `monitoring`, `repository`) are never taken for a
suffix, and re-using a new id as the base again and again keeps its length. `DiagramService.import_new` strips the
suffix before it cuts a document id to 60 characters, so an id of 61 to 64 characters keeps its slug. A digit-free
suffix issued before M26 (about one id in eight) stays once and is followed by one new suffix; ids then stay stable.
Tenant isolation is unchanged: the new id still has a fresh random suffix, whatever the requested base.

### Cosmos DB request units (#201, ADR-0154)

`CosmosStore` sends every request through `_MeteredContainer` (`self._requests`; `self.container` stays the raw
container for tests and setup). While `telemetry.enabled()` each of `read_item`, `create_item`, `upsert_item`,
`replace_item`, `delete_item`, `query_items` and `execute_item_batch` gets a `response_hook` that records the
response's `x-ms-request-charge` with `telemetry.record_request_units(operation, document, charge)`; a failed request's
charge is read from the error's `headers`. A query's pages are fetched while the caller iterates, so its results are
wrapped (`_metered_pages`) and a page that fails then is charged too. The histogram `arcviz.store.request_units` (unit `{request_unit}`) has two
attributes, added to `telemetry.METRIC_ATTRIBUTES`: `db.operation.name` (`STORE_OPERATIONS`: read, create, upsert,
replace, delete, query, batch, patch) and `arcviz.store.document` (`store_document(partition_key)`: `diagram` for a
bare key, the prefix of `<kind>:<key>` when it is `project`, `presence` or a keyed document kind, `any` for a
cross-partition query, else `other`). Never an id, a partition key or a query text. Without telemetry the container's
methods are called unchanged (no hook, no OpenTelemetry import). No new dependency.

### The summary respects the budget for every shape (#115, ADR-0152)

`Diagram.summary(budget)` keeps its steps (notes shortened, then hints only, then containers collapsed largest first)
and adds a last one when the text is still too long: `_SummaryParts.fit(budget)` cuts edge lines from the end, then
leaf lines from the end, then container lines from the end (a sequence diagram: its lines from the end), adds one count
line per cut with a hint to call `get_diagram`, and cuts a long title to `SUMMARY_TITLE_CHARS` (200). Therefore
`len(summary(budget)) <= budget + SUMMARY_SLACK` (1 000) for every diagram and budget; within the budget nothing changes.

## Testing

- `tests/test_store_file.py`: versions beyond 9999 (listing, numbering, `version_count`), empty and cut-off version
  files (load raises `StoreError`, listing skips, the next number is free), a purge racing a temporary file (renamed
  away, created meanwhile), a failing purge (`StoreError` with the cause), the syncs of the generation and the
  tombstone; the three older purge tests expect `StoreError` instead of `OSError`.
- `tests/test_gitmirror.py`: a push blocked on an event delays neither commits nor three concurrent `/ops` requests;
  the three push requests coalesce into one more push on the worker with the push timeout; `close` returns within its
  bound while a push hangs, and a later commit pushes again; the lifespan drains the mirror.
- `tests/test_server_live.py`: the condition-based change test, the lifetime alone, the change after a stream ended.
- `tests/test_element_bounds.py` (bounds, stored oversize projects, the size guard: ASCII and German at the bounds fit
  with margin, the three-byte worst case is refused on create and import, an oversize stored project takes shrinking
  and same-size writes), `tests/test_m25_review.py` (budget at the bounds).
- `tests/test_ids.py` (`real_ids`): suffixes hold a digit, `id_base` cases, `new_ids` of an id, round trips of a diagram
  and a project bundle keep one suffix.
- `tests/test_request_units.py`: charges by operation and document kind (a failed read too), no ids in the export,
  every keyed document kind is a known value, no hook and no meter without telemetry.
- `tests/test_model.py` (the flat 2 000-service diagram of the issue, a long sequence) and `tests/test_properties.py`
  (the budget property for diagrams from the operation steps and for large flat and nested ones).

## Migration / compatibility

No stored data changes. Version files keep their names; projects with longer element texts stay valid; ids issued
before M26 keep working (only a re-import replaces their suffix). `/api/config` `git` gains `push_pending`.

## Open questions

- Whether Cosmos DB counts the 2 MB item limit on the SDK's ASCII-escaped request body (six bytes per `€`) or on the
  stored UTF-8 (the worst case is 2.9 MB vs 1.67 MB). Handled by the size guard, which measures the escaped body;
  recorded in arc42 R22.

## Decisions

Taken by the coordinator before implementation: fsync file and directory, numeric ordering, `StoreError` for empty or
partial version files, `purge` raises only `StoreError`, no file name migration (#117); ordered commits on the request
path, pushes on a coalescing background worker with a bounded timeout, bounded drain at shutdown (#113); the summary
truncates edges and leaves with counts and a `get_diagram` hint, checked by a Hypothesis property (#115);
deterministic stream tests and a stream fix only if it can miss a change (#213); bounded element names and
descriptions for new, changed and imported elements, never in `_model_errors` (#212); strip a trailing
`-<ID_SUFFIX_LENGTH base32>` suffix before `new_ids` (#200); the histogram `arcviz.store.request_units` with
attributes from `METRIC_ATTRIBUTES` only, a no-op without `start_telemetry`, inside the existing extras (#201).

Taken with the coordinator while implementing: the bounds 100/300 (the M25 worst-case standard does not hold with a
2 000-character description) plus the size guard of the whole document measured as the SDK sends it, refusing only
writes that grow a document above the budget; suffixes always hold a digit and only such a suffix is stripped (a plain
base32 rule strips ten-letter words); damaged version files are reported and skipped by the listing, not repaired;
the push timeout 60 s and the shutdown drain 10 s as constants (no new variables).
