---
name: implement
description: Standard implementation workflow for a feature or refactor in this repo - spec in docs/architecture first (with ADRs), one issue per topic, tests before code, implementation along the module boundaries, verification against the spec, then a PR. Use for any change larger than a bug fix, and whenever the user says "implement", "build", "add feature" or points at an issue.
---

# Implementation workflow

One topic per spec, per issue and per PR. If a request mixes topics (for example "swap the
storage backend" and "compact the chat"), split it before step 1 and run the workflow once per topic.

The steps are sequential. Do not start a later step while an earlier one has open questions; ask the
user instead.

## 1. Spec first (docs/architecture)

Two kinds of documents exist, with different jobs: `docs/spec/` describes the system **as
implemented** and is updated together with the code (CLAUDE.md rule 1); `docs/architecture/` holds
the **design** of a change before it exists (`specs/`) and the decisions behind it (`adr/`). This
step writes the design; step 6 updates `docs/spec/`.

- Write or update `docs/architecture/specs/<topic>.md`. Use the structure of the existing specs:
  Summary, Goals, Non-goals, Design (interfaces, data models, configuration, error handling,
  concurrency), Testing, Migration/compatibility, Open questions, Decisions.
- Every decision with a real alternative gets an ADR in `docs/architecture/adr/` (copy
  `0000-template.md`, next free number, status `Accepted` once the user agreed). A spec links its ADRs;
  an ADR links the spec.
- If the change moves a module boundary, update the module table in `docs/architecture/index.md`.
- The spec is in English, like the rest of the repository.
- Resolve open questions with the user **before** writing tests. Record the answers under Decisions.

## 2. Issue

- One GitHub issue per topic. Title in Conventional Commits style (`feat(store): …`,
  `refactor(agent): …`), label `enhancement` or `documentation`.
- Body: two-paragraph summary, link to the spec and its ADRs, acceptance criteria as a checklist,
  the planned PR(s). The spec is the source of truth; do not duplicate it into the issue.
- If an issue exists already, update it to point at the spec and trim it to this topic.

## 3. Tests before code

- Translate the spec into tests first: contract tests for interfaces (parametrised over all
  implementations), unit tests for logic, a server test for every new or changed endpoint, an e2e
  assertion when the UI changes. Use `ScriptedProvider` from `tests/conftest.py` for anything that goes
  through the agent; tests must not need network or API keys.
- Run them; they must fail for the right reason before implementation starts.

## 4. Implement along the boundaries

- Respect the module table in `docs/architecture/index.md`: a module only imports what the table
  allows. New runtime code lives under `arcviz/` (the Dockerfile copies nothing else).
- Configuration is read from the environment in exactly one place per subsystem (a factory), never
  scattered through modules.
- Optional dependencies are extras in `pyproject.toml` and imported lazily with a clear error.
- Keep the change to the spec's scope. Something useful you notice on the way becomes a new issue,
  not part of this PR.

## 5. Verify

Run the project's own checks and fix everything they report:

```bash
make lint typecheck test      # ruff, mypy, pytest with coverage
make e2e                      # when server.py, static/ or the SSE protocol changed
```

## 6. Spec and docs conformance

Walk the spec section by section against the diff and tick each item off:

- Every interface, data model, config variable and error behaviour in the spec exists in the code
  with the same name and semantics, and nothing in the code exists that the spec does not mention.
- If the implementation deviated for a good reason, update the spec (and ADR if it was a decision)
  in the same PR and list the deviation in the PR body. The spec must describe what was merged.
- Update the as-implemented reference: the matching page(s) under `docs/spec/`,
  `docs/reference/configuration.md` for new variables, `docs/reference/api.md` for endpoints,
  `docs/guide/` for user-visible behaviour (CLAUDE.md rule 1). Run `make docs`.
- Update `CLAUDE.md`, `README.md` and the architecture overview if the change affects them.

## 7. Pull request

- Issue of a milestone: commit it on the milestone branch `milestone/m<N>-<slug>` (CLAUDE.md rule 6)
  with footer `Refs #<issue>`; the milestone gets one PR into `main` once all its issues are done.
  Otherwise one branch per topic, base `main`.
- CI runs only for PRs into `main` and bills every job by the full minute (CLAUDE.md *CI minutes*):
  run the local checks first, push once, do not push to re-trigger CI.
- Title in Conventional Commits (enforced by CI; release-please reads it).
- Body: Summary, Spec/ADR links, `Closes #<issue>`, the acceptance checklist from the issue with
  the boxes ticked, "Deviations from spec" (or "none"), "How verified" (the commands you ran).
- Push, open the PR, watch CI and reviews until it is merged (`subscribe_pr_activity` when available).

## Definition of done

Spec and ADRs merged and matching the code; issue closed by the PR; `make check` green in CI;
README/CLAUDE.md updated; no TODOs in the diff that the spec does not list as a follow-up.
