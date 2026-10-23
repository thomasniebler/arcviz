# 8. Crosscutting concepts

## Domain model

A diagram is a dictionary of nodes plus a list of edges. Nodes have an id (a unique slug), a name, a
kind, an optional parent (nesting into container kinds) and string properties. Edges have a kind, an
optional direction, a label and properties. Kinds are closed sets: built-in sets plus an optional
kinds file, active process-wide ([Domain model](../../spec/domain-model.md),
[ADR-0010](../adr/0010-kinds-extended-from-a-json-file.md)). Node references from the LLM are resolved
loosely (id, name or slug), and operations store the resolved id. Integrity rules: unknown parents,
containment cycles and dangling edges are errors, and `save_latest` refuses such a diagram. M13 adds a
diagram *type* that selects kinds, rules, tools and renderer (#98).

## Single mutation path and atomicity

Every change goes through `operations.apply`: dispatch, argument validation against the operation's
signature, deep-copy snapshot, and rollback on *any* exception
([ADR-0045](../adr/0045-argument-validation-from-operation-signatures.md)). Error messages list valid
ids and kinds and name wrongly typed arguments so the LLM can correct itself; an unexpected exception is
reported as an internal error, never persisted half-applied. The property tests generate operation
sequences and check integrity and rendering after each step
([ADR-0021](../adr/0021-hypothesis-property-tests.md)).

## Versioning and history

Full snapshots (no diffs) with `source` (`initial | auto | manual | restore | undo | redo`), a content
fingerprint and a linear history. Undo, redo and restore append new versions. Version numbers are
allocated atomically per backend. The transcript is a separate, append-only list with per-diagram
sequence numbers, compacted into cumulative summaries
([Persistence](../../spec/persistence.md), [ADR-0003](../adr/0003-snapshots-not-diffs.md),
[ADR-0005](../adr/0005-chat-compaction-by-cumulative-summary.md)). Responses read the version metadata once and carry
the newest page; versions are pruned only with an opt-in retention policy (#114, ADR-0129, ADR-0130).

## Concurrency

- **Inside a process:** FastAPI runs sync endpoints and the agent generator in a threadpool. The
  backends make their *primitives* thread-safe (`O_EXCL` version files, SQLite `BEGIN IMMEDIATE`,
  Cosmos deterministic ids with a retry on 409).
- **Per request:** the read → mutate → write sequence is a compare-and-swap on the revision of the
  latest state, atomic with the version it records ([concurrent mutations](../specs/concurrent-mutations.md),
  [ADR-0044](../adr/0044-conditional-writes-on-a-latest-revision.md)). Operations, restore, undo/redo,
  snapshots and proposal confirms re-apply themselves on a conflict (up to 5 times, then 409); an agent
  turn rebases its next tool call and never writes its finalization over a newer state; nothing
  recreates a deleted diagram.
- **Across processes:** `file` and `sqlite` assume one process per data directory (the file store's
  locks are per process). `cosmos` is safe with replicas: the condition is the item's ETag. Clients get `ETag`/`If-Match` on
  the latest revision (412) and a live stream per tab (#86, [collaboration](../specs/collaboration.md)); the stream
  re-checks access on every event and poll. Presence is in memory on one instance and shared across replicas as
  expiring Cosmos DB items (ADR-0104).
- **Background work:** chat compaction runs on a single-worker executor per agent. Git mirror commits
  and pushes run synchronously under one lock in the request path (#113).

## LLM integration

The provider-neutral loop lives in `LLMProvider.run_turn`, and providers translate formats. The system
prompt contains a summary of the current diagram, compacted to `ARCVIZ_CONTEXT_BUDGET` characters by
collapsing large containers (it is not enforced for flat diagrams, #115). History is text only (tool
calls are not replayed). Resilience comes from SDK retries and timeouts, a round limit, an optional
token budget per turn, and the Anthropic server-side fallback
([LLM call resilience](../specs/llm-resilience.md)). Prompt caching is on for the system block. The
quality of prompts and tools is measured by an eval set against a real model, outside CI
([Agent evals](../specs/agent-evals.md)).

**Trust boundary:** diagram content (names, properties, transcript) is user-controlled and goes into
the system prompt unescaped. A crafted imported diagram can therefore steer the model on that diagram.
The damage is limited to operations on that same diagram, which are validated and versioned; names,
titles and labels cannot carry line breaks or other control characters into the prompt (#103, ADR-0160).

## Rendering and escaping

`mermaid.render` maps node ids to sanitised, collision-free Mermaid ids (`id_map`) and escapes all
user text with Mermaid entity codes. The browser renders with `securityLevel: "strict"`, and the e2e
test asserts that hostile names render literally, entity codes typed into a name included (`#` is
escaped first, #102); names, titles and labels are one line without control characters, normalised by the
operations and again by the renderer (#103, ADR-0160). The UI maps rendered SVG elements back to
element ids, both for clicks and for review highlights. Edge highlights depend on Mermaid's internal
`L_<from>_<to>_<n>` id scheme ([ADR-0025](../adr/0025-review-highlights-on-the-rendered-svg.md); risk R7).
The own sequence renderer (ADR-0038) avoids that coupling for sequence diagrams.

## Security

| Concern | State |
|---|---|
| Authentication and authorisation | Authentication optional, off by default: a shared token (bearer header, or a login page that sets an HttpOnly `SameSite=Strict` session cookie) or the identity headers of an authenticating proxy (id, name, e-mail; presets for Easy Auth and oauth2-proxy); probes stay public ([authentication](../specs/authentication.md), [ADR-0047](../adr/0047-authentication-off-by-default-shared-token-or-trusted-proxy.md), #82). `off` and `token` mode act as one admin identity. Authorisation (M20, reworked in M21): every user has a personal workspace; the owner of a project shares it with known users as viewer or editor; the effective role (owner → share → none) is checked by one access policy in the service layer for every route and both agents; invisible resources answer 404, too low a role 403 ([personal workspaces](../specs/personal-workspaces.md), ADR-0080, ADR-0084, ADR-0100..0103). |
| Cross-site requests | Mutating endpoints accept only `application/json` bodies (FastAPI rejects a missing, `text/plain` or form content type with 422). A browser sends JSON cross-site only after a CORS preflight, and the app sends no CORS headers. `DELETE` also needs a preflight. So foreign web pages cannot change a local instance; `tests/test_cross_origin.py` pins this. There is no CSRF token, so a future endpoint that accepts form data would break this; the session cookie of token mode is `SameSite=Strict`, and the login form is the only form endpoint. |
| Input limits | Body, import, chat message and proposal sizes are limited (413, on by default); optional rate limits per client for chat, review and other writes (429) ([request limits](../specs/request-limits.md), #85). |
| Output encoding | Mermaid entity escaping and strict mode. DOM building with `textContent` in the UI; markup only for Mermaid's SVG, enforced by `tests/js/sinks.test.mjs` (#119, ADR-0125). |
| Static files | Starlette `StaticFiles` for `/static` and `/docs`: resolves real paths, refuses traversal and symlinks out of the directory (tested for `/docs`). |
| Secrets | Only from the environment, and never logged on purpose. `/api/config` shows the provider and model and the Git mirror's status (including its last error text), behind authentication when it is on; it never shows the auth token. |
| Supply chain | Mermaid vendored and pinned. Python dependencies locked in `uv.lock` (with hashes; every install uses it), base images pinned by version and digest ([ADR-0109](../adr/0109-uv-lock-for-every-python-install.md), [ADR-0110](../adr/0110-base-images-pinned-by-digest.md)); LLM SDKs, Azure and OpenTelemetry packages are optional extras. |

## Configuration

Environment variables only, read in factories (`llm/factory.py`, `store/factory.py`) and in
`server.create_app`. Modules receive their dependencies as arguments
([Configuration](../../reference/configuration.md)). The kind catalogue is a process-wide global, so
two apps in one process share the last one activated. That is harmless for the server and handled in
the tests by an autouse fixture.

## Testing and quality assurance

- pytest with `ScriptedProvider` (no network), the store contract run against every backend (Cosmos
  against an in-process fake and, non-blocking, against the emulator), and Hypothesis property tests.
- `node --test` for the pure frontend modules and the module layering.
- A Playwright smoke test against the real app with a scripted LLM.
- Docs: a strict Zensical build, and the architecture diagrams checked against their sources and the imports.
- CI: one job, scoped by changed paths ([ADR-0006](../adr/0006-ci-minutes-single-job-and-milestone-branches.md)).

## Documentation

As-implemented spec (`docs/spec/`), design specs and ADRs (`docs/architecture/`), this arc42 layer, and
the user guide, built into every image, wheel and sdist and served by the app under `/docs`; there is no separate
publication ([ADR-0042](../adr/0042-docs-site-built-into-the-package.md),
[ADR-0043](../adr/0043-serving-the-documentation-under-docs.md),
[ADR-0116](../adr/0116-documentation-only-in-the-project.md)). Keeping the spec current is a
review rule (CLAUDE.md rule 1), not a check. Drift is possible: for example, the spec overview called
propose-then-confirm "rejected" after it had been implemented (corrected with this documentation).
