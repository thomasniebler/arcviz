# ADR-0031: Diagram type as a field with per-type tables in each layer

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/diagram-types.md`

## Context

Every module assumes a deployment diagram: `model.py` holds one kind catalogue, `operations.py` one
operation table, `agent.py` one tool list and prompt, `mermaid.py` one notation, `exchange.py` one rule
set. New types differ in content: activity diagrams are graphs like deployment diagrams, sequence
diagrams are an ordered tree of interaction fragments. The store, versioning, fingerprints, propose
mode and the Git mirror only need a serialisable `Diagram`. The module table forbids lower layers
from importing higher ones (`model` imports nothing; `operations` imports `model`).

## Options

1. **A `type` field on the one `Diagram` model, type-specific content in optional fields, and a
   table keyed by type in each layer** (kinds in `model`, operations in `operations`, renderers in
   `render`, tools and prompts in the agent, rules in `exchange`), with a test that every table covers
   every type. The store and everything generic stay untouched; the dependency direction stays.
2. **A `DiagramType` registry object** bundling kinds, operations, tools, prompt and renderer. One
   place to read, but it must import the agent, the LLM dataclasses and every renderer, so it sits at
   the top of the graph while `operations` (low) needs the type's operation set: either a cycle or
   dependency injection at startup.
3. **One model class per type** (pydantic discriminated union). Precise types, but every signature
   that takes a `Diagram` (store, proposal, diff, exchange, agent, server) changes, stored data without
   a `type` needs a custom discriminator, and the parallel milestone M12 would conflict everywhere.

## Decision

Option 1. It keeps one serialisable model and the existing dependency direction, and the coverage
test gives the "one place" guarantee of option 2 without its cycle. Within it:

- Kind names are disjoint across types, so `Node.is_container` stays context-free.
- `OPERATIONS` remains the one table of functions; `TYPE_OPERATIONS` says which names a type offers,
  and `apply` rejects the others.
- Fingerprints omit `type` for deployment and `interaction` when it is empty, so stored versions
  keep their fingerprints.
- JSON exports of non-deployment types use `schema_version` 2 so older versions refuse them instead
  of silently importing an empty deployment diagram; deployment exports stay version 1.
- A feature a type cannot support yet (PlantUML export of activity and sequence diagrams, version
  comparison of sequence diagrams) answers 400 with a message naming the follow-up issue rather than
  producing a half-right result.

## Consequences

- Adding a type means adding one entry to each table; the coverage test lists what is missing.
- `Diagram` grows an optional field per type-specific content model (`interaction`).
- Code that iterates `nodes`/`edges` keeps working for graph types and sees empty collections for
  sequence diagrams; type-specific code paths dispatch on `diagram.type`.
