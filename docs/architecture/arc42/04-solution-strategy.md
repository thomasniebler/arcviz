# 4. Solution strategy

| Quality goal | Approach | Details |
|---|---|---|
| Model integrity | **Typed operations as the only mutation path.** The LLM never writes diagram source or files; it calls tools whose arguments are validated by `operations.apply`, which snapshots the diagram and rolls back on error. The REST endpoint for direct edits uses the same function. Error messages are written for the LLM, so it can correct itself. The store refuses to save a diagram with integrity errors. | [Operations](../../spec/operations.md), [ADR-0007](../adr/0007-atomic-batch-as-an-ordinary-operation.md) |
| Recoverability | **Immutable snapshots per change, linear history.** Every turn that changed something creates a version. Restore, undo and redo are recorded as new versions. An optional Git mirror keeps a second record. | [ADR-0003](../adr/0003-snapshots-not-diffs.md), [ADR-0008](../adr/0008-redo-from-undo-redo-version-sources.md), [Persistence](../../spec/persistence.md) |
| Live feedback | **Server-sent events per tool call.** The agent persists `latest` after each applied operation and emits the diagram plus Mermaid source. Reply text is streamed as deltas. Rendering is Mermaid in the browser, generated on the server. | [Agent](../../spec/agent.md), [ADR-0014](../adr/0014-reply-text-deltas.md) |
| Self-containment | **One Python process with a static frontend, everything vendored.** No Node build, Mermaid vendored, documentation built into the package, store defaults to JSON files. | [ADR-0020](../adr/0020-frontend-native-es-modules.md), [ADR-0042](../adr/0042-docs-site-built-into-the-package.md) |
| Changeability | **Interfaces at the volatile edges.** `LLMProvider` owns the tool loop, and providers only translate formats. `DiagramRepository` owns versioning, and backends implement primitives. Rule checks are registered per diagram type. Kinds can be extended from a file, and renderers are chosen per diagram type. | [LLM layer](../../spec/llm-layer.md), [ADR-0002](../adr/0002-repository-pattern-for-persistence.md), [ADR-0010](../adr/0010-kinds-extended-from-a-json-file.md), [ADR-0022](../adr/0022-rule-checks-registered-per-diagram-type.md), [ADR-0038](../adr/0038-renderer-per-diagram-type.md) |
| Cost and safety of LLM use | Compact the diagram summary to a character budget, keep a cumulative chat summary, cap rounds and (optionally) tokens per turn, cache prompts, and offer a propose mode for risky changes. | [ADR-0005](../adr/0005-chat-compaction-by-cumulative-summary.md), [ADR-0013](../adr/0013-per-turn-token-budget.md), [ADR-0015](../adr/0015-client-held-proposals.md) |
| Verifiability | Tests before code. A `ScriptedProvider` instead of a real model. A store contract suite run against every backend. Hypothesis property tests across operations, integrity, rendering and exchange. A Playwright smoke test. An eval set against a real model outside CI. | [ADR-0016](../adr/0016-eval-set-outside-ci.md), [ADR-0021](../adr/0021-hypothesis-property-tests.md) |

Two strategic choices worth stating explicitly, because they shape the risks in section 11:

- **Optimistic concurrency on a revision.** The first version was last-writer-wins: concurrency
  control existed only inside store primitives. Since #65 every write of a diagram is a
  compare-and-swap on the revision of the state it was computed from, atomic with its version, and
  intent-based requests re-apply themselves on a conflict
  ([ADR-0044](../adr/0044-conditional-writes-on-a-latest-revision.md)). Since M10 (#86) clients get the revision
  as `ETag` and may send it back as `If-Match` (412 when stale, ADR-0094); other browsers learn of every change
  through a live stream fed by an in-process bus plus a revision poll (ADR-0095).
- **Authentication delegated to the deployment, authorisation in the services.** Authentication is optional (a
  shared token, or an authenticating proxy that passes the user's identity; ADR-0047, ADR-0080), CSRF protection
  rests on same-origin JSON and `SameSite=Strict`, and request sizes are limited with optional rate limits
  (ADR-0048). The reference deployment puts an identity-aware proxy in front. What a user may see and change
  (own projects in a personal workspace, projects shared with them by their owner) is decided by one access policy
  in the service layer, which every entry point goes
  through (ADR-0084).
