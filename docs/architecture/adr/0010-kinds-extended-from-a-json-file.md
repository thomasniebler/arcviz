# ADR-0010: Element kinds extended from a JSON file at startup

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/configurable-kinds.md`

## Context

Kinds are closed `frozenset`s in `model.py` that feed validation, tool enums, the system prompt,
`/api/config` and Mermaid styles. Issue #72 asks for extensible vocabularies (AWS, GCP, K8s)
without code changes. Configuration is environment-only today (`docs/reference/configuration.md`),
read in factories and `server.py`. Runtime dependencies must be justified (CLAUDE.md rule 5).
Several modules import the kind constants at import time.

## Options

Format and source:

1. **JSON file named by `ARCVIZ_KINDS_FILE`.** Standard library only, the same format as the API
   and stored data, easy to mount into the container. No comments in the file.
2. **TOML file** (`tomllib` is in the standard library since 3.11). Comments allowed; a second
   format in the project.
3. **Environment variables listing kinds** (`ARCVIZ_EXTRA_LEAF_KINDS=lambda,s3`). Trivial, but no
   room for styles, shapes or edge semantics.
4. **Kinds stored in the database, editable via API.** Per-deployment changes at runtime; needs
   an API, UI, migrations and cache invalidation; out of scope for the issue.

Semantics: extend-only vs. allow overriding/removing built-ins; strict validation (fail to start)
vs. lenient (skip bad entries); process-wide catalogue vs. passing it through every call.

## Decision

Option 1, **extend-only**, **strict** (any error stops the server with a message naming the kind),
**process-wide** (`model.use_kinds()` at startup, read through `model.active_kinds()` at call
time). Extend-only keeps every stored diagram valid and the built-in styles stable; strictness
avoids a silently different vocabulary; a process-wide catalogue matches how kinds are used
(`Node.is_container` has no context to receive it).

## Consequences

- New module `arcviz/kinds.py` (loading and validation, imports `model`); `model` gains the
  catalogue type and the active-catalogue accessor; the module table is updated.
- `TOOLS` remains the built-in tool list; the agent builds tools per turn from the active catalogue.
- Tests must restore the built-in catalogue (autouse fixture).
- Kind names are restricted to `[a-z][a-z0-9_]*` because they are Mermaid class names.
- Removing a kind from the file leaves elements of that kind loadable and renderable with the
  default style; a node with children is always drawn as a subgraph.
