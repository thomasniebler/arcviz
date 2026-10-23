# ADR-0085: Who may create, delete, invite and move

- Status: Superseded by ADR-0101
- Date: 2026-10-07
- Spec: `docs/architecture/specs/workspaces-and-sharing.md`

## Context

The maintainer fixed the roles and that only the workspace owner manages membership. Several actions were left
open; the maintainer was not available, so the more restrictive option was taken where the choice affects other
people's data.

## Options and decisions

1. **Deleting a diagram**: editor or project owner? Deletion removes the history irreversibly; editors can
   change content, which stays recoverable through versions. **Project owner.**
2. **Deleting a project**: **project owner** (and only an empty project, ADR-0054).
3. **Creating a project in a workspace**: workspace owner only, or every member? Creating a project changes no one
   else's data, and teams work in their own projects. **Every member** (the creator becomes its owner).
4. **Creating a workspace**: admins only, or anyone? A new workspace contains only its creator. **Anyone.**
5. **Adding a member**: any id, or only known users found by a directory search? A search exposes the whole
   directory to everyone. **Exact id or e-mail of a user who has signed in once; no search endpoint.**
6. **Moving a project to another workspace**: project owner, or workspace owner? Moving takes the project away
   from the team. **Owner of the source workspace, who must be a member of the target.**
7. **Moving a diagram into another project**: editor on both, or the owner of the source project? An editor could
   otherwise move a diagram into a project of their own (also in a workspace of their own) and delete it there,
   which cancels decision 1. **Owner of the source project** (and editor on the target); moves within a project
   need editor.
8. **Running the LLM review as a viewer**: reading and reviewing were listed as viewer rights in #160. **Allowed**
   (it writes nothing; rate limits apply).

## Consequences

Each of these is a one-line change in the policy if the maintainer decides otherwise; the matrix test documents
the current choice.
