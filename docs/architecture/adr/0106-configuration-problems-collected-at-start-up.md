# ADR-0106: Configuration problems are collected across subsystems and reported together at start-up

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/operations.md` (#118)

## Context

Numeric variables were parsed in four places: bare `int(...)` in `server.py` (`ARCVIZ_MAX_ROUNDS`, `ARCVIZ_CONTEXT_BUDGET`,
`ARCVIZ_HISTORY_TURNS`, `ARCVIZ_CHAT_COMPACT_AFTER`, `ARCVIZ_CHAT_KEEP`, `ARCVIZ_TURN_TOKEN_BUDGET`, `ARCVIZ_PORT`), which
stop the start with a `ValueError` that does not name the variable, and three helpers (`limits.env_int`,
`live._env_seconds`, `llm.factory._env_number`) that name it but stop at the first problem. An operator who fixes one
typo and redeploys learns about the next one a revision later (several minutes on Container Apps).

## Options

1. **Keep the helpers, wrap the bare `int()` calls.** Smallest change; still one problem per start.
2. **One `Env` reader that records problems and returns the default, one collector in `create_app`.** Every subsystem
   keeps its own `from_env`, which raises a `ConfigError` (a `ValueError`) with all of its problems; `create_app`
   gathers the problems of every subsystem and raises once. A module `arcviz/envconfig.py` without package imports
   that `limits`, `live` and `llm/` may use.
3. **A pydantic-settings model for the whole configuration.** One schema, but a new dependency and one large class
   that every subsystem would depend on.

## Decision

Option 2: one reader, one message listing every problem, no new dependency, and the subsystems keep owning their
variables.

## Consequences

Start-up fails once with the complete list. Stricter rules come with it (`ARCVIZ_MAX_ROUNDS ≥ 1`, `ARCVIZ_CHAT_KEEP ≥ 1`,
non-negative agent variables, `ARCVIZ_PORT` 1–65535); values that used to start with nonsensical behaviour now stop
the start. The module table allows `limits`, `live`, `llm/` and `observability` to import `envconfig`.
