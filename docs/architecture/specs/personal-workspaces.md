# Personal workspaces and direct sharing

- Status: Implemented (milestone M21)
- Issues: #177 (one personal workspace per user), #178 (direct sharing by the project owner), #179 (migration of
  M20 workspaces); in the same milestone #173 (presence across replicas) and #176 (versioned watches), see
  [Collaboration](collaboration.md)
- ADRs: [0100 One personal workspace per user, created on first sign-in](../adr/0100-one-personal-workspace-per-user.md),
  [0101 Projects are shared directly with users by their owner](../adr/0101-direct-sharing-by-the-project-owner.md),
  [0102 The migration keeps access explicit](../adr/0102-migration-keeps-access-explicit.md),
  [0103 Projects outside a personal workspace are visible to nobody](../adr/0103-no-access-outside-personal-workspaces.md)
- Supersedes: the team workspaces of [Users, workspaces and project sharing](workspaces-and-sharing.md) (M20):
  ADR-0081, ADR-0083, ADR-0085 and ADR-0087 are superseded; identities (ADR-0080), keyed documents (ADR-0082),
  the access policy in the services (ADR-0084), authors (ADR-0086), the membership repair (ADR-0088), free id
  variants (ADR-0089), a turn that loses its role (ADR-0090) and trusted e-mail addresses (ADR-0091) stay.

## Summary

The deployment is one service, scaled out to a few replicas, on one Cosmos DB, with many users who must be isolated
from each other (owner decision, 2026-10-08). M20's team workspaces (an owner, members, a default role, ownership
transfer) do not fit that: membership made every project of a team visible to every member. M21 replaces them:

- Every user has exactly **one personal workspace** and in it an own **default project** (*Standardprojekt*), both
  created idempotently on the first request (deterministic ids, safe across replicas). There is no other kind of
  workspace, no membership and no ownership transfer. A project has exactly one owner.
- A user sees **their own projects** (*Meine Projekte*) and the **projects shared with them** (*Mit mir geteilt*).
- Only the **project owner** shares a project, with any known user (by user id, or by a trusted and unambiguous
  e-mail address, ADR-0091), as `viewer` or `editor`, and revokes it. Editors cannot re-share.
- **Effective role:** the owner of the project's workspace → `owner`; an explicit share → that role; otherwise
  none (404, no existence leak).
- **Migration**, keeping access explicit: `default` and every team workspace's projects move into the personal
  workspace of their workspace's owner, or of their explicit M20 owner (the member who created them; the former
  workspace owner keeps them as an editor); everybody who could see a project gets a share with the role they had,
  so nobody loses or gains read or write access.

## Goals

- Strict tenant isolation: nothing is visible across users unless the owner shared it; outsiders get 404 on every
  route; viewers 403 on every write; editors cannot share, revoke, delete the project or take diagrams out of it.
- Authorization stays in the service layer (`Guard`, ADR-0084); `tests/test_access.py` lists every public service
  method, `tests/test_access_matrix.py` every route × (outsider, viewer, editor, project owner).
- A revoked recipient's live streams end (`gone`), on the same replica at once, on others within `1.5 × poll`.
- An idempotent, best-effort migration at start-up and through `arcviz-migrate --workspaces`, tested against the
  v0.1.8 fixtures and new M20-era fixtures (`tests/fixtures/m20/`).

## Non-goals

- Groups, teams or organisation-wide sharing; link sharing; guests who never signed in.
- Transferring a project to another user (a follow-up; operators can move stranded projects with the CLI).
- Separate stores or encryption per tenant: users share one store; diagram and project ids stay global (ADR-0089).

## Design

### Personal workspaces (`arcviz/workspaces.py`) — #177

```python
def personal_workspace_id(user_id: str) -> str:
    return "user_" + sha256(user_id)[:32]  # "_" never occurs in M20 workspace ids (slugs of titles)


class Workspace(BaseModel):
    id: str
    title: str
    owner: str
    members: list[Member] = []  # M20 data only (read by the migration); always empty for personal workspaces
    personal: bool = False  # True for every workspace created since M21
    created_at: str
    updated_at: str

    def is_personal_of(self, user_id) -> bool: ...  # personal, owned by user_id, id == personal_workspace_id(user_id)
```

`ensure_personal_workspace(documents, identity)` (`services/access.py`) creates the caller's workspace with
`create_document` (create-if-absent: two replicas creating it at once both succeed, one of them by reading the other's)
and returns it. A document under that id that is not the caller's personal workspace is never used (StoreError,
logged): the id cannot come from M20 (`_` is not in a slug), so it can only be data written by hand. The title is
*Persönlicher Arbeitsbereich*. It runs:

- for every authenticated request in the dependency that refreshes the user directory (`record_caller`), cached per
  process (one point read per user and process, nothing on later requests), and never fails the request;
- again, without the cache, before a project is created or imported, so a failed first attempt is repaired by the
  first write that needs the workspace.

`WorkspaceService` (bound to the caller) keeps one query, `mine()`: the caller's workspace with the number of own
projects and of projects shared with the caller (`GET /api/workspace`). Everything else of M20's service is removed
(list, create, rename, delete, members, transfer); `UserService` moves to `services/users.py` unchanged.

### Effective role (`workspaces.project_role`) — #178

```python
SHARE_ROLES = ("viewer", "editor")  # what an owner may grant through the API
ROLES = ("viewer", "editor", "owner")  # "owner" only through the workspace: a project has exactly one owner


def project_role(user_id, workspace, project) -> Role | None:
    if workspace is None or project.workspace != workspace.id or not workspace.is_personal_of(workspace.owner):
        return None  # unmigrated, dangling or not a personal workspace: nobody (ADR-0103)
    if user_id == workspace.owner:
        return "owner"
    role = project.roles.get(user_id)
    return role if role in SHARE_ROLES else None  # an M20 "owner" entry grants nothing
```

`Project.roles` keeps its name and JSON (user id → role) but now holds the **shares**; there is no default role and
no membership check. The project document is read with every check, so a revoked share is effective at the next
request; checks above viewer read the workspace again as in M20.

### Sharing (`ProjectService`) — #178

| Method | Need | Effect |
|---|---|---|
| `access(pid)` | viewer | The owner (workspace owner), the caller's role, and the shares: all of them for an owner, only the caller's own entry for anybody else. |
| `share(pid, user, role)` | owner | `user` is a user id or a trusted, unambiguous e-mail of a **known** user (`UserService.find`, ADR-0091); `role` is `viewer` or `editor` (400 otherwise, also for `owner`); sharing with the project's owner is 400. Sets or changes the share (idempotent). |
| `revoke(pid, user_id)` | owner | Removes the share (a missing one is a no-op). The workspace owner cannot be revoked (400). |
| `list_projects()` | – | Every project the caller owns or that is shared with them, with `role`, `owner` and `shared` (False for own projects). |
| `create(…)`, `create_with_sections(…)` | caller | Always into the caller's personal workspace (a `workspace` other than it is 404). |

`ProjectService` gets the `UserService` for `share`. Every share change is a conditional read-modify-write of the
project and publishes `{"type": "project", "project"}`, so live streams re-check: a revoked recipient's stream ends
with `gone` (ADR-0096); on other replicas the poll finds the new project revision. `move_to_workspace` is removed.
The owner may delete the project, take diagrams out of it and delete them (ADR-0085's rules stay for
these): an editor cannot.

**Moving diagrams between projects of different owners** stays as in M20: editor on the diagram and on the target,
owner of the source project when the diagram leaves its project. An editor of somebody else's project therefore
cannot take a diagram out of it; a user may move their own diagram into a project shared with them as editor.

### Listings (no cross-tenant scans)

A caller's project list must not read every project of every tenant (Cosmos bills every item read). Each user has a
**listing**: a keyed document of kind `listing` (key `user_key(user)`, `services/listing.py`) with the ids of their
own projects (`own`) and of the projects shared with them (`shared`). `ProjectService.list_projects()`,
`visible_diagrams()` and `WorkspaceService.mine()` read it and then each listed project with one point read,
re-validating every id (the project must exist and the caller must have a role on it), so a stale or extra id is
harmless and never a grant. Writers add an id before the write that makes a project visible to a user (`share`, the
migration's moves) or right after a create (`_create_free`, `ensure_own_default`, `ensure_default`, imports), and
never remove one right after the write that hides it (`revoke`, `delete`): that could hide a project that is shared
again or created again under the same id meanwhile. Since M22 stale entries are pruned instead (#183, ADR-0124,
[scale and robustness](scale-and-robustness.md)): every add stamps its entry
(`added`, so it always changes the revision), and `Guard.listed` and the start-up repair remove the entries of
projects that are definitely gone or foreign (not in the user's personal workspace, no share for them) with a write
conditional on the revision read before the projects, and only once their stamp is older than one hour. Only a missing listing
reads as empty; any other read failure (a throttle) is an error, never an empty project list. A failed listing write after a completed write is
logged and repaired by the migration at the next start (`PersonalMigration.indexed`), which adds every project of a
personal workspace that its owner's or a recipient's listing misses. Since M22 the remaining per-request scans are gone too ([scale and robustness](scale-and-robustness.md), #181):
locating a diagram's project goes through the diagram locator (ADR-0120) and `GET /api/diagrams` describes only the
diagrams of the caller's listed projects.

### Who learns what about other users

- Project responses never carry `roles` (the shares): every view, update, listing and export bundle leaves them
  out; only `GET /api/projects/{p}/access` shows them, and in full to the owner only.
- Recipients see the owner by id and display name only (`GET /api/projects`, `GET /api/projects/{p}/access`); the
  owner's e-mail address is shown to the owner alone.
- Share rows show id and display name, never an e-mail address; `added` (the answer to a share) is what the owner
  typed plus the display name it resolved to.
- Every reference that does not identify exactly one known user (unknown, untrusted or ambiguous e-mail) gets the
  same 400 message, so a failed share does not say which case it was.
- Sharing and revoking are their own rate class `share` (`ARCVIZ_RATE_LIMIT_SHARE`, 30 per minute and client by
  default). Residual risk (ADR-0101): an owner can still learn that a user id exists, and its display name, by sharing
  their own project with it; the rate limit bounds how fast.

### Default projects

Every user has an own **default project**: id `personal_project_id(user)` = `home_` + the same 32 hex digits as the
workspace, title *Standardprojekt*, in their personal workspace. `ProjectService.ensure_own_default()` creates it
with create-if-absent (replicas racing for it end with the same project; only "already exists" counts as a concurrent
create, any other store error propagates with its cause) at the first request (`ServiceRoot.signed_in`, together with
the workspace) and again whenever it is needed. If the id is taken by a project outside the caller's workspace (an M20
project the migration could not rename yet), it creates a free variant (`home_…-2`), records it on the workspace
(`Workspace.default_project`, a conditional write; a concurrent fallback that recorded first wins and the other copy
is removed) and logs it once per store and process; it never fails every request. A diagram created or imported **without a project** goes into the caller's own default project
(`check_target(None)`). Ids of the forms `home_` + 32 hex digits and `user_` + 32 hex digits are reserved: a create that asks for one gets
the next free variant (`…-2`), like the reserved id `default` (ADR-0089), and the migration renames M20 projects with
such an id (below).

The reserved project `default` (legacy data, orphans of the membership repair, ADR-0088) is the admin's: it is in the
admin's personal workspace after the migration, only the system context creates it (`ensure_default`), and while no
admin is configured it is created under the reserved workspace id `default`, which nobody sees, until the next
migration with an admin moves it. The UI creates the demo diagram of a fresh installation (in the caller's own
default project) for the admin only, while that empty project is all they have, and once per browser.

### Access policy (`services/access.py`)

Unchanged structure (ADR-0084): `Guard.project`, `project_doc`, `diagram`, `role_of` compute `project_role`.
`Guard.workspace(id)` is gone; `Guard.own_workspace()` returns the caller's personal workspace (ensured).
`WorkspaceNotFound` (404) is raised for a `workspace` argument that is not the caller's own.

Endpoint × role (`tests/test_access_matrix.py`):

| Need | Endpoints |
|---|---|
| public | `GET /healthz`, `GET /readyz`, `/login`, `/logout`, `/docs`, `/static/*`, `/` |
| authenticated | `GET /api/config`, `GET /api/me`, `GET /api/workspace`, `GET /api/projects`, `POST /api/projects`, `POST /api/projects/import`, `GET /api/diagrams` (lists filtered; creates go into the caller's own workspace) |
| viewer | as in M20 (project and diagram reads, review, export, compare, live, presence), `GET /api/projects/{p}/access` |
| editor | as in M20 (content writes, chats, proposals, moving a diagram within the project, creating diagrams in it) |
| project owner | `DELETE /api/projects/{p}`, `POST /api/projects/{p}/access`, `DELETE /api/projects/{p}/access/{user}`, `DELETE /api/diagrams/{d}`, moving a diagram out of the project |

Removed (breaking): `GET/POST /api/workspaces`, `GET/PATCH/DELETE /api/workspaces/{w}`,
`POST /api/workspaces/{w}/members`, `DELETE /api/workspaces/{w}/members/{user}`, `POST /api/workspaces/{w}/owner`,
`POST /api/projects/{p}/workspace`, `PUT /api/projects/{p}/access/{user}`; `GET /api/projects?workspace=` and the
`workspace` field of `POST /api/projects` / `POST /api/projects/import` are ignored.

### Migration (`services/workspaces.py`) — #179

`migrate_to_personal_workspaces(repository, admin)` (system context), run by `create_app` at start-up and by
`arcviz-migrate --workspaces STORE [--admin USER]`. For every project, until it is in a personal workspace:

| The project names | It moves into the personal workspace of | Shares |
|---|---|---|
| nothing (v0.1.8 data), or `default` while that workspace does not exist | the admin (none configured: it waits, hidden) | its roles as they are (none for v0.1.8 data) |
| an M20 workspace (`default` or a team) without an explicit owner among its members, or one whose workspace owner is itself an explicit owner of the project, or the reserved project `default` | that workspace's owner | every member's M20 effective role (`roles[m]`, else `viewer`; explicit owners `editor`); roles of non-members are dropped (they were inert) |
| an M20 workspace with a member whose explicit role is `owner` (the project's creator) and the workspace owner is none | that member (several: the first by user id, since projects do not record their creator) | the former workspace owner `editor`; other explicit owners `editor`; every other member their M20 effective role; reported as `reowned` and printed by the CLI |
| a workspace that does not exist (not `default`) | – | reported as **stranded** (nobody sees it); `--reassign MISSING=USER` moves it into USER's personal workspace without shares |
| a personal workspace | – (already migrated) | – |

Each project is moved by one conditional write (workspace and shares together; a conflict re-reads the project and
the workspace and starts again), so a crash leaves every project either migrated or not. A project that fails (a bad
document, a store error) is logged, reported (`failed`, printed by the CLI, an error line at start-up) and retried by
the next run; the others go on, so one bad document never keeps other tenants' projects hidden. An M20 project whose id
has a reserved form (`home_…`/`user_…`, which M20 accepted) is first renamed to the first free `<id>-2`, …, and the
rename is reported (`renamed`; a project that failed and is renamed by a retry is no longer `failed`). The rename is
crash-safe and idempotent: its target is recorded first (keyed document of kind `rename`, key the original id), so a
restart resumes the same target instead of choosing `<id>-3`; the copy is created without memberships (the
transcript and the placement records follow), the memberships are recorded there, taken from the original by a
conditional write and only then given to the copy, so two projects never hold the same diagram; then the original and
the record are deleted. A rename target is not migrated on its own until its rename is done; a record whose original
is gone is finished (or cleared) by the next run. The target personal
workspaces are created first (`ensure_personal_workspace` for the owner's id; a missing directory record is fine).
The M20 workspace documents are **kept** (read-only history, ignored by the policy, ADR-0103): a project that an M20
replica still running during a rolling upgrade adds to a team workspace is migrated by the next run with the
members known. Then the membership repair runs (`ProjectService.reconcile`, best effort as before) and the
projects it created (the default project) are migrated in a second pass. Idempotent: a second run changes nothing.
The report (`PersonalMigration`) lists moved projects (with their target), the number of shares written, created
personal workspaces, stranded projects, projects waiting for an admin, and the repair.

At start-up the migration is best effort: an exception is logged, the app starts, and nobody sees the unmigrated
projects until the next start or the CLI completes it (ADR-0103). `--set-owner` is removed (there is no workspace
ownership to recover); `--reassign MISSING=USER` replaces `--reassign MISSING=WORKSPACE`.

### Frontend

- Navigator: the workspace switcher, **Mitglieder**, **Neuer Arbeitsbereich** and **In anderen Arbeitsbereich
  verschieben…** are gone. The project select has two groups, **Meine Projekte** and **Mit mir geteilt** (shared
  projects are labelled with their owner's name); new and imported projects go into the own workspace.
- **Freigeben…** (sharing dialog): the owner sees the owner line and every share with a role select (*Betrachten*,
  *Bearbeiten*) and **Entfernen**, plus a form to share with a user id or
  e-mail address and a role; the confirmation repeats what the owner typed and the display name it resolved to.
  While the owner types (two characters or more) the form suggests users who have signed in before
  (`GET /api/users?q=`, ADR-0165): name, id or trusted e-mail address contains the text, never the caller, at most
  10, those starting with it first; choosing one fills in the **user id**, so sharing by a suggestion never depends on
  an e-mail address (ADR-0091). The directory listing behind it is cached per process for 60 s
  (`users.DIRECTORY_TTL`), so typing does not list the store per keystroke.
  Others see the owner (id and name) and their own role, read-only.
- A new user starts in their empty default project (*Standardprojekt*). Should they delete every project, the
  navigator says *Sie haben noch keine Projekte. …* with **Neues Projekt anlegen**.
- When a share is revoked while the recipient has the project open, the live stream's `gone` reloads the navigator
  (existing behaviour), which no longer lists the project.
- Pure helpers in `static/access.js` (`projectGroups`, `shareRows`, `sharedLabel`, `offerDemo`, role labels), tested in
  `tests/js/access.test.mjs`; the dialog lives in `static/workspaces.js` (renamed in purpose: sharing and the
  current user).

## Testing

- `tests/test_workspaces.py`: `personal_workspace_id`, `ensure_personal_workspace` (idempotent, two replicas at
  once, a foreign document under the id), `GET /api/workspace`, the directory dependency creating the workspace;
  the own default project (idempotent, eight threads at once, diagrams without a project, a squatted `home_` id
  diverted, a foreign project under the id refused).
- `tests/test_sharing.py`: `project_role` (owner, shares, no default, non-personal workspaces), share by id and by
  e-mail (trusted only, ambiguous refused), roles allowed, revoke, the access view for owners and others, the
  routes.
- `tests/test_access.py`: every public service method by role (outsider, viewer, editor, owner); a revoked
  recipient's turn and stream; tenant isolation between two users' own projects.
- `tests/test_access_matrix.py`: every route × (outsider, viewer, editor, project owner); a second owner's project
  stays 404 to the first.
- `tests/test_live.py`, `tests/test_server_live.py`: a revoked recipient's stream ends with `gone`.
- `tests/test_compat_v018.py` (v0.1.8 fixtures) and `tests/test_compat_m20.py` (new M20 fixtures,
  `tests/fixtures/m20/generate.py`): data loads unchanged; after the migration every person's effective role on
  every project equals the M20 one, except that the former workspace owner of a project created by a member is now
  its editor; several explicit owners; the CLI names every re-owned project; twice is idempotent; proxy mode without an admin keeps legacy data hidden; the
  server migrates on start; the CLI migrates and reassigns stranded projects.
- `tests/js/access.test.mjs`; `tests/e2e/smoke.mjs` step 17: alice and bob (proxy mode), bob starts empty, alice
  shares a project with bob by e-mail as viewer (read-only, 403), raises him to editor (he edits), carol (outsider)
  sees nothing and gets 404; step 18 (live, presence) as before; at the end alice revokes bob, whose open stream
  ends and whose navigator drops the project.

## Migration and compatibility

- Data: automatic (above). Diagrams, versions and transcripts are unchanged; projects get a new `workspace` and
  their `roles` become shares; M20 workspace documents stay.
- API: breaking (`feat!`), see the removed endpoints above. Clients that list projects get own and shared ones.
- `off`/`token` mode: one admin identity, which owns everything after the migration.
- `proxy` mode: `ARCVIZ_ADMIN_USER` still names the owner of the data from before workspaces.

## Decisions

- One personal workspace per user with a deterministic id; no team workspaces (owner, 2026-10-08; ADR-0100).
- Only the project owner shares, viewer or editor, with any known user; effective role owner → share → none (owner;
  ADR-0101).
- Migration keeps access explicit; a project has exactly one owner: one with an explicit M20 owner goes to that
  member (several: the first by id), the former team owner becomes an editor (owner, 2026-10-08; ADR-0102); `default`
  goes to its current owner (owner: OK).
- Every user has an own default project for diagrams created without a project; the reserved `default` stays the
  admin's (owner, 2026-10-08; ADR-0100).
- Projects not migrated yet stay invisible and are reported (owner: OK; ADR-0103).
- Projects outside personal workspaces are visible to nobody until migrated (ADR-0103).
- The share list is shown in full to owners only (stricter than M20, where every member saw every role).

## Open questions

None blocking. Follow-ups: transferring a project to another user, groups.
