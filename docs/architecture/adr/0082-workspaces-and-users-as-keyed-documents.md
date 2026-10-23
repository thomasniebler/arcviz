# ADR-0082: Workspaces and users as keyed documents; a project names its workspace

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/workspaces-and-sharing.md`

## Context

Workspaces and the user directory must be persisted on the file, sqlite and Cosmos DB backends with conditional
writes. Cosmos partitions by `/diagram_id`; projects live in partitions `project:<id>` (ADR-0051, ADR-0078). The
relation "every project in exactly one workspace" spans documents unless one side owns it.

## Options

1. **Typed primitives per entity** (`list_workspaces`, `read_user`, ...): five primitives per entity and backend,
   mostly copies of the project primitives.
2. **One set of keyed-document primitives** (`list_docs(kind)`, `read_doc`, `create_doc`, `commit_doc`,
   `delete_doc` for the kinds `workspace` and `user`), typed in the services. Half the backend code; new small
   entities need no new primitives.
3. **Membership in the workspace document** (a list of project ids) instead of a field on the project: a second
   cross-document invariant with ordered writes and a repair, like diagram membership (ADR-0052).
4. **Repartition Cosmos DB by workspace**: every item moves; no transaction needs it.

## Decision

Option 2 for storage, with the workspace named by the project (`Project.workspace`, not option 3), and no
repartitioning (not option 4, the conservative default of #159): a project's workspace is one field, so "exactly
one workspace" holds by construction; workspaces and users are items in their own partitions
(`workspace:<id>`, `user:<key>`) on Cosmos, a table `documents` on sqlite and `workspaces/`, `users/` folders on
the file store.

## Consequences

- Listing the projects of a workspace reads every project (as the project listing already does, R15).
- User ids may contain any character, so users are keyed by `u-<sha256 prefix>`.
- `arcviz-migrate --from/--to` copies the keyed documents too.
