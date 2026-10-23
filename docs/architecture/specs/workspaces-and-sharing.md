# Users, workspaces and project sharing

- Status: Superseded by [Personal workspaces and direct sharing](personal-workspaces.md) (M21): team workspaces,
  members, the default role and the M20 migration were replaced; identities, the user directory, the access policy
  in the services, authors and the membership repair below still apply
- Issues: #158 (identities), #159 (workspaces), #160 (project roles), #161 (authorization in the services),
  #162 (authors), #163 (migration), #164 (frontend)
- ADRs: [0080 Identities from the authenticating proxy](../adr/0080-identities-from-the-authenticating-proxy.md),
  [0081 Workspaces are teams with one owner](../adr/0081-workspaces-are-teams-with-one-owner.md),
  [0082 Workspaces and users as keyed documents; a project names its workspace](../adr/0082-workspaces-and-users-as-keyed-documents.md),
  [0083 Project roles and the default role of members](../adr/0083-project-roles-and-default-viewer.md),
  [0084 One access policy in the service layer, services bound to the caller](../adr/0084-access-policy-in-the-service-layer.md),
  [0085 Who may create, delete, invite and move](../adr/0085-role-requirements-per-action.md),
  [0086 Authors as id plus display name, omitted when unknown](../adr/0086-authors-on-versions-and-transcripts.md),
  [0087 Existing data moves into a default workspace owned by the configured admin](../adr/0087-default-workspace-migration.md)
- Builds on: [Authentication](authentication.md) (#82, ADR-0047), [Application services](application-services.md)
  (ADR-0050), [Projects](projects.md), [Project chat](project-chat.md), [Shared project model](shared-project-model.md)

## Summary

Until now every authenticated client could do everything (ADR-0047, arc42 R1). Arcviz is used by teams
inside one organisation, so M20 adds **identities**, **workspaces** and **per-project sharing**:

- Authentication stays with the authenticating proxy (`ARCVIZ_AUTH=proxy`, e.g. Azure Easy Auth with Entra ID,
  or oauth2-proxy). The proxy now provides a real identity: a stable **user id** plus display name and e-mail,
  taken from configurable headers with presets. A **user directory** is filled on first sight. `off` and `token`
  mode map every request to one configured **admin identity**, so local use keeps working.
- A **workspace** is a team and the top-level container above projects. It has exactly one **owner**, who alone
  decides who may access it (adds and removes members, transfers ownership). Every project belongs to exactly
  one workspace; users see only the workspaces they own or are a member of.
- **Projects are shared** with roles `owner`, `editor`, `viewer`. Members of the workspace get `viewer` on its
  projects unless a project owner raises (or lowers) their role. Viewers read, export and review; they cannot
  change anything.
- Authorization lives in **one access policy in the service layer**. Routes, both agents, proposals, review,
  import/export and comparison go through services bound to the caller. Resources the caller cannot see answer
  **404** (no existence leak), an insufficient role **403**.
- Every version and transcript entry records its **author**.
- Existing data moves into a **default workspace** owned by the admin identity, automatically at start-up and with
  `arcviz-migrate --workspaces`.

## Goals

- `Identity` (id, name, e-mail) for every request that reaches a route; proxy mode refuses a request without the
  identity header (401) and never redirects it.
- Workspaces and users persisted on all three backends through the ports, with conditional writes, covered by
  `tests/store_contract.py` (also against `tests/cosmos_fake.py`).
- Project roles stored in the project document; the effective role of a user is a pure function of the workspace
  and the project.
- Every service method that touches a diagram, project or workspace checks the caller; no route can reach data
  without a bound service (`tests/test_layering.py`), and a matrix test covers every endpoint × role.
- Authors on versions (`initial`, `auto`, `manual`, `restore`, `undo`, `redo`, and the versions written by model
  propagation) and on transcript entries; data written before stays valid (author unknown).
- An idempotent migration of v0.1.8 data, verified against stores written by the v0.1.8 code.
- A German UI: current user, workspace switcher, members dialog, sharing dialog, read-only mode.

## Non-goals

- OIDC in the app (the proxy does it, ADR-0047/0080); groups or claims-based roles; external guests.
- Strict multi-tenant isolation (separate stores, encryption per tenant): workspaces share one store; ids of
  diagrams and projects stay global (see *Existence of ids*).
- Per-diagram permissions (sharing is per project), a "no access" role for workspace members.
- Global administrators: the admin identity owns the default workspace and nothing else. Operators recover a
  workspace whose owner left with `arcviz-migrate --set-owner`.
- Audit log beyond the authors on versions and transcript entries.

## Design

### Identities (`arcviz/workspaces.py`, `arcviz/auth.py`) — #158

```python
@dataclass(frozen=True)
class Identity:
    id: str  # stable user id: Entra object id, oauth2-proxy user, or the admin id
    name: str  # display name (falls back to the id)
    email: str | None = None
```

`AuthConfig.from_env()` reads (besides the variables of ADR-0047):

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_AUTH_PROXY_PRESET` | unset | `easyauth` or `oauth2-proxy`: default header names (below). |
| `ARCVIZ_AUTH_PROXY_HEADER` | preset | Header with the **user id** (required in proxy mode unless a preset gives it). |
| `ARCVIZ_AUTH_PROXY_NAME_HEADER` | preset | Header with the display name (optional). |
| `ARCVIZ_AUTH_PROXY_EMAIL_HEADER` | preset | Header with the e-mail address (optional). |
| `ARCVIZ_ADMIN_USER` | `admin` in `off`/`token`, unset in `proxy` | Id of the admin identity: every request in `off`/`token` mode; the owner of the default workspace. |
| `ARCVIZ_ADMIN_NAME` | `Administrator` | Display name of the admin identity in `off`/`token` mode. |
| `ARCVIZ_ADMIN_EMAIL` | unset | E-mail of the admin identity in `off`/`token` mode. |

Presets:

| Preset | Id | Name | E-mail |
|---|---|---|---|
| `easyauth` | `X-MS-CLIENT-PRINCIPAL-ID` | claim `name` of `X-MS-CLIENT-PRINCIPAL`, else `X-MS-CLIENT-PRINCIPAL-NAME` | claim `email`/`preferred_username` of `X-MS-CLIENT-PRINCIPAL`, else `X-MS-CLIENT-PRINCIPAL-NAME` when it is an address |
| `oauth2-proxy` | `X-Forwarded-User` | `X-Forwarded-Preferred-Username` | `X-Forwarded-Email` |

`X-MS-CLIENT-PRINCIPAL` is Easy Auth's base64-encoded JSON (`{"claims": [{"typ", "val"}]}`); an undecodable
value is ignored. Header values are decoded as UTF-8 when their Latin-1 bytes are valid UTF-8 (Easy Auth passes
UTF-8 names). An id is valid when, after stripping, it has 1–256 characters and no control characters; names are
cut to 200 characters, an e-mail is kept only if it contains `@`.

`auth.identify(config, headers)` returns the `Identity` (or None). The middleware stores it as
`scope["state"]["arcviz_identity"]` and its id as `arcviz_user` (the rate limiter keys on it, ADR-0048). In
`off` and `token` mode the identity is the admin identity. In `proxy` mode a request without a valid identity
header gets **401** `{"detail": "Authentication required: no identity from the authenticating proxy."}`, also
for browser navigations (a redirect to `/login` would loop, because there is nothing to log in to).

**Trust.** The identity headers are trusted only because the app must be reachable **only through the proxy**:
an internal ingress or private network, and a proxy that overwrites (or strips) these headers on every request
(Easy Auth strips `X-MS-CLIENT-PRINCIPAL*` from client requests; oauth2-proxy sets `X-Forwarded-*` with
`--pass-user-headers`/`--set-xauthrequest`). Anyone who can reach the app directly can claim any identity.
`docs/deploy-azure.md` and the configuration reference say so.

**User directory** (`UserService`, services/workspaces.py): a `UserRecord` (`id`, `name`, `email`,
`email_trusted`, `first_seen`, `last_seen`) per identity, written when an identity is seen for the first time, when its name or
e-mail changed, or on the first request of a day (`last_seen` has day granularity); a per-process cache keeps
this to one write per user and day. The directory is keyed by `user_key(id)` (`u-` + 32 hex digits of the
SHA-256 of the id), because ids may contain any character. It is not searchable through the API: the members
dialog adds users by exact id or e-mail (ADR-0085). `seen` never raises (it runs for every request): failures are
logged, a malformed record is replaced, and only a write that happened is remembered in the cache.

**Trusted e-mail** (ADR-0091). `Identity.email_trusted` is true only when the final address is exactly the value of
the configured e-mail header (a header value without `@` replaced by the name header is not trusted) (`ARCVIZ_AUTH_PROXY_EMAIL_HEADER`, e.g. the `oauth2-proxy` preset) or `ARCVIZ_ADMIN_EMAIL`; an address
from an Easy Auth claim (`email`, `preferred_username`) or the name header is display data. Adding a member by
e-mail matches trusted addresses only; several matches are refused (400: add by user id). The response carries
`added` (id, name, e-mail) and the dialog shows it, so the owner sees exactly who was added.

`GET /api/me` → `{"id", "name", "email", "admin": bool, "auth_mode"}`. `/api/config` keeps `auth.user` (the proxy
user id, else null).

### Workspaces (`arcviz/workspaces.py`, `services/workspaces.py`) — #159

```python
class Member(BaseModel):
    user: str  # user id
    added_at: str


class Workspace(BaseModel):
    id: str  # slug; "default" is reserved for the migrated data
    title: str
    owner: str  # user id; exactly one
    members: list[Member] = []  # never contains the owner
    created_at: str
    updated_at: str
```

`workspace_role(user_id, workspace)` is `"owner"`, `"member"` or None. `WorkspaceService` (bound to the caller):

| Method | Who | Effect |
|---|---|---|
| `list()` | anyone | Workspaces the caller owns or is a member of, with the caller's role, owner, member and project counts. |
| `create(title)` | anyone | A new workspace owned by the caller; id = slug of the title, `-2`, `-3`, … when taken; `default` is never chosen. |
| `get(id)` | member | The workspace with its members resolved against the directory. |
| `rename(id, title)` | owner | |
| `delete(id)` | owner | Only an empty workspace (409 otherwise); the default workspace cannot be deleted (409). |
| `add_member(id, user)` | owner | `user` is an id, or a trusted and unambiguous e-mail (case-insensitive, ADR-0091), of a **known** user (seen at least once); 400 otherwise. Adding a member twice is a no-op. The view's `added` names who was added. |
| `remove_member(id, user)` | owner, or the member themself (leaving) | The owner cannot be removed (transfer first, 400). Afterwards the member's explicit project roles in this workspace are removed (best effort; they are inert anyway, see below). |
| `transfer(id, user)` | owner | `user` must be a member; the old owner becomes a member. |

Workspace writes are conditional read-modify-writes on the workspace revision (retried on conflicts, like
projects). Each project names its workspace in the new field `Project.workspace` (ADR-0082), so "every project in
exactly one workspace" holds by construction. `POST /api/projects/{pid}/workspace {"workspace"}` moves a project
to another workspace (owner of the current workspace and member of the target, ADR-0085); explicit roles of users
who are not members of the target are dropped.

### Project roles (`Project.roles`) — #160

`Project.roles: dict[str, Role]` maps user ids to `owner | editor | viewer`. Unset `workspace` and empty `roles` are
omitted from dumps, so v0.1.8 documents keep their JSON. The creator of a project gets `owner`.

```python
def project_role(user_id, workspace: Workspace | None, project: Project) -> Role | None:
    if workspace is None or project.workspace != workspace.id:  # unassigned or dangling: nobody
        return None
    if user_id == workspace.owner:
        return "owner"  # the workspace owner manages every project of the workspace (ADR-0083)
    if not any(m.user == user_id for m in workspace.members):
        return None  # outsiders never see it, whatever roles say (stale roles are inert)
    return project.roles.get(user_id, DEFAULT_ROLE)  # DEFAULT_ROLE = "viewer"
```

A project owner sets roles of workspace members (`PUT /api/projects/{pid}/access/{user}` `{"role"}`) or resets
them to the default (`DELETE …/access/{user}`); a user who is not a member answers 400, the workspace owner's role
cannot be changed (400). There is no "none" role: every member sees every project of the workspace.
`GET /api/projects/{pid}/access` lists the workspace owner and every member with their effective role, its source
(`workspace-owner`, `explicit`, `default`), name and e-mail, plus the caller's role.

### Authorization in the services — #161

`services/access.py`:

```python
class Forbidden(Exception): ...  # 403: visible, but the role is too low
class DiagramNotFound(StoreError): ...  # 404, same message as a missing diagram
class WorkspaceNotFound(StoreError): ...  # 404
# ProjectNotFound (existing) is the 404 of a project

class AccessPolicy:  # reads through the ports; one instance per application
    def guard(self, actor: Identity) -> Guard

class Guard:  # the policy bound to one caller (one request, one chat turn)
    actor: Identity
    def workspace(self, workspace_id, need: "member" | "owner") -> Workspace
    def project(self, project_id, need: Role) -> Project
    def project_doc(self, project, need: Role) -> Role
    def diagram(self, diagram_id, need: Role) -> Project  # the diagram's project, as just read
    def role_of(self, project) -> Role | None
```

The services take an optional `Guard` (None = system context: tests, the CLI, the start-up migration, repairs).
`ServiceRoot` (`services/context.py`) builds the repository-level pieces once and hands out
`Services(actor, diagrams, projects, models, bundles, workspaces, users)` bound to a caller with
`for_actor(identity)`; `server.py` gets the bundle through one FastAPI dependency per request and never constructs
a service. Both agents work on bound services: `DiagramAgent.bound(services)` / `ProjectAgent.bound(services)` are
shallow copies sharing the compaction executor.

Checks, by service (need):

- `DiagramService`: `list_diagrams` (visible only), `exists` (false when invisible), `get`, `read`, `state`,
  `versions`, `version`, `chat`, `chat_generation`, `access` → viewer; `create`, `import_new` → editor on the target
  project; `move` → editor on the diagram and on the target, and the owner of the source project when it leaves
  its project; `update`, `apply_operation`, `apply_if_changed`,
  `snapshot`, `restore`, `undo`, `redo`, `write_through`, `commit_turn`, `append_chat`, `compact_chat`,
  `clear_chat` → editor; `delete` → owner. `write_through` answers a lost diagram or role as an outcome (`deleted`,
  `forbidden`) instead of raising, and `commit_turn` never raises for access (see *A turn that loses its role*).
- `ProjectService`: `list_projects` (visible only), `get`, `view`, `load_members`, `locate` (None when
  invisible), `chat`, `chat_generation`, `access` → viewer; `apply`, `apply_all`, `append_chat`, `compact_chat`,
  `clear_chat`, `check_target`, `place` → editor; `delete`, `set_role` (with `None`: back to the default) → owner; `create`,
  `create_with_sections` → member of the workspace; `move_to_workspace` → owner of the source workspace and member
  of the target, who stays owner of the project there (an explicit role, unless they own the target); the default
  project never moves (ProjectError). `check_target(None)` (a diagram without a project) checks the role the caller
  would have on the default project before `ensure_default` writes anything for them (ProjectNotFound for
  non-members of the default workspace, Forbidden for its viewers). `reconcile`, `ensure_default`, `adopt` are repairs
  of the system context (`reconcile` and `adopt` refuse a guarded call); `intend`/`settle` are the placement record
  of `DiagramService.create`, which checked the target first.
- `ModelService`: reads → viewer, every write → editor on the project, checked before the first write.
- `ProjectExchangeService`: `export` → viewer; `import_bundle` → member of the target workspace.

Errors: invisible (no role, unknown, unassigned) → `DiagramNotFound` / `ProjectNotFound` / `WorkspaceNotFound`
with the message the store gives for a missing document → **404**; visible with a lower role → `Forbidden` →
**403** (an app-wide exception handler), message e.g. *Read-only access: your role on project 'webshop' is
viewer; this needs editor.* The checks run before anything is written. Chat routes check the role before the
event stream starts, so a viewer gets a plain 403 instead of a stream; the agents check again.

A guard caches the project of a diagram and the workspaces it read for its lifetime (one request or one chat
turn) **for reads**: every check above viewer, and every `Guard.workspace` decision, reads the workspace again, so a
member removed or an owner who handed the workspace over during a chat turn cannot change anything from their next
write on (ADR-0090). A project role is read with the project on every check anyway.

**Workspace deletion races** (no transactions across documents). Deleting an empty workspace re-counts its projects
after the delete and puts the workspace back (409) if a project arrived meanwhile; creating a project (also by
importing a bundle) or moving one into a workspace re-reads the workspace after the write and undoes it (404) if the
workspace is gone **or is not the one that was checked** (another owner or creation time: the id was deleted and
taken by somebody else's new workspace meanwhile). Only "not found" counts as gone; any other store failure of the
re-read propagates and undoes nothing. A move retried after a conflict is refused (409) when the project left the
source workspace meanwhile, and undoing a move reverts only what the move changed (the workspace, the roles it
dropped, the mover's owner role); role changes made meanwhile stay. Each side writes before it re-reads, so one of the two always sees the other: no project is left
in a workspace that does not exist. Should one be left anyway (a crash, data from elsewhere), the start-up migration
and `arcviz-migrate --workspaces` report it as **stranded** and `arcviz-migrate --workspaces STORE --reassign
MISSING=WORKSPACE` moves it into an existing workspace.

**Existence of ids** (ADR-0089). Diagram and project ids stay global. Creating or importing a diagram, or creating a
project, with an id that is taken (by anything, visible to the caller or not) uses the first free variant `<id>-2`,
`<id>-3`, … and the response names it; nothing answers 409 for a taken id, and the project id `default` is reserved
for the default project. The answer is the same whether the caller may see the holder of the id or not; that the
requested id was taken somewhere remains observable (a variant came back), nothing else about it.

**The membership repair** (ADR-0088). Before a diagram is created, `DiagramService.create`/`import_new` record its
target project in a keyed document of kind `placement` (key: the diagram id; `{"project", "at"}`), and remove it once
the diagram is placed. A record also claims the id for the create in flight. `ProjectService.reconcile` runs only in
the system context (at start-up, `arcviz-migrate --projects`); a caller's read repairs nothing. It puts an orphan
into its recorded project; an orphan without a record is data from before projects and goes into the default
project; one whose recorded project is gone is **quarantined**: it stays in no project (nobody sees it), the CLI
lists it, and an admin places it with `arcviz-migrate --projects STORE --adopt DIAGRAM=PROJECT`. Leftover records
(the diagram placed, or no diagram after one hour) are dropped by the repair.

**A turn that loses its role** (ADR-0090). When a project owner lowers the caller's role, or the diagram is deleted or
moved out of sight, while a chat turn runs, the next tool call is a tool error (nothing more is changed), the
changes the turn already wrote keep their automatic version attributed to the caller (`commit_turn` allows exactly
the revision `write_through` of the same request last wrote), no transcript entry is written once the caller is below
editor, and the `final` event says *Your role on this project was lowered during this turn: …*. A caller who cannot
see the diagram any more gets neither version nor transcript entry, as for a deleted diagram.

Endpoint × role (`tests/test_access_matrix.py` checks every route of the app against this table; the role of a
route is that of its service call):

| Need | Endpoints |
|---|---|
| public | `GET /healthz`, `GET /readyz`, `/login`, `/logout`, `/docs` (if public), `/static/*`, `/` |
| authenticated | `GET /api/config`, `GET /api/me`, `GET /api/workspaces`, `POST /api/workspaces`, `GET /api/projects`, `GET /api/diagrams` (lists filtered) |
| workspace member | `GET /api/workspaces/{w}`, `POST /api/projects` (`workspace`), `POST /api/projects/import` (`workspace`), `DELETE /api/workspaces/{w}/members/{own id}` |
| workspace owner | `PATCH`/`DELETE /api/workspaces/{w}`, `POST /api/workspaces/{w}/members`, `DELETE /api/workspaces/{w}/members/{user}`, `POST /api/workspaces/{w}/owner`, `POST /api/projects/{p}/workspace` |
| viewer | `GET` project, `…/export`, `…/diagrams`, `…/model`, `…/elements`, `…/checks`, `…/chat`, `…/access`; `POST …/review`; `GET` diagram, `…/mermaid`, `…/render`, `…/export`, `…/checks`, `…/versions`, `…/versions/{n}`, `…/compare`; `POST /api/diagrams/{d}/review` |
| editor | `PATCH /api/projects/{p}`, `…/ops`, sections (`POST`, `PATCH`, `DELETE`), elements (`POST`, `PATCH`, `DELETE`), `…/model/sync`, project chat (`POST`, `DELETE`, `…/compact`), `…/proposal/apply`, `…/proposal/discard`, `…/diagrams/{d}/move`; `POST /api/diagrams` and `/api/diagrams/import` (target project); diagram `…/ops`, `…/versions` (`POST`), `…/restore`, `…/undo`, `…/redo`, `…/chat` (`POST`, `DELETE`, `…/compact`), `…/proposal/apply`, `…/proposal/discard`, `…/link`, `…/unlink`, `…/promote` |
| project owner | `DELETE /api/projects/{p}`, `PUT`/`DELETE /api/projects/{p}/access/{user}`, `DELETE /api/diagrams/{d}`, `…/diagrams/{d}/move` into another project (owner of the source, editor on the target) |

Responses carry what the UI needs: project summaries and views get `workspace` and `role`; a diagram detail gets
`access: {"role", "project", "workspace"}`.

### Authors — #162

`VersionInfo` gets `author: str | None` and `author_name: str | None` (the display name when the version was
written). Every derived store operation that records a version takes `author: Identity | None` (`create`,
`commit`, `update` through `Change.author`, `snapshot_latest`, `restore`, `undo`, `redo`). The bound
`DiagramService` passes its caller; model propagation records the caller who renamed or removed the element.
`DiagramService.append_chat` and `ProjectService.append_chat` stamp every new entry with `author` and
`author_name` unless the entry has them; summaries carry none. The Git mirror adds `Arcviz author: <name> (<id>)`
to the commit body. Unset fields are omitted from dumps (the
`VersionInfo` serializer drops them), so data written before keeps its exact JSON, and the API shows a missing
author as unknown. The version list shows the author; the chat shows the author of user messages.

### Migration — #163

`WorkspaceService.migrate(admin)` (system context), run by `create_app` at start-up and by
`arcviz-migrate --workspaces STORE [--admin USER]`:

1. With an admin id: create the default workspace (`id` `default`, title *Standard-Arbeitsbereich*, owner the
   admin) if it does not exist. An existing one is never changed (its owner may have transferred it).
2. Repair project membership (`ProjectService.reconcile`, ADR-0088), so diagrams without a project (data from
   before projects) are in the default project, and orphans of a crashed create in the project they were created
   for.
3. Every project without a workspace gets `workspace = "default"` (conditional write per project).

Steps 1 and 3 come first; the repair (step 2) runs after them and is best effort: a failure is logged
(`repair_error`) and repeated at the next start or with `arcviz-migrate --projects`. At start-up the whole
migration is best effort too: an exception (a busy Cosmos DB, several replicas starting) is logged and the app
starts anyway; nothing is half-assigned, because projects are assigned only to a default workspace that exists (or
is reserved, waiting for its admin), and the next start repeats it. Projects naming a workspace that does not exist
are reported as `stranded` (logged at start, printed by the CLI).

Idempotent: a second run changes nothing. In `off`/`token` mode the admin is always known
(`ARCVIZ_ADMIN_USER`, default `admin`). In `proxy` mode without `ARCVIZ_ADMIN_USER`, step 1 is skipped and a
warning is logged: the old projects go into the (not yet existing) default workspace, which nobody can see, until
the operator sets `ARCVIZ_ADMIN_USER` and restarts (or runs the CLI). The id `default` is reserved, so nobody can
create that workspace meanwhile. The default project (`default`) is created in the default workspace. Operators
recover a workspace whose owner left with `arcviz-migrate --workspaces STORE --set-owner WORKSPACE=USER`.

`arcviz-migrate --from/--to` also copies workspaces and users.

### Frontend — #164

- Header: the current user's name (from `/api/me`).
- Navigator: a workspace select above the project select (remembered in `localStorage`), **Mitglieder…**
  (members dialog), **Neuer Arbeitsbereich…**; the project select lists the projects of the selected workspace.
- Members dialog: owner and members with name and e-mail; the owner adds (id or e-mail), removes and transfers
  ownership; a member can leave; others see the list read-only.
- Project menu: **Freigeben…** (sharing dialog: every member with a role select, editable for project owners) and
  **In anderen Arbeitsbereich verschieben…** (workspace owners).
- Read-only mode for viewers: `state.role`; every control marked `data-edit` is disabled (and enabled again when
  the role allows editing: `setBusy` recomputes them, `access.editLock` restores those marked `data-edit-restore`),
  the inspector's inputs too, the navigator's edit actions are hidden, the chat input is disabled with the hint *Nur Lesezugriff: Sie
  können dieses Projekt ansehen, aber nicht ändern.*; a refused change (403) is shown as a German message
  (`api.js` maps 403).
- Pure helpers in `static/access.js` (role checks, role labels, member rows), tested in `tests/js/access.test.mjs`;
  the dialogs live in the feature module `static/workspaces.js`.

### Concurrency and errors

Workspace and user writes are conditional on their revision; the user directory's upsert retries a lost race
once and otherwise gives up silently (it is display data). Role changes and membership changes are single
conditional writes of one document. Moving a project and removing a member write two documents in order
(workspace first, then projects); a crash in between leaves stale roles, which are inert (`project_role` checks
membership first).

## Testing

- `tests/test_auth.py`: presets, header overrides, UTF-8 decoding, the Easy Auth principal, invalid ids, 401 in
  proxy mode without a redirect, identity in `off`/`token` mode, `/api/me`.
- `tests/store_contract.py`: keyed documents (create-if-absent, conditional commit and delete, listing by kind)
  and authors on versions, for file, sqlite and the Cosmos fake.
- `tests/test_workspaces.py`: the domain functions (`workspace_role`, `user_key`), `WorkspaceService` and the
  workspace routes; `tests/test_sharing.py`: `project_role`, roles, sharing routes, moving a project; the user
  directory in `tests/test_auth.py`.
- `tests/test_access.py`: the policy and every guarded service method, incl. the agents with `ScriptedProvider`
  (a viewer's turn is refused before any event; an editor's turn records the author).
- `tests/test_access_matrix.py`: every route × (outsider, viewer, editor, project owner, workspace owner): 404,
  403 or allowed as in the table above, plus a check that every route of the app is in the table.
- `tests/test_layering.py`: `server.py` constructs no service, never calls a system context, and every route that
  takes a path id gets its services from the dependency.
- `tests/test_compat_v018.py` with fixtures written by the v0.1.8 code (`tests/fixtures/v0_1_8/generate.py`):
  data loads unchanged, the migration moves it into the default workspace for all three backends, twice is
  idempotent, versions and transcripts keep their JSON, proxy mode without an admin keeps the data hidden until one
  is configured, the server migrates on start, the CLI migrates and recovers an owner.
- `tests/test_authors.py` and the store contract: authors on every kind of version and on transcript entries.
- `tests/js/access.test.mjs`; `tests/e2e/smoke.mjs`: a proxy-mode server with two users (alice, bob) via headers:
  alice creates a workspace and project, adds bob, bob sees it read-only, alice makes bob an editor, bob edits;
  an outsider does not see it.

## Migration and compatibility

- Data: automatic and idempotent (above). Diagram documents and their versions do not change; projects get the
  `workspace` field. Fingerprints, exports and bundles are unchanged.
- `off`/`token` deployments: nothing to do; everything is owned by the admin identity (`ARCVIZ_ADMIN_USER`,
  default `admin`).
- `proxy` deployments: set `ARCVIZ_ADMIN_USER` to the id of the person who should own the existing data (the
  value of the id header for them, e.g. their Entra object id with the `easyauth` preset, or their user name with
  the previous `ARCVIZ_AUTH_PROXY_HEADER`), optionally a preset. Until then existing data is hidden, not lost.
- API clients: list endpoints now return only what the caller may see; new fields only add to responses.
  `POST /api/projects` without `workspace` uses the default workspace.

## Decisions

- Identities come from the proxy; `off`/`token` map to the admin identity (ADR-0080, user decision).
- Workspaces are teams with one owner who alone manages access (ADR-0081, user decision).
- Workspaces and users are keyed documents in the existing stores; a project names its workspace; no Cosmos
  repartitioning (ADR-0082, conservative default of #159).
- Roles per project; members default to `viewer`; the workspace owner is owner of every project (ADR-0083).
- One access policy in the services, services bound per caller, 404/403 (ADR-0084, user decision).
- Deleting a diagram or a project needs the project owner; any member may create projects; anyone may create a
  workspace; members are added by exact id or e-mail of a known user; moving a project between workspaces needs
  the owner of the source workspace (ADR-0085, conservative choices to review).
- Authors as id plus display-name snapshot (ADR-0086).
- Migration into a default workspace owned by the configured admin; proxy mode waits for `ARCVIZ_ADMIN_USER`
  (ADR-0087).

## Open questions

None blocking. Decisions taken without the maintainer are *Proposed* in their ADRs. Follow-ups: groups from
Entra ID claims, a per-project default role, an audit log.
