# Use case diagrams

A use case diagram (type **Anwendungsfalldiagramm**) shows **who uses a system and what for**: the actors, the goals
they pursue with it (the use cases) and the boundary of the system. It is the entry point for requirements, one level above
components and classes. It follows the *Use Cases* chapter of UML 2.5.1. Create one with **Neu → Typ:
Anwendungsfalldiagramm**, either empty or from the template **Webshop-Anwendungsfälle** (`webshop-use-cases`). A new use
case diagram reads left to right.

## Elements

| Kind | Drawn as | Use it for |
|---|---|---|
| `actor` | box `«actor»` with the name | a role outside the system: a person, an organisation, another system |
| `use_case` | rounded pill with the name | a goal of an actor that the system supports, named with a verb ("Bestellung aufgeben") |
| `subject` | box around its contents | the system boundary; the use cases of the system stand inside, the actors outside |

## Relationships

| Kind | Drawn as | Use it for |
|---|---|---|
| `association` | plain line (an arrow when you make it directed) | an actor takes part in a use case |
| `include` | dashed arrow `«include»` | the base use case always uses the included one ("Bestellen" → "Zahlung abwickeln") |
| `extend` | dashed arrow `«extend» [condition] (point)` | an optional addition ("Gutschein einlösen" → "Bestellen", condition "Gutschein vorhanden") |
| `generalization` | line with an arrow at the general element | a specific actor or use case inherits from a general one ("Stammkunde" → "Kunde") |

Include and extend are drawn with their keyword automatically: do not write it into the label. For an extend, set the
**condition** and, if you like, the **extension point** (rows `condition` and `extension_point` of the edge's properties).
The edge always points from the element that uses to the element that is used, which is the UML direction of all four.

Arcviz keeps the diagram well-formed: an association joins an actor and a use case, include and extend join two different
use cases, a subject has no edges, actors and subjects stand at the top level, and includes and generalizations never form a
cycle. A request that breaks a rule is refused with the reason.

## The specification of a use case

Write the specification of a use case into its **note** (inspector, field *Notiz*, up to 2 000 characters): goal,
precondition, postcondition, main flow, alternatives and exceptions. The note is not drawn; it is part of the diagram, the
comparison of versions, the assistant's context and, shortened to 200 characters, the PlantUML export. The review reminds
you of use cases without one.

## Review

**Prüfen** looks for use cases without an actor, actors without use cases, use cases without a specification, extends
without a condition and in a cycle, includes used only once, use cases outside the system boundary and duplicate names.

## Project model

Actors and systems are shared with the rest of the project: an actor becomes an *actor* element and a subject a *system*
element when you create it (as long as no element of the same name exists, in which case it is linked to that one).
Use cases are not linked.

## What works as for other diagrams

The system boundary can be focused, versions compared, the layout direction changed, and the diagram exported as SVG, PNG,
Mermaid source, JSON and PlantUML.
