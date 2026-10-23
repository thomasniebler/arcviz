# Use case diagrams

- Status: Implemented
- Issue: #246
- ADR: [0180 Use cases: the subject is a container, the specification is the note](../adr/0180-use-cases-subject-container-and-specification-note.md)
- Shared design: [More UML diagram types](uml-graph-types.md) (ADR-0177, 0182..0185)

## Summary

A diagram type `use_case` following the UML 2.5.1 **Use Cases** chapter (18): actors, use cases, the subject (the system
boundary), and the relationships association, include, extend and generalization. It is the requirements engineer's
entry point: who uses the system and what for, one level above any component or class. The design is a graph on the
node/edge operations through a graph profile, rendered as a Mermaid flowchart. The written specification of a use case
(goal, preconditions, main flow, alternatives) is the node's note (`description`, up to 2 000 characters), so the
diagram stays an overview and the text lives where the use case is.

## Goals

- Actors, use cases and one or several subjects; associations between actors and use cases; include, extend (with
  condition and extension point) and generalization.
- Actors are reused across diagrams through project elements (ADR-0182).
- Review findings that matter to requirements engineers: use cases without an actor, without a specification,
  actors without a use case, extends without a condition.
- A prompt that guides the conversation by goals and actors.

## Non-goals

- Structured use-case specifications (step lists, extension points as model elements, preconditions as fields), use case
  scenarios as sequence diagrams, requirement traceability (follow-up, shared design open question 1).
- Extension points as nodes, include/extend ordering, behaviours owned by the use case, classifier behaviour.
- Nested subjects, packages, actors owned by a subject, multiple subjects sharing a use case.

## Design

### Kinds (`model.USE_CASE_KINDS`)

| Kind | UML 2.5.1 metaclass | Container | Notation (Mermaid) | Fallback |
|---|---|---|---|---|
| `actor` | Actor | no | rectangle with `«actor»` and the name (stick figure is not available in a flowchart) | – |
| `use_case` | UseCase | no | stadium (rounded ends) with the name; UML draws an ellipse, Mermaid 11.17 has no ellipse shape | – |
| `subject` | the subject classifier | yes | subgraph labelled with the name (the system boundary) | – |

Edge kinds:

| Kind | UML | Source → target | Mermaid |
|---|---|---|---|
| `association` | Association | actor → use case (or use case → actor), undirected; `direction: forward` shows the initiator | `---` or `-->` |
| `include` | Include | base use case → included use case | `-.->` with the text `«include»` |
| `extend` | Extend | extending use case → extended (base) use case | `-.->` with the text `«extend»`, then `[condition]` |
| `generalization` | Generalization | specific → general, actor–actor or use case–use case | `-->` (UML: hollow triangle) |

Edges point from the element that refers to the other to the element it refers to (ADR-0184), which matches the UML
direction of include, extend and generalization.

### Typed fields (ADR-0177)

On `extend` only, in `fields`: `condition` (one line, ≤ 120) and `extension_point` (one line, ≤ 60). The text of the edge
is `«extend» [condition] (extension_point)`, then the `label` if any. The specification of a use case is the note
(`description`), set by `add_node`/`update_node`; no new field. The prompt gives a template for it
(*Ziel, Vorbedingung, Nachbedingung, Hauptablauf, Alternativen, Ausnahmen*).

### Rules (profile)

1. A `subject` takes part in no edge.
2. `association` joins an `actor` and a `use_case`; `direction` is unset or `forward`.
3. `include` and `extend` join two different `use_case` nodes; `include` creates no cycle.
4. `generalization` joins two nodes of the same kind (`actor`–`actor`, `use_case`–`use_case`), not the same node, and creates
   no cycle.
5. Parents: only a `subject` has children; a `use_case` is a child of a `subject` or of nothing; an `actor` and a `subject`
   are top level (actors are outside the system).
6. `condition` and `extension_point` only on `extend`.

### Review findings (`rules.RULES["use_case"]`)

| Id | Category / severity | Condition |
|---|---|---|
| `usecase-without-actor` | risk / medium | a use case that is not included and not an extension (a user goal) has no actor, neither directly nor through an actor generalization |
| `usecase-actor-unused` | suggestion / low | an actor without any edge |
| `usecase-without-specification` | suggestion / low | a use case with an empty `description` (message: *Spezifikation fehlt*) |
| `usecase-extend-without-condition` | suggestion / low | an `extend` without `condition` |
| `usecase-extend-cycle` | risk / medium | extends form a cycle |
| `usecase-include-used-once` | suggestion / low | an included use case that exactly one use case includes (extracting it is only worth it when it is reused) |
| `usecase-outside-subject` | suggestion / low | a use case outside every subject while the diagram has a subject |
| `usecase-subject-empty` | suggestion / low | a subject without use cases |
| `usecase-duplicate-name` | risk / low | two use cases or two actors with the same name (case-insensitive) |

### Rendering

`_NODE_SHAPES["use_case"]` as in the table; new diagrams of the type start with layout direction `LR` (profile
`model.TYPE_DIRECTIONS`, a use case diagram reads left to right); highlight, focus on a subject, comparison marks work
unchanged. The automatic edge texts (`«include»`, `«extend»`) come from `model.EDGE_STEREOTYPES` (a table by edge kind), so they
are not stored in the label and cannot be forgotten.

### Exchange: PlantUML (`plantuml_graph.use_case`)

`left to right direction` (`top to bottom direction` for a diagram that was given another one); `actor "Name" as n_id`
(the ids of `mermaid.id_map`); `rectangle "Subject" as n_id { usecase "Name" as n_id … }`; association `a -- uc` (`-->` when
forward); include `uc1 ..> uc2 : <<include>>`; extend `uc1 ..> uc2 : <<extend>> [condition] (point)` and the name;
generalization `general <|-- specific`. The note of a use case follows all relationships as `note right of n_id : …` (its
whitespace folded to one line, the first 200 characters; the rest is a loss, stated in the guide). Text through
`plantuml_text.plain`. Checked with PlantUML 1.2026.8 (`tests/test_plantuml_use_case.py`).

### Tools and prompt

Graph tools with use-case descriptions; `fields.condition`/`fields.extension_point` on edges; `description` is
encouraged on use cases. The prompt (`prompts/types/use_case.txt`) is written for requirements engineers: it starts by asking
for the system boundary, then the actors and their goals (*Wer nutzt das System, und wozu?*), names use cases as verb
phrases from the actor's view, keeps include and extend to a minimum, and offers to write the specification into the
note after the diagram is agreed.

### Template (`webshop-use-cases`)

Subject *Webshop* with *Artikel suchen*, *Bestellen* and *Abo verlängern* (both include *Zahlung abwickeln*, so the review's
"include used once" stays quiet), *Gutschein einlösen* (extends *Bestellen* with the condition *Gutschein vorhanden* at the
extension point *Zahlung*) and *Bestellung verfolgen*; every use case has a specification note; outside the subject the
actors *Kunde* (associated with four use cases), *Stammkunde* (specializes *Kunde*) and *Zahlungsdienst* (associated with
*Zahlung abwickeln*); layout `LR`.

### Project model (ADR-0182)

`actor` links to an element of kind `actor`; `subject` to `system` or `subsystem`; `use_case` is behaviour and is never
linked. Actors and subjects are adopted when created (ADR-0164: a named structural element that other diagrams will
mention); use cases are not.

### Frontend

`typeConfig` reports the kinds; the inspector shows the specification note as a larger textarea for a use case and the
condition and extension point inputs for an `extend`.

## Testing

- `tests/test_use_case.py`: kinds; every rule in operations and importer; cycle detection; `fields` on `extend`; edge
  texts; default direction `LR`; Mermaid with hostile names; adoption of actors and subjects; tools and prompt; template;
  PlantUML text and JAR test.
- `tests/test_rules_use_case.py`: one test per finding id, including actor generalization for `usecase-without-actor`.
- Properties and e2e as in the shared design (create from the template, add an actor and an association, edit the
  specification note).
- Comparison: an added include is marked; a changed note marks the use case changed.

## Migration / compatibility

New type only.

## Open questions

1. **Traceability** from a use case to the scenarios and drivers of the architecture dialog, and to components that
   realize it (shared design, open question 1).
2. **Stick-figure actors**: needs an icon pack with a person glyph (CC0, a few hundred bytes) in `arcviz/static/vendor/icons`
   and the existing `@{ icon: … }` node syntax. Proposed: later, when the notation is the only complaint.

## Decisions

- The subject is a container kind; the specification is the node's note (ADR-0180, Accepted).
- Actors and subjects link to project elements and are adopted at creation; use cases are not linked (ADR-0182, Accepted).
