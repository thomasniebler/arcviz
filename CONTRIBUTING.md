# Contributing

## Setup

```bash
make install      # venv from uv.lock (dev extras, docs toolchain), Playwright Chromium; `make lock` after editing pyproject.toml
make check        # lint, typecheck, unit tests, browser smoke test
make docs         # build the documentation (strict)
```

## Before you push

GitHub Actions bills every CI job rounded up to a full minute, and every push to a pull request into
`main` starts a new run. Run the checks locally first and push once they pass:

```bash
make lint typecheck test    # always
make docs                   # when docs/, CHANGELOG.md, CONTRIBUTING.md or zensical.toml changed
make test-js e2e            # when the frontend or the SSE protocol changed
```

`tests/test_properties.py` runs Hypothesis property tests over random operation sequences (operations →
integrity → Mermaid, exchange, comparison). Locally the examples are random, in CI (`CI` set) they are
fixed. A failure prints the minimal example: turn it into an example test next to the fix. For a deeper
search, register a profile with more `max_examples` and select it with `HYPOTHESIS_PROFILE`.

Do not push to find out whether CI passes or to re-trigger it (no empty commits, no close/reopen),
and batch commits into one push. Open a pull request as a draft while it is in progress: CI skips
drafts and runs when the pull request is marked ready for review.

## Prompt and tool evals

Changes to the system prompt, the tool descriptions or the agent loop should be checked against a real
model with the eval set in `tests/evals/` (a starting diagram, an instruction and loose expectations per
case; design in `docs/architecture/specs/agent-evals.md`). It calls the configured provider and
costs API tokens, so it never runs in `make check` or CI:

```bash
ANTHROPIC_API_KEY=... make eval                                     # all cases
make eval EVAL_ARGS="--case vnet-move --json evals.json --min-score 0.8"
```

Each line shows the score (share of passed checks), the operations, tokens, time and the failed checks.
Compare the mean score before and after your change and mention both in the pull request. New cases go
into `tests/evals/cases.json`; `tests/test_evals.py` checks the file offline.

## Workflow

- Issues are grouped in milestones. A milestone is worked on one branch `milestone/m<N>-<slug>`
  cut from `main`, with one [Conventional Commit](https://www.conventionalcommits.org/) per issue
  (`feat: …`, `fix: …`, `docs: …`, footer `Refs #n`) and one pull request into `main` that lists
  `Closes #n` for every issue. Pull requests are squash-merged (the only merge method enabled), so the
  milestone becomes one changelog entry: its PR title summarises the milestone with the most
  significant type (`feat` if any issue adds a feature, `!` if any is breaking). A change outside a
  milestone gets its own branch and pull request.
- PR titles must follow Conventional Commits; CI enforces this because squash merges use the title
  as the commit message and release-please derives versions and the changelog from it.
- `CHANGELOG.md`, the version in `pyproject.toml` and `arcviz/__init__.py` are maintained by
  release-please. Do not edit them by hand.

## Documentation is part of the change

The technical specification under `docs/spec/` describes the system as implemented. **A pull
request that changes the model, operations, HTTP API, LLM layer, agent behaviour, persistence,
rendering, configuration, CI or the release process must update the matching spec page.** New
environment variables go into `docs/reference/configuration.md`, endpoint changes into
`docs/reference/api.md`, user-visible behaviour into `docs/guide/`. Reviewers should treat a
missing spec update like a missing test.

## The documentation in the app

The documentation is not published separately: every image, wheel and sdist contains the built site
(`arcviz/docs_site/`), which the app serves under `/docs` (ADR-0116). `make docs` builds it strictly (CI does the
same when the docs change); `make docs-bundle` builds the copy the app serves, and `make run` does so when it is
missing. A wheel or sdist build without it fails with instructions.

## Code conventions

- Python 3.11+, type hints everywhere, `ruff` for lint and format (line length 120), `mypy` clean.
- All model mutations go through `arcviz/operations.py`.
- Frontend is plain ES modules without a build step; keep it that way unless an issue says
  otherwise.
- Tests: unit tests for every operation and store behaviour; the Playwright smoke test for
  anything that changes the UI or the SSE protocol.
