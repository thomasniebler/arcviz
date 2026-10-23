# Operation arguments: validation before mutation

- Status: Implemented
- Issue: #66
- ADR: [0045](../adr/0045-argument-validation-from-operation-signatures.md)
- Depends on: [Operations](../../spec/operations.md) (`operations.apply`), [Atomic batch](batch-operations.md)

## Summary

Operation arguments arrive as arbitrary JSON objects, from the LLM's tool calls and from
`POST /api/diagrams/{id}/ops`. Until now `apply()` rolled back only `OperationError` and `TypeError`;
an argument of the wrong type that reached code like `kind.strip()` raised `AttributeError` after an
earlier assignment of the same operation, left that assignment in memory, and the agent's `finally`
block then persisted it (`update_node {"name": "changed", "kind": 42}` kept the new name). The REST
endpoint answered 500 for `{"name": 42}`.

This change validates every argument against the operation's signature before anything is touched,
and makes `apply()` restore the diagram on *every* exception. Wrong types, missing and unknown
arguments become `OperationError`s ("Invalid arguments for <op>: …"), which the REST API answers with
400 and the agent returns to the model as an error tool result. An exception that is not an
`OperationError` is a defect, not bad input: it is not disguised as an input error, but it can no
longer leave a partial change behind.

## Goals

- Wrong JSON types, missing required arguments, unknown arguments and a non-object `args` produce an
  `OperationError` naming the argument and the expected type, for every operation of every diagram
  type (deployment, activity, sequence), before the first change.
- Invalid enum values (kinds, directions, sorts, operators) stay the operations' own checks, with their
  messages listing the allowed values.
- A failed operation leaves the whole diagram unchanged, `updated_at` included, whatever it raised.
- REST: a documented 400 with the message in `detail`. Agent: an error tool result with the message.
- No new dependency; nothing to keep in sync by hand when an operation is added.

## Non-goals

- Changing the tool JSON schemas sent to the model (they already describe the types; the validation
  is the server-side guarantee that does not trust the model to follow them).
- Pydantic models per operation (see the ADR).
- Validation of semantic constraints across arguments (they stay in the operations).

## Design

### Validation from the signature

`operations.check_arguments(op, args)` runs in `apply()` after the operation lookup and before the
deep copy. It reads the operation function's keyword parameters (everything after `diagram`) with
`typing.get_type_hints` and `inspect.signature`, cached per function, and checks in this order:

1. `args` is an object, else `Invalid arguments for <op>: expected an object of named arguments, got …`.
2. No unknown names: `… unknown argument 'bogus'. Allowed: name, kind, id, parent, properties.`
3. Every given value matches its annotation: `… 'kind' must be a string or null, got a number.`
4. Every parameter without a default is present: `… missing required argument 'name'.`

The annotations are the contract. The checker understands exactly the types the operations use:
`str`, `bool`, `int`/`float` (numbers, never booleans), `None`, `Any`, unions of these, `list[T]` and
`dict[str, T]`. A new operation with another annotation fails loudly in the tests (`TypeError` for an
unsupported type), not silently at runtime.

Two annotations change:

- `properties: dict[str, PropertyValue] | None` with `PropertyValue = str | int | float | bool` on
  `add_node`, `update_node`, `add_edge` and `update_edge`. Scalar values are still stored as strings
  (`443` → `"443"`); `null`, arrays and nested objects are rejected (before, `{"a": {"b": 1}}` was
  stored as the string `"{'b': 1}"` and a list of properties raised `AttributeError`).
- `apply_batch(operations: list[Any])`: the generic check only asserts a list, so the batch keeps
  its own per-step messages (`Batch step 2 must be an object …`).

Booleans are strict: `cascade: "false"` is rejected (before, the non-empty string was truthy and
cascaded the deletion).

### Rollback on every exception

`apply()` takes the deep copy after validation and restores every model field on any exception
(`BaseException`, so even a `KeyboardInterrupt` in the middle of an operation cannot leave a half
change in an object the agent later persists). `OperationError` is re-raised as is; any other
exception is re-raised unchanged too. The former wrapping of `TypeError` into `OperationError` is
gone: with the signature check, a `TypeError` can only come from a defect and must not be reported as
the caller's mistake.

### Callers

- `POST /api/diagrams/{id}/ops`: `OperationError` → 400 (unchanged); any other exception → 500 with
  nothing written (the exception happens before `save_latest`).
- `DiagramAgent.chat`: `OperationError` → error tool result (unchanged). Any other exception is
  logged with its traceback and becomes the error tool result `Internal error while applying <op>
  (<ExceptionType>); nothing was changed.`, so the turn continues and ends with a `final` event. The
  model is told it is an internal error, not an input error.
- `proposal.apply_proposal`: replays through `apply()`; `OperationError` → `ProposalConflict` (409).

## Testing

- `tests/test_operation_arguments.py`: the issue's example (`update_node` with a valid name and
  `kind: 42`) and malformed properties leave the diagram unchanged incl. `updated_at`; a table of
  wrong types, missing/unknown arguments, invalid enums and non-object `args`; every string parameter
  of every operation of every diagram type rejects a number, an array and an object (generated from
  the signatures, so new operations are covered automatically); an injected defect is rolled back and
  re-raised unchanged; REST answers 400 and writes no version; the agent returns an error tool result,
  persists nothing partial and finishes with `final`, for bad input and for an injected defect.
- `tests/strategies.py`: one generated operation in ten gets an argument of the wrong JSON type, a
  dropped or an unknown argument, or a non-object `args` (`ill_typed`), batch steps included. The
  property test fails on any exception other than `OperationError` and checks that a rejected
  operation changes nothing.

## Migration / compatibility

Callers that sent correctly typed arguments see no difference. Behaviour changes for inputs that were
accepted by accident: property values `null`, arrays and objects; non-boolean `cascade`/`actor`. The
LLM tools always declared these types. Error texts for wrong keyword arguments changed from Python's
`TypeError` text to the messages above (still starting with `Invalid arguments for <op>`).

## Decisions

- Validation derived from the operation signatures, not hand-written per operation or pydantic models
  (ADR-0045, *Proposed*).
- Strict booleans and scalar-only property values (conservative: reject what the tool schema does not
  allow).
- Defects are reported to the model as internal errors and the turn continues (a crash would lose the
  `final` event the UI needs); REST keeps answering 500.

## Open questions

None.
