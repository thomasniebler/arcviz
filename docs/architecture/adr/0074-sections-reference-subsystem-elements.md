# ADR-0074: Sections reference model elements instead of becoming elements

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/shared-project-model.md` (case study: `shared-project-model-case-study.md`)

## Context

Sections (M16) are folders that in practice describe subsystems. ADR-0063 expected M18 to make
subsystems model elements so project rules get a precise basis.

## Options

1. **Turn sections into elements.** Changes every stored project and the navigator.
2. **A section references an element** (`Section.element`, usually a subsystem or system); its title
   follows the element's name like a view; elements nest through `Element.parent`.

## Decision

Option 2: sections keep working unchanged, the link is optional, and both live in the project document,
so linking and renaming are one conditional write. A linked section cannot be renamed directly (rename the
element), mirroring ADR-0071.

## Consequences

- Removing an element unlinks its sections; removing a section never touches the model.
