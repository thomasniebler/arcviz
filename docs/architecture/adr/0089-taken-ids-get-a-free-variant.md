# ADR-0089: A taken id gets a free variant, whoever can see its owner

- Status: Superseded by [ADR-0131](0131-server-generated-ids.md)
- Date: 2026-10-07
- Spec: `docs/architecture/specs/workspaces-and-sharing.md`

## Context

Diagram and project ids are global (arc42 R20, ADR-0082). Creating or importing a diagram, or creating a project,
with an id that exists answered 409, also when the existing diagram or project is in a workspace the caller cannot
see. That made the create endpoints an existence probe across workspaces.

## Options

1. **A generic error** that reads the same for visible and invisible ids: the status code alone still says "taken".
2. **The first free variant** (`<id>-2`, `<id>-3`, …) for every taken id, visible or not, the way imports, project
   bundles and workspaces already allocate ids. The response names the id actually used.
3. **Ids scoped per workspace** or opaque generated ids: no probe at all, but a storage and URL change for every
   backend (Cosmos partition keys, file paths, links), far beyond this milestone.

## Decision

Option 2 for `POST /api/diagrams`, `POST /api/diagrams/import` (also with an explicit `id`) and `POST /api/projects`.
The answer no longer depends on whether the caller may see the holder of the id; nothing answers 409 for a taken id.
An invalid id is still 400.

## Consequences

- API clients that sent an explicit `id` get a different id back instead of 409 when it is taken; they must read the
  id from the response (the UI always did).
- **Residual:** a caller still learns that *some* diagram or project has the requested id (it got a variant), since
  ids are global. It learns nothing else about it (not its workspace, title or content). Removing this bit needs
  option 3; recorded in arc42 R20.
