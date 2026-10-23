# Hardening (M23)

Status: implemented in M23 (#119, #112, #143, #155, #114, #167). ADRs:
[ADR-0125](../adr/0125-no-derived-markup-in-the-frontend.md),
[ADR-0126](../adr/0126-the-server-closes-a-turn-in-a-worker-thread.md),
[ADR-0127](../adr/0127-transcript-writes-bound-to-a-generation.md),
[ADR-0128](../adr/0128-kind-changes-and-links-verify-after-writing.md),
[ADR-0129](../adr/0129-version-metadata-read-once-and-paged.md),
[ADR-0130](../adr/0130-opt-in-version-retention.md),
[ADR-0131](../adr/0131-server-generated-ids.md).

## Summary

v0.3.0 runs as one service, scaled out to a few replicas, on Azure with one Cosmos DB container shared by many
tenant-isolated users behind an authenticating proxy. M23 closes the hardening issues that matter for that
deployment: error text that became markup in the browser (#119), a chat turn whose finalization waited for the
garbage collector after a disconnect and store calls on the event loop (#112), narrow race windows around
transcripts and re-created projects (#143) and between an element kind change and a concurrent link (#155), a version
history that every response read in full and that grew forever (#114), and ids that showed across tenants that a
name is taken (#167, see its own section).

The M20–M22 rules stay: authorization only in the services, 404 for outsiders, a failed store read never counts as
"absent" (fail closed), no cross-partition query on a per-request path, every write conditional on what it read.

## Goals

- No server- or user-derived string ever reaches the DOM as markup; a test fails on a new unsafe sink (#119).
- A turn is finalized (version, transcript) when its client disconnects, at once and on a worker thread; no blocking
  store call runs on the event loop in the chat routes (#112).
- A transcript append or clear lands only in the transcript incarnation the caller started from; a diagram created
  by a project turn lands only in the project incarnation the turn started in; the outcome note of a confirmed project
  proposal is not written into a project the diagram has left (#143).
- An element kind change and a concurrent link cannot both succeed when the link cannot represent the new kind (#155).
- One metadata listing per diagram response, no snapshot bodies read for it in any backend; responses carry a bounded
  page of versions; an opt-in retention policy bounds the history (#114).

## Non-goals

- A Content-Security-Policy or Trusted Types rollout (a follow-up; the sink rule covers the code we own).
- Cross-document transactions: Cosmos DB has none across partitions; the designs below use conditional writes plus
  verification.
- Pruning chat transcripts (compaction already bounds what the model sees).

## Design

### Error messages as text (#119, ADR-0125)

- `dom.placeholder(text)` builds `<div class="placeholder">` with `textContent`. The start failure (`app.js`) and the
  render failure (`canvas.js`) use it. The review panel's empty-state paragraph is built with DOM APIs too.
- Remaining `innerHTML` assignments are `""` (clearing), constant literals without interpolation (the whole right-hand
  side one literal: `"" + text` is not constant), or one of two
  reviewed sinks that insert Mermaid's SVG output (`canvas.js` on screen, `export.js` detached for the export). They
  carry a `// trusted-html: <reason>` marker.
- `tests/js/sinks.test.mjs` scans every module under `arcviz/static/`: it forbids `insertAdjacentHTML`, `outerHTML =`,
  `document.write`, `innerHTML +=` and `DOMParser` with `text/html`, and any non-constant `innerHTML` assignment
  without the marker; the number of markers per file is pinned (a new trusted sink is a reviewed change to the test).
- Server-rendered SVG (the own renderers; since M24 Mermaid's output too, [follow-ups](follow-ups.md), ADR-0132) goes
  through the allow-list sanitizer `svgsafe.js` (`sanitizeSvg`) before it
  is inserted and before it is exported. Elements: only those the renderers produce (svg, g, path, rect, circle,
  ellipse, line, polyline, polygon, text, tspan, title, desc, defs, marker, clipPath, gradients, stop, use, style) and
  only in the SVG namespace; anything else, XHTML included, goes with its subtree (Mermaid's output never passes
  here, so no `foreignObject` is needed). Attributes: an allow-list plus `aria-*`/`data-*`; `href` only on `use` and
  only a `#fragment`. CSS in `<style>`, `style` and presentation values must be strict instead of decoded: no
  backslash (escapes), no comment, no at-rule but `@media`, no `expression(`, no `javascript:`/`data:`/`vbscript:`,
  and every `url()` exactly `url(#id)`; checks run after removing whitespace and control characters. `tests/js/svgsafe.test.mjs` runs it on hostile payloads (review of #194).
- e2e step 15b: a start failure whose server detail is `<b>…</b><img onerror=…>` and a Mermaid parse error that
  quotes such a line both show the text literally and create no element.

### A turn is closed by the server (#112, ADR-0126)

Before: `chat`/`project_chat` iterated the agent's synchronous generator with `iterate_in_threadpool` and never closed
it. When the client went away, Starlette stopped iterating; the generator's `finally` (version, transcript) ran only
when the garbage collector found it, on whatever thread that was. The route also read the diagram on the event loop.

After:

- `server.TurnResponse(StreamingResponse)` owns the turn's generator. However the response ends (completed, the
  client disconnected and the stream task was cancelled, a send failed with `OSError` under ASGI 2.4), its `finally`
  closes the generator **on a worker thread**, shielded from cancellation, and waits for it: the agent's `finally`
  writes the version and the transcript before the request is over. Closing is idempotent; an exhausted generator is
  a no-op. anyio does not abandon a running `next()` on cancellation, so the generator is never closed while it runs.
- The events generator wraps the agent's generator in `contextlib.closing`, so closing it closes the agent's at once
  (not by reference counting).
- Everything the chat routes did on the event loop before streaming (the existence check, the role check, building
  the agent) runs in one `run_in_threadpool` call; `HTTPException`s raised there propagate unchanged.
- Test: `tests/test_turn_disconnect.py` runs the app under a real uvicorn server, posts a chat with a raw socket,
  closes it after the first `tool_result`, and checks — with the garbage collector disabled — that the auto version
  and the transcript entry (`error`: interrupted) are written within a few seconds, and that the finalization ran on
  a worker thread, not the event loop's. A second test does the same for the project chat.

### Transcripts and placements bound to their incarnation (#143, ADR-0127)

The store primitives get an optional token, checked atomically with the write:

- `append_chat(diagram_id, entries, generation=None)` and `append_project_chat(project_id, entries, generation=None)`:
  with a generation, the append happens only while the transcript has that generation; otherwise `ConflictError`
  and nothing is written. File: compared under the owner's lock; SQLite: in the same `BEGIN IMMEDIATE`; Cosmos: the
  batch carries a `replace` of the `chat-generation` item conditional on its ETag (same partition), like the
  compaction guard.
- `clear_chat(diagram_id, generation=None)` and `clear_project_chat(project_id, generation=None)`: with a generation,
  the clear happens only while the transcript has it; it replaces the generation first (conditional). On Cosmos every
  chat item carries the generation it was written in, and *every* append and compaction is guarded in its batch by
  the generation it read (one without a `generation` binds itself to the current one), so no item is ever written
  with a generation a clear already replaced. The clear's sweep deletes every item of another generation (untagged
  legacy items too) in batches guarded by the new generation: an append there only makes it read the item again;
  a newer generation (another clear, a re-created owner) stops it. `load_chat` hides items of other generations,
  and untagged ones once a clear made the generation `strict`, so an unfinished sweep is never visible (reviews of
  #194).
- Services: `DiagramService.append_chat(…, generation=None)`, `ProjectService.append_chat(…, generation=None)` return
  False on a mismatch (like a deleted owner); `clear_chat` reads the generation between two editor checks (editor
  checks always read the project again, ADR-0121) and passes it, so the clear acts on the incarnation the second
  check approved; a change in between retries.
- Agents: the diagram turn reads the generation at its start (before the role check) and appends with it, like the
  project turn already did — a chat cleared, imported or deleted during a turn no longer receives its entry. The
  project turn's check-then-append becomes the conditional append.
- Placement: `ProjectService.check_target(project, section, incarnation=None)` and `place(…, incarnation=None)`
  compare the project's incarnation (`projects.incarnation_of`: a random `Project.incarnation` that `new_project`
  sets, omitted from dumps while unset; `created_at` for projects written before M23, whose one-second resolution
  alone could not tell two quick re-creations apart) inside the conditional placement write; a mismatch is
  `ProjectNotFound`, and `_place` rolls the new diagram back as for any failed placement. The project turn passes
  the incarnation it loaded at its start to `import_new`.
- Confirmed project proposal: the outcome note is appended only while the diagram is still a member of the project
  (re-checked after the replay with `ProjectService.holds`) and with the generation read before the membership check. Residual: a move between
  that re-check and the append still writes the note (one line naming the version); the window is one store round
  trip.

### Kind changes and links verify after writing (#155, ADR-0128)

Both sides write first, then read the other side, and compensate conditionally:

- `ModelService.update_element` with a new kind: after the project write, re-read the member diagrams; if a view now
  links the element and cannot represent the new kind, put the old kind back (the internal project operation
  `revert_element(element, name, description, kind, parent, expected)`, which restores the element as it was
  before the update, also a name, description or parent the same update changed, only while its kind is still
  `expected`) and raise
  the same `ProjectError` as the up-front check.
- Compensations are retried (`COMPENSATION_ATTEMPTS` 3) and never undo somebody else's change: `revert_element`
  puts back only the fields this update changed, each only while it still has the value the update wrote; if that
  fails with a `ProjectError` (the old name taken, the old parent gone), the kind alone. A take-back or
  revert that still cannot be written is said in the error (the link or kind stays, the project review reports it)
  and logged; verification reads that fail count as a conflict (fail closed) on both paths (review of #194).
- `ModelService.link` (REST) and the agents' `link_element`: after the diagram write, re-read the element
  (`ModelService.link_problem`, never the request's cached project); if its kind no longer suits the view, or it is
  gone, or it cannot be read (fail closed), unlink it again, only while the view still stands for that element (REST:
  `DiagramService.update`, a version `Verknüpfung zurückgenommen`; agents: `ModelTools.verify_link` through the
  turn's write-through) and report the error (400 / tool error).

In every interleaving at least one side sees the other's write (each reads after its own write), so the pair can
never both succeed; both may fail, which is safe. With Cosmos DB's session consistency a read on another replica
can lag the other's write by a moment; the project review's `incompatible-element-link` finding remains the
backstop.

### Version history read once, paged and optionally pruned (#114, ADR-0129, ADR-0130)

**Reads.** `list_versions` reads metadata only in every backend:

- File: each version gets a small metadata sidecar `versions/NNNN.meta.json`, written (atomic rename) after the
  snapshot is complete; `list_versions` reads the sidecars, and creates the missing sidecar of a version written
  before M23 from its snapshot once (a lazy migration; an empty, still reserved snapshot is skipped as before).
  Numbering continues after the highest stored number (it was "count + 1", which a prune would have broken).
- SQLite: already a `meta` column.
- Cosmos: a single-partition projection `SELECT c.version, c.meta FROM c WHERE c.type = @type ORDER BY c.version`.

The new pure module `arcviz/history.py` holds `undo_target_of(versions)`, `redo_target_of(versions)` and
`history_of(versions)`; `DiagramRepository.undo_target`/`redo_target` call them, and the derived operation
`history(diagram_id)` lists once and returns `ports.History(versions, undo_target, redo_target)`.
`DiagramService.state(diagram_id, page)` uses it: one metadata listing per diagram response, no snapshot load
(`tests/store_contract.py` counts the calls).

**Pages.** Diagram responses carry the newest `ARCVIZ_VERSIONS_PAGE` versions (default 50) in `versions`, plus
`versions_total` and `versions_more`. `GET /api/diagrams/{id}/versions` keeps its list body and gains `before` (only
older versions) and `limit` (1..500); with either it answers the newest `limit` (default the page size) matching
versions, ascending (`ports.page_of`); without parameters every version as before. The UI's version list shows the
page and an entry *Ältere Versionen laden … (n)* that fetches the next page and prepends it.

**Retention (opt-in).** `HistoryConfig` (`arcviz/history.py`, read by `create_store` and checked at start-up):
`ARCVIZ_VERSIONS_KEEP` (default `0`: keep everything), `ARCVIZ_VERSIONS_PRUNE_SLACK` (default 20),
`ARCVIZ_VERSIONS_PRUNE_MANUAL` (default off). With `N > 0` (at least 10), after a write that added a version
(`commit`, `snapshot`) and left more than `N + slack` versions, `history.prunable` selects the versions beyond the
newest `N`, except:

- the first and the latest version,
- the version that last cleared the redo stack and every undo/redo after it (the replay that decides the redo
  target), the redo stack's entries, the undo target and its cursor, and the `restored_from` of every kept version,
- manual snapshots, unless `ARCVIZ_VERSIONS_PRUNE_MANUAL=1` (an explicit setting).

A prune is due (`history.prune_due`) from the new version's number alone — once the history can have outgrown
`keep + slack`, then every `slack` versions — so commits do not list the history in between; it keeps the newest
`keep + slack` versions (a grace: a version goes only once it was already beyond the window at an earlier prune). A
target that is pruned anyway while somebody uses it (preview, restore, undo, redo, comparison) answers 409 "reload
the version list" (`load_kept_version`, `ports.VersionGone`), not 404 or 500. The backend migration copies versions
under their own numbers (`put_version`), so a pruned history keeps its gaps and `restored_from`; a target diagram
with versions but no latest (an interrupted run) is an error without `--overwrite` and replaced with it, never mixed
(reviews of #194).

The new primitive `delete_versions(diagram_id, numbers)` removes them (file: snapshot, then sidecar; SQLite: one
`DELETE`; Cosmos: batches in the diagram's partition, a missing item is skipped). Pruning is best effort (logged,
never fails the write). A Hypothesis test checks that pruning never changes the undo or redo target. The Git mirror
is not affected.

### Ids that do not reveal other tenants (#167, ADR-0131)

Options (owner's decision: A):

- **A. Server-generated ids with a random suffix, existing ids kept** — chosen. No migration at all.
- B. Tenant-scoped namespaces — every storage key, URL and the locator would change; a multi-step copy of every Cosmos
  item into new partitions.
- C. Pure opaque ids — as A for migration, less readable, no security gain.
- D. Keep ADR-0089 and accept the residual.

Design:

- `ports.new_ids(base)` yields `<base>-<10 random base32 characters>` (`secrets`, 50 bits), a fresh suffix per
  attempt; `base` is cut to 53 characters (no trailing `-`/`_`) so the id fits `ID_RE`; an empty base gives the
  suffix alone.
- `DiagramService._create_free` and `ProjectService._create_free` take their candidates from it (looked up at call
  time) instead of `<base>`, `<base>-2`, …; a taken or reserved candidate, a lost race or leftovers whose `vacate`
  fails (M22) take a fresh suffix, at most `CREATE_ATTEMPTS` (8) times, then `StoreError` (since M24 one helper,
  `ports.id_attempts(base, what)`, [follow-ups](follow-ups.md)). The base is the requested
  `id` (still checked: 400), else the title's slug or the imported document's id.
- Every create path goes through them: `POST /api/diagrams` (also templates and the UI's demo), `POST
  /api/diagrams/import`, `POST /api/projects`, `POST /api/projects/import` (project and each diagram), the project
  chat's `create_diagram`, and the squatted personal default project's fallback (`home_<hash>-<suffix>`).
- Unchanged: the reserved deterministic ids (`default`, `home_<hash>`, `user_<hash>`), existing ids and every
  storage key, URL, export and the locator; the M20 start-up migration's renames (system context, legacy data only).
- Tests: `tests/test_ids.py` (marked `real_ids`) creates through every path as one tenant while another holds the
  requested slug and while nobody does, and checks status and id shape are the same; plus the generator, the
  squatted-default fallback and the failed-vacate path. The rest of the suite pins readable ids in
  `tests/conftest.py` (`readable_ids`: `<base>`, `<base>-2`, …) so its tests can name what they create; the e2e
  server does the same.

**Breaking:** a requested `id` is only a prefix; clients must read the id from the response.

## Testing

- JS: `tests/js/sinks.test.mjs`; e2e step 15b (#119).
- `tests/test_turn_disconnect.py`: real uvicorn, raw socket disconnect, GC disabled (#112).
- `tests/store_contract.py`: conditional appends and clears for every backend (file, SQLite, Cosmos fake); service and
  agent tests for a cleared/re-created transcript and a re-created project during a turn (#143).
- `tests/test_project_model.py`: an interleaved link during a kind change, both orders (#155).
- `tests/store_contract.py`: `state()` lists once and loads no snapshot; pagination; retention keeps what it must (#114).

## Migration and compatibility

- File stores written before M23 get their version sidecars lazily on the first listing; nothing else changes on disk.
- `versions` in diagram responses is now a page (newest 50 by default); API clients that need all versions page
  through `GET /api/diagrams/{id}/versions?before=…`.
- Retention is off unless configured (`ARCVIZ_VERSIONS_KEEP`, see the configuration reference).
- New diagram and project ids carry a random suffix; existing ids are kept (no migration). Breaking for API clients
  that relied on getting the requested `id`.

## Decisions

- Error text is text; trusted markup sinks are listed in a test (ADR-0125).
- The server closes a turn's generator on a worker thread when the response ends (ADR-0126).
- Transcript writes and clears are bound to a generation, placements to the project's `created_at` (ADR-0127).
- Kind changes and links verify after writing and compensate conditionally (ADR-0128).
- Version metadata is read once per response and paged (ADR-0129); retention is opt-in and never drops the
  versions undo/redo need, nor manual snapshots without an explicit setting (ADR-0130).
- New ids are `<slug>-<random suffix>`, no migration, explicit `id` as the prefix, breaking for API clients
  (ADR-0131, the owner's decision; supersedes ADR-0089).
