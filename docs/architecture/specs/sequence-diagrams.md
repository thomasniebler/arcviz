# Sequence diagrams

- Status: Implemented (milestone M13)
- Issue: #101
- ADR: [0035 Sequence diagrams as an ordered interaction tree, drawn server-side as SVG](../adr/0035-sequence-diagrams-interaction-tree-and-server-side-svg.md)
- Rendering: [ADR-0032 Rendering of diagram types in M13](../adr/0032-rendering-of-diagram-types-in-m13.md), implementing [ADR-0038](../adr/0038-renderer-per-diagram-type.md)
- Builds on: [Diagram types](diagram-types.md) (ADR-0030, ADR-0032, ADR-0033)

## Summary

A diagram type `sequence` following the UML 2.5.1 **Interactions** chapter (17): Lifelines, Messages
with their MessageSort and MessageKind, ExecutionSpecifications, CombinedFragments with every
InteractionOperatorKind and guarded InteractionOperands, InteractionUses and
DestructionOccurrenceSpecifications. The content is an `Interaction` on the diagram: an ordered list of
lifelines and an ordered tree of fragments, because time order and nesting are what a sequence diagram
is about; nodes and edges stay empty. Operations, tool names and descriptions use the UML terms. An own
renderer draws the diagram as SVG on the server in UML notation, with the model id of every element on
its SVG group, so clicks, highlights and marks map directly (ADR-0032, ADR-0035).

## Goals

- Every construct listed in #101, in correct UML notation.
- Operations that keep the interaction consistent (references, operand counts, execution order) and
  error messages the model can act on.
- Clicks on any element open an inspector for it; changed elements are highlighted; the renderer
  accepts comparison marks.
- All user/model text escaped; a hostile-name e2e check as for Mermaid.
- Batch, propose mode, undo/redo, versions, JSON import/export and property tests work for the type.

## Non-goals

- Version comparison (400, follow-up #108) and PlantUML export (400, follow-up #107); both done in M27, see
  [version comparison](version-comparison.md#sequence-diagrams-108) and
  [diagram exchange](diagram-exchange.md#sequence-diagrams-107).
- Gates, general orderings, state invariants, continuations, duration/time constraints and
  observations, coregions, message arguments as structured values, lifeline selectors,
  decomposition (`ref` in a lifeline head), lost/found messages to gates of a frame.
- Automatic layout beyond columns and rows (no label wrapping; text widths are estimated).
- Focus/drill-down and layout direction (not meaningful for sequence diagrams).

## Design

### Model (`arcviz/sequence.py`, used by `model.Diagram.interaction`)

```python
class Lifeline(BaseModel):  # UML Lifeline; head label "name : type"
    id: str
    name: str
    type: str | None = None
    actor: bool = False


class Message(BaseModel):  # UML Message
    element: Literal["message"]
    id: str
    name: str
    sort: str = "synchCall"  # MessageSort: synchCall asynchCall asynchSignal reply createMessage deleteMessage
    kind: str = "complete"  # MessageKind: complete found lost
    sender: str | None  # lifeline id; None for a found message
    receiver: str | None  # lifeline id; None for a lost message


class InteractionOperand(BaseModel):
    id: str
    guard: str | None = None
    fragments: list[Fragment] = []


class CombinedFragment(BaseModel):
    element: Literal["combined_fragment"]
    id: str
    operator: str  # InteractionOperatorKind: alt opt par loop critical neg assert strict seq ignore consider break
    covered: list[str]  # lifeline ids, never empty
    operands: list[InteractionOperand]  # >= 1; exactly 1 unless alt, par, strict, seq
    messages: list[str] = []  # message names of ignore / consider


class InteractionUse(BaseModel):  # "ref"
    element: Literal["interaction_use"]
    id: str
    refers_to: str
    covered: list[str]


class DestructionOccurrenceSpecification(BaseModel):
    element: Literal["destruction_occurrence_specification"]
    id: str
    lifeline: str


class ExecutionSpecification(BaseModel):  # activation bar
    id: str
    lifeline: str
    start: str
    finish: str | None = None  # message ids


class Interaction(BaseModel):
    lifelines: list[Lifeline] = []  # left to right
    fragments: list[Fragment] = []  # top to bottom (Fragment = discriminated union on `element`)
    executions: list[ExecutionSpecification] = []
```

`Diagram.interaction` is created for sequence diagrams and must be `None` for other types.
Ids are unique across the whole interaction (lifelines, fragments, operands, executions).
Enumeration values use the UML literal spelling; inputs are matched case-insensitively and with or
without underscores (`synch_call` → `synchCall`).

`sequence.integrity_errors(interaction)` (part of `Diagram.integrity_errors()`): unique non-empty ids,
known enumeration values, message kind consistent with sender/receiver (complete: both, found: no
sender, lost: no receiver), referenced lifelines exist, `covered` non-empty and known, operand count
per operator, `messages` only on ignore/consider, at most one destruction per lifeline (a deleteMessage
counts as the destruction of its receiver), no message involving a lifeline after its destruction (in
the drawn time order), executions on
existing lifelines whose start and finish messages exist, involve the lifeline, and finish does not
come before start.

### Operations (`arcviz/sequence_ops.py`, registered in `operations.OPERATIONS`)

| Operation | UML | Notes |
|---|---|---|
| `add_lifeline(name, type?, actor?, id?, before?)` | Lifeline | `before`: lifeline to insert before, else rightmost |
| `update_lifeline(id, name?, type?, actor?)` | | `type=""` removes the type |
| `move_lifeline(id, before?)` | | reorder; no `before` = rightmost |
| `remove_lifeline(id)` | | removes its messages, executions and destruction; drops it from `covered` (a fragment left without covered lifelines keeps those of its contents, or is removed when empty) |
| `add_message(name, sender?, receiver?, sort?, kind?, operand?, before?, id?)` | Message | kind derived from the ends if omitted; `operand`: InteractionOperand to place it in; `before`: fragment to insert before |
| `update_message(id, name?, sort?, kind?, sender?, receiver?)` | | `""` for sender/receiver makes it found/lost |
| `add_combined_fragment(operator, covered?, guard?, contents?, messages?, operand?, before?, id?)` | CombinedFragment | creates the first operand with `guard`; `contents`: ids of sibling fragments to move into it; `covered` defaults to all lifelines |
| `update_combined_fragment(id, operator?, covered?, messages?)` | | |
| `add_interaction_operand(fragment, guard?, before?)` | InteractionOperand | only alt, par, strict, seq |
| `update_interaction_operand(id, guard)` | | `""` removes the guard |
| `add_interaction_use(refers_to, covered?, operand?, before?, id?)` | InteractionUse | |
| `update_interaction_use(id, refers_to?, covered?)` | | |
| `add_execution_specification(lifeline, start, finish?, id?)` | ExecutionSpecification | start/finish are message ids |
| `add_destruction_occurrence_specification(lifeline, operand?, before?, id?)` | DestructionOccurrenceSpecification | one per lifeline |
| `move_fragment(id, operand?, before?)` | | reorder or move into/out of operands |
| `remove_fragment(id)` | | any fragment, operand (not the last one) or execution; removing messages also removes executions that start or finish at them |

Plus the shared `rename_diagram` and `apply_batch`. Results are `{"element": …, "touched": [ids]}`;
`touched` ids exist in the interaction (`Diagram.has_element`). Operations validate before mutating
where feasible; checks that depend on the final order (execution start before finish after a move)
run afterwards and raise `OperationError`, which `operations.apply` rolls back.

Ids: lifelines get the slug of the name (made unique), messages `m1, m2, …`, combined fragments
`cf1, …`, operands `op1, …`, interaction uses `ref1, …`, destructions `x1, …`, executions `ex1, …`
(first free number). References accept an id or, for lifelines, a case-insensitive name or slug.

### Rendering (`arcviz/sequence_svg.py`, `render.SequenceSvgRenderer`)

Server-side SVG (ADR-0035) in UML 2.5.1 notation (17.12):

- Lifeline: head rectangle with `name : Type` (or a stick figure with the name below for `actor`),
  dashed line down to the end of the diagram or to its destruction.
- Message lines and heads: synchCall solid line, filled head; asynchCall and asynchSignal solid line,
  open head; reply dashed line, open head; createMessage dashed line, open head ending at the head of
  the created lifeline, which starts at that height; deleteMessage solid line, filled head, ending in an
  X that terminates the receiver. Found: starts at a small filled circle left of the receiver; lost:
  ends at a small filled circle right of the sender. Self messages loop to the right.
- ExecutionSpecification: narrow rectangle on the lifeline from the start to the finish message
  (without finish: to the lifeline's last message); nested ones are offset; message ends attach to
  the outermost bar edge.
- CombinedFragment: frame with the operator in a pentagon (ignore/consider with `{messages}`), operands
  separated by dashed lines, guards `[guard]` at the top of each operand. InteractionUse: frame with
  `ref` in the pentagon and the referenced name centred. DestructionOccurrenceSpecification: X.
- Every element is a `<g data-id="…" data-element="…">`; highlight adds class `changed`, marks add
  `added`/`removed`/`changed`. Colours come from a scoped `<style>` (`.arcviz-seq`), with a dark
  variant under `prefers-color-scheme: dark`.
- Layout: columns from estimated text widths (head labels, message labels between neighbours, self,
  found and lost messages), rows top to bottom; frames enclose their covered lifelines and the
  lifelines their contents touch, inset by nesting depth; the `viewBox` covers everything drawn.
- Escaping: every text and attribute value is XML-escaped; nothing from the model reaches markup
  unescaped. The frontend additionally strips active content when it inserts the SVG.

### Agent

`agent_types` defines the sequence tools (one per operation, UML terms in names and descriptions,
enumerations for sorts, kinds and operators) and a prompt with notation, rules and examples.
`Diagram.summary()` lists lifelines left to right and the fragment tree top to bottom with ids;
`get_diagram` returns the interaction JSON.

### API and UI

- `/api/config` `diagram_types.sequence` adds `message_sorts`, `message_kinds`,
  `interaction_operators`; features `focus`, `direction`, `compare`, `plantuml` are false (`compare` and
  `plantuml` true since M27, #108 and #107).
- The detail payload has `renderer: "svg"`, `svg`, `mermaid: null`.
- `proposal.diagram_diff` adds `elements: {added, removed, changed}` (interaction elements by id with a
  short description) for sequence diagrams; the chat shows them in the proposal.
- Inspector: a click on an element with `data-id` opens a form built from the pure module
  `elements.js` (fields per element kind, the resulting `update_*` operation, delete via
  `remove_lifeline`/`remove_fragment`).
- Template `checkout`.

## Testing

- `tests/test_sequence.py`: model validation and integrity, every operation (success and errors,
  rollback), ids, summary, tools and prompt, renderer notation per construct, escaping, API, template.
- Property tests: random sequence operations keep integrity (rejected ones change nothing), the SVG is
  well-formed XML, every element id appears exactly once as `data-id`, hostile text is shown literally,
  JSON round trip.
- JS: `elements.test.mjs`.
- e2e: create from the template, click a message, edit it in the inspector, hostile lifeline name.

## Migration / compatibility

New type; JSON exports use `schema_version` 2.

## Open questions

None open; see Decisions.

## Decisions

- Interaction as an ordered fragment tree beside nodes/edges; lifelines are not nodes (ADR-0035,
  Proposed).
- Server-side SVG with estimated text widths (ADR-0035, Proposed).
- `covered` defaults to all lifelines at creation and is stored explicitly.
- A deleteMessage terminates its receiver like a DestructionOccurrenceSpecification.
