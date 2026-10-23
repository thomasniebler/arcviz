# ADR-0030: UML 2.5.1 as the reference for diagram types

- Status: Accepted
- Date: 2026-10-06
- Spec: `docs/architecture/specs/diagram-types.md`

## Context

Milestone M13 adds diagram types next to the deployment diagram (#98): activity diagrams (#99) and
sequence diagrams (#101). Each type needs a vocabulary for its elements, its operations and the tool
descriptions the model reads. Vocabularies can be borrowed from a standard (UML 2.5.1, the current
OMG specification), from a tool (Mermaid's or PlantUML's keywords) or invented. The existing
deployment kinds (`deployment`, `vnet`, `subnet`, `service`, `database`, …) are pragmatic cloud terms;
they only loosely follow UML deployment concepts (UML has Node, Device, ExecutionEnvironment, Artifact,
DeploymentSpecification and CommunicationPath, but no vnet or queue). Thousands of stored diagrams
and every existing tool call use them.

## Options

1. **UML 2.5.1 for every type.** Element and operation names follow the metaclasses of the matching
   chapter (15 Activities, 17 Interactions). Models know these terms well, users can look them up,
   and notation questions have one answer. Some names are long (`DestructionOccurrenceSpecification`).
2. **The renderer's vocabulary** (Mermaid `participant`, `activate`, `alt`). Short, but ties the model
   to one renderer, which the renderer case study (#105) may replace, and Mermaid cannot express
   several UML constructs (`neg`, `assert`, found/lost messages).
3. **Own vocabulary per type.** Free choice, nothing to look up, nothing to check against.

## Decision

Option 1, decided by the maintainer (issues #99, #101): all diagram types build on UML 2.5.1. Sequence
diagrams follow the Interactions chapter, activity diagrams the Activities chapter; the model,
operation names and tool descriptions use the UML terms (snake_case for kind names, which are Mermaid
class names; the UML literal spelling for enumeration values such as `synchCall`). The existing
deployment kinds keep their names and only loosely follow UML deployment concepts; they are not
renamed, so stored diagrams stay valid and are of type `deployment`.

## Consequences

- New types have closed vocabularies taken from the specification; extending them means following
  the specification, not adding free kinds (ADR-0037).
- Documentation of each type names the UML chapter and the metaclasses it covers, and states what it
  leaves out.
- A future cleanup of deployment kinds towards UML terms would need a migration and its own ADR.
