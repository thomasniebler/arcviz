# ADR-0021: Hypothesis as a dev-only dependency for property-based tests

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/property-tests.md`

## Context

Issue #89 asks for property-based tests of the pipeline operations → integrity → Mermaid. The
operations accept free-form strings from an LLM, and the rendering has subtle rules (id sanitising is
not injective, labels must be escaped, focused views add stubs). The existing tests are example-based.
CLAUDE.md requires a stated reason for new dependencies and a self-contained wheel and image; CI is
billed per minute and must not be flaky.

## Options

1. **Hypothesis in the `dev` extra.** The standard property-testing library for Python: strategies,
   shrinking to a minimal failing example, a pytest plugin, settings profiles. Pure Python, no runtime
   impact; the wheel and the Docker image do not include dev extras.
2. **Hand-written random tests with `random.Random(seed)`.** No dependency, but no shrinking (a failure
   is a 40-operation sequence nobody can read), and every generator and its reporting would have to be
   written and maintained here.
3. **No property tests; more examples.** Cheapest, but does not answer the issue and keeps the class of
   bugs that only show up for unexpected combinations.

How Hypothesis runs in CI:

A. **Derandomised in CI** (`derandomize=True` when `CI` is set), random locally. CI results depend only
   on the code, so a red run is reproducible and never a lucky seed; local runs keep exploring.
B. **Random everywhere.** Finds more over time, but a failure can appear on an unrelated PR and costs
   a billed re-run to diagnose.

## Decision

Option 1 with A: Hypothesis as a dev-only dependency, derandomised in CI and random locally, because
shrinking makes failures actionable, it adds nothing to what is shipped, and a fixed CI search keeps
billed runs deterministic.

## Consequences

- `make install` installs Hypothesis; `tests/conftest.py` registers the `dev` and `ci` profiles.
- A property failing locally prints the minimal example; it becomes a regression example test (and an
  issue if the behaviour is a defect).
- CI explores the same examples on every run; the random local runs are where new counterexamples are
  found. The CI profile can be switched to random later without changing the tests.
