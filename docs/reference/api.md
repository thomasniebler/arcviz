# HTTP API

Everything the UI does goes through this JSON API, so it can be scripted. Authentication is off by
default; see [Authentication](#authentication) for the token and proxy modes.

Interactive API documentation is served by FastAPI at `/api/docs` (Swagger UI) and `/api/redoc`;
the schema is at `/openapi.json`.

## Language

Every user-facing message of the server (rule and review findings, error `detail` texts, templates, the login page, the
release highlights) is German or English. The language comes from the `Accept-Language` header (the UI sends the user's
choice; q-values are honoured, unsupported languages are skipped). Without the header, or without `de`/`en` in it, the
answer is German. The assistants also answer in that language. Identifiers, kinds, operation names and stored user
content are never translated. Spec: [Internationalization](../architecture/specs/internationalization.md).

## Authentication

With `ARCVIZ_AUTH=token` every request except `/healthz`, `/readyz`, `/login` and `/logout` (and
`/docs` with `ARCVIZ_AUTH_DOCS=public`) needs either `Authorization: Bearer <ARCVIZ_AUTH_TOKEN>` or the
session cookie set by the login page; such requests act as the admin identity (`ARCVIZ_ADMIN_USER`). With
`ARCVIZ_AUTH=proxy` it needs the configured identity header from the proxy (the user id; name and e-mail headers
are optional), and every request without it gets `401 {"detail": "Authentication required: no identity from the
authenticating proxy."}`, browser navigations included. In token mode otherwise:

- a browser navigation (`GET` with `Accept: text/html`, outside `/api/`) gets `303` to
  `/login?next=<path>`;
- any other request gets `401 {"detail": "Authentication required."}` with
  `WWW-Authenticate: Bearer realm="arcviz"`.

What an identity may see and change is decided per project: its own projects and those shared with it; see
[Personal workspaces and sharing](#personal-workspaces-and-sharing).

```bash
curl -H "Authorization: Bearer $ARCVIZ_AUTH_TOKEN" http://localhost:8000/api/diagrams
```

| Method and path | Purpose | Status codes |
|---|---|---|
| `GET /login?next=/path` | Token mode: the login page (a form with the token). Other modes: redirect to `next`. | 200, 303 |
| `POST /login` | Form-encoded or JSON `{"token": "...", "next": "/path"}`. Sets the HttpOnly, `SameSite=Strict` session cookie `arcviz_session` (7 days) and redirects to `next` if it is a local path, else `/`. | 303, 401 wrong token (the page again) |
| `POST /logout` | Deletes the session cookie and redirects to `/login` (token mode) or `/`. | 303 |

`GET /api/config` reports `auth: {"mode": "off" | "token" | "proxy", "user": <the proxy's user id or null>}`.

### `GET /api/me`

The caller: `{"id", "name", "email", "admin", "auth_mode", "workspace"}`; `admin` is true for the admin identity
(`ARCVIZ_AUTH=off`/`token`: always); `workspace` is the id of the caller's personal workspace. Every authenticated
request also records the caller in the user directory (id, name, e-mail, first and last day seen), which sharing
uses to find people by id or e-mail, and creates the caller's personal workspace on their first request.

### `GET /api/users?q=`

Users who have signed in before whose name, id or trusted e-mail address contains `q` (case-insensitive), for the
share dialog ([ADR-0165](../architecture/adr/0165-share-dialog-suggests-signed-in-users.md)): `{"users": [{"id", "name",
"email"}]}`, at most 10, those starting with `q` first, never the caller; `email` is null unless the sign-in proxy
vouches for it. Fewer than two characters answer an empty list. Any signed-in user may ask; the directory listing is
cached per process for 60 s.

## Limits

Request bodies, chat messages, import documents and proposals have size limits, and mutating requests
can be rate-limited per client; see [Configuration](configuration.md#request-limits). Rejections are
JSON `{"detail": "..."}`:

| Status | When | `detail` |
|---|---|---|
| 413 | body larger than `ARCVIZ_MAX_BODY_BYTES` (imports of diagrams and projects: `ARCVIZ_MAX_IMPORT_BYTES`), with or without `Content-Length` | `Request body too large (max N bytes).` |
| 413 | chat `message` longer than `ARCVIZ_MAX_CHAT_CHARS` | `Chat message too long (N characters, max M).` |
| 413 | more than 500 operations in `POST …/proposal/apply` | `Too many operations in the proposal (N, max 500).` |
| 429 | rate limit of the request's class (`chat`, `review`, `ops`) exhausted; header `Retry-After: <seconds>` | `Too many chat requests; try again in S s.` |

Nothing is processed or written for a rejected request. `GET` requests are never rate-limited.

## Documentation

### `GET /docs/`

The documentation site of the running version (user guide, reference, technical specification,
architecture documentation), as static HTML that works without network access. Responses carry
`Cache-Control: no-cache`. Unknown paths answer 404 with the site's error page. If the
running build contains no site (a development checkout without `make docs-bundle`), every path
under `/docs` answers 404 `text/plain` with instructions to build it.

## Probes

For container orchestrators and load balancers. Neither probe calls the LLM.

Neither probe needs authentication.

### `GET /healthz`

Liveness. `200 {"status": "ok"}` while the process serves requests; it does not touch the store or the provider.
`503 {"status": "fenced", "error": "…"}` once another replica declared this process dead (its writer heartbeat stood
still for a lease, #189): it places no diagrams any more, and a restart makes it a new writer incarnation.

### `GET /readyz`

Readiness. `200` when the store answers a cheap probe and the LLM provider is configured, `503`
otherwise. Every check runs, so one response lists all problems:

```json
{
  "status": "unavailable",
  "checks": {
    "store": { "ok": true },
    "provider": { "ok": false, "error": "ANTHROPIC_API_KEY is not set." },
    "writer": { "ok": true }
  }
}
```

On success `status` is `"ready"` and every check is `{"ok": true}`. "Configured" means the provider
could be created and has credentials; whether the key is accepted is only known on the first chat
request. The first probe creates the provider, so `provider_ready` in `/api/config` is `true`
afterwards. `writer` fails once another replica declared this process dead (its writer heartbeat stood still for a
lease, #189): it places no diagrams any more and must be restarted.

## Meta

### `GET /api/config`

```json
{
  "provider": "anthropic",
  "model": "claude-opus-5-5",
  "provider_ready": true,
  "provider_error": null,
  "version": "0.4.2",
  "whats_new": { "version": "0.4.1", "items": [{ "emoji": "🔗", "text": "…" }] },
  "store": "file",
  "templates": ["aks-database", "demo", "empty", "event-driven", "hub-spoke"],
  "git": { "enabled": true, "dir": "/data/git", "push": false, "remote": null, "commits": 12, "push_pending": false, "last_commit": "3f2c…", "last_error": null, "commit_error": null, "push_error": null },
  "node_kinds": ["artifact", "cluster", "..."],
  "container_kinds": ["cluster", "deployment", "..."],
  "edge_kinds": ["association", "connection", "..."],
  "directions": ["TB", "BT", "LR", "RL"],
  "diagram_types": {
    "deployment": {
      "uml": "UML 2.5.1, 19 Deployments (loosely: cloud-oriented kinds, ADR-0030)",
      "renderer": "mermaid",
      "node_kinds": ["artifact", "..."], "container_kinds": ["cluster", "..."], "edge_kinds": ["association", "..."],
      "operations": ["add_node", "...", "apply_batch"],
      "templates": ["aks-database", "demo", "event-driven", "hub-spoke", "empty"],
      "features": { "focus": true, "direction": true, "compare": true, "plantuml": true },
      "node_fields": {}, "edge_fields": {}
    }
  },
  "template_types": { "empty": null, "demo": "deployment", "...": "..." },
  "icon_packs": ["aws", "azure", "gcp"],
  "project_model": { "element_kinds": ["actor", "system", "subsystem", "component", "node", "data"], "max_elements": 500,
                     "relation_kinds": ["communicates", "deployed_on", "data_flow"], "max_relations": 500,
                     "relation_rules": { "deployed_on": { "source": ["component", "…"], "target": ["node"] }, "…": {} },
                     "relation_properties": { "communicates": ["protocol", "port", "direction", "mode"], "…": [] },
                     "property_values": { "direction": ["forward", "both", "none"], "mode": ["sync", "async"] } }
}
```

The top-level `node_kinds`, `container_kinds` and `edge_kinds` are those of deployment diagrams (kept
for older clients). `node_fields` and `edge_fields` list the typed fields of a type by kind (`{"name", "list", "max_items",
"max_length"}`); they are passed in the `fields` argument of `add_node`, `update_node`, `add_edge` and `update_edge`
(see [operations](../spec/operations.md)); both are empty for types without a graph profile. `diagram_types` describes every [diagram type](../spec/diagram-types.md): its UML
reference, renderer, kinds, operations, templates, the features it supports and type-specific
enumerations; `template_types` gives the type each template creates (`null`: any type). `icon_packs`
names the vendored icon packs; with configured icon kinds the deployment entry also has `node_icons`
(kind → `"<pack>:<icon>"`). Every type entry has `linkable`: the view kinds that may stand for a [project
element](#shared-project-model) with the element kinds they may stand for (`{"lifeline": ["component", …]}`);
`project_model` lists the element kinds and the element limit. `node_roles` and `node_traits` (kind → role, kind →
`{"redundancy": "built_in"}`, [Managed services and redundancy](../guide/elements.md#managed-services-and-redundancy)) are
empty without configured or pack kinds that have them. `mcp` is `{"enabled", "path", "oauth"}`: whether the
[MCP endpoint](#agent-access-tokens-proposals-mcp) is served, where, and whether OIDC access tokens are accepted there.

`store` names the persistence backend (`file`, `sqlite`, `cosmos`). `provider_ready` is `false` until the first chat message or `GET /readyz` created the provider. `provider_error`
carries the reason if that failed. `git` is `{"enabled": false}` unless `ARCVIZ_GIT_DIR` is set;
otherwise it reports the mirror's directory, push setting, commit count, last commit and the last
error (unconfigured remote, failed push, missing binary).

## Agent access: tokens, proposals, MCP

User guide: [Coding agents](../guide/agents.md); as implemented: [agent access](../spec/agents.md).

### `GET /api/tokens`, `POST /api/tokens`, `DELETE /api/tokens/{id}`

The caller's own personal access tokens for the MCP endpoint (any authenticated caller; in `token` mode the admin's). These
routes do not accept a personal access token themselves.

| Route | Body | Answer |
|---|---|---|
| `GET /api/tokens` | | `{"tokens": [{"id", "label", "scope", "created_at", "expires_at"}], "scopes": ["read", "write"], "max_tokens": 20, "default_days": 90, "max_days": 365}` (never a hash or a value; expired ones left out) |
| `POST /api/tokens` | `{"label" (≤ 64), "scope"? ("read" default, "write"), "expires_in_days"? (1..365, default 90)}`, unknown fields 422 | 201 `{"token": "avp_…", "record": {…}}`: the value is in this answer **only**; 400 invalid input or 20 tokens already |
| `DELETE /api/tokens/{id}` | | `{"revoked": "<id>"}`, 400 unknown id |

### Proposals stored by agents

An agent's `propose_change` is stored per project and decided here (spec: [stored proposals](../spec/agents.md#stored-proposals)).

| Route | Role | Answer |
|---|---|---|
| `GET /api/projects/{pid}/proposals?status=` | viewer | `{"project", "proposals": [{"id", "diagram", "author", "author_name", "client", "created_at", "rationale", "operations", "diff", "status" (pending, applying, applied, discarded, stale), "decided_by", "decided_at", "version", "base", "result"}]}`, newest first; 400 unknown status, 404 |
| `GET /api/projects/{pid}/proposals/{id}` | viewer | `{"proposal": {…}}`, 400 unknown id, 404 |
| `POST /api/projects/{pid}/proposals/{id}/apply` | editor | A **project** proposal (`"kind": "project"`, with `items` instead of `diagram`/`operations`/`diff`; the list shows each item's `key`, `op`, `title`, `derived_from`, `state`, `created`, `error`, and `arguments` until decided) takes an optional body `{"accept": ["key", …]}` (default every pending item) and applies those items in order; `{"proposal": …}` stays `pending` while items wait; 400 for an unknown or decided key or an item whose needed item is not selected. Diagram proposals: replays the operations on the diagram as one version (the new views become project elements); `{"proposal": {…, "status": "applied", "version"}}`; claims the proposal (`applying`) before writing, so a concurrent apply or discard is refused (400 "being applied right now"); **409** and the proposal `stale` when the diagram changed since or is gone; 400 when it is already decided |
| `POST /api/projects/{pid}/proposals/{id}/discard` | editor | `{"proposal": {…, "status": "discarded"}}`, 400 when it is already decided. A project proposal takes an optional body `{"keys": ["key", …]}` (default every pending item) and discards those items and the ones that need them |

### `POST {ARCVIZ_MCP_PATH}` (default `/mcp`)

Only with `ARCVIZ_MCP=1`. [MCP](https://modelcontextprotocol.io) over Streamable HTTP, stateless, JSON answers; any other method
answers `405` with `Allow: POST`. Credentials, only here: `Authorization: Bearer <personal access token>`, an OIDC access
token (with `ARCVIZ_MCP_OAUTH_ISSUER`) or in `token` mode the shared secret; `off` mode needs none. A missing or invalid
credential gets `401 {"detail": "Authentication required."}` with `WWW-Authenticate: Bearer realm="arcviz"`, with OIDC also
`resource_metadata="<origin>/.well-known/oauth-protected-resource<path>"` and, after a rejected token, `error="invalid_token"`.
`ARCVIZ_RATE_LIMIT_AGENT` limits it. The tools are listed in the [specification](../spec/agents.md#tools).

### `GET /.well-known/oauth-protected-resource[/mcp]`

Public, only with `ARCVIZ_MCP_OAUTH_ISSUER` ([RFC 9728](https://www.rfc-editor.org/rfc/rfc9728)):
`{"resource": "<origin>/mcp", "authorization_servers": ["<issuer>"], "bearer_methods_supported": ["header"], "scopes_supported"?: […]}`.

## Diagrams

Every diagram route needs a role on the diagram's project ([Personal workspaces and sharing](#personal-workspaces-and-sharing)):
reading, exporting, checks, the review and comparisons need viewer; every change (operations, versions,
undo/redo, chat, proposals, links) editor; deleting the diagram the project owner. Creating or importing a diagram
needs editor on the target project. A diagram the caller may not see answers 404, too low a role 403.

| Method and path | Purpose | Status codes |
|---|---|---|
| `GET /api/diagrams` | List of the diagrams the caller may see: `id`, `title`, `updated_at`, `version_count` | 200 |
| `POST /api/diagrams` | Create. Body: `{"title": "...", "id": "optional-slug", "type": "deployment", "template": "empty" \| "demo" \| …, "project": "optional", "section": "optional"}`; `type` defaults to the template's type (else `deployment`), `empty` works for every type; without `project` the diagram goes into the caller's own default project ([Personal workspaces and sharing](#personal-workspaces-and-sharing)). The new id is `<id>-<10 random base32 characters>`, `id` (default: the slug of the title) being only its prefix (ADR-0131): read the id from the response | 201, 400 unknown template, type or section, template of another type, invalid id, 404 unknown project |
| `GET /api/diagrams/{id}?focus=` | Detail (see below), optionally focused on one node | 200, 404 (also when the caller may not see it) |
| `DELETE /api/diagrams/{id}` | Delete with versions and transcript; also removes it from its project. `If-Match` (the diagram's ETag) makes it conditional (#175) | 204, 403, 404, 412 |
| `GET /api/diagrams/{id}/render?focus=` | Clean rendering (no highlight): the render payload `{"renderer", "mermaid", "svg", "mermaid_ids"}` plus `focus` | 200, 404 (diagram or focus node unknown) |
| `GET /api/diagrams/{id}/mermaid?focus=` | `{"mermaid": "flowchart TB ...", "focus": null}` (Mermaid-rendered types only) | 200, 400 not rendered with Mermaid, 404 (diagram or focus node unknown) |
| `GET /api/diagrams/{id}/export?format=json\|plantuml&version=` | Download the whole model, see [Import and export](#import-and-export) | 200, 400 unknown format or a diagram the PlantUML export refuses (nested more than 150 levels deep), 404 diagram or version |
| `POST /api/diagrams/import` | Create a new diagram from an exported document, see below | 201, 400 invalid document, id or section, 404 unknown project |

### Import and export

`GET /api/diagrams/{id}/export` returns the current state, or with `version=<n>` that version, as a
download (`Content-Disposition: attachment`):

- `format=json` (default): `application/json`, file `<id>[-v<n>].arcviz.json`:

    ```json
    { "format": "arcviz-diagram", "schema_version": 1, "exported_at": "2026-10-06T12:00:00+00:00", "diagram": { "id": "demo", "title": "...", "nodes": {}, "edges": [], "direction": "TB", "created_at": "...", "updated_at": "..." } }
    ```

    `schema_version` is 1 for deployment diagrams and 2 for the other types (whose documents
    older Arcviz versions refuse); the reader accepts both. A diagram with links to its project's model also gets
    `"model": {"elements": [...], "relations": [...]}`: the elements it refers to with their ancestors and the
    relations between them (#153, ADR-0161); every release ignores the key, so the version stays.

- `format=plantuml`: `text/plain`, file `<id>[-v<n>].puml`, a PlantUML diagram in the type's notation: a deployment
  diagram (no properties), an activity diagram in the activity syntax, or a sequence diagram (see
  [Exchange](../spec/exchange.md#plantuml)).

`POST /api/diagrams/import` takes `{"document": <exported document or bare diagram>, "id": "optional",
"title": "optional", "project": "optional", "section": "optional"}` and creates a **new** diagram with one `initial` version; the response is the
detail payload. The document is validated completely (schema, kinds, direction, parents, edges);
a 400 lists every problem and nothing is stored. Without `id` the id is derived from the
document's id; an explicit `id` must be valid. Either is only the prefix of the new id `<id>-<10 random base32
characters>` (ADR-0131), so the answer never tells whether anybody holds that id; read the id from the response. A
generated suffix at the end of the prefix (`-` and 10 base32 characters with a digit, as every id issued since M26
ends) is replaced, not extended: re-importing an export keeps one suffix (#200, ADR-0150).
A document with links brings its `model` into the target project (validated like a bundle's model; 400 lists its
problems) and stays linked: mapped, copied or, when the target cannot take it, dropped (ADR-0161); a document without
`model` drops its links (ADR-0079). Either way the answer then also carries `carried`, as the move route does, and
`warnings` when element texts of the model were shortened to the bounds (#212) or the document has no title (the
diagram is then called "Unbenanntes Diagramm").

### Focus

Every endpoint that returns a render accepts `?focus=<node id>`. The render then contains only that
node's subtree; edges crossing the boundary are kept and their outside endpoint is drawn as a
dashed *stub*. On the detail-returning endpoints an unknown focus falls back to the whole diagram
(`"focus": null`); on `/mermaid` it is a 404.

Every response and SSE event that carries a rendering has the **render payload**: `renderer`
(`"mermaid"` or `"svg"`), the source under that key (`mermaid` or `svg`; the other one is `null`) and
`mermaid_ids` (empty for SVG, whose elements carry the model id as `data-id`).

The **detail** payload is returned by most mutating calls as well:

```json
{
  "diagram": { "id": "demo", "title": "Demo deployment", "nodes": { "...": {} }, "edges": [], "created_at": "...", "updated_at": "..." },
  "access": { "role": "editor", "project": "shop", "workspace": "user_2c26b46b68ffc68ff99b453c1d304134" },
  "renderer": "mermaid",
  "mermaid": "flowchart TB\n ...",
  "svg": null,
  "mermaid_ids": { "prod-deployment": "n_prod_deployment", "...": "..." },
  "focus": null,
  "breadcrumb": [],
  "versions": [ { "version": 1, "label": "Initial state", "created_at": "...", "source": "initial", "restored_from": null, "node_count": 8, "edge_count": 6, "fingerprint": "…" } ],
  "versions_total": 1,
  "versions_more": false,
  "undo_target": 3,
  "redo_target": null,
  "chat": [ { "role": "user", "content": "..." }, { "role": "assistant", "content": "...", "operations": [], "version": 2, "error": null, "usage": { "input_tokens": 2500, "output_tokens": 42 } } ]
}
```

`versions` is the newest page of the history (`ARCVIZ_VERSIONS_PAGE`, default 50, ascending); `versions_total` counts
every stored version and `versions_more` says whether older ones exist; fetch them with
`GET /api/diagrams/{id}/versions?before=<oldest shown>`. `undo_target` and `redo_target` always refer to the whole
history (#114).

## Personal workspaces and sharing

Design: [Personal workspaces and direct sharing](../architecture/specs/personal-workspaces.md); user guide:
[Own and shared projects](../guide/workspaces.md). Every user has exactly one **personal workspace**, created on
their first request (id `user_` + 32 hex digits derived from the user id). Every project is in its owner's personal
workspace; nobody else sees it unless the owner shares it. Everything a caller cannot see answers `404` as if it did
not exist, and an action their role does not allow answers `403`.

| Method and path | Who | Purpose | Status codes |
|---|---|---|---|
| `GET /api/workspace` | anyone | The caller's personal workspace: `{"id", "title", "owner": {"id", "name", "email"}, "project_count" (own), "shared_count" (shared with the caller), "created_at"}` | 200 |

**Roles.** `owner`, `editor` and `viewer`, each including the ones before it. The owner of the project's workspace
is its owner; a **share** gives its role; nobody else has access (there is no default role). Viewers read, export
and review; editors change content, sections, the model and the chats; owners also share the project, take
diagrams out of it, and delete it or its diagrams. A project has exactly one owner; a share is `viewer` or
`editor`. Every user also has an own default project (*Standardprojekt*, id `home_` + 32 hex digits), created on
their first request; diagrams created without a project go there.

| Method and path | Who | Purpose | Status codes |
|---|---|---|---|
| `GET /api/projects/{pid}/access` | viewer | Access view (below): every share for an owner, only the caller's own for anybody else | 200, 404 |
| `POST /api/projects/{pid}/access` | project owner | Body `{"user", "role": "viewer" \| "editor"}`: share with a user who has signed in to Arcviz once, by exact id or by the e-mail address from the proxy's e-mail header (there is no user search); sharing again changes the role. The view carries `added: {"user", "name"}`: what was typed and the display name it resolved to. Rate class `share` | 200 access view, 400 a reference that identifies nobody exactly (one message for unknown, untrusted and ambiguous), the owner, or a role other than viewer/editor, 403, 404, 429 |
| `DELETE /api/projects/{pid}/access/{user}` | project owner | Revoke the user's share (none: no change); their live streams end with `gone`. Rate class `share` | 200 access view, 400 the owner, 403, 404, 429 |

Access view (for a recipient, `owner` is `{"id", "name"}` only: the e-mail address is shown to the owner alone):

```json
{"project": "shop", "owner": {"id": "ada", "name": "Ada Lovelace", "email": "ada@example.org"}, "role": "owner",
 "roles": ["viewer", "editor"],
 "shares": [{"id": "bob", "name": "Bob", "role": "editor"}]}
```

Removed in M21 (breaking): `GET/POST /api/workspaces`, `GET/PATCH/DELETE /api/workspaces/{wid}`,
`POST /api/workspaces/{wid}/members`, `DELETE /api/workspaces/{wid}/members/{user}`, `POST /api/workspaces/{wid}/owner`,
`PUT /api/projects/{pid}/access/{user}` and `POST /api/projects/{pid}/workspace`; the `workspace` query parameter
of `GET /api/projects` and the `workspace` field of `POST /api/projects` and `POST /api/projects/import` are
ignored.

## Projects

Design: [Projects](../architecture/specs/projects.md). A project groups diagrams in a tree of
sections; every diagram belongs to exactly one project. Project writes are rate-limited in the `ops`
class and need authentication when it is on, like every other `/api` write. Reading a project (view, export,
diagrams, model, checks, review, chat) needs viewer, changing it (title, sections, operations, the model, the
project chat, moves) editor, deleting it the project owner; anyone may create or import a project, which goes into
their own personal workspace. Lists contain only what the caller may see.

| Method and path | Purpose | Status codes |
|---|---|---|
| `GET /api/projects` | List `{"id", "title", "description", "diagram_count", "updated_at", "workspace", "role", "owner": {"id", "name", "email"}, "shared"}` of the caller's own projects (`shared` false) and those shared with them (`shared` true; the owner as `{"id", "name"}`, without the e-mail address). Repairs nothing: the membership repair runs at start-up and with `arcviz-migrate --projects` (diagrams from before projects go into the reserved project `default`, "Standardprojekt", which belongs to the admin); every user also sees their own default project | 200 |
| `POST /api/projects` | Create in the caller's personal workspace. Body `{"title", "id"?, "description"?}`; the id is `<id or title slug>-<10 random base32 characters>` (ADR-0131), whether or not anybody holds the prefix; read it from the response | 201 project view, 400 empty title or invalid id |
| `GET /api/projects/{pid}` | Project view (below), with `ETag: "<project revision>"` | 200, 404 |
| `PATCH /api/projects/{pid}` | Body `{"title"?, "description"?}` | 200 project view, 400 empty title, 404 |
| `DELETE /api/projects/{pid}` | Delete an **empty** project (owner). `If-Match` (the project's ETag or `revision`) makes it conditional (#175) | 204, 403, 404, 409 while it has diagrams, 412 |
| `POST /api/projects/{pid}/ops` | One project operation `{"op", "args"}` (see [Projects: operations](../spec/projects.md#operations)); `add_diagram`/`remove_diagram` and the element operations are refused | 200 `{"result", …project view}`, 400 invalid or internal operation, 404 |
| `POST /api/projects/{pid}/sections` | Body `{"title", "parent"?, "position"?, "id"?}` | 201 `{"section", …project view}`, 400, 404 |
| `PATCH /api/projects/{pid}/sections/{sid}` | Body `{"title"?, "parent"?, "position"?, "element"?}`; `parent` or `position` present means move (`"parent": null` = top level; only `position` keeps the parent); `element` present links the section to a [project element](#shared-project-model) (its title follows the element; `null` unlinks), applied first | 200 project view, 400 unknown section or element, move into itself or a subsection, a new title for a linked section, 404 |
| `DELETE /api/projects/{pid}/sections/{sid}` | Remove the section; its subsections and diagrams move into its parent | 200 project view, 400 unknown section, 404 |
| `GET /api/projects/{pid}/diagrams` | The members as in the project view's `diagrams` | 200, 404 |
| `GET /api/projects/{pid}/export` | Download the project bundle (below), `Content-Disposition: attachment; filename="<pid>.arcviz-project.json"` | 200, 404 |
| `POST /api/projects/import` | Body `{"bundle": <bundle>, "title"?}`: create a **new** project from a bundle in the caller's personal workspace. Element names over 100 and descriptions over 300 characters are shortened (ending in `…`) | 201 project view plus `warnings` (list of strings, e.g. how many element texts were shortened; empty when nothing changed), 400 `Invalid bundle: …` (every problem listed, nothing created; also when the project document would exceed 1.9 MB) |
| `POST /api/projects/{pid}/diagrams/{did}/move` | Body `{"project"?, "section"?, "position"?}`: within the project (only `position`: same section; editor) or into another project (owner of `pid`, editor on the target) | 200 view of the target project; into another project also `carried`: `{"elements": {"copied", "mapped", "dropped"}, "relations": {…}}` with `{id, as}` / `{id, reason}` entries ([what comes along](../spec/projects.md#shared-model), ADR-0161), 400 unknown section or negative position, 403 role too low, 404 diagram not in `pid` or unknown target project |

Project view:

```json
{
  "project": {"id": "shop", "title": "Shop", "description": "", "sections": [{"id": "backend", "title": "Backend", "parent": null}],
              "diagrams": [{"diagram": "orders", "section": "backend", "added_at": "..."}], "created_at": "...", "updated_at": "..."},
  "tree": {"diagrams": [], "sections": [{"id": "backend", "title": "Backend", "diagrams": ["orders"], "sections": []}]},
  "diagrams": [{"id": "orders", "title": "Orders", "updated_at": "...", "version_count": 3, "section": "backend"}]
}
```

A **project bundle** (design: [Export and import of whole projects](../architecture/specs/project-exchange.md)):

```json
{ "format": "arcviz-project", "schema_version": 1, "exported_at": "...",
  "project": { "id": "shop", "title": "Shop", "description": "", "sections": [{"id": "backend", "title": "Backend", "parent": null}],
               "diagrams": [{"diagram": "orders", "section": "backend"}] },
  "diagrams": [ { "format": "arcviz-diagram", "schema_version": 1, "exported_at": "...", "diagram": { "id": "orders", "...": "..." } } ] }
```

The diagrams are ordinary [diagram documents](#import-and-export) in membership order; versions and
chats are not included. The import validates everything first, then creates the project with a new id
(`<title slug>-<random suffix>`, ADR-0131), its sections, and every diagram with a new id prefixed by its
document id, in order; if a write fails, what was created is removed again.

The project view also has `"revision"`: the project's revision (its ETag, for `If-Match` on `DELETE`), `"role"`: the
caller's role on the project (`owner`, `editor` or `viewer`), and
`project.workspace` names the personal workspace that holds it; `project` never carries the shares (`roles`): they
are only in the access view. A diagram detail's `access` names the caller's role,
the project and the workspace of the diagram; the UI is read-only for `viewer`.

`tree` nests sections in their order with the ids of their diagrams; `diagrams` lists the members
whose diagram exists, in membership order, with the fields of `GET /api/diagrams` plus `section`.
`position` is a zero-based index among the target's siblings (omitted or too large: at the end).

A project with a shared model exports `project.elements` (and `sections[].element`) as `schema_version` 2,
and with relations also `project.relations` (and `project.relation_seq`, the highest generated `r<n>`, so ids are never
issued again after an import) as `schema_version` 3, and with architecture drivers also `project.drivers` and
`project.driver_seq` as `schema_version` 4 (5 with decision support, 6 when an element has code links, 7 with requirements or links to them and to stakeholders, 8 with ISO 25010 characteristics on scenarios, 9 with a project vision; the arc42 notes are not part of a bundle); without a model the bundle stays version 1. The import creates the elements with their ids, so the links inside
the diagram documents stay valid.

## Decision support: stakeholders, scenarios, decisions

Design: [Decision support](../architecture/specs/decision-support.md) (M29). Every list lives in the project document;
writes need editor, reads viewer. Bodies are the arguments of the project operations; unknown fields answer 400.

| Method and path | Purpose | Status codes |
|---|---|---|
| `GET /api/projects/{pid}/stakeholders` | `{"project", "stakeholders"}` | 200, 404 |
| `POST /api/projects/{pid}/stakeholders` | Body `{"name", "role"?, "concerns"?, "influence"? (high/medium/low), "drivers"?}` | 201 `{"stakeholder", "project", "stakeholders"}`, 400, 403, 404 |
| `PATCH`, `DELETE /api/projects/{pid}/stakeholders/{id}` | Change (any field) or remove | 200 `{"stakeholder", …}`, 400, 403, 404 |
| `GET /api/projects/{pid}/scenarios` | `{"project", "scenarios"}` | 200, 404 |
| `POST /api/projects/{pid}/scenarios` | Body `{"title", "kind"? (usage, growth, change, failure, threat, cost, other), "source"?, "stimulus"?, "environment"?, "artifact"?, "response"?, "response_measure"? (each ≤ 500), "driver"?, "core"?, "elements"?, "qualities"? (one, at most two of `functional_suitability`, `performance_efficiency`, `compatibility`, `interaction_capability`, `reliability`, `security`, `maintainability`, `flexibility`, `safety`)}`; a core scenario needs a driver | 201 `{"scenario", …}`, 400, 403, 404 |
| `PATCH`, `DELETE /api/projects/{pid}/scenarios/{id}` | Change (`"driver": ""` unassigns; `"status": "retired"`/`"active"`) or remove (decisions from it stay); only a retired scenario can be removed ([graded deletion](../architecture/specs/graded-deletion.md)) | 200 `{"scenario", …}`, 400, 403, 404, 409 (DELETE of an active scenario) |
| `POST /api/projects/{pid}/scenarios/qualities` | Suggest the ISO 25010 characteristics of the scenario text in the body (`title`, `kind`, the six parts): one model call, nothing stored, invalid names dropped; rate class `review`. `{"qualities", "reason", "llm": {"ok", "error", "dropped", "usage"}}` | 200 (also when the call failed: `llm.ok` false), 400 (no text), 404 |
| `POST /api/projects/{pid}/scenarios/{id}/analysis` | The what-if analysis: one model call, nothing stored; rate class `review`. `{"project", "scenario", "summary", "drivers": [{"driver", "effect", "reason"}], "elements": [{"element", "reason"}], "sensitivity_points", "tradeoffs", "options": [{"title", "description", "pros", "cons", "instruction"?}], "recommended", "llm": {"ok", "error", "dropped", "usage"}}` | 200 (also when the call failed: `llm.ok` false), 404 |
| `GET /api/projects/{pid}/decisions` | `{"project", "decisions"}` | 200, 404 |
| `POST /api/projects/{pid}/decisions` | Body `{"title", "status"? (draft, accepted, superseded, rejected), "context"?, "options"? ([{title, description, pros, cons}], ≤ 5), "chosen"?, "decision"?, "consequences"?, "scenario"?, "drivers"?, "elements"?, "superseded_by"?}` | 201 `{"decision", …}`, 400, 403, 404 |
| `PATCH /api/projects/{pid}/decisions/{id}` | Any field; the decision text is `text`; `chosen: -1` clears the choice; `accepted` sets `decided_at`; `superseded` needs `superseded_by` | 200 `{"decision", …}`, 400, 403, 404 |
| `DELETE /api/projects/{pid}/decisions/{id}` | Remove (a decision it superseded becomes rejected); an `accepted` decision must be rejected or superseded first | 200, 400, 403, 404, 409 (accepted) |
| `GET /api/projects/{pid}/decisions/{id}/markdown` | The ADR as `text/markdown` (download `adr-<n>.md`) | 200, 404 |

Bundles with any of the three lists are `schema_version` 5.

## Shared project model

Design: [Shared project model](../architecture/specs/shared-project-model.md); as implemented:
[Projects: shared model](../spec/projects.md#shared-model). Project elements are actors, systems,
subsystems, components, nodes and data that several diagrams show; deployment and activity nodes and
sequence lifelines link to them through `ref`. Writes are rate-limited in the `ops` class.

| Method and path | Purpose | Status codes |
|---|---|---|
| `GET /api/projects/{pid}/model` | The model (below); reads every member diagram for the usages | 200, 404 |
| `GET /api/projects/{pid}/elements` | `{"project", "elements", "relations"}`: the elements and relations only (no usages), from the project document | 200, 404 |
| `POST /api/projects/{pid}/elements` | Body `{"name", "kind"?, "description"?, "parent"?, "id"?}` | 201 `{"element", "model"}`, 400 empty or duplicate name, name over 100 or description over 300 characters, unknown kind or parent, limit reached, project document too large (any project write that would grow it past 1.9 MB), 404 |
| `PATCH /api/projects/{pid}/elements/{eid}` | Body `{"name"?, "description"?, "kind"?, "parent"?}` (`parent` present = move, `null` = top level). A new name renames every linked node and lifeline (one version per diagram); a new kind must suit every linked view | 200 `{"element", "propagated", "failed", "model"}`, 400, 404 |
| `DELETE /api/projects/{pid}/elements/{eid}` | Unlink every view (one version per diagram; the views stay), then remove the element | 200 `{"element", "unlinked", "failed", "model"}`, 400 unknown element, 404, 409 a diagram kept changing |
| `GET /api/projects/{pid}/elements/{eid}/code-refs` | The element's code links | 200 `{"element", "code_refs": [{"path", "repo", "symbol", "note"}]}`, 400 unknown element, 404 |
| `POST /api/projects/{pid}/elements/{eid}/code-refs` | Body `{"path", "repo"?, "symbol"?, "note"?}`: link the element to repository code. `path` is repository-relative (`src/orders`, no leading `/`, no `..`); the same path, repo and symbol again updates the note; at most 20 per element | 201 `{"element", "code_refs"}`, 400, 403, 404, 422 unknown field |
| `DELETE /api/projects/{pid}/elements/{eid}/code-refs?path=&repo=&symbol=` | Remove one link | 200 `{"element", "code_refs"}`, 400 no such link, 403, 404 |
| `GET /api/projects/{pid}/code-owners?path=&repo=` | The elements that own a repository path: the links with the longest matching prefix | 200 `{"path", "owners": [{"element", "code_ref"}]}` (empty for an invalid path), 404, 422 no `path` |
| `GET /api/projects/{pid}/adoption` | What **Unverknüpfte übernehmen** would do, nothing written: `{"diagrams": [{"id", "title", "linked", "created", "skipped"}], "counts": {"linked", "created", "skipped"}}` for the diagrams with unlinked structural views ([elements by default](../architecture/specs/elements-by-default.md)) | 200, 404 |
| `POST /api/projects/{pid}/adoption` | Do it: missing elements first, then one version *Projektelemente übernommen* per diagram | 200 `{"diagrams", "counts", "failed", "model"}` (`failed`: diagram ids not written), 403, 404 |
| `POST /api/projects/{pid}/model/sync` | Give every linked view its element's name again (repairs drift); writes nothing when consistent | 200 `{"changed", "failed", "model"}`, 404 |
| `POST /api/projects/{pid}/relations` | Body `{"kind", "source", "target", "properties"?, "description"?, "id"?}`: a relation between two elements ([model relations](../architecture/specs/model-relations.md)) | 201 `{"relation", "model"}`, 400 unknown kind or element, a kind rule, a property, a duplicate, limit reached, 404 |
| `PATCH /api/projects/{pid}/relations/{rid}` | Body `{"kind"?, "source"?, "target"?, "properties"?, "remove_properties"?, "description"?}` | 200 `{"relation", "unlinked", "failed", "model"}`, 400, 404 |
| `DELETE /api/projects/{pid}/relations/{rid}` | Remove the relation | 200 `{"relation", "unlinked", "failed", "model"}`, 400 unknown relation, 404 |
| `GET /project/{pid}/arc42` and `GET /project/{pid}/arc42/{section}` | The live arc42 page ([design](../architecture/specs/arc42-export.md)): the project assembled into the twelve sections (`01`…`12`) and rendered on the server as HTML (viewer; no model call, never cached). Strict `Content-Security-Policy`; Mermaid figures are drawn in the browser by the vendored Mermaid, SVG figures are images of the figure route. Headings follow `Accept-Language` | 200 `text/html`, 404 (also an unknown section) |
| `GET /api/projects/{pid}/arc42` | `{"project", "title", "sections": [{"id", "title", "markdown", "gaps", "figures"}]}`: every section as Markdown, what the model does not say, the diagram ids it shows | 200, 404 |
| `GET /api/projects/{pid}/arc42.md` | The document as one Markdown file (attachment); SVG figures link to `figures/<diagram>.svg`, Mermaid figures are fenced `mermaid` blocks | 200, 404 |
| `GET /api/projects/{pid}/arc42.zip?format=asciidoc\|markdown` | `arc42/index.adoc` (default) or `arc42/index.md`, and `arc42/figures/*.svg` for SVG-drawn diagrams (attachment) | 200, 404, 422 (unknown format) |
| `GET /api/projects/{pid}/arc42.docx` | The document as a Word file (attachment); Mermaid figures appear as their source | 200, 404, 501 naming the `docx` extra when python-docx is not installed |
| `GET /api/projects/{pid}/arc42/figures/{diagram}.svg` | The SVG rendering of a diagram of the project, sandboxed with `Content-Security-Policy: sandbox` | 200, 400 (the diagram is drawn by Mermaid), 404 |
| `PUT /api/projects/{pid}/vision` | Replace the product vision ([design](../architecture/specs/project-vision.md)): body `{"statement"?, "goals"?, "non_goals"?}` (statement ≤ 2000 characters, ≤ 10 entries of ≤ 200 characters per list); all empty clears it. Editor or owner | 200 `{"project" (with `vision`), "tree", "diagrams", "role", "revision"}`, 400, 403, 404 |
| `GET /api/projects/{pid}/drivers` | `{"project", "drivers"}`: the architecture drivers ([design](../architecture/specs/architecture-drivers.md)) in list order, each with `id`, `title`, `kind`, `importance`, `risk`, `description`, `elements` and `rank` (1 = highest priority: importance, then risk, then list order) | 200, 404 |
| `POST /api/projects/{pid}/drivers` | Body `{"title", "kind"?, "importance"?, "risk"?, "description"?, "elements"?, "position"?, "requirements"?}`; kinds `quality_goal`, `constraint`, `requirement`, `risk`; levels `high`, `medium`, `low`; `elements` ids or names of project elements | 201 `{"driver", "project", "drivers"}`, 400 empty, too long (title 120, description 1000) or duplicate title, unknown kind, level or element, more than 100 drivers, 403, 404 |
| `PATCH /api/projects/{pid}/drivers/{did}` | Body: any of the fields above; `position` moves it in the list. One write: a 400 changes nothing | 200 `{"driver", "project", "drivers"}`, 400 (also an unknown driver), 403, 404 |
| `DELETE /api/projects/{pid}/drivers/{did}` | Remove the driver; only a retired one (`PATCH` `{"status": "retired"}`, which also leaves it out of prompts, review rules and coverage; `"active"` reactivates) | 200 `{"driver", "project", "drivers"}`, 400 unknown driver, 403, 404, 409 (not retired) |
| `GET /api/projects/{pid}/requirements` | The requirements ([design](../architecture/specs/requirements.md)). Query: `status`, `kind`, `theme`, `stakeholder`, `coverage` (`uncovered`, `driver`, `scenario`, `decision`, `none`), `step` (`raw`, `suggested`, `refined`, `derived`, `rejected`, `duplicate`), `q`, `suggested`, `limit` (1 to 200, default 50), `offset`. `{"project", "requirements", "total", "digest", "dangling"}`; each requirement has `coverage` and `step` | 200, 400 unknown filter value or limit, 404 |
| `POST /api/projects/{pid}/requirements` | Body `{"raw", "source"?, "external_id"?, "theme"?, "priority"? (must, should, could, wont), "stakeholders"?, "elements"?}` | 201 `{"requirement"}`, 400 empty or too long, unknown priority, stakeholder or element, duplicate external id, more than 600, 403, 404 |
| `GET /api/projects/{pid}/requirements/{rid}` | One requirement (id, external id or unique text prefix) with `trace` (the drivers, scenarios and decisions that name it) | 200 `{"requirement"}`, 400 unknown, 404 |
| `PATCH /api/projects/{pid}/requirements/{rid}` | Body: any of `statement`, `kind`, `priority`, `measure`, `theme`, `source`, `stakeholders`, `elements`, `issues`; never `raw` | 200 `{"requirement"}`, 400, 403, 404 |
| `DELETE /api/projects/{pid}/requirements/{rid}` | Remove it and its references in drivers, scenarios and decisions | 200 `{"requirement"}`, 400, 403, 404 |
| `POST /api/projects/{pid}/requirements/accept` | Body `{"items": [{"id", "expected", "fields"?}]}`; `expected` is the `suggestion.rev` the user saw | 200 `{"accepted", "stale", "skipped"}` (a replaced suggestion is `stale` and not accepted), 403, 404, 422 |
| `POST /api/projects/{pid}/requirements/delete` | Body `{"ids"}` (at most 500): delete the requirements in one write and strip them from drivers, scenarios and decisions | 200 `{"removed", "skipped": [{"id", "reason"}]}`, 403, 404, 422 |
| `POST /api/projects/{pid}/requirements/status` | Body `{"ids", "status" (raw, refined, rejected, duplicate), "duplicate_of"?, "reason"?}` | 200 `{"changed", "skipped"}`, 403, 404 |
| `POST /api/projects/{pid}/requirements/{rid}/reject-suggestion` | Drop the suggestion | 200 `{"requirement"}`, 400, 403, 404 |
| `PATCH /api/projects/{pid}/requirements/{rid}/issues/{index}` | Body `{"resolved"}` | 200 `{"requirement"}`, 400, 403, 404 |
| `GET /api/projects/{pid}/traceability` | `{"project", "rows": [{"id", "statement", "coverage", "drivers", "scenarios", "decisions"}], "objects": {"drivers", "scenarios", "decisions"}}` for the refined requirements | 200, 404 |
| `POST /api/projects/{pid}/requirements/import` | Body `{"text", "format"? (auto, lines, markdown, csv), "source"?, "dry_run"?}` (text ≤ 1 MB, ≤ 500 requirements): lines (bullets, numbers and `REQ-12:` prefixes removed), Markdown (headings become the theme) or CSV (a `text`/`Anforderung` column; optional id, source, theme, priority, stakeholder). Idempotent by `(source, external id)` and by the same text. `{"format", "dry_run", "parsed", "added", "skipped_duplicates", "rejected", "problems"}` (rows are the source's rows); `dry_run` writes nothing | 200, 400 unknown format, 403, 404, 422 text too long |
| `POST /api/projects/{pid}/requirements/refine` | Body `{"ids"? (≤ 25), "limit"? (≤ 25)}`: one model call refines one chunk (default: the raw requirements without a suggestion, in table order) into **suggestions**; rate class `review`. `{"chunk", "refined", "rejected", "remaining", "llm": {"ok", "error", "dropped", "usage"}}`; loop until `remaining` is 0 | 200 (also when the call failed: `llm.ok` false), 400 unknown id, 403, 404 |
| `POST /api/projects/{pid}/requirements/derive` | Body `{"ids"? (≤ 30, refined only), "theme"?, "limit"? (≤ 30)}`: one model call over one chunk of refined requirements that nothing covers and no pending proposal serves; stores a **project proposal** (`client` `derive`) and writes nothing to the project; rate class `review`. `{"chunk", "proposal" (or null), "unmapped", "remaining", "llm"}` | 200, 400 a raw requirement named or five pending project proposals already, 403, 404 |
| `POST /api/projects/{pid}/requirements/chat` | Body `{"message"}`: one turn of the requirements chat as server-sent events (`text_delta`, `text`, `tool_call`, `tool_result`, `done`, `error`, `final` with `reply`, `error`, `operations` as counts, `usage`); rate class `chat` | 200 stream, 403 viewer, 404, 413 too long |
| `GET`, `DELETE /api/projects/{pid}/requirements/chat` | The transcript of that chat (`{"chat": […]}`, the newest 60 entries) / empty it | 200 / 204, 403, 404 |
| `POST /api/diagrams/{id}/link?focus=` | Body `{"view", "element"}`: the node or lifeline `view` stands for the element of the diagram's project and takes its name (a version) | 200 diagram detail plus `result`, 400 unknown view or element, a view that cannot stand for the element's kind, a diagram in no project, 404 |
| `POST /api/diagrams/{id}/unlink?focus=` | Body `{"view"}` | 200 detail plus `result`, 400 not linked, 404 |
| `POST /api/diagrams/{id}/promote?focus=` | Body `{"view", "kind"?, "description"?}`: create an element named like the view and link it | 201 `{"element", …detail, "result"}`, 400, 404 |
| `POST /api/diagrams/{id}/link-relation?focus=` | Body `{"view", "relation"}`: the edge or message `view` shows the relation of the diagram's project (a version); its ends must stand for the relation's elements. `If-Match` optional | 200 detail plus `result`, 400 unknown view or relation, a connection that cannot show it, 404, 412 |
| `POST /api/diagrams/{id}/unlink-relation?focus=` | Body `{"view"}` | 200 detail plus `result`, 400 not linked, 404, 412 |
| `POST /api/diagrams/{id}/hidden-relations?focus=` | Body `{"relation", "hidden"}` (default `true`): mark a relation of the project as intentionally left out of this diagram, or not any more (a version) | 200 detail plus `result`, 400 unknown relation, 404, 412 |

```json
{ "project": "shop",
  "elements": [ { "id": "order-service", "name": "Order Service", "kind": "component", "description": "Nimmt Bestellungen an",
                  "parent": null, "sections": [],
                  "usages": [ { "diagram": "platform", "title": "Plattform", "type": "deployment", "view": "orders", "name": "Order Service" },
                              { "diagram": "checkout", "title": "Checkout", "type": "sequence", "view": "orders", "name": "Order Service" } ] } ],
  "relations": [ { "id": "r1", "kind": "communicates", "source": "web", "target": "order-service",
                   "properties": { "protocol": "HTTPS" }, "description": "",
                   "usages": [ { "diagram": "platform", "title": "Plattform", "type": "deployment", "view": "e1", "label": "Bestellung" } ],
                   "shown": [] } ],
  "diagrams": [ { "id": "platform", "title": "Plattform", "type": "deployment", "complete": [], "hidden": [] } ] }
```

`POST /api/diagrams/{id}/ops` refuses the internal operations `link_element`, `sync_element` and
`clear_element` (400, pointing to `/link`) and `link_relation`, `sync_relation`, `clear_relation` (pointing to
`/link-relation`); `unlink_element` and `unlink_relation` are ordinary operations. Renaming a linked node
or lifeline through `update_node`/`update_lifeline` is refused (400): rename the element.

## Direct operations (no LLM)

### `POST /api/diagrams/{id}/ops?focus=`

Applies one operation with the same validation the model's tools use, saves the state and records
an `auto` version.

```json
{ "op": "add_node", "args": { "name": "prod-vnet", "kind": "vnet", "parent": "prod-deployment" }, "label": "optional version label" }
```

Response: `{"result": {...}, "version": {...}, "adoption"?, ...detail}` with the touched elements highlighted
in the rendering (`mermaid` or `svg`). In a diagram of a project, structural views the operation creates become
project elements in the same version ([elements by default](../architecture/specs/elements-by-default.md)):
`adoption` then lists `linked` (`{view, element, name}`: an existing element), `created` (`{view, element, name,
kind}`: a new one) and `skipped` (`{view, name, reason}`: left unlinked); it is omitted when nothing was adopted. Validation failures return 400 with the message in `detail`
and write nothing: an unknown operation, an argument of the wrong JSON type, a missing or unknown
argument (`Invalid arguments for add_node: 'name' must be a string, got a number.`), or a value the
operation rejects (unknown kind, unknown id, a containment cycle). Property values must be strings,
numbers or booleans (stored as strings); booleans such as `cascade` must be `true`/`false`.
The operations a diagram accepts depend on its [type](../spec/diagram-types.md)
(`/api/config` → `diagram_types.<type>.operations`); another type's operation is a 400. Sequence
diagrams take, for example:

```json
{ "op": "add_message", "args": { "sender": "client", "receiver": "server", "name": "login(user)", "sort": "synchCall" } }
```

`apply_batch` applies several operations as one: one save, one version, and on failure nothing is
applied and `detail` names the failing step (`Batch step 2 (move_node) failed: …`).

### Concurrent changes

Every request that changes a diagram (`/ops`, manual snapshot, restore, undo, redo, proposal confirm, a
chat turn) writes conditionally on the state it read. If another request changed the diagram in
between, the server re-reads the diagram and applies the request again (an operation is validated
against the newer state, undo undoes the newest change), up to 5 times. Only if every attempt
conflicts does it answer **409** with `detail` ending in `try again.`; nothing of that request was
written. A diagram deleted in between answers **404**. Responses are read after the write: they always
contain the request's own change and may contain changes of concurrent requests.

**Optimistic concurrency** (#86, [Collaboration](../architecture/specs/collaboration.md), ADR-0094): `GET
/api/diagrams/{id}`, `/render`, `/mermaid` and `/export` (current state) answer `ETag: "<revision>"`, and every
diagram detail carries `"revision"`. Send it back as `If-Match` on `POST …/ops`, `/link`, `/unlink`, `/versions`,
`/versions/{n}/restore`, `/undo` or `/redo` to write only if nobody changed the diagram since: otherwise the answer
is **412** `{"detail", "revision": "<current>"}` and nothing was written (no re-apply). `"…"` and a bare token are
accepted; a list matches when any of its tags is the current revision; a weak tag `W/"…"` never matches (If-Match
compares strongly, RFC 9110); `*` anywhere or an empty value mean no condition. Access is checked first
(404/403 before 412). `DELETE /api/diagrams/{id}` and `DELETE /api/projects/{pid}` honour `If-Match` too (#175,
ADR-0123): a delete on another revision answers 412 with the current one and deletes nothing; for projects the
revision is the project's (`GET /api/projects/{pid}` answers it as `ETag`, every project view as `"revision"`).
Chat turns, proposal confirmation (it has its own base fingerprint) and the other project routes ignore `If-Match`.

```json
{ "op": "apply_batch", "args": { "operations": [
  { "op": "add_node", "args": { "name": "prod-vnet", "kind": "vnet", "parent": "prod-deployment" } },
  { "op": "move_node", "args": { "id": "service-a", "parent": "prod-vnet" } }
] } }
```

Operations and their arguments:

| `op` | `args` |
|---|---|
| `add_node` | `name`, `kind`, optional `id`, `parent`, `properties` (object of strings), `description` (note) |
| `update_node` | `id`, optional `name`, `kind`, `properties`, `remove_properties` (list of keys), `description` (`""` removes it) |
| `move_node` | `id`, `parent` (container id or `null` for top level) |
| `remove_node` | `id`, optional `cascade` (default `false`) |
| `add_edge` | `source`, `target`, optional `label`, `kind` (default `connection`), `direction` (`forward`, `both`, `none`, `default`), `properties` (object of strings) |
| `update_edge` | `id`, optional `label`, `kind`, `direction` (`default` resets), `properties`, `remove_properties` (list of keys) |
| `remove_edge` | `id`, or `source` and `target` |
| `rename_diagram` | `title` |
| `set_direction` | `direction` (`TB`, `BT`, `LR`, `RL`; `TD` is accepted as `TB`) |
| `apply_batch` | `operations`: list of `{"op": ..., "args": {...}}` (1 to 50), applied in order, all or nothing |

For the node operations, `id`, `parent`, `source` and `target` accept an element id or, as a
fallback, an exact name (case-insensitive). `update_edge` and the id form of `remove_edge` take an
**edge id** (`e1`, `e2`, … as listed in `diagram.edges`); names are not resolved there.

## Live updates and presence

Spec: [Collaboration](../architecture/specs/collaboration.md) (#86, #171).

### `GET /api/diagrams/{id}/live?session=`, `GET /api/projects/{pid}/live?session=`

`text/event-stream` for a caller who may view the diagram (project); **404** otherwise, **429** over
`ARCVIZ_LIVE_MAX_STREAMS_PER_USER`, **503** over `ARCVIZ_LIVE_MAX_STREAMS` or while shutting down (both with
`Retry-After: 30`). `session` is the tab's id (`[A-Za-z0-9_-]{8,64}`; a new stream of the same caller and session
replaces the old one); without a valid one the server picks one. Each event is one `data:` line of JSON; `: keep-alive`
comments follow quiet polls.

| `type` | Fields | Meaning |
|---|---|---|
| `hello` | `session`, `diagram`, `project`, `revision`, `project_revision`, `role`, `poll_seconds` | First event |
| `presence` | `viewers` (`session`, `user`, `name`, `diagram`, `selection`), `project_viewers`, `chatting` (`user`, `name`, `diagram`, `project`) | Who is here (in this process) |
| `changed` | `diagram`, `revision`, `author`, `author_name`, `origin` (`user`, `assistant`; `null` when another replica wrote) | The diagram changed: refetch it |
| `chat` | `diagram`, `project` | A transcript changed |
| `project` | `project`, `revision` | The project changed (sections, diagrams, roles), or the diagram moved to another project |
| `access` | `role` | The caller's role changed |
| `gone` | | The diagram or project is no longer available to the caller (deleted, moved away, access lost); the stream ends |
| `bye` | `reason`: `expired`, `shutdown`, `replaced`, `error`, `stale` (its loop had not woken up for the presence TTL) | The stream ends; reconnect |

### `POST /api/diagrams/{id}/presence`

`{"session": "…", "selection": "<element id>" | null}` → **204**: the element the caller's open stream `session`
on this diagram has selected. **404** when the caller may not view the diagram, **409** without such an open stream,
**400** for a selection over 200 characters. Bodies are limited to 4 KiB; not rate-limited (throttled by the
streams).

## Versions

| Method and path | Purpose | Status codes |
|---|---|---|
| `GET /api/diagrams/{id}/versions?before=&limit=` | List of version metadata, ascending; `author` (user id) and `author_name` name who caused the version (absent for versions written before users existed, or by the system). Without parameters every version; with `before` (only older versions) and/or `limit` (1..500, default `ARCVIZ_VERSIONS_PAGE`) the newest `limit` of them (#114) | 200, 404, 422 |
| `POST /api/diagrams/{id}/versions?focus=` | Manual snapshot of the current state. Body: `{"label": "..."}` | 201, 404, 409 see [Concurrent changes](#concurrent-changes) |
| `GET /api/diagrams/{id}/versions/{n}?focus=` | `{"meta": {...}, "diagram": {...}, "mermaid": "...", "focus": null}`. A version pruned by the retention policy meanwhile answers 409 ("reload the version list"); restore, undo, redo and compare likewise | 200, 404, 409 |
| `POST /api/diagrams/{id}/versions/{n}/restore?focus=` | Make version `n` current; records a `restore` version | 200, 404, 409 see [Concurrent changes](#concurrent-changes) |
| `POST /api/diagrams/{id}/undo?focus=` | Restore the most recent different state; records an `undo` version | 200, 409 nothing to undo or [concurrent changes](#concurrent-changes) |
| `POST /api/diagrams/{id}/redo?focus=` | Re-apply the state the last undo replaced; records a `redo` version | 200, 409 nothing to redo or [concurrent changes](#concurrent-changes) |
| `GET /api/diagrams/{id}/compare?base=<n>&target=<m>&focus=` | Compare version `n` with version `m` (default: the current state), see below | 200, 404 diagram or version, 422 `base` missing |

### Comparison

`GET /compare` returns the difference and one render of it: the newer state plus the
removed elements and edges, marked with the classes `added`, `removed` and `changed` (edges via
`linkStyle`). `target: null` means the current state. The render is the type's payload (`renderer`, `mermaid` or
`svg`, `mermaid_ids`); for sequence diagrams `diff` lists interaction element ids in `added_elements`,
`removed_elements` and `changed_elements` (empty for the other types) and the SVG marks the `data-id` groups.

```json
{
  "base": { "version": 3, "label": "...", "source": "auto", "...": "..." },
  "target": null,
  "diff": {
    "added_nodes": ["prod-vnet"], "removed_nodes": ["events"], "changed_nodes": ["service-a"],
    "added_edges": [], "removed_edges": ["e4", "e5"], "changed_edges": ["e1"],
    "title_changed": false, "direction_changed": false,
    "added_elements": [], "removed_elements": [], "changed_elements": [],
    "summary": { "added": 1, "removed": 3, "changed": 2 }
  },
  "mermaid": "flowchart TB ...",
  "mermaid_ids": { "events": "n_events", "...": "..." },
  "focus": null
}
```

Nodes count as changed when name, kind, container or properties differ; edges when label, kind,
direction or properties differ. Edges are matched by id; an id reused for an edge between other elements counts as removed
and added.

## Architecture review

| Method and path | Purpose | Status codes |
|---|---|---|
| `GET /api/diagrams/{id}/checks` | Deterministic rule checks of the current diagram (no LLM), see below | 200, 404 |
| `POST /api/diagrams/{id}/review` | Rule checks plus an LLM review of the current diagram, see below | 200, 404, 503 provider unavailable |

### `GET /api/diagrams/{id}/checks`

Runs the rules registered for the diagram's type (`deployment` for every diagram today) and returns
their findings, most severe first. Nothing is written.

```json
{
  "diagram_type": "deployment",
  "rules": [{ "id": "database-without-replication", "title": "Datenbank ohne Replikation (Single Point of Failure)", "category": "risk" }],
  "findings": [
    {
      "id": "database-without-replication:orders-db", "source": "rule", "rule": "database-without-replication",
      "category": "risk", "severity": "high",
      "message": "Die Datenbank „orders-db“ hat keine Replikation und ist ein Single Point of Failure.",
      "elements": ["orders-db"], "edges": [],
      "suggestion": "Füge für die Datenbank „orders-db“ (id orders-db) eine Replik hinzu …"
    }
  ]
}
```

`category` is `strength`, `risk` or `suggestion`; `severity` is `info` (always for strengths), `low`,
`medium` or `high`. `elements` are node ids, `edges` edge ids of the current diagram. `suggestion` is
an instruction that can be sent to the chat (it is `null` for strengths). The rules are listed in
[Architecture review](../spec/review.md#rule-checks).

### `POST /api/diagrams/{id}/review`

No body. Runs the rule checks and one model call that reviews the diagram (strengths, risks,
suggestions) and returns both sets of findings in one list, sorted like the rule findings:

```json
{
  "diagram_type": "deployment",
  "summary": "Klare Struktur hinter einem Gateway; die Datenanbindung ist der Schwachpunkt.",
  "findings": [
    { "id": "database-without-replication:orders-db", "source": "rule", "...": "..." },
    {
      "id": "llm-1", "source": "llm", "rule": null, "category": "risk", "severity": "medium",
      "message": "service-a spricht ohne TLS mit orders-db.", "elements": ["service-a", "orders-db"], "edges": ["e3"],
      "suggestion": "Setze an der Kante e3 die Eigenschaft protocol=postgres+tls."
    }
  ],
  "llm": { "ok": true, "error": null, "dropped": 0, "usage": { "input_tokens": 1500, "output_tokens": 200 }, "partial": false }
}
```

The model's findings are validated: element references are resolved to node ids (unknown ones are
removed), unknown edge ids are removed, invalid findings are dropped and counted in `llm.dropped`.
If the model call fails or its answer is unusable, the response is still 200 with `llm.ok: false`,
`llm.error` naming the reason, `summary: null` and only the rule findings. Nothing is written; the
review is not stored. `llm.partial` is `true` when the diagram was too large for the prompt and
the model saw its largest containers collapsed (it is told not to judge their hidden contents).

## Chat

### `POST /api/diagrams/{id}/chat`

Body: `{"message": "...", "focus": null, "mode": "apply"}`. `focus` is the node the UI is focused on; live renders
in the stream use the same view and fall back to the whole diagram if that node disappears. `mode` is
`apply` (default: every operation is saved, the turn becomes a version) or `propose` (dry run: nothing
but the transcript is saved, the `final` event carries a `proposal` to confirm or discard, see below);
anything else is a 422.
Responds with `text/event-stream`; each event is one line
`data: <json>` followed by a blank line. Status 503 if the provider cannot be created.

| `type` | Fields | When |
|---|---|---|
| `text_delta` | `text` | A piece of the reply text while the model is still writing (streaming providers; off with `ARCVIZ_STREAM=0`). The pieces of a round are followed by that round's `text`. |
| `text` | `text` | The model produced text in a round (may happen several times). Carries the round's complete text; a client that showed the `text_delta` pieces replaces them with it. |
| `tool_call` | `id`, `name`, `arguments` | Before a tool is executed. |
| `tool_result` | `id`, `name`, `is_error`, `content`; on success also `diagram`, `mermaid` and `focus` with the intermediate state | After a tool was executed. |
| `error` | `message`, `usage` | Provider error (also a stream that broke mid-reply), refusal, cut-off reply, round limit or token budget. The turn ends; `usage` counts the model calls that did complete before the error. |
| `done` | `text`, `usage` | All rounds finished; `text` is the joined reply, `usage` the token counts summed over the turn (`input_tokens`, `output_tokens`; empty if the provider reports none). |
| `final` | `reply`, `error`, `changed` (ids), `operations` (applied calls), `version` (metadata or `null`), `diagram`, `mermaid`, `focus`, `usage`, `proposal` | The last event of a connected stream, also after an `error`. `proposal` is `null` except in `propose` mode when something was applied: `{"base", "result", "operations": [{"op", "arguments"}], "diff"}`; `diagram` and `mermaid` then show the proposed state and `version` is `null`. |

If the client disconnects mid-turn, the server still persists everything that was applied (latest
state, an automatic version, the transcript entry with an error note), but no `final` event can be
delivered. A client that reconnects should fetch `GET /api/diagrams/{id}` instead of waiting for it.

Order within one model round: `text_delta`* → `text`? → (`tool_call` → `tool_result`)*, or `error`.

```bash
curl -N -X POST http://127.0.0.1:8000/api/diagrams/demo/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "Rename orders-db to Orders database"}'
```

### `POST /api/diagrams/{id}/proposal/apply?focus=`

Confirms a proposal from a `propose` turn. Body: the `proposal` object of the `final` event (`operations`,
`base`, `result`; other fields are ignored) plus an optional `label` for the version (default
"Proposal applied"). The server checks that the diagram still has the content fingerprint `base`, replays
the operations through the same validation as `/ops`, checks that the result has the fingerprint
`result`, checks the base once more, and only then saves it as one `auto` version and appends the transcript note
"Proposal applied as version vN." (`proposal_outcome: "applied"`).

Response: the detail payload plus `version` and `changed` (touched ids; the `mermaid` highlights
them). 404 for an unknown diagram; 409 when the diagram changed since the proposal, an operation no
longer applies, or the replay does not reproduce the preview (nothing is written; ask again).

```json
{ "operations": [ { "op": "add_node", "arguments": { "name": "cache", "kind": "storage", "parent": "prod-deployment" } } ],
  "base": "3f2a9c1d0b7e4a65", "result": "9b1e77c0a2d34f18", "label": "Lege einen Cache an" }
```

The `diff` of a proposal has the shape `{"title": [old, new] | null, "nodes": {"added", "removed",
"changed"}, "edges": {"added", "removed", "changed"}}`; added/removed nodes carry `id`, `name`, `kind`,
`parent`, changed nodes `id`, `name` and `changes` (`{"field": [old, new]}`, properties as
`properties.<key>`), edges their fields or `changes` for `label`/`kind`.

### `POST /api/diagrams/{id}/proposal/discard?focus=`

Records that a proposal was discarded: appends the transcript note "Proposal discarded; the diagram was
not changed." (`proposal_outcome: "discarded"`). No body. Returns the detail payload; 404 for an unknown
diagram.

### `POST /api/diagrams/{id}/chat/compact`

Folds the older part of the transcript into one summary entry now (the agent also does this
automatically after a turn once the transcript exceeds `ARCVIZ_CHAT_COMPACT_AFTER` entries).
Runs inline; the model call takes a few seconds.

```json
{ "compacted": true, "chat": [ { "role": "summary", "seq": 42, "content": "...", "covers": { "from_seq": 1, "to_seq": 42, "turns": 21 }, "created_at": "..." }, { "role": "user", "seq": 43, "content": "..." } ] }
```

`compacted` is `false` when there is nothing to fold. 404 for an unknown diagram, 502 when the model
or the store failed (the transcript is then unchanged).

### `DELETE /api/diagrams/{id}/chat`

Clears the transcript (status 204). The versions are not affected.

## Project chat

One conversation over the diagrams of a project (design: [project chat](../architecture/specs/project-chat.md),
as implemented: [agent](../spec/agent.md#project-chat)). The transcript belongs to the project; the
per-diagram chats above are unaffected.

### `POST /api/projects/{pid}/chat`

Body: `{"message": "...", "active": "<diagram id>" | null, "focus": null, "mode": "apply" | "propose"}`.
`active` is the diagram the UI shows: the turn starts with it as the active diagram when it belongs to the
project (otherwise without one). Responds with `text/event-stream` like the diagram chat, with these
differences:

| `type` | Differences |
|---|---|
| `tool_result` | after `open_diagram`, `create_diagram` and every successful editing call also `diagram_id` plus the active diagram (`diagram`, the render payload with the turn's changes highlighted, `focus`); `list_diagrams`, `read_diagram` and `get_diagram` carry no rendering |
| `final` | additionally `active` (diagram id or `null`), `diagrams` (`[{"id", "title", "type", "version", "created", "changed", "operations"}]`, one per diagram the turn changed or created), `project_changed`, `diagram_id`; `operations` name their `diagram`; `diagram`, render payload, `changed` and `version` refer to the active diagram; `proposal` additionally carries `diagram_id` |

404 unknown project, 413 message too long, 422 empty message, 503 provider not available. In
`propose` mode `create_diagram` is refused and only one diagram can be changed per turn (tool errors).

### `GET /api/projects/{pid}/chat`, `DELETE /api/projects/{pid}/chat`

`{"chat": [...]}` with the project transcript (assistant entries also carry `diagrams` and `active`),
or clear it (204). 404 for an unknown project.

### `POST /api/projects/{pid}/chat/compact`

Like `POST /api/diagrams/{id}/chat/compact`, for the project transcript. 404 unknown project, 502 failure.

### `POST /api/projects/{pid}/proposal/apply?focus=`

Confirms the `proposal` of a project turn. Body: `{"diagram": "<proposal.diagram_id>", "operations", "base",
"result", "label"?}`. Same checks and answer as `POST /api/diagrams/{id}/proposal/apply`; the note
"Proposal applied as version vN." goes into the **project** transcript. 404 when the project is unknown
or the diagram is not a member, 409 stale proposal, 413 too many operations.

### `POST /api/projects/{pid}/proposal/discard`

Body `{"diagram"?}`. Appends "Proposal discarded; the diagram was not changed." to the project transcript
and answers `{"chat": [...]}`. 404 unknown project, or a `diagram` that is not a member.

## Project review

Design: [project review](../architecture/specs/project-review.md); as implemented:
[review](../spec/review.md#project-review). Neither route writes anything.

### `GET /api/projects/{pid}/checks`

The project rules plus the rule checks of every member diagram, without a model call:

```json
{ "project": "shop",
  "rules": [ { "id": "empty-section", "title": "Abschnitt ohne Diagramme", "category": "suggestion" } ],
  "findings": [ { "id": "empty-section:ops", "source": "rule", "rule": "empty-section", "category": "suggestion",
                  "severity": "low", "message": "Abschnitt „Betrieb“ enthält keine Diagramme.", "elements": [], "edges": [],
                  "suggestion": null, "diagram": null, "diagrams": [], "section": "ops" },
                { "id": "demo:database-without-replication:orders-db", "source": "rule", "rule": "database-without-replication", "diagram": "demo",
                  "diagrams": ["demo"], "section": null, "elements": ["orders-db"], "…": "…" } ],
  "diagrams": { "demo": { "title": "Demo deployment", "type": "deployment", "section": null } },
  "model": { "order-service": { "name": "Order Service", "kind": "component" } } }
```

Findings are those of the diagram review plus `diagram` (the diagram whose `elements`/`edges` they
name), `diagrams`, `section`, `model_element` (the [project element](#shared-project-model) they concern),
`relation` (the model relation they concern) and `resolutions`; diagram findings have the id `<diagram>:<id>`.
`model` names every project element. 404 unknown project.

`resolutions` (correspondence findings only, [model relations](../architecture/specs/model-relations.md)) lists what a
person can do; nothing is applied by the review:

```json
{ "action": "update_relation", "label": "Im Modell protocol = „gRPC“ setzen", "relation": "r1",
  "args": { "properties": { "protocol": "gRPC" } }, "diagram": null, "view": null, "model_element": null, "prompt": null }
```

| `action` | Fields | The UI sends |
|---|---|---|
| `propose` | `diagram`, `prompt` | `prompt` as a propose-mode chat turn on `diagram` |
| `hide` | `diagram`, `relation` | `POST /api/diagrams/{diagram}/hidden-relations` `{relation, hidden: true}` |
| `link_relation` | `diagram`, `view`, `relation` | `POST /api/diagrams/{diagram}/link-relation` `{view, relation}` |
| `update_relation` | `relation`, `args` | `PATCH /api/projects/{pid}/relations/{relation}` with `args` |
| `remove_relation` | `relation` | `DELETE /api/projects/{pid}/relations/{relation}` (after a confirmation) |
| `create_relation` | `args` | `POST /api/projects/{pid}/relations` with `args` |
| `move_element` | `model_element`, `args` | `PATCH /api/projects/{pid}/elements/{model_element}` with `args` (`parent`) |
| `sync` | | `POST /api/projects/{pid}/model/sync` |

### `POST /api/projects/{pid}/review`

The checks plus one LLM review of the whole project: `{project, summary, findings, diagrams, model, llm}` with
`llm = {ok, error, dropped, usage, partial}` as for the diagram review. A failed model call answers 200
with `llm.ok = false` and the rule findings. 404 unknown project, 503 provider not available; rate-limited
in the `review` class.
