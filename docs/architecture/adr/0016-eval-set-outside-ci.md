# ADR-0016: Eval set as JSON cases with a standalone runner outside pytest and CI

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/agent-evals.md`

## Context

Issue #76 asks for a small eval suite (request → expected diagram, scored loosely) that runs on demand
against a real provider and is never part of the default CI run. Unit tests must not need network or API
keys (CLAUDE.md); CI minutes are billed and the CI job is the only workflow that runs on pull requests.

## Options

1. **pytest module with a marker** (`@pytest.mark.eval`, deselected by default via `addopts`). Familiar
   runner, but a plain `pytest tests/test_x.py` or a changed `addopts` would call a paid API from CI;
   pass/fail per test fits partial scores badly.
2. **JSON cases plus a standalone runner** (`python -m tests.evals.run`, `make eval`). Not collectable
   by pytest at all, prints a score table, writes a JSON report. The harness can still be unit-tested
   offline with `ScriptedProvider`.
3. **A `workflow_dispatch` GitHub workflow** running option 2 with a repository secret. Convenient for
   comparing branches, but adds a workflow (CLAUDE.md asks for a stated budget for new workflows) and a
   secret; can be added later on top of option 2.
4. **LLM-as-judge grading.** Handles open-ended answers, but costs a second model call per case and is
   itself noisy; the expected outcomes here are structural and checkable.

## Decision

Option 2 with check-based loose scoring: the eval cannot run by accident in pytest or CI, and the
scoring is deterministic given the model's output.

## Consequences

- Running the evals is a manual step (`make eval` with a key); results are not tracked automatically.
- `tests/test_evals.py` keeps the case file and the harness working in every CI run without a provider.
- A workflow (option 3) remains a possible follow-up issue with its own budget.
