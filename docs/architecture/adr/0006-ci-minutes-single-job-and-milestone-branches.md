# ADR-0006: One CI job, path-scoped steps and milestone branches

- Status: Accepted
- Date: 2026-10-04
- Spec: `docs/spec/build-and-release.md` (CI section)

## Context

GitHub Actions bills every job rounded up to a full minute. CI had ten jobs per run (lint, mypy,
three pytest versions, Playwright, Docker, docs, the Cosmos DB emulator, the `ci-ok` aggregator);
each took 2–35 s but cost a full minute, so one run cost ten billed minutes for about three minutes
of work. CI ran on every PR push and again on every push to `main`, and `pr-title.yml` ran on every
PR push. Of the last 300 workflow runs, 128 were CI (about half on `main`) and 118 the title check.
Work was done as one PR per issue, so every issue paid for several full runs.

## Options

1. **Keep parallel jobs, trim triggers.** Fast feedback (about one minute wall clock), but each job
   still costs at least a minute, and branch protection needs an aggregator job (another minute).
2. **Merge only the short jobs** (lint, typecheck, docs) as originally proposed in #55. Saves three
   to four minutes per run; pytest, Playwright, Docker and Cosmos still cost one minute each.
3. **One job, every check a step**, gated by the changed paths, plus fewer triggers. About three
   billed minutes for a full run and one for a docs-only change; wall clock grows to about three
   minutes; one job is the single required check without an aggregator.

Independently of the CI layout: **one PR per issue** (every issue pays for its own runs) or **one
branch per milestone** with one commit per issue and one PR into `main` (CI runs for the milestone as
a whole).

## Decision

Option 3, with CI triggered only by pull requests into `main` (plus a weekly and a manual full run),
and milestone branches, because the job count, not the run time, drives the bill.

## Consequences

- Branch protection (none is configured yet) should require `CI` and the commit status `PR title`;
  `CI OK` and the old per-job check names no longer exist. The title check posts a commit status
  instead of failing `CI` because title edits do not start CI: `pr-title.yml` re-checks on title edits
  and overwrites the status, so a title made invalid after a green run still blocks the merge.
- A failing step does not stop the others (`if: !cancelled()`), so one run still reports every
  problem; the log is longer and there is no per-check status in the PR's check list.
- Merges to `main` are not re-tested; the weekly run (and `workflow_dispatch`) covers the Python
  matrix, the Docker build and the Cosmos emulator. A break on `main` caused by a stale PR base can
  surface up to a week late.
- Milestone PRs are merged with a merge commit or rebase, not squashed, so release-please still sees
  one Conventional Commit per issue.
- Agent instructions (`CLAUDE.md`, section *CI minutes*) make local checks before every push and
  batched pushes the default.

## Amendment (2026-10-04)

The repository allows squash merges only (merge commits and rebase merges are disabled), so milestone
PRs are squash-merged after all: one commit and one changelog entry per milestone instead of one per
issue. The milestone PR title carries the most significant Conventional Commits type of its issues
(`feat` over `fix` over the rest, `!` if any change is breaking) so release-please still bumps the
version correctly; the squash message lists the issue commits and the `Closes` lines. The consequence
above about merge commits or rebase no longer applies.
