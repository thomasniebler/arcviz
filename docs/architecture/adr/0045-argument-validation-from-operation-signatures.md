# ADR-0045: Validate operation arguments against the operation signatures, roll back on every exception

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/operation-arguments.md`

## Context

Operation arguments are untrusted JSON from the model and from REST clients. `operations.apply`
rolled back only `OperationError` and `TypeError`; other exceptions raised by a wrong type (an
`AttributeError` on `kind.strip()`) escaped after earlier assignments and were persisted by the
agent's finalization (#66). Activity and sequence diagrams (M13) added 16 operations with their own
helpers, so checks written by hand per operation would have to cover 26 functions and every future one.
All operation functions already carry precise type annotations (`str | None`, `list[str] | None`,
`dict[str, Any] | None`, `bool`).

## Options

1. **Hand-written checks in every operation.** Exact messages, but 26 functions to audit and keep
   consistent; the bug class reappears with the next operation.
2. **A pydantic model per operation.** Strong typing and good errors, but a second declaration of every
   signature to keep in sync with the function and its `ToolSpec`, and pydantic's coercion (`"1"` → 1)
   would have to be switched off per field.
3. **Validate against the function signature** (`inspect.signature` + `typing.get_type_hints`), in
   `apply()`, before the deep copy; plus restore the diagram on every exception. One small checker for
   the handful of annotation shapes in use; adding an operation needs nothing extra.
4. **Only roll back on every exception.** Fixes the persistence of partial changes, but wrong types
   still surface as 500s and as defects instead of correctable tool errors.

## Decision

Option 3 together with the rollback of option 4: the signature is the single declaration of an
operation's arguments, and the rollback makes even a defect harmless to the stored state.

## Consequences

- Wrong types, unknown and missing arguments are `OperationError`s with the argument named, for every
  operation; the REST API answers 400, the model gets an error tool result it can act on.
- `TypeError` is no longer converted into an input error; an exception other than `OperationError` is
  a defect (REST 500, agent tool result "Internal error …", logged), never a partial change.
- Annotations of operation functions are now part of the API contract; the checker raises on an
  annotation shape it does not know, which the generated tests catch.
- Property values are restricted to scalars, and booleans are strict (inputs that were accepted by
  accident before are rejected now).
