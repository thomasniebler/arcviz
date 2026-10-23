# Operations

Source: `arcviz/operations.py`. Tests: `tests/test_operations.py`.

Operations are the only way a `Diagram` is mutated, by the LLM tools (`arcviz/agent.py`) and by
`POST /api/diagrams/{id}/ops` alike. Each operation validates its input and raises
`OperationError` with a message that is safe to show to the model and the user. The message
lists known ids or allowed kinds where that helps the model to correct itself.

## Dispatcher

```python
apply(diagram, op: str, args: dict) -> {"ok": True, "op": op, "result": ..., "touched": [ids]}
```

- Unknown `op` → `OperationError` listing the available operations.
- Activity diagrams add a `guard` argument to `add_edge`/`update_edge` and UML rules, see
  [Diagram types](diagram-types.md#activity-diagrams); `kind` defaults depend on the type.
- **Graph profiles and typed fields** ([uml-graph-types](../architecture/specs/uml-graph-types.md), ADR-0183, ADR-0177):
  for the types with a profile (`graph_types.PROFILES`; today `activity`) the default kinds, the edge, node and
  whole-diagram rules and the typed fields come from `graph_types.GraphProfile`. `add_node`, `update_node`, `add_edge`
  and `update_edge` take an optional `fields` object (`{"attributes": ["- id: UUID"]}`): a text field is one line
  (`single_line`, stripped, at most `max_length` characters), a list field a list of such lines (at most `max_items`),
  stored as the property of the same name (lists newline-joined); `""` or `[]` removes it. A name the kind does not
  define, a wrong JSON type or a bound is an `OperationError` naming the field. The property keys that any typed field
  of the type owns are refused in `properties` and `remove_properties` (`use fields.<name>`); a kind change drops the
  fields the new kind lacks. A profile's `edge_problem`/`node_problem` run on the candidate element (the element as it
  would be stored) in `add_edge`, `update_edge`, `add_node`, `move_node` and on a kind change (`Cannot connect …`,
  `Cannot place …`, `Cannot change … would break a rule`). Deployment and sequence diagrams have no profile and
  therefore no fields.
- An operation outside `TYPE_OPERATIONS[diagram.type]` → `OperationError("Operation '<op>' is not
  available for <type> diagrams. Available: …")` (see [Diagram types](diagram-types.md#operations)).
- `check_arguments(op, args)` validates the arguments against the operation function's signature
  before anything is touched (#66, [design](../architecture/specs/operation-arguments.md)): `args`
  must be an object, unknown names are rejected, every value must match the parameter's annotation
  (`str`, `bool`, numbers, `None`, unions, `list[T]`, `dict[str, T]`; booleans are strict) and every
  parameter without a default must be given. Failures raise `OperationError("Invalid arguments for
  <op>: …")` naming the argument, e.g. `'kind' must be a string or null, got a number.`
- **One-line texts** (#103, [ADR-0160](../architecture/adr/0160-control-characters-in-names-are-normalised.md)):
  `check_arguments` also returns the arguments the operation is called with, in which every string argument named in
  `operations.ONE_LINE_ARGUMENTS` (`name`, `title`, `label`, `guard`, `type`, `refers_to`) is `model.single_line`:
  a run of CR, LF, TAB, U+2028 or U+2029 becomes one space, every other C0/C1 control character (and DEL) is dropped.
  Never an error: a text that is empty afterwards is refused as empty, like a blank one. Descriptions and notes keep
  their line breaks. Project operations (`projects.apply`), the title of a new diagram (`DiagramService` create and
  import: stripped; the create refuses an empty one with `OperationError`, an import calls it "Unbenanntes Diagramm" and
  warns) and of a new project (`projects.new_project`) are normalised the same
  way, and so is every imported document (`exchange._one_line`; never the `properties` maps); `mermaid.escape_text` and `sequence_svg.escape`
  apply it again, so data stored before keeps rendering. A linked view whose stored name equals the new one once
  normalised is not renamed (`_check_linked_rename`, `update_lifeline`).
- A deep copy of the diagram is taken before the call. On **any** exception every model field
  (`updated_at` included) is restored from the copy and the exception is re-raised unchanged. An
  exception other than `OperationError` is a defect, not an input error: REST answers 500, the agent
  returns an "Internal error …" tool result; neither can persist a partial change.
- `touched` lists the node ids to highlight: the node for node operations, source and target for
  edge operations, the re-parented children for `remove_node`, the batch's `touched` for
  `apply_batch` (a dict result's `touched` key wins over `reparented`).
- `result` is the created/changed object as JSON, or the dict returned by `remove_node` /
  `apply_batch`.
- Every successful operation sets `diagram.updated_at`.

## Node operations

| Operation | Arguments | Validation and behaviour |
|---|---|---|
| `add_node` | `name`, `kind="component"`, `id=None`, `parent=None`, `properties=None`, `description=None` | Name must be non-empty. Kind must be in `ALL_KINDS` (lower-cased). Parent must resolve and be a container. Id defaults to `unique_id(name)` (also when the given id is blank); an explicit id is stripped and must be free. Property values must be strings, numbers or booleans (`PropertyValue`) and are stored as `str`. `description` is the node's note (see below). |
| `update_node` | `id`, `name=None`, `kind=None`, `properties=None`, `remove_properties=None`, `description=None` | Node must resolve. Empty names are ignored. Changing to a leaf kind is rejected while the node has children. A node linked to a project element (`ref`) refuses a new name (rename the element) and a kind that cannot stand for any element. `remove_properties` is applied before `properties`. `description`: `None` keeps the note, a (blank) empty string removes it. |

**Notes** (#87, ADR-0092): `clean_description` strips a `description` argument and refuses a non-string or one
longer than `MAX_DESCRIPTION` (2000) characters with an `OperationError` that names the limit, before anything
is changed. `add_lifeline` and `update_lifeline` take `description` with the same rules (sequence diagrams). The
tools (`add_node`, `update_node`, and the activity and sequence variants) describe the argument.
| `move_node` | `id`, `parent` | `None` or `""` moves to top level. Otherwise the parent must be a container and must not be the node itself or one of its descendants. Edges are untouched. |
| `remove_node` | `id`, `cascade=False` | Without cascade, children are re-parented to the removed node's parent. With cascade, the whole subtree is removed. All edges touching a removed node are removed. Returns `removed_nodes`, `removed_edges`, `reparented`. |

## Edge operations

| Operation | Arguments | Validation and behaviour |
|---|---|---|
| `add_edge` | `source`, `target`, `label=None`, `kind="connection"`, `direction=None`, `properties=None` | Both endpoints must resolve. Kind must be in `EDGE_KINDS`. `direction` (lower-cased) must be in `EDGE_DIRECTIONS` or `default` (stored as `None`). `properties` must be an object of string, number or boolean values, stored as `str`. Everything is validated before the first change. Blank labels become `None`. If an edge with the same source, target, kind, label and direction exists, the given properties are merged into it and it is returned (idempotent). |
| `update_edge` | `id`, `label=None`, `kind=None`, `direction=None`, `properties=None`, `remove_properties=None` | Edge id must exist. All arguments are validated before the first assignment (kind, direction, `properties` an object, `remove_properties` a list). Blank label clears it; `direction="default"` resets to the kind's default; `remove_properties` is applied before `properties`. |
| `remove_edge` | `id=None`, `source=None`, `target=None` | Either by id, or all edges between source and target (any kind/label). Returns the removed ids. Rejected if neither is given. |

## Diagram operations

| Operation | Arguments | Behaviour |
|---|---|---|
| `rename_diagram` | `title` | Non-empty, trimmed. |
| `set_direction` | `direction` | Layout direction, case-insensitive, trimmed; `TD` is stored as `TB`. Anything outside `DIRECTIONS` is rejected with the allowed values. Returns `{"direction": …}`, touches no node. |

## Shared project model

Every diagram type has these operations (`MODEL_OPERATIONS`; design: [Shared project
model](../architecture/specs/shared-project-model.md), ADR-0070/0071). The view element is a node (resolved
like `find_node`) or, in sequence diagrams, a lifeline (by id or name); the element kinds a view element may
stand for are `project_model.linkable_kinds(type, view_kind)` ([Projects](projects.md#shared-model)).

| Operation | Arguments | Behaviour |
|---|---|---|
| `link_element` | `id`, `element`, `name`, `element_kind` | The view element must be linkable and `element_kind` allowed for it; `element` must be a slug. Sets `ref = element` and the name to `name`. Returns `{"id", "element", "name", "renamed_from", "touched"}`. **Internal.** |
| `unlink_element` | `id` | Clears `ref` (the name stays); not linked → error. |
| `sync_element` | `element`, `name` | Renames every view element with `ref == element` to `name`; `touched` lists the renamed ones (none: `updated_at` unchanged). **Internal.** |
| `clear_element` | `element` | Clears the `ref` of every view element of the element. **Internal.** |

`INTERNAL_OPERATIONS` (`link_element`, `sync_element`, `clear_element`) take the element's name and kind from
the caller, so only `ModelService` runs them: `POST /api/diagrams/{id}/ops` answers 400 (pointing to
`/link`), a batch step naming one fails, the agent answers a tool call with that name with an error, and a
proposal containing one is a `ProposalConflict`. `update_lifeline` refuses a new name for a linked lifeline
like `update_node`; a linked node may only change to a kind that can stand for every element kind its current kind
can (else: unlink first). `batch_operations(type)` is the one list of operations a batch step may name (REST and the
tools' `apply_batch` enum): the type's operations without `apply_batch` and the internal ones.

`link_element`, `unlink_element` and `clear_element` also clear the relation links of the edges and messages touching a
view element whose `ref` changes (result key `unlinked_relations`, ADR-0139).

## Model relations

Deployment, sequence and component diagrams have these operations (`RELATION_OPERATIONS`; design: [Model
relations](../architecture/specs/model-relations.md), ADR-0139); activity diagrams do not. The connection is a
deployment or component edge (by id) or a sequence message (by id); which relation kinds it may show is
`project_model.relation_kinds_for(type, view_kind)` (edge kind, or `message`): `connection` → communicates,
data_flow; `dependency`, `association`, `replication` → communicates; `deploys` → deployed_on; configured edge kinds →
communicates, data_flow; complete messages → communicates. `ends_fit` accepts either orientation except for
`data_flow`.

| Operation | Arguments | Behaviour |
|---|---|---|
| `link_relation` | `id`, `relation`, `kind`, `source`, `target` | The connection must be able to show `kind`, and the views at its ends must stand for `source` and `target` (their `ref`s). Sets `ref = relation`. **Internal.** |
| `unlink_relation` | `id` | Clears `ref`; not linked → error. |
| `sync_relation` | `relation`, `kind`, `source`, `target` | Clears the links of the relation that no longer fit; `touched` lists them. **Internal.** |
| `clear_relation` | `relation` | Clears every link of the relation and its hidden marker (`unhidden`). **Internal.** |
| `set_relation_hidden` | `relation`, `hidden` | Adds the id to (or removes it from) `Diagram.hidden_relations`, kept sorted; `relation` must be a slug. **Internal** (the model service checks the relation exists). |
| `set_completeness` | `kinds` | Sets `Diagram.complete` (sorted, unique) to relation kinds of `shown_relation_kinds(type)`; `["all"]` expands to all of them, `[]` makes the diagram open world again; another kind → error naming the allowed ones. Agent tool `set_completeness` (deployment, sequence). |

`update_message` clears the link of a message whose sender or receiver changes (`unlinked_relation`); `update_edge`
refuses a kind change of a linked edge to a kind that cannot show every relation kind the current kind can.
`POST /api/diagrams/{id}/ops` answers 400 for the internal ones, pointing to `/link-relation` (`set_relation_hidden`:
`/hidden-relations`).

## Batch

| Operation | Arguments | Validation and behaviour |
|---|---|---|
| `apply_batch` | `operations` (list of `{"op": name, "args": {...}}`) | 1 to `MAX_BATCH` (50) items. The whole list is checked first: each item an object, `op` a known operation other than `apply_batch` (no nesting) and not internal, `args` an object (omitted or `null` = `{}`). Steps then run in order through `apply()`, so each has the single operation's validation; later steps can reference elements created by earlier ones. If a step raises `OperationError`, every model field is restored to its pre-batch value (`_rollback`, shared with `apply`) and `OperationError("Batch step <i> (<op>) failed: <message> No step of the batch was applied.")` is raised (1-based). Any other exception also restores the pre-batch state and is re-raised unchanged. Returns `{"results": [{"op", "result", "touched"}, ...], "touched": [...]}`; `touched` is the ordered union of the steps' ids that still exist. |

Design: [Atomic batch operation](../architecture/specs/batch-operations.md). Since #66 a single
operation is atomic for every exception as well; the batch's own restore covers the steps before the
failing one.

## Reference resolution

`id`, `parent`, `source` and `target` go through `Diagram.find_node()` (id, then case-insensitive
name, then slugified ref). Error messages for unresolved references include the list of known
ids.

## Invariants guaranteed after any successful operation

- Every `parent` points to an existing container; no containment cycles.
- Every edge's endpoints exist.
- Node ids are unique (dict keys); edge ids are unique.
- The model is unchanged after a failed operation.
- Names, titles, labels and guards written by an operation are one line without control characters.

`DiagramRepository.save_latest()` re-checks the first two through `integrity_errors()`.
