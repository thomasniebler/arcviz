# Agent access: an MCP endpoint, code links and stored proposals

- Status: Implemented
- Issue: #237
- Credentials: [Agent authentication](agent-authentication.md) (ADR-0173)
- ADRs: [0170 Agents reach Arcviz through an MCP endpoint](../adr/0170-mcp-endpoint-for-agents.md),
  [0171 Code links live on project elements](../adr/0171-code-links-on-project-elements.md),
  [0172 Agents change structure only through stored proposals](../adr/0172-agent-changes-are-stored-proposals.md)
- Builds on: [Shared project model](shared-project-model.md), [Model relations](model-relations.md),
  [Propose and confirm](propose-confirm.md), [Personal workspaces](personal-workspaces.md),
  [Authentication](authentication.md), [Application services](application-services.md)

## Summary

Arcviz today serves people: a chat turns a message into operations on a diagram. A coding agent working on the code of
the system has no way to ask "which components exist, what are they responsible for, what do they talk to, and where
is the code of this one?", and the model does not know where the code of a component lives.

This change adds three things:

1. **Code links** on the shared project elements: an element lists the repository paths (and optionally symbols) that
   implement it. Free text; Arcviz never reads a repository.
2. **An MCP endpoint** (`/mcp`, Streamable HTTP) with a small tool set: read the components, their responsibilities,
   relations, views, code links, findings and the architecture context; find the component that owns a path; create
   and remove code links.
3. **Stored proposals**: an agent may propose structural changes to a diagram. A proposal is saved, shown to people in
   the UI with its diff, and applied or discarded by a person. Agents never write diagram structure directly.

## Goals

- A coding agent can answer "what is this component, what does it depend on, who calls it, which code is it" and "which
  component owns `src/orders/api.py`" with one or two tool calls, in a compact, token-bounded form.
- Code links are entered by humans, the chat assistants and agents, and survive renames and moves of the element.
- Agents propose changes; people decide. The existing validation (`operations.apply`, internal operations refused,
  fingerprints) applies unchanged.
- The same access rules as the UI: an agent sees exactly what its identity may see, and 404s for the rest.
- Opt-in, off by default; no new mandatory dependency.

## Non-goals

- Connecting to a repository (clone, file tree, symbol resolution, drift detection). `repo` in a link is only a label;
  a later Git integration builds on the same field.
- An Agent2Agent endpoint (ADR-0170: it can be added later over the same services).
- Credentials: see [Agent authentication](agent-authentication.md) (PATs and OIDC bearer tokens, valid only on `/mcp`).
- Letting an agent apply its own proposal, or write diagram structure, over MCP.
- MCP resources, prompts or sampling. Tools only.

## Design

### Code links on project elements (`project_model.py`, `projects.py`)

```python
class CodeRef(BaseModel):
    path: str  # repository-relative POSIX path of a file or directory, no leading "/", no "..", no "\\"
    repo: str = ""  # optional label of the repository ("backend"); empty = the project's only repository
    symbol: str = ""  # optional: class, function or module name inside the path
    note: str = ""  # optional, <= MAX_CODE_REF_NOTE (200)
```

`Element.code_refs: list[CodeRef]` (omitted from dumps while empty, so stored data and fingerprints are unchanged;
`tests/test_compat_v040.py` and a new v0.5 fixture check it). Bounds: `MAX_CODE_REFS = 20` per element, `path` ≤ 200,
`repo` and `symbol` ≤ 64; the arithmetic is added to the size budget of the project item. A path may appear once per
(repo, symbol).

Public project operations (they touch only the project document, so unlike `link_element` they need no ordered
cross-document write): `add_code_ref(element, path, repo?, symbol?, note?)`, `remove_code_ref(element, path, repo?,
symbol?)` in `projects.CODE_REF_OPERATIONS`, added to `projects.apply`'s signature checks. `remove_element` drops the
links with the element; `update_element` never touches them.

`project_model.owner_of_path(elements, path, repo=None)` returns the elements whose link is the longest prefix of
`path` (a link to a directory owns everything below it; ties keep all). Pure, linear in links, tested.

REST: `GET/POST /api/projects/{pid}/elements/{eid}/code-refs`, `DELETE` with the same body fields; bundles become
`schema_version` 6 when any element has links. UI: the **Modell** panel lists an element's links and lets an editor add
and remove them, and the inspector's **Projektelement** section shows them (a follow-up issue if the milestone is
tight). The chat assistants get `add_code_ref`/`remove_code_ref` tools and `prompts/shared/model.txt` names the
feature; the elements summary shows the number of links, not the links (token budget).

### The MCP endpoint (`arcviz/mcp_server.py`)

- Mounted at `ARCVIZ_MCP_PATH` (default `/mcp`) only when `ARCVIZ_MCP=1` (default `0`). Streamable HTTP in stateless
  mode (every request stands alone; no session state to replicate; works with several replicas).
- The official `mcp` Python SDK is an optional extra (`pip install "arcviz-agent[mcp]"`, in the image's
  `ARCVIZ_EXTRAS`); `create_app` checks it at start-up like the LLM SDKs (`llm.check_provider` pattern) and stops with a
  clear message when `ARCVIZ_MCP=1` and it is missing.
- It sits **behind** `AuthMiddleware` and the limits middleware: the caller's `Identity` is read from the request scope
  and every tool runs on `ServiceRoot.for_actor(identity)`; the tools hold no store reference. Authorization stays in
  the services (CLAUDE.md: never in routes). `/mcp` is not public.
- Rate class `agent` in `limits.py` (default like `review`), body size limit as for REST.
- Observability: one span `arcviz.mcp.tool` and the metric `arcviz.mcp.calls` with attribute `arcviz.mcp.tool` (tool
  name) and outcome only; logs by route template and tool name, never arguments or results.

### Tools

All results are JSON with stable keys, bounded (`limit`, `truncated: true` and a `next` hint), and written for an
LLM: errors are `isError` results that name the known ids, like the operation errors.

| Tool | Access | Result |
|---|---|---|
| `list_projects` | viewer | id, name, role, counts |
| `get_architecture_context(project)` | viewer | the project's drivers (ranked), accepted decisions, stakeholders in the same compact form the chat prompts use |
| `list_components(project, kind?, query?, limit?)` | viewer | id, name, kind, one-line description, parent, number of code links |
| `get_component(project, component)` | viewer | name, kind, **description (the responsibilities)**, parent and children, **relations in and out with the neighbour's id, name and kind** (kind, protocol, port, mode, description), the views it appears in (diagram id, title, node id), its code links, and the current findings that name it |
| `find_component_by_path(project, path, repo?)` | viewer | the owning element(s) by longest link prefix, with the same short form as `list_components` |
| `list_findings(project, severity?, limit?)` | viewer | the deterministic project checks (`run_project_checks`, no LLM call) |
| `add_code_ref`, `remove_code_ref` | editor | the element's links after the change |
| `propose_change(project, diagram, operations, rationale)` | editor | the stored proposal: id, status, the diff, the problems if rejected |
| `list_proposals(project, status?)`, `get_proposal(project, proposal)` | viewer | status and diff of proposals, so the agent learns whether its proposal was applied |

A *responsibility* is the element's `description` (the shared model already has it; the prompts tell the assistants to
keep it to what the element is responsible for). No new field. The model panel's wording changes to
**Zuständigkeit** where it says description (follow-up, UI only).

### Stored proposals (`arcviz/proposal.py`, `services/proposals.py`, store documents)

Today a proposal is held by the client and sent back to confirm. An agent's proposal must outlive the call, so it is
stored in the keyed document of the new kind `proposal`, **one per project** (key = project id; `DOCUMENT_KINDS`: file
`proposals/`, sqlite table `documents`, Cosmos partition `proposal:<key>`; no new store primitive), so that listing is one
read and never a cross-partition query. The document is `{"seq": n, "proposals": [...]}`; a proposal is:

```json
{"id": "p<n>", "project": "...", "diagram": "...", "author": "<identity id>", "author_name": "...", "client": "mcp",
 "created_at": "...", "rationale": "<= 2000 chars", "operations": [{"op": "...", "arguments": {}}],
 "base": "<fingerprint>", "result": "<fingerprint>", "diff": {}, "status": "pending|applied|discarded|stale",
 "decided_by": null, "decided_at": null, "version": null}
```

`ProposalService` (`services/proposals.py`), every method guarded:

- `create(project, diagram, operations, rationale, actor)`: editor of the project; the diagram must belong to the
  project; at most 50 operations (`proposals.MAX_OPERATIONS`), at most 16 KB of operations and diff, internal operations refused
  exactly as in tool calls; dry run on a copy through `operations.apply` (a failing operation rejects the whole proposal with the
  operation error, nothing is stored); stores base and result fingerprints and the `diagram_diff`; at most
  `MAX_PENDING` (30) pending per project, else an error asking to wait for a decision. No live event: the UI reads the
  list when a project is shown, every 30 seconds and after a decision.
- `list`, `get`: viewer.
- `apply(project, proposal, actor)` (editor; **REST only**) replays through the existing `apply_proposal` (with the
  adopter, so views become elements as for any write): 409 when the diagram changed (`status: stale`), else one version
  noted with the proposal id and its author, status `applied`.
- `discard(project, proposal, actor)` (editor; REST only), status `discarded`. Decided proposals stay for listing and
  are dropped by the next write once older than `ARCVIZ_PROPOSAL_RETENTION_DAYS` (default 30), and all but the newest 20
  (`KEEP_DECIDED`) go; a read never writes, and leaves expired ones out of its answer.

REST: `GET /api/projects/{pid}/proposals`, `GET …/{id}`, `POST …/{id}/apply`, `POST …/{id}/discard`; all take `svc: Svc`
and are added to `tests/test_access_matrix.py`. UI: a **Vorschläge** list in the project navigator (count badge) opening the same diff presentation as the propose mode of the chat, with **Übernehmen** and **Verwerfen**.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_MCP` | `0` | `1` mounts the MCP endpoint (needs the `mcp` extra) |
| `ARCVIZ_MCP_PATH` | `/mcp` | mount path |
| `ARCVIZ_PROPOSAL_RETENTION_DAYS` | `30` | how long decided proposals stay listed |

All read through `envconfig.Env` in the subsystem's `from_env`; documented in `docs/reference/configuration.md`.

### Security notes

- Everything an agent reads is the content of the caller's own projects; the guard, not the tool, decides.
- Tool results are data for the agent: Arcviz text (names, descriptions) is user-controlled and can contain
  instructions. Documented for agent authors in `docs/guide/agents.md`; results are JSON values, never prompts.
- Credentials (PAT, OIDC) are accepted only on the MCP endpoint and REST refuses them, so a credential held by an agent
  cannot call `…/proposals/{id}/apply` or any other route: "agents propose, people decide" is enforced
  ([agent authentication](agent-authentication.md), ADR-0173). The exception is `token` mode's shared secret, which is the admin
  identity and is accepted on REST as before; use PATs for agents.

## Testing

- `tests/test_code_refs.py`: model bounds and validation, operations, round trips (dumps omit empty links,
  bundles, fingerprints unchanged), `owner_of_path` table, removal with the element; strategies extended in
  `tests/strategies.py` (CLAUDE.md: new operation → strategy and structural checks).
- `tests/test_proposals.py` (service) over all store backends via the store contract for the new document kind:
  create/list/apply/discard, stale base, internal operations refused, cap, retention sweep, access (viewer cannot
  create, other user 404), no diagram write on create.
- `tests/test_mcp.py`: tool list and schemas, every tool against a seeded project, errors name known ids, bounds
  (`truncated`), `ARCVIZ_MCP=0` answers 404, missing extra stops the start, unauthenticated 401 (token and proxy mode),
  another user's project is "not found", viewer cannot write, no tool applies a proposal. Uses the SDK's in-process
  client, no network.
- `tests/test_access_matrix.py` and `tests/test_access.py`: new routes and service methods classified;
  `tests/test_layering.py`: `mcp_server.py` imports services only.
- `tests/test_log_privacy.py` and telemetry attribute tests cover the new span and metric.
- e2e: one scripted flow: an MCP client proposes, the UI shows the proposal, a user applies it, the agent reads
  `applied` (`make e2e`, both id modes).
- Eval case: the assistant keeps `description` to responsibilities and uses `add_code_ref` only on request.

## Migration and compatibility

No migration: `code_refs` and the `proposal` documents are new and omitted while empty/absent. A downgrade is lossy:
the project models do not forbid extra fields, so an older version loads an element with `code_refs`, ignores them and
drops them on its next write (to be stated in the release notes). A bundle with links is `schema_version` 6 and older
versions refuse it with their existing message.

## Deviations from the design

The implementation differs from the first draft in these points; the text above describes what was merged.

- The tools live in `arcviz/agent_tools.py` (plain methods, tested without the protocol); `mcp_server.py` only registers
  them. Three read tools were added so that an agent can actually propose: `list_diagrams`, `get_diagram`, `list_operations`.
  `get_component` answers `responsibilities` (the element's description) and lists `parts`, `parent`, `views`, `findings`.
- The `mcp` extra is the MCP SDK 2.x **and** `pyjwt[crypto]` (one extra for the endpoint and its OIDC validation). The SDK's
  2026 protocol revision is a per-request envelope without handshake and the earlier ones a handshake: serving through the
  SDK covers both, which a hand-written endpoint would not (ADR-0170).
- Code links: `DELETE …/code-refs` takes `path`, `repo`, `symbol` as query parameters, and `GET /api/projects/{pid}/code-owners`
  was added for the path lookup. `ProjectModel` views carry `code_refs` only when an element has links.
- Rate class `agent` (`ARCVIZ_RATE_LIMIT_AGENT`), a span `arcviz.mcp.tool` and the histogram `arcviz.mcp.calls.duration`.
- The SDK's DNS-rebinding check is switched off: credentials are bearer tokens, never ambient, and the check would only block
  deployments behind a domain name.
- Review of #239: `apply` **claims** a proposal (`status: applying`, conditional on the revision) before it writes the diagram, so a
  concurrent discard or apply is refused and the status cannot contradict the diagram; an interrupted application is recognised by
  its version label and a claim older than five minutes is taken over. The proposal document carries the project's incarnation
  (ADR-0127), so a project created again under a deleted project's id starts without the old proposals. Expired tokens no longer
  count against the cap of 20.
- The replay and the diff moved from `proposal.py` into the service layer (`services/proposals.py`, `proposals.py`) because
  the service applies proposals; `proposal.py` re-exports the names.

## Open questions

All three were answered as proposed (deterministic findings only; one diagram per proposal; `repo` is a free label).


1. Should `get_component` include findings from the LLM review? Proposed: no, deterministic checks only (no cost, no
   latency).
2. Should proposals be allowed across several diagrams? Proposed: one diagram per proposal (as the project chat's
   propose mode, ADR-0060).
3. `repo` as a free label versus a registered list of repositories per project. Proposed: free label now.

## Decisions

ADR-0170..0172, accepted.
