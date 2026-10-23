# ADR-0185: Kind names may be shared across diagram types

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/uml-graph-types.md`

## Context

ADR-0031 and ADR-0037 keep kind names disjoint across types, because `Node.is_container` tests a kind against the union of all
container kinds without knowing the diagram, the kinds file reserves every built-in name, and `_STYLES` and `_EDGE_LINES`
are keyed by kind. The new types need names that already exist with the same UML meaning: the deployment `component`, `dependency`
and `association`; `interface`, `generalization`, `realization` and `package` in more than one new type. Deployment's
`dependency` is dotted with an arrow and `association` a plain line, which is what UML says.

## Options

1. **Keep the names disjoint** by prefixing: `uml_component`, `class_interface`, `component_interface`,
   `usecase_association`. Safe for the invariant, but the model, the prompts and the users see the same UML concept under
   several names, and a rename of a shared concept touches several tables.
2. **Allow the same name in several types when it is the same UML metaclass with the same container-ness.** The union
   test stays valid; a test fixes the allow-list; the notation may still depend on the type.
3. **Make kinds per type fully independent** (`is_container(diagram)`). Removes the constraint, but `Node.is_container` is called
   without a diagram all over the code.

## Decision

Option 2. Shared names are listed in `model.SHARED_KINDS` with their types; `tests/test_diagram_types.py` checks that every
name in more than one catalogue is on the list and has the same container-ness. Notation per type is the renderer's
business (`_NODE_SHAPES[type]`); colours stay per kind. The kinds file keeps reserving every built-in name.

## Consequences

- One concept, one name in prompts, tools and exports.
- A kind with a different meaning in another type needs a different name (a test cannot know the meaning: the allow-list
  is reviewed by people).
- `kinds.BUILTIN` is computed from `TYPE_KINDS`, so the new names are reserved for kinds files automatically.
