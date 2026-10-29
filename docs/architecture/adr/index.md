# Architecture decision records

Short documents that capture one decision each: the context, the options considered, the choice and
its consequences. They are append-only; a reversed decision gets a new ADR that supersedes the old
one (and the old one's status changes to `Superseded by ADR-NNNN`).

Create a new ADR by copying `0000-template.md` to the next free number. Status values: `Proposed`,
`Accepted`, `Deprecated`, `Superseded by ADR-NNNN`.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-repository-pattern-for-persistence.md) | Repository pattern for persistence | Accepted |
| [0003](0003-snapshots-not-diffs.md) | Store full snapshots per version, not diffs | Accepted |
| [0004](0004-cosmos-db-nosql-first-database-backend.md) | Azure Cosmos DB (NoSQL API) as the first database backend | Accepted |
| [0005](0005-chat-compaction-by-cumulative-summary.md) | Compact the chat transcript with a cumulative LLM summary | Accepted |
| [0006](0006-ci-minutes-single-job-and-milestone-branches.md) | One CI job, path-scoped steps and milestone branches | Accepted |
| [0007](0007-atomic-batch-as-an-ordinary-operation.md) | Atomic batch as an ordinary operation | Proposed |
| [0008](0008-redo-from-undo-redo-version-sources.md) | Redo from undo/redo version sources | Proposed |
| [0009](0009-optional-edge-direction.md) | Optional edge direction overriding the kind's arrow | Proposed |
| [0010](0010-kinds-extended-from-a-json-file.md) | Element kinds extended from a JSON file at startup | Proposed |
| [0011](0011-starter-templates-as-code.md) | Starter templates as code, listed statically in the UI | Proposed |
| [0012](0012-sdk-retries-and-timeouts.md) | Use the SDKs' retry and timeout handling for provider calls | Proposed |
| [0013](0013-per-turn-token-budget.md) | Per-turn token budget checked before each model call | Proposed |
| [0014](0014-reply-text-deltas.md) | Stream reply text as `text_delta` events next to the round's `text` | Proposed |
| [0015](0015-client-held-proposals.md) | Proposals are dry runs held by the client and replayed on confirm | Proposed |
| [0016](0016-eval-set-outside-ci.md) | Eval set as JSON cases with a standalone runner outside pytest and CI | Proposed |
| [0017](0017-layout-direction-on-the-diagram-elk-not-vendored.md) | Layout direction on the diagram, ELK not vendored | Proposed |
| [0018](0018-exchange-formats-json-and-plantuml-export.md) | Exchange formats: versioned JSON both ways, PlantUML export only | Proposed |
| [0019](0019-compare-versions-as-one-merged-render.md) | Compare versions on the server as one merged render | Proposed |
| [0020](0020-frontend-native-es-modules.md) | Native ES modules by feature, pure logic in separate Node-testable modules | Proposed |
| [0021](0021-hypothesis-property-tests.md) | Hypothesis as a dev-only dependency for property-based tests | Proposed |
| [0022](0022-rule-checks-registered-per-diagram-type.md) | Rule checks as plain functions registered per diagram type | Proposed |
| [0023](0023-one-finding-schema.md) | One finding schema for rule checks and the LLM review | Proposed |
| [0024](0024-llm-review-as-one-validated-tool-call.md) | The LLM review is one validated tool call, neither streamed nor stored | Proposed |
| [0025](0025-review-highlights-on-the-rendered-svg.md) | Review panel highlights findings on the rendered SVG | Proposed |
| [0026](0026-suggestion-as-propose-turn-composed-in-the-browser.md) | A review suggestion becomes an ordinary propose-mode chat turn composed in the browser | Proposed |
| [0030](0030-uml-2-5-1-as-the-reference-for-diagram-types.md) | UML 2.5.1 as the reference for diagram types | Accepted |
| [0031](0031-diagram-type-field-with-per-type-tables.md) | Diagram type as a field with per-type tables in each layer | Proposed |
| [0032](0032-rendering-of-diagram-types-in-m13.md) | Rendering of diagram types in M13 (implements ADR-0038) | Accepted |
| [0033](0033-render-payload-names-its-renderer.md) | Render payload names its renderer | Proposed |
| [0034](0034-activity-diagrams-reuse-node-and-edge-operations.md) | Activity diagrams reuse the node and edge operations | Proposed |
| [0035](0035-sequence-diagrams-interaction-tree-and-server-side-svg.md) | Sequence diagrams as an ordered interaction tree, drawn server-side as SVG | Proposed |
| [0036](0036-vendored-cc0-icon-subsets-as-mermaid-icon-packs.md) | Vendored CC0 icon subsets as Mermaid icon packs | Proposed |
| [0037](0037-configurable-kinds-extend-deployment-only.md) | Configurable kinds extend deployment diagrams only | Proposed |
| [0038](0038-renderer-per-diagram-type.md) | Renderer per diagram type: Mermaid for deployment and activity, own SVG for sequence | Accepted |
| [0040](0040-architecture-diagrams-as-arcviz-documents.md) | Architecture diagrams as Arcviz documents with C4 kinds from a kinds file | Proposed |
| [0041](0041-arc42-as-a-linking-layer.md) | arc42 as a linking layer over the specs and ADRs, with an explicit assessment | Proposed |
| [0042](0042-docs-site-built-into-the-package.md) | Documentation site built into the package by a script, a Docker stage and the release job | Proposed |
| [0043](0043-serving-the-documentation-under-docs.md) | Serving the documentation under /docs with revalidation, a 404 when not built, and Swagger UI under /api | Proposed |
| [0044](0044-conditional-writes-on-a-latest-revision.md) | Conditional writes on a latest revision, retried per mutation path | Proposed |
| [0045](0045-argument-validation-from-operation-signatures.md) | Validate operation arguments against the operation signatures, roll back on every exception | Proposed |
| [0046](0046-transcript-generation-guards-compaction.md) | A transcript generation guards chat compaction | Proposed |
| [0047](0047-authentication-off-by-default-shared-token-or-trusted-proxy.md) | Authentication off by default, a shared token with a session cookie, or a trusted proxy header | Proposed |
| [0048](0048-request-limits-and-in-process-rate-limiting.md) | Request size limits on by default, in-process rate limiting off by default | Proposed |
| [0049](0049-ui-skins-as-token-overrides-diagram-excluded.md) | UI skins as token overrides on a root attribute, diagram stage excluded | Accepted |
| [0050](0050-application-services-over-repository-ports.md) | Application services over repository ports | Accepted |
| [0051](0051-projects-as-own-documents-diagrams-unchanged.md) | Projects as their own documents, diagrams unchanged | Proposed |
| [0052](0052-membership-ordered-writes-and-repair.md) | Membership across documents: ordered writes and a repair step | Proposed |
| [0053](0053-default-project-lazily-and-by-cli.md) | Existing diagrams move into a default project, lazily and by CLI | Proposed |
| [0054](0054-section-removal-keeps-contents.md) | Removing a section keeps its contents; only empty projects can be deleted | Proposed |
| [0055](0055-navigator-column-and-layer.md) | A navigator column with its own layer between the hub and the features | Proposed |
| [0056](0056-project-bundle-embeds-diagram-documents.md) | A project bundle embeds the diagram documents; import always creates a new project | Proposed |
| [0057](0057-project-transcript-next-to-the-project-document.md) | A project transcript next to the project document, through new store primitives | Proposed |
| [0058](0058-active-diagram-per-turn-tools-follow-its-type.md) | The active diagram is per-turn state seeded by the client; the tools follow its type | Proposed |
| [0059](0059-multi-diagram-turn-versions-each-diagram.md) | A multi-diagram turn writes through per call and versions each changed diagram | Proposed |
| [0060](0060-project-propose-mode-one-diagram.md) | Propose mode in a project turn covers one existing diagram | Proposed |
| [0061](0061-project-chat-as-a-chat-scope.md) | The project chat is a scope of the chat pane, opened from the navigator | Proposed |
| [0062](0062-project-findings-reference-diagrams.md) | Project findings extend the finding schema with diagram references | Proposed |
| [0063](0063-project-rules-and-overview-diagrams.md) | Project rules, and an overview diagram is recognised by its title | Proposed |
| [0064](0064-project-llm-review-one-call-budgeted-digest.md) | The project LLM review is one tool call over a budgeted project digest | Proposed |
| [0065](0065-system-prompts-from-shared-blocks-and-expert-parts.md) | System prompts from shared blocks and per-type expert parts in text files | Proposed |
| [0066](0066-topic-scope-in-the-prompt-without-classifier-or-switch.md) | Topic scope enforced in the system prompt, without a classifier or a switch | Proposed |
| [0070](0070-shared-model-elements-in-the-project-document.md) | Model elements in the project document, referenced from views by an optional ref | Proposed |
| [0071](0071-names-propagated-into-views-drift-repaired.md) | Names live in the model and are propagated into views; drift is repaired by sync | Proposed |
| [0072](0072-deletion-from-view-or-model-dangling-refs-tolerated.md) | Deleting from a view keeps the element; deleting from the model unlinks; dangling refs are tolerated | Proposed |
| [0073](0073-element-kinds-and-view-compatibility.md) | Abstract element kinds and which view elements may represent them | Proposed |
| [0074](0074-sections-reference-subsystem-elements.md) | Sections reference model elements instead of becoming elements | Proposed |
| [0075](0075-cross-diagram-consistency-rules.md) | Cross-diagram consistency rules over element references | Proposed |
| [0076](0076-shared-elements-in-the-chats.md) | Shared elements in the diagram chat and the project chat | Proposed |
| [0077](0077-project-bundles-carry-the-model.md) | Project bundles carry the model; schema version 2 only when there is one | Proposed |
| [0078](0078-no-repartitioning-for-the-model.md) | No repartitioning of the stores for the shared model | Proposed |
| [0079](0079-links-dropped-when-a-diagram-leaves-its-project.md) | Links are dropped when a diagram leaves its project | Superseded by ADR-0161 |
| [0080](0080-identities-from-the-authenticating-proxy.md) | Identities from the authenticating proxy; off and token mode act as the admin identity | Accepted |
| [0081](0081-workspaces-are-teams-with-one-owner.md) | Workspaces are teams with exactly one owner who controls access | Superseded by ADR-0100 |
| [0082](0082-workspaces-and-users-as-keyed-documents.md) | Workspaces and users as keyed documents; a project names its workspace | Proposed |
| [0083](0083-project-roles-and-default-viewer.md) | Project roles owner/editor/viewer; members default to viewer; the workspace owner owns every project | Superseded by ADR-0101 |
| [0084](0084-access-policy-in-the-service-layer.md) | One access policy in the service layer; services bound to the caller; 404/403 | Accepted |
| [0085](0085-role-requirements-per-action.md) | Who may create, delete, invite and move | Superseded by ADR-0101 |
| [0086](0086-authors-on-versions-and-transcripts.md) | Authors as user id plus display name, omitted when unknown | Proposed |
| [0087](0087-default-workspace-migration.md) | Existing data moves into a default workspace owned by the configured admin | Superseded by ADR-0102 |
| [0088](0088-membership-repair-never-crosses-workspaces.md) | The membership repair never moves a diagram across workspaces | Proposed |
| [0089](0089-taken-ids-get-a-free-variant.md) | A taken id gets a free variant, whoever can see its owner | Superseded by ADR-0131 |
| [0090](0090-a-turn-that-loses-its-role.md) | A chat turn whose caller loses the editor role keeps what it wrote | Proposed |
| [0091](0091-members-by-e-mail-only-when-trusted.md) | An e-mail address adds a member only when it is trusted and unambiguous | Proposed |
| [0092](0092-notes-as-optional-description.md) | Notes are an optional `description` set by the existing add/update operations | Accepted |
| [0093](0093-notes-in-the-prompt-are-budgeted.md) | Notes in the assistant's prompt are budgeted before containers collapse | Accepted |
| [0094](0094-etag-and-if-match-on-the-latest-revision.md) | ETag is the latest revision; `If-Match` makes a write a single compare-and-swap (412) | Accepted |
| [0095](0095-live-stream-bus-plus-poll.md) | One live stream per tab: in-process bus plus a revision poll; events are refetch hints | Accepted |
| [0096](0096-streams-recheck-access.md) | A stream re-checks access on every event and every poll; it ends with an unexplained `gone` | Accepted |
| [0097](0097-presence-in-process-memory.md) | Presence lives in process memory, per instance | Superseded by ADR-0104 |
| [0098](0098-bounded-streams.md) | Streams are bounded: caps per user and process, a maximum lifetime, an end on shutdown | Accepted |
| [0099](0099-inspector-conflicts.md) | The inspector keeps typed values and resolves a 412 field by field | Accepted |
| [0100](0100-one-personal-workspace-per-user.md) | One personal workspace per user, created on first sign-in | Accepted |
| [0101](0101-direct-sharing-by-the-project-owner.md) | Projects are shared directly with users by their owner; owner → share → none | Accepted |
| [0102](0102-migration-keeps-access-explicit.md) | The migration to personal workspaces keeps access explicit | Accepted |
| [0103](0103-no-access-outside-personal-workspaces.md) | Projects outside a personal workspace are visible to nobody | Accepted |
| [0104](0104-presence-across-replicas-in-cosmos.md) | Presence across replicas as expiring Cosmos DB items, one per project and replica | Accepted |
| [0105](0105-versioned-watches.md) | A stream's watch is versioned; a stale re-check never overwrites a newer watch | Accepted |
| [0106](0106-configuration-problems-collected-at-start-up.md) | Configuration problems are collected across subsystems and reported together at start-up | Accepted |
| [0107](0107-llm-sdks-as-extras-checked-at-start-up.md) | LLM SDKs are extras; the configured provider's SDK is checked at start-up | Accepted |
| [0108](0108-the-image-installs-every-runtime-extra.md) | The Docker image installs every runtime extra | Accepted |
| [0109](0109-uv-lock-for-every-python-install.md) | `uv.lock` is the single lock for CI, the image, the docs and the release | Accepted |
| [0110](0110-base-images-pinned-by-digest.md) | Base images and GitHub Actions are pinned exactly; Dependabot updates them monthly | Accepted |
| [0111](0111-json-logs-with-an-in-house-formatter.md) | JSON logs are opt-in and written by an in-house formatter | Accepted |
| [0112](0112-logs-carry-pseudonymous-users-and-route-templates.md) | Log records carry a keyed pseudonym and route templates, never raw ids, paths, messages or content | Accepted |
| [0113](0113-request-ids-and-trace-ids.md) | The server generates every request id; a safe client `X-Request-ID` is logged apart; the trace id from `traceparent` | Accepted |
| [0114](0114-opentelemetry-as-an-optional-extra.md) | OpenTelemetry and Azure Monitor export through one optional `telemetry` extra | Accepted |
| [0115](0115-entra-id-tokens-for-azure-openai.md) | Entra ID tokens for Azure OpenAI through an in-house token provider over `azure-identity` | Accepted |
| [0116](0116-documentation-only-in-the-project.md) | The documentation is built into every artifact; no GitHub Pages | Accepted |
| [0120](0120-diagram-locator-as-a-re-validated-hint.md) | A diagram→project locator as a re-validated hint that heals on read | Accepted |
| [0121](0121-project-reads-cached-per-request.md) | Project documents are cached per HTTP request, never in chat turns or live streams | Accepted |
| [0122](0122-revision-only-read.md) | A revision-only read primitive for live polling | Accepted |
| [0123](0123-conditional-deletes.md) | Conditional deletes; If-Match on DELETE of diagrams and projects | Accepted |
| [0124](0124-stamped-listing-entries-pruned-after-a-grace.md) | Listing entries are stamped and pruned after a grace period, conditionally | Accepted |
| [0125](0125-no-derived-markup-in-the-frontend.md) | No derived markup in the frontend; trusted sinks are listed in a test | Accepted |
| [0126](0126-the-server-closes-a-turn-in-a-worker-thread.md) | The server closes a chat turn on a worker thread when its response ends | Accepted |
| [0127](0127-transcript-writes-bound-to-a-generation.md) | Transcript writes are bound to a generation; placements to a project incarnation | Accepted |
| [0128](0128-kind-changes-and-links-verify-after-writing.md) | Kind changes and links verify after writing and compensate conditionally | Accepted |
| [0129](0129-version-metadata-read-once-and-paged.md) | Version metadata is read once per response, without snapshots, and paged | Accepted |
| [0130](0130-opt-in-version-retention.md) | Version retention is opt-in and never drops what undo, redo or manual snapshots need | Accepted |
| [0131](0131-server-generated-ids.md) | New diagram and project ids carry a random suffix | Accepted |
| [0132](0132-mermaid-output-through-the-svg-allow-list.md) | Mermaid's output goes through the SVG allow-list, parsed in an inert template | Accepted |
| [0133](0133-pending-tokens-settled-on-proof.md) | Pending locator tokens are settled once their writer provably stopped or died | Accepted |
| [0134](0134-own-spans-and-metrics-by-allow-list.md) | The app's own spans and metrics, by allow-list, and keyless Azure Monitor export | Accepted |
| [0135](0135-relations-in-the-project-document.md) | Model relations live in the project document, sized by `MAX_RELATIONS` | Accepted |
| [0136](0136-relation-kinds-and-containment.md) | A closed set of relation kinds; containment stays `Element.parent` | Accepted |
| [0137](0137-open-world-completeness-and-hidden-relations.md) | Open world; completeness is declared per diagram, hiding is marked per view | Accepted |
| [0138](0138-views-primary-findings-resolved-by-people.md) | Views stay primary; findings never pick a winner | Accepted |
| [0139](0139-view-connections-link-to-relations.md) | Deployment edges and sequence messages link to relations | Accepted |
| [0140](0140-agent-links-relations-and-proposes-missing-ones.md) | The agent links matching relations and proposes missing ones | Accepted |
| [0141](0141-correspondence-rules.md) | Correspondence rules across diagrams; `message-without-connection` generalised | Accepted |
| [0150](0150-generated-id-suffixes-are-replaced.md) | Generated id suffixes hold a digit and are replaced, not extended | Accepted |
| [0151](0151-git-pushes-on-a-background-worker.md) | Git pushes run on a coalescing background worker | Accepted |
| [0152](0152-the-summary-cuts-edges-then-leaves.md) | The prompt summary cuts edges, then leaves, to respect its budget | Accepted |
| [0153](0153-element-texts-bounded-on-write-not-by-integrity.md) | Element names and descriptions are bounded on write, not by integrity | Accepted |
| [0154](0154-cosmos-request-units-as-a-metric.md) | Cosmos DB request units as a metric by operation and document kind | Accepted |
| [0155](0155-damaged-version-files-are-reported.md) | Damaged version files are reported and skipped, not repaired | Accepted |
| [0160](0160-control-characters-in-names-are-normalised.md) | Control characters in names, titles and labels are normalised, never refused | Accepted |
| [0161](0161-linked-elements-come-along.md) | Linked elements and relations come along when a diagram moves or is imported | Accepted |
| [0162](0162-own-markdown-subset-for-replies.md) | Assistant replies are rendered by an own Markdown subset, built with DOM APIs | Accepted |
| [0163](0163-architecture-drivers-in-the-project-document.md) | Architecture drivers in the project document, prioritised like an ATAM utility tree | Accepted |
| [0164](0164-structural-views-become-project-elements.md) | Structural views become project elements when they are created | Accepted |
| [0165](0165-share-dialog-suggests-signed-in-users.md) | The share dialog suggests signed-in users while typing | Accepted |
| [0166](0166-decision-support-in-the-project-document.md) | Stakeholders, scenarios and decisions live in the project document | Accepted |
| [0167](0167-analyses-are-kept-only-as-decisions.md) | An analysis is kept only as the decision it led to | Accepted |
| [0168](0168-accepted-decisions-bind-by-prompt.md) | Accepted decisions bind the assistants and the reviews by prompt | Accepted |
| [0169](0169-kind-roles-traits-and-node-override.md) | Kinds get a role and traits; a node property overrides them | Accepted |
| [0170](0170-mcp-endpoint-for-agents.md) | Agents reach Arcviz through an MCP endpoint | Accepted |
| [0171](0171-code-links-on-project-elements.md) | Code links live on project elements | Accepted |
| [0172](0172-agent-changes-are-stored-proposals.md) | Agents change structure only through stored proposals | Accepted |
| [0173](0173-agent-credentials-pat-and-oidc-on-mcp-only.md) | Agents authenticate with a PAT or an OIDC token, only on the MCP endpoint | Accepted |
| [0174](0174-onboarding-opt-in-tour-and-starters.md) | The tour is opt-in and the landing is the empty project | Accepted |
| [0175](0175-release-highlights-curated-banner.md) | Release highlights are a curated file, shown as a dismissible banner | Accepted |
| [0176](0176-menu-bar-and-personal-settings-dialog.md) | A menu bar for diagram actions and a personal settings dialog behind the user menu | Accepted |
| [0177](0177-typed-node-and-edge-fields-are-stored-as-properties.md) | Typed node and edge fields are stored as properties | Accepted |
| [0178](0178-state-machines-simple-and-composite-states.md) | State machines: simple and composite states, regions as containers | Accepted |
| [0179](0179-class-members-are-node-fields.md) | Class members are node fields, not child nodes | Accepted |
| [0180](0180-use-cases-subject-container-and-specification-note.md) | Use cases: the subject is a container, the specification is the note | Accepted |
| [0181](0181-component-diagrams-ports-interfaces-and-relation-links.md) | Component diagrams: ports are node fields, interfaces are nodes, relation links come second | Accepted |
| [0182](0182-which-new-view-elements-link-to-the-project-model.md) | Which new view elements link to the project model | Accepted |
| [0183](0183-graph-types-behind-one-profile-table.md) | Graph types sit behind one profile table | Accepted |
| [0184](0184-new-graph-types-render-as-mermaid-flowcharts.md) | The new graph types render as Mermaid flowcharts | Accepted |
| [0185](0185-kind-names-may-be-shared-across-types.md) | Kind names may be shared across diagram types | Accepted |
| [0186](0186-agents-add-and-edit-but-never-decide.md) | Agents add and edit staging data directly; binding architecture objects only through proposals | Accepted |
| [0187](0187-project-proposals-with-items-and-partial-apply.md) | Project proposals carry independent items that a person applies in part | Accepted |
| [0188](0188-requirements-are-their-own-object-in-their-own-document.md) | Requirements are their own object, kept in their own keyed document | Accepted |
| [0189](0189-traceability-refs-on-the-architecture-objects.md) | Traceability is stored on drivers, scenarios and decisions; coverage is computed | Accepted |
| [0190](0190-refinement-is-a-suggestion-that-a-person-accepts.md) | A refinement is a suggestion stored on the requirement; only a person accepts it | Accepted |
| [0191](0191-elaboration-as-a-main-view-with-table-and-chat.md) | The elaboration mode is a main view with a requirements table and a scoped chat | Accepted |
| [0192](0192-bulk-steps-are-chunked-and-client-driven.md) | Import is deterministic; refinement and derivation run in chunks driven by the client | Accepted |
| [0193](0193-requirements-chat-transcript-in-its-own-document.md) | The requirements chat keeps its transcript in its own keyed document | Accepted |
| [0194](0194-architecture-as-a-hash-routed-main-view.md) | The Architektur tool is a main view addressed by hash routes | Accepted |
| [0195](0195-remove-serviette-and-codex-skins.md) | Remove the Serviette and Codex skins | Accepted |
| [0196](0196-master-detail-editor-and-component-layer.md) | Master/detail with one editor per tab and a shared component layer | Accepted |
| [0197](0197-palette-registry-executes-existing-controls.md) | One palette registry of declarative commands, executed through the existing controls | Accepted |
| [0198](0198-graded-deletion-retire-before-delete.md) | Graded deletion: requirements delete directly, drivers, scenarios and decisions are retired first | Accepted |
| [0199](0199-german-source-text-as-message-key.md) | The German source text is the message key; English lives in catalogs beside it | Accepted |
| [0200](0200-deployment-lives-in-a-separate-repository.md) | Deployment lives in a separate repository that pulls the published image | Accepted |
| [0201](0201-documentation-on-github-pages.md) | The documentation is also published on GitHub Pages | Accepted |
| [0202](0202-iso25010-characteristics-on-scenarios.md) | ISO/IEC 25010 characteristics are stored on scenarios, suggested by the model and chosen by a person | Accepted |
| [0203](0203-vision-is-a-structured-project-object.md) | The vision is a structured object of the project document | Accepted |
| [0204](0204-steward-runs-on-change-with-a-lease.md) | The steward runs in process, on change, with a lease, in the system context | Accepted |
| [0205](0205-independent-verifier-model.md) | A different model verifies what the steward proposes | Accepted |
| [0206](0206-project-proposals-carry-fix-operations.md) | Project proposals carry the operations that fix findings | Accepted |
| [0207](0207-arc42-assembled-deterministically-and-rendered-on-the-server.md) | arc42 is assembled deterministically and rendered on the server | Accepted |
| [0208](0208-arc42-prose-for-unmodelled-sections.md) | Prose for the sections the model does not cover | Accepted |
| [0209](0209-adr-assumptions-stored-and-suggested.md) | Assumptions and a falsification criterion are stored on the decision, suggested by the model and confirmed by a person | Accepted |
| [0210](0210-adr-export-follows-madr.md) | The exported ADR follows MADR 4 | Accepted |
