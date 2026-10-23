# ADR-0151: Git pushes run on a coalescing background worker

- Status: Accepted
- Date: 2026-10-11
- Spec: `docs/architecture/specs/robustness.md`

## Context

`GitMirror.record` committed and then pushed synchronously under one process-wide lock, with a 120 s timeout per git
call (#113, arc42 R5). With `ARCVIZ_GIT_PUSH=1` and a slow remote every version-creating request on every diagram
queued behind the push and the threadpool filled up. The module said pushing "never blocks the application".

## Options

1. **Everything on a background queue** (commit and push). Requests never wait for git, but commits could be reordered
   or lost on a crash between the store write and the queue.
2. **Commits on the request path, pushes on a worker that coalesces.** Commits are local and fast and keep the order of
   the versions; the slow, network-bound part runs once per burst of commits.
3. **Shorter timeouts only.** Still blocks every request for the timeout.

## Decision

Option 2: `_commit` asks for a push; one daemon worker pushes HEAD (bounded by `PUSH_TIMEOUT_SECONDS`, 60 s), requests
made meanwhile become one more push; shutdown drains it for at most `DRAIN_SECONDS` (10 s).

## Consequences

A slow remote delays no request. At shutdown a push that does not finish in time is abandoned (logged); the next
process pushes once at start (and every later commit pushes everything, since a push sends HEAD), as long as the
mirror directory survives the process (the server's lifespan starts that push; the constructor never does). Commit and
push errors are reported apart; a push clears only its own. The push status is visible as `git.push_pending` in `/api/config`.
