# Atomic batch operation

- Status: Implemented (milestone M6)
- Issue: #69
- ADR: [0007 Atomic batch as an ordinary operation](../adr/0007-atomic-batch-as-an-ordinary-operation.md)
- Depends on: [Operations](../../spec/operations.md) (`operations.apply` and its rollback)

## Summary

Building a structure such as "a VNet with three services and their edges" takes the agent several
tool calls, often in several rounds. Each successful call is saved immediately, so a failure halfway
leaves a half-built structure in the diagram, and every call costs a round trip and a save. A new
operation `apply_batch` takes a list of operations and applies them in order, all or nothing, with
one result and (in the agent) one save. It goes through the same dispatcher as every other
operation, so the REST endpoint `POST /api/diagrams/{id}/ops` gets it for free.

## Goals

- One operation that applies a list of existing operations in order; if any step fails, the diagram
  is exactly as before the batch and the error names the failing step.
- Later steps can refer to elements created by earlier steps (by explicit id or by name).
- One tool call for the model; one `save_latest` and one entry in `applied` in the agent.
- Available to the LLM (tool `apply_batch`) and to the REST API without new endpoints.

## Non-goals

- Typed per-operation argument validation and rollback on arbitrary exceptions in `apply` itself:
  that is #66. The batch is built on the current `apply`.
- Nested batches, conditionals or variables inside a batch.
- Making the inspector's multi-operation saves atomic (possible follow-up; the UI keeps posting
  single operations).

## Design

### Operation

```python
MAX_BATCH = 50

def apply_batch(diagram: Diagram, operations: list[dict[str, Any]]) -> dict[str, Any]
```

- `operations` must be a non-empty list of at most `MAX_BATCH` items. Each item is an object
  `{"op": <name>, "args": {...}}`; `args` may be omitted (empty). `op` must be a key of
  `OPERATIONS` other than `apply_batch` (no nesting).
- The whole list is validated for shape before the first step runs (wrong types, unknown or nested
  `op`, too many items) and rejected with `OperationError`.
- Steps run in order through `apply(diagram, op, args)`, so each step has exactly the validation,
  reference resolution and error messages of the single operation.
- If a step raises `OperationError`, the batch restores `nodes`, `edges` and `title` from a deep
  copy taken before the first step and raises
  `OperationError("Batch step <i> (<op>) failed: <message> No step of the batch was applied.")`
  (1-based `i`).
- If a step raises any other exception, the batch restores the same copy and re-raises the
  exception unchanged (it is not converted into an input error). This is stricter than single
  operations today (see *Interaction with #66*).
- Return value:

  ```json
  {"results": [{"op": "add_node", "result": {...}, "touched": ["prod-vnet"]}, ...],
   "touched": ["prod-vnet", "service-a", ...]}
  ```

  `touched` is the ordered union of the steps' `touched` ids that still exist after the batch.

### Dispatcher

`apply_batch` is an entry of `OPERATIONS`. `apply()` takes `touched` from a dict result's `touched`
key when present (batch), else from `reparented` (`remove_node`) as before.

### Tool

`agent.TOOLS` gains `apply_batch` with the schema

```json
{"type": "object",
 "properties": {"operations": {"type": "array", "minItems": 1, "maxItems": 50,
   "items": {"type": "object",
     "properties": {"op": {"type": "string", "enum": ["add_node", "update_node", "..."]},
                    "args": {"type": "object"}},
     "required": ["op", "args"], "additionalProperties": false}}},
 "required": ["operations"], "additionalProperties": false}
```

The `op` enum is every operation except `apply_batch`; `args` are described as "the arguments of the
tool of the same name". The system prompt gains one rule: use `apply_batch` to build several related
elements in one call; it applies all steps or none, and later steps may refer to elements created
by earlier ones.

### Agent

No special handling: a batch is one tool call, one `operations.apply`, one `save_latest`, one entry
in `applied` (`{"op": "apply_batch", "arguments": {...}, "result": {...}}`) and one `tool_result`
event with the intermediate diagram. `changed` gets the batch's `touched`. The compaction input
describes a batch as `apply_batch (<n> steps)`.

### REST

`POST /api/diagrams/{id}/ops` with `{"op": "apply_batch", "args": {"operations": [...]}}` works like
any operation: 400 with the batch error message on failure (nothing saved), otherwise one save and
one `auto` version.

### Interaction with #66

`apply()` currently rolls back only on `OperationError` and `TypeError`; other exceptions in a single
operation can leave a partial mutation. Inside a batch every exception restores the pre-batch state
before it propagates, so a batch never leaves earlier steps applied. Once #66 adds typed argument
validation in `apply`, the batch benefits without changes: its steps call `apply`. If #66 changes
`apply` to restore on every exception, the batch's own restore becomes redundant but harmless.

## Testing

- `tests/test_operations.py`: a batch builds container + children + edges with name references to
  elements created in the same batch; a failing step leaves the diagram unchanged (nodes, edges,
  title) and names the step; nested batch, empty list, too many items, non-dict items and unknown
  ops are rejected before any step runs; a non-`OperationError` exception restores and propagates;
  `apply()` reports the batch's `touched`.
- `tests/test_agent.py`: one `apply_batch` tool call produces one `applied` entry, one version and
  `changed` with all touched ids; a failing batch yields an error tool result and no version.
- `tests/test_server.py`: `POST /ops` with a batch returns 200 and one version; a failing batch
  returns 400 and leaves the diagram unchanged.

## Migration/compatibility

Additive. Stored data is unchanged. Clients that ignore unknown tools/operations are unaffected.

## Open questions

None.

## Decisions

- Batch as an operation in `OPERATIONS` rather than a separate dispatcher or endpoint; flat (no
  nesting); limit of 50 steps: [ADR-0007](../adr/0007-atomic-batch-as-an-ordinary-operation.md)
  (Proposed; chosen without the user, see the PR).
