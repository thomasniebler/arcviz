# Application services that hide persistence from the business logic

- Status: Implemented (milestone M16)
- Issue: #130
- ADR: [0050 Application services over repository ports](../adr/0050-application-services-over-repository-ports.md)
- Extended by: [Users, workspaces and project sharing](workspaces-and-sharing.md) (M20; workspaces reworked by
  [Personal workspaces](personal-workspaces.md), M21): the services are bound to
  the caller and check one access policy (ADR-0084)

## Summary

The entry points of the server call the store directly: `server.py`, `agent.py` and `proposal.py`
together have about 45 call sites of `DiagramRepository`, and they import its module, its error types
and helpers (`check_id`) from `arcviz/store/`. The read-modify-write loops with conditional writes
(#65) are spread over those callers: the agent retries a conflicting tool call itself, the server
decides whether a failed write means "gone" by asking the store, and the import route allocates a free
id with its own loop. That works, but every new feature (projects, M16) would add more of the same, and
nothing stops business logic from depending on a backend.

This change introduces an **application service layer** between the entry points and the store, and a
**ports module** with the persistence contract the services depend on. Entry points (HTTP routes, the
agent, proposals) call services; services depend only on domain types and the abstract ports; only
`arcviz/store/` knows backends, file layouts, tables and Cosmos DB partition keys. It is a pure
refactor: no endpoint, event, file or behaviour changes.

## Goals

- `arcviz/ports.py`: the persistence contract as seen from the application, with no backend in it:
  the repository protocol (`DiagramRepositoryPort`), the value types it exchanges (`VersionInfo`,
  `DiagramInfo`, `Change`, `Updated`), the errors (`StoreError`, `ConflictError`), the id rule
  (`check_id`) and the retry budget (`MUTATION_ATTEMPTS`). Revisions are opaque strings (`Revision`).
- `arcviz/services/`: `DiagramService` owns every read-modify-write of a diagram, its versions and
  its transcript, including the conflict handling the agent and the server do today.
- `server.py` routes, `agent.py` and `proposal.py` use services only. `server.py` stays the
  composition root: it creates the repository with `create_store()` and hands it to the services; it
  never calls the repository itself.
- An import-rule test fails when business logic imports `arcviz/store/` (or a storage library), and
  when a route calls the repository instead of a service.
- The module table, the arc42 building block view and the C4 server component view show the new
  layer.

## Non-goals

- Changing the repository contract or a backend. `store/base.py` keeps the versioning semantics
  (derived operations); the port describes them, it does not move them.
- Projects (#131) and their service; this refactor is their foundation.
- The migration CLI: `arcviz-migrate` copies raw repository data between backends and is a storage
  tool, so it stays in `arcviz/store/migrate.py` and talks to repositories directly.

## Design

### Layers

```
entry points      server.py (routes), agent.py, proposal.py
                        │ call
services          arcviz/services/  (DiagramService; ProjectService from #131)
                        │ depend on
ports             arcviz/ports.py   (DiagramRepositoryPort, value types, errors)
                        ▲ implemented by
store             arcviz/store/     (DiagramRepository base + file, sqlite, cosmos, factory, migrate)
```

`server.py` wires the store into the services (`DiagramService(create_store())`), and the agent gets
the service in its constructor. Domain modules (`model`, `operations`, `rules`, `render`, …) are
unchanged and know neither layer.

### `arcviz/ports.py`

Moved here from `store/base.py` (which re-exports every name, so `from arcviz.store import …` keeps
working for tests and tools): `StoreError`, `ConflictError`, `MUTATION_ATTEMPTS`, `VersionInfo`,
`DiagramInfo`, `Change`, `Updated`, `ID_RE`, `check_id`. `fingerprint` (the content hash of a diagram)
is domain logic and moves to `model.py`; `store/base.py` and `store/__init__.py` re-export it as well.

`DiagramRepositoryPort` is a `typing.Protocol` with the operations the services use: `name`,
`git_status()`, `ping()`, `list_diagrams()`, `exists()`, `create()`, `load()`, `load_with_revision()`,
`save_latest()`, `commit()`, `update()`, `delete()`, `list_versions()`, `load_version()`,
`snapshot_latest()`, `restore()`, `undo()`, `redo()`, `undo_target()`, `redo_target()`, `load_chat()`,
`append_chat()`, `chat_generation()`, `compact_chat()`, `clear_chat()`. Every `DiagramRepository`
satisfies it structurally (mypy checks the wiring). `git_status()` is new on the repository base: the
Git mirror's status, or `{"enabled": False}`; it replaces `store.mirror.status()` in `/api/config`.

`Revision` is `str`. Callers receive it from `read()` and hand it back unchanged; nothing outside the
store interprets it (#65 keeps `LEGACY_REVISION` and the token format inside the store).

### `arcviz/services/diagrams.py`: `DiagramService`

| Method | Replaces | Semantics |
|---|---|---|
| `storage_name`, `git_status()`, `ping()` | `store.name`, `store.mirror.status()`, `store.ping()` | unchanged |
| `list_diagrams()`, `exists(id)`, `get(id)`, `read(id)` | `list_diagrams`, `exists`, `load`, `load_with_revision` | `read` returns the state and its opaque revision |
| `state(id)` | the server's `detail()` reads | `DiagramState(diagram, versions, undo_target, redo_target, chat)` |
| `versions(id)`, `version(id, n)` | `list_versions`, `load_version` | unchanged |
| `create(title, id, diagram)` | `store.create` | `StoreError("… already exists.")` on a taken id |
| `import_new(diagram, title, id=None)` | the import route's id loop | with `id`: create or `StoreError`; without: the first free `<base>`, `<base>-2`, … (a lost race takes the next suffix) |
| `delete(id)` | `store.delete` | unchanged |
| `apply_operation(id, op, args, label)` | the `/ops` route's `update` | conditional write, re-applied on conflicts |
| `update(id, change)` | `store.update` | the generic read-modify-write (proposals) |
| `snapshot(id, label)`, `restore(id, v)`, `undo(id)`, `redo(id)` | the derived operations | unchanged |
| `write_through(id, diagram, revision, change, failed)` | the agent's retry loop | see below |
| `commit_turn(diagram, revision, label, details)` | the agent's finalization commit | the version, or `None` when latest moved on (conflict) |
| `chat(id)`, `chat_generation(id)`, `compact_chat(…)`, `clear_chat(id)` | the chat primitives | unchanged |
| `append_chat(id, entries)` | `append_chat` plus the agent's "deleted meanwhile" check | `False` when the diagram no longer exists, `StoreError` for any other failure |

`write_through` is the agent's live write of one tool call, moved unchanged: apply `change` to the
turn's state and write it conditionally on `revision`; on a conflict re-load latest and apply again, up
to `MUTATION_ATTEMPTS`. It returns a `LiveWrite(outcome, diagram, revision, result)` with `outcome`
one of `written`, `rejected` (`failed(result)` said the change was refused; nothing written),
`deleted` (the diagram vanished) or `busy` (attempts exhausted). The agent maps the outcomes to the
tool results it produced before.

### Entry points

- `server.py` builds `DiagramService(store)` and uses it in every route; the `gone()` helper asks
  `diagrams.exists`. `create_app(store=…)` keeps its signature. `app.state.store` is replaced by
  `app.state.diagrams`.
- `DiagramAgent(provider, diagrams, …)` takes the service instead of the repository.
- `proposal.apply_proposal(diagrams, …)` and `discard_proposal(diagrams, …)` take the service.
- `ProjectAgent(provider, diagrams, projects, …)` (M17, [project chat](project-chat.md)) takes both
  services; its transcript is the project's, through `ProjectService.chat`/`append_chat`/
  `chat_generation`/`compact_chat`/`clear_chat` (same signatures as `DiagramService`), and it reads the
  members with `ProjectService.load_members`. `apply_proposal`/`discard_proposal` take an optional
  `note` callable so the project routes record proposal outcomes in the project transcript.

### Import rules (`tests/test_layering.py`)

1. Business-logic modules (every module of the package except `server.py`, `store/` and
   `gitmirror.py`) import nothing from `arcviz.store` and no storage library (`sqlite3`,
   `azure.cosmos`, `azure.identity`).
2. `services/` imports only `model`, `ports`, `operations` and (from #131) `projects` and (from
   #134) `exchange` from the package.
3. `server.py` imports exactly `create_store` from `arcviz.store`, and never accesses an attribute of
   its `store` variable: the repository is only passed to service constructors.

## Testing

- `tests/test_layering.py` with the rules above (fails before the refactor: the agent, the proposal
  module and `server.py` import the store).
- The existing suite is the behaviour check and must pass unchanged, except for the constructor
  arguments of `DiagramAgent` and `apply_proposal` in the tests.
- `tests/test_services.py`: `import_new` suffixes and its race with a concurrent create, `append_chat`
  returning `False` after a delete, `write_through` outcomes (`written`, `rejected`, `deleted`,
  `busy`) and `commit_turn` returning `None` on a conflict.

## Migration / compatibility

None. Stored data, endpoints and events are unchanged. `arcviz.store` re-exports the moved names, so
code that imported them from there keeps working.

## Open questions

None.

## Decisions

- Services over protocol ports, the versioning logic stays in the repository base (ADR-0050).
- The migration CLI stays a storage tool in `arcviz/store/` (non-goal above).
