# 9. Architecture decisions

All decisions are recorded as ADRs: [decision record index](../adr/index.md). They are append-only,
and a reversed decision gets a new ADR. Many are still **Proposed**, meaning taken and implemented,
but not yet confirmed by the maintainer. The index shows the status of each.

The decisions that shape the architecture most, grouped by concern:

| Concern | Decisions |
|---|---|
| Model and mutation | [0007 Atomic batch as an ordinary operation](../adr/0007-atomic-batch-as-an-ordinary-operation.md), [0009 Optional edge direction](../adr/0009-optional-edge-direction.md), [0010 Kinds from a JSON file](../adr/0010-kinds-extended-from-a-json-file.md), [0011 Starter templates as code](../adr/0011-starter-templates-as-code.md) |
| Persistence and history | [0002 Repository pattern](../adr/0002-repository-pattern-for-persistence.md), [0003 Snapshots, not diffs](../adr/0003-snapshots-not-diffs.md), [0004 Cosmos DB first](../adr/0004-cosmos-db-nosql-first-database-backend.md), [0008 Redo from version sources](../adr/0008-redo-from-undo-redo-version-sources.md) |
| LLM and agent | [0005 Chat compaction](../adr/0005-chat-compaction-by-cumulative-summary.md), [0012 SDK retries and timeouts](../adr/0012-sdk-retries-and-timeouts.md), [0013 Per-turn token budget](../adr/0013-per-turn-token-budget.md), [0014 Reply text deltas](../adr/0014-reply-text-deltas.md), [0015 Client-held proposals](../adr/0015-client-held-proposals.md), [0016 Evals outside CI](../adr/0016-eval-set-outside-ci.md) |
| Rendering and UI | [0017 Layout direction, ELK not vendored](../adr/0017-layout-direction-on-the-diagram-elk-not-vendored.md), [0019 Compare as one merged render](../adr/0019-compare-versions-as-one-merged-render.md), [0020 Native ES modules](../adr/0020-frontend-native-es-modules.md), [0025 Review highlights on the SVG](../adr/0025-review-highlights-on-the-rendered-svg.md), [0038 Renderer per diagram type](../adr/0038-renderer-per-diagram-type.md), [0049 UI skins, diagram excluded](../adr/0049-ui-skins-as-token-overrides-diagram-excluded.md) |
| Review | [0022 Rule checks per diagram type](../adr/0022-rule-checks-registered-per-diagram-type.md), [0023 One finding schema](../adr/0023-one-finding-schema.md), [0024 LLM review as one tool call](../adr/0024-llm-review-as-one-validated-tool-call.md), [0026 Suggestions as propose turns](../adr/0026-suggestion-as-propose-turn-composed-in-the-browser.md) |
| Exchange | [0018 JSON both ways, PlantUML export](../adr/0018-exchange-formats-json-and-plantuml-export.md) |
| Users, workspaces and sharing | [0080 Identities from the proxy](../adr/0080-identities-from-the-authenticating-proxy.md), [0081 Workspaces with one owner](../adr/0081-workspaces-are-teams-with-one-owner.md), [0082 Keyed documents](../adr/0082-workspaces-and-users-as-keyed-documents.md), [0083 Project roles](../adr/0083-project-roles-and-default-viewer.md), [0084 Access policy in the services](../adr/0084-access-policy-in-the-service-layer.md), [0085 Role requirements](../adr/0085-role-requirements-per-action.md), [0086 Authors](../adr/0086-authors-on-versions-and-transcripts.md), [0087 Default workspace migration](../adr/0087-default-workspace-migration.md); M21: [0100 One personal workspace per user](../adr/0100-one-personal-workspace-per-user.md), [0101 Direct sharing by the owner](../adr/0101-direct-sharing-by-the-project-owner.md), [0102 Migration keeps access explicit](../adr/0102-migration-keeps-access-explicit.md), [0103 No access outside personal workspaces](../adr/0103-no-access-outside-personal-workspaces.md) |
| Process, build and docs | [0001 Record decisions](../adr/0001-record-architecture-decisions.md), [0006 CI minutes](../adr/0006-ci-minutes-single-job-and-milestone-branches.md), [0021 Hypothesis](../adr/0021-hypothesis-property-tests.md), [0040 Diagrams as Arcviz documents](../adr/0040-architecture-diagrams-as-arcviz-documents.md), [0041 arc42 as a linking layer](../adr/0041-arc42-as-a-linking-layer.md), [0042 Docs site in the package](../adr/0042-docs-site-built-into-the-package.md), [0043 Serving the docs under /docs](../adr/0043-serving-the-documentation-under-docs.md) |

Decisions that are implicit (no ADR), but would deserve one if revisited:

- ~~Last writer wins~~ for concurrent requests on a diagram: replaced by conditional writes on a
  revision with a consistency contract ([ADR-0044](../adr/0044-conditional-writes-on-a-latest-revision.md), #65).
- ~~No authentication in the app~~: optional token or trusted-proxy authentication
  ([ADR-0047](../adr/0047-authentication-off-by-default-shared-token-or-trusted-proxy.md), #82).
- ~~Every authenticated client may do everything~~: identities from the proxy, workspaces and project roles
  checked in the service layer ([ADR-0080](../adr/0080-identities-from-the-authenticating-proxy.md)..[0087](../adr/0087-default-workspace-migration.md), M20).
- ~~Workspaces are teams with members~~: one personal workspace per user, projects shared directly by their owner
  ([ADR-0100](../adr/0100-one-personal-workspace-per-user.md)..[0103](../adr/0103-no-access-outside-personal-workspaces.md), M21).
- **Synchronous Git mirror** in the request path (#113).
