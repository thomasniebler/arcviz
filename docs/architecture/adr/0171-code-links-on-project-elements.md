# ADR-0171: Code links live on project elements

- Status: Accepted
- Date: 2026-10-19
- Spec: `docs/architecture/specs/agent-access.md`

## Context

A component should be connected to the code that implements it. The same component can appear as a node in several
diagrams (ADR-0070..0078, ADR-0164); the shared project element is the one identity they have in common. A repository
integration is wanted later but not now.

## Options

1. **On the project element** (`Element.code_refs`): one link serves every view; survives renames and moves.
2. **On each diagram node** (a property or field): simple, but one component linked in three views needs three links
   and they drift.
3. **A separate code index document** keyed by path: fast reverse lookup, but a new document, a new consistency
   problem and nothing the element does not already give.

## Decision

Option 1, as free text (repository-relative path, optional repo label, symbol and note). Reverse lookup (path →
element) is a pure function over the project's elements; the model is small by design (`MAX_ELEMENTS`).

## Consequences

Elements grow by an omitted-while-empty list (bounded, size budget in the spec); fingerprints and old data are
unchanged. Elements that are not yet adopted (templates, imports) have nothing to link until adopted. A later Git
integration validates and resolves the same fields. A downgrade loses the links.
