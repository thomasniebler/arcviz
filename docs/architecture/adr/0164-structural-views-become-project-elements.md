# ADR-0164: Structural views become project elements when they are created

- Status: Accepted
- Date: 2026-10-12
- Spec: `docs/architecture/specs/elements-by-default.md`
- Amends: [ADR-0070](0070-shared-model-elements-in-the-project-document.md) (elements were created only on request)

## Context

Since M18 a project shares elements, but a view element becomes one only when somebody links or promotes it. In
practice the assistants and people draw the same component again in every diagram, so the shared model stays empty
and the cross-diagram checks see nothing. The user wants components to be project components at once, and new
diagrams to reuse the existing ones.

## Options

1. **Prompt only.** Tell the assistants to create and link elements. Cheap, but not guaranteed, and manual edits
   (REST, UI) stay local.
2. **Adopt on the server, on every write path that creates views.** After an operation adds structural views, link
   each to the element of the same name or create one. Guaranteed; changes the effect of `add_node`/`add_lifeline`
   inside projects; two documents are written (project first, then the diagram).
3. **Like 2, switchable per project.** More flexible, one more setting to explain and test.

## Decision

Option 2, always on: the server adopts new structural views (not grouping containers, not behaviour) in the same
diagram write as the operation that created them, matching existing elements by name before creating one; the
prompts additionally tell the assistants to reuse names and to report what they created. Existing diagrams are
adopted only on request.

## Consequences

- An undo of the diagram does not remove an element created for it (the model has no history; like removing a
  linked node, ADR-0072).
- Near-duplicate names would now multiply elements; the project review reports them (`similar-element-names`).
- Templates and imports stay unadopted until the model panel's action runs.
