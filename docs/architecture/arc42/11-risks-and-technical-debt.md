# 11. Risks and technical debt

Ranked by severity, which combines how likely the risk is in the documented deployments and how bad
it is. Every item links to the issue that tracks it. Items #112–#119 and #120 were filed while this
documentation was written. The PR that resolves an item removes or downgrades its row.

## Risks

| # | Risk | Severity | Scenario | Issue |
|---|---|---|---|---|
| R1 | **Authentication is off by default.** A deployment that neither sets `ARCVIZ_AUTH` nor puts a proxy in front lets anyone who reaches the port act as the admin identity: read, change and delete everything the admin owns and spend the LLM budget. In `token` mode every holder of the token is the admin, too; only `proxy` mode tells users apart (personal workspaces and shares, M20/M21). | Medium (if exposed and not configured) | `docker compose up` on a shared network without a token | #82 (token and proxy modes, ADR-0047); limits #85 (ADR-0048); users, workspaces and roles #158..#164 (ADR-0080..0087), personal workspaces #177..#179 (ADR-0100..0103). OIDC in the app is a follow-up. |
| R19 | **Identity headers are trusted blindly.** In proxy mode the user id, name and e-mail come from request headers; whoever reaches the app without passing the proxy can act as any user, including project owners. | High (if the app is reachable directly) | A container port exposed next to the ingress, a proxy that does not overwrite the headers | Documented next to the variables and in the deployment guide; refusal of requests without the id header (#158, ADR-0080). No signature check of the headers (Easy Auth's are not signed). |
| R21 | **Locating a diagram's project falls back to reading every project.** Since M22 a diagram→project locator (one `location` document per diagram, a re-validated hint) answers with point reads, and `GET /api/diagrams` and project views describe only the caller's diagrams (#181, ADR-0120); a request reads its project once (#166, ADR-0121). The scan remains as the fallback for an existing diagram without a valid entry (it heals the entry; requests arriving during that first scan may scan too) and for orphans in no project (at most once per ten minutes). | Low | A rolling upgrade from v0.2.0 with several replicas, or a crash between writes, costs one scan per affected diagram; an orphan one scan per ten minutes however often it is requested; outsiders probing unknown ids cost point reads, never a scan | ADR-0120, ADR-0121, R15, #181, #166 |
| R6 | **Growth of the history.** Since M23 every response reads the version metadata once (no snapshots; Cosmos a projection) and carries one page; retention is opt-in (`ARCVIZ_VERSIONS_KEEP`, ADR-0130). Without it, versions are never pruned and the metadata listing grows linearly. | Low | A long-lived diagram on an installation without retention: each response lists all version metadata (small items, one partition) | #114, ADR-0129, ADR-0130 |
| R7 | **Coupling to Mermaid internals.** Review highlights, click mapping and comparison marks rely on Mermaid's SVG element ids (`L_<from>_<to>_<n>`). | Medium | A Mermaid upgrade (`make vendor`) silently breaks highlights. Only the e2e test would notice. | ADR-0025, ADR-0038 (own renderer for sequence); no issue for deployment and activity |
| R9 | **Prompt injection through diagram content.** Names and properties are interpolated into the system prompt. | Low–medium | A shared or imported diagram steers the model. The impact stays within that diagram (validated, versioned); names, titles and labels are one line since #103 (ADR-0160). | Trust boundary in section 8 |
| R15 | **Project membership spans documents.** "Every diagram in exactly one project" is kept by ordered writes and a repair step, not by a transaction; the repair reads every project (finding a diagram's project uses the locator since M22, ADR-0120). | Low | A crash between writes leaves an orphan, duplicate or dangling membership until the next start or `arcviz-migrate --projects` repairs it (a caller's read repairs nothing, ADR-0088); an orphan goes back into the project recorded before its creation, never across workspaces; one whose project is gone stays invisible until an admin places it. Installations with thousands of projects make listings slow. | ADR-0052, ADR-0088; M18 kept the storage (ADR-0078) |
| R16 | **Cosmos DB partitions per diagram, not per project.** Projects are their own items; reading a project and its diagrams touches one partition per diagram. | Low | Fine at tens of diagrams per project. The shared model (M18) lives in the project item and needs no cross-partition transactions (ADR-0078); repartitioning by `/project_id` would mean moving every item. Re-evaluate if per-project transactions become a requirement. | #137, ADR-0078 |
| R18 | **Shared-model renames span documents.** A rename writes the project, then each referencing diagram (ADR-0071); there is no transaction. | Low | A crash, a diagram that keeps conflicting, a link racing a rename or an undo in one diagram leaves a view with the old name (*drift*). The project review reports it (`element-name-drift`) and **Abgleichen** (`POST …/model/sync`) repairs it. A diagram moved to another project brings its elements and relations along (#153, ADR-0161); a crash during the move leaves views unlinked at worst. | #147, #153 |
| R22 | **The project item holds elements and relations.** Every field is bounded: relations (`MAX_RELATIONS` 500, description 300, property values 64 characters) and, since M26, element names (100) and descriptions (300) of new, changed and imported elements (#212); every element and relation at its bounds in three-byte characters next to 1 000 memberships is 1.67 MB of UTF-8. Elements stored before M26 keep longer texts (the bound is no integrity rule). Relation changes span documents like renames (project first, then each linking diagram; deletes the other way round), so a crash leaves at worst a link that no longer fits, which the review reports (`invalid-relation-link`) and **Abgleichen** clears. | Low | The per-field bounds cover realistic text; for the rest (the Cosmos DB SDK escapes non-ASCII characters, six bytes for `€`, and which encoding the 2 MB limit counts is not documented: the worst case is about 2.9 MB escaped) a size guard refuses a write that makes the document, measured as the SDK sends it, larger than 1.9 MB (never one that shrinks it); a project with long element texts from before M26 can be over the budget and then only shrinks; a crash between the writes of a relation change | [Model relations](../specs/model-relations.md) (size budget), ADR-0135, ADR-0139, ADR-0153 |
| R17 | **A project chat turn is not atomic across diagrams.** Each diagram it edits gets its own write-through and automatic version (ADR-0059); the tool set changes between rounds when the active diagram's type changes (ADR-0058). | Low | A turn that stops halfway leaves diagram A changed and B unchanged (both consistent on their own); undo works per diagram. A provider that rejected tool calls of tools no longer offered would fail the turn after a type switch (Anthropic and OpenAI accept them); prompt caching of the tool block is lost after a switch. | #135 |

Resolved risks keep their number so references stay valid; they are listed under *Resolved* below.

## Technical debt

| # | Debt | Consequence | Issue |
|---|---|---|---|
| D5 | Process-wide kind catalogue (`use_kinds`) | Several apps per process share it, and tests need an autouse reset. Per-type kinds (#98) will have to replace or scope it. | #98 |
| D6 | The store depends on the renderer through the Git mirror | Renderer changes touch persisted mirror output; per-type renderers must be reachable from the store | #98 (per-type renderer) |
| D7 | Many ADRs are still *Proposed* | Decisions in force without confirmation; reviewers can't tell settled from open | ADR index |
| D8 | The as-implemented spec is kept current by review only | Drift (for example, propose mode described as "rejected" until M14) | CLAUDE.md rule 1 |

## Resolved

| # | Risk | Resolved by |
|---|---|---|
| R2 | Lost updates (load → mutate → unconditional write; finalization wrote again; deleted diagrams resurrected) | #65: conditional writes on a revision, atomic with the version, retried per path; [concurrent mutations](../specs/concurrent-mutations.md), [ADR-0044](../adr/0044-conditional-writes-on-a-latest-revision.md). Clients send `If-Match` and get 412 since #86 ([collaboration](../specs/collaboration.md)). |
| R3 | Partial mutations on unexpected input types | #66: arguments validated against the operation signatures, rollback on every exception; [operations](../../spec/operations.md#dispatcher), [ADR-0045](../adr/0045-argument-validation-from-operation-signatures.md) |
| R11 | Stale compaction after a chat clear (sequence numbers are reused) | #67: a transcript generation guards `compact_chat`; [chat compaction](../specs/chat-compaction.md#transcript-generation-67), [ADR-0046](../adr/0046-transcript-generation-guards-compaction.md) |
| R12 | Containment cycles in stored data hang `descendants()` and focused rendering | #68: every traversal terminates; [domain model](../../spec/domain-model.md#tree-queries) |
| D4 | Two error placeholders in the UI used `innerHTML` | #119: error text is `textContent`, `tests/js/sinks.test.mjs` forbids derived markup; [hardening](../specs/hardening.md), [ADR-0125](../adr/0125-no-derived-markup-in-the-frontend.md) |
| R4 | Finalization deferred on disconnect (the chat generator was not closed by the server) | #112: `TurnResponse` closes the turn on a worker thread when the response ends; store calls of the chat routes off the event loop; [hardening](../specs/hardening.md), [ADR-0126](../adr/0126-the-server-closes-a-turn-in-a-worker-thread.md) |
| R20 | Ids were global and a taken id showed that it existed somewhere (ADR-0089) | #167: new ids are `<slug>-<random suffix>`, a requested id is only a prefix; no create answer depends on other tenants' data; [hardening](../specs/hardening.md), [ADR-0131](../adr/0131-server-generated-ids.md) |
| R13 | File store durability (empty version placeholders after a crash gave a 500; versions beyond 9999 were ordered wrongly; a purge racing a write raised `OSError`) | #117: every replaced file is `fsync`ed with its directory, versions are ordered by number, an empty or damaged version file is a `StoreError`, `purge` raises only `StoreError`; [persistence](../../spec/persistence.md#file-default) |
| R5 | Git mirror blocked writes (commit and push synchronous under one lock, 120 s timeout) | #113: commits stay ordered on the request path; pushes run on a background worker that coalesces them, bounded by 60 s each, drained for at most 10 s at shutdown; [persistence](../../spec/persistence.md#git-mirror), [ADR-0151](../adr/0151-git-pushes-on-a-background-worker.md) |
| R8 | Context budget not enforced for flat diagrams | #115: what collapsing cannot shorten is cut (edges, then leaves, then container lines, with counts and a `get_diagram` hint), `len(summary(budget)) <= budget + 1000` for every shape (property test); [domain model](../../spec/domain-model.md#budget), [ADR-0152](../adr/0152-the-summary-cuts-edges-then-leaves.md). `ARCVIZ_TURN_TOKEN_BUDGET` still defaults to no cap. |
| R14 | Escaping gaps: `#` was not escaped, line breaks in names were undefined | #102: `#` is escaped as `#35;`, so the escaping is injective; #103: names, titles and labels are one line without control characters, normalised by the operations and the renderer; [rendering](../../spec/rendering.md#labels-and-escaping), [ADR-0160](../adr/0160-control-characters-in-names-are-normalised.md) |
| R10 | Non-reproducible builds (no lock, floating base images) | #116: `uv.lock` for every install, base images pinned by digest, hashed requirements attached to releases; [ADR-0109](../adr/0109-uv-lock-for-every-python-install.md), [ADR-0110](../adr/0110-base-images-pinned-by-digest.md). Debian packages and GitHub Actions tags are not pinned. |

## Assessment

The core design is sound for its original scope, one user per diagram on one instance. The single mutation path,
snapshot versioning behind a contract-tested repository and a provider-neutral tool loop meet quality goals 1–5 in
the common case, and the test suite is unusually strong for the size of the code (contract tests per
backend, property tests, offline agent tests, e2e).

The weak spots are all at the **edges of that scope**:
- concurrency (R2 and R4 are resolved: conditional writes, #65; turns closed on disconnect, #112);
- operation as a shared service (R1 when authentication is left off, R19 when the proxy can be bypassed, R6; R5 is
  resolved since M26: Git pushes run off the request path, #113).

M9 (*Operations & deployment*) resolved reproducibility (R10) and the operational debt of unstructured logs, mandatory
LLM SDKs and unclear start-up errors (#83, #84, #116, #118, #156).

M15 (*Robustness*) closed the top risks of the first assessment: lost updates (#65), partial mutations
(#66), stale compaction (#67), containment cycles (#68), and added optional authentication (#82) and
request limits (#85). M20 added users, workspaces and project roles behind an authenticating proxy (#158..#164); M21 replaced team
workspaces by personal workspaces with direct sharing (#177..#179).
Before Arcviz is offered to a team, the remaining minimum is to run it **behind a proxy that is the only way in**
(R19); #112 is fixed since M23. M10 added client-side conflict detection, live updates and presence (#86, #171).
The Cosmos backend makes replicas safe for concurrent edits since #65.
M26 (*Store, mirror and runtime robustness*) resolved the file store's durability gaps (R13, #117), the Git mirror's
blocking pushes (R5, #113) and the unenforced context budget (R8, #115), and bounded element names and descriptions
(R22, #212).
