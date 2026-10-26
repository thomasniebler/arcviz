# Build, CI and release

Sources: `pyproject.toml`, `Makefile`, `.github/workflows/*.yml`, `.github/actions/setup/action.yml`,
`Dockerfile`, `docker-compose.yml`, `release-please-config.json`, `.release-please-manifest.json`,
`zensical.toml`, `uv.lock`, `scripts/ci_scope.py`, `scripts/check_pr_title.py`.

## Packaging

- Build backend `hatchling`; package `arcviz` including `arcviz/static/` (with the vendored
  Mermaid build in `static/vendor/`, about 3.6 MB, and the CC0 icon packs in `static/vendor/icons/`,
  about 88 kB), the kind packs in `arcviz/kindpacks/` and the documentation site `arcviz/docs_site/`
  (git-ignored, a hatch `artifact`; about 7 MB, 1.5 MB compressed;
  [ADR-0042](../architecture/adr/0042-docs-site-built-into-the-package.md)). Every sdist and wheel contains it
  ([ADR-0116](../architecture/adr/0116-documentation-only-in-the-project.md)): the custom build hook `hatch_build.py`
  (`[tool.hatch.build.hooks.custom]`) fails a non-editable build without `arcviz/docs_site/index.html` and says how
  to build it; editable installs are exempt. `scripts/check_wheel_docs.py <wheel>…` checks a built wheel for the
  site's `index.html` and the arc42 index.
- Console script `arcviz = arcviz.server:main`.
- Runtime dependencies: `fastapi`, `uvicorn[standard]`, `pydantic`.
- Extras (imported lazily, [ADR-0107](../architecture/adr/0107-llm-sdks-as-extras-checked-at-start-up.md)): `anthropic`, `openai` (the LLM SDKs; start-up checks the configured provider's; `openai>=1.106` for a callable key), `azure-openai` (`openai`, `azure-identity`: Entra ID tokens, ADR-0115), `telemetry` (`azure-monitor-opentelemetry`, `opentelemetry-exporter-otlp-proto-http`: `ARCVIZ_TELEMETRY`, ADR-0114), `cosmos` (`azure-cosmos`, `azure-identity`), `mcp` (`mcp` SDK 2.x for `ARCVIZ_MCP=1`, `pyjwt[crypto]` for OIDC access tokens; ADR-0170, ADR-0173), and `dev` (`pytest`, `pytest-cov`, `httpx`, `ruff`, `mypy`, `hypothesis` and, through a self-reference, every runtime extra).
  Hypothesis drives the property tests in `tests/test_properties.py`; `tests/conftest.py` selects the
  settings profile `ci` (derandomised) when `CI` is set, else `dev` (random), overridable with
  `HYPOTHESIS_PROFILE` (ADR-0021).
- Docs toolchain in the PEP 735 dependency group `docs` (`zensical`; not package metadata).
- Build backend pinned exactly (`hatchling==1.32.4` in `[build-system]`).

## Locked dependencies (`uv.lock`)

[ADR-0109](../architecture/adr/0109-uv-lock-for-every-python-install.md): `uv.lock` (uv's universal lock: every
package of every extra and the dependency group, with hashes) is committed and
is the only source of Python packages for every install of the project:

| Where | Command |
|---|---|
| `make install` | `uv sync --locked --extra dev --group docs` |
| CI (`.github/actions/setup`) | `uv sync --locked --extra dev --python <v>` (uv `0.12.24`, cache keyed on `uv.lock`) |
| CI docs step | `uv sync --locked --extra dev --group docs` |
| CI 3.11/3.13 (scheduled) | `UV_PROJECT_ENVIRONMENT=<venv> uv sync --locked --extra dev --python <v>` |
| CI wheel check | `uv sync --locked --extra dev --group docs`, `scripts/build_docs_bundle.py`, `uv build --wheel`, `scripts/check_wheel_docs.py` |
| `Dockerfile` | `uv sync --locked --only-group docs --no-install-project` (docs stage); `uv sync --locked --no-dev [--no-install-project / --no-editable]` with the image's extras into `/opt/venv` |
| release (`publish-wheel`) | the docs stage's command, `uv build`, and `uv export --frozen --no-emit-project --no-default-groups --format requirements.txt` with every runtime extra → `dist/arcviz-agent-<version>-requirements.txt` |

`--locked` fails when `pyproject.toml` changed without `uv lock`. The wheel's metadata keeps the lower bounds of
`pyproject.toml`; the requirements file attached to a release reproduces the tested set:
`pip install --require-hashes -r arcviz-agent-<v>-requirements.txt` and then
`pip install --no-deps arcviz_agent-<v>-py3-none-any.whl`.

### Updating dependencies

- After editing `pyproject.toml`: `make lock` (`uv lock`), commit `uv.lock` with the change.
- Move every pin to the newest allowed version: `make upgrade` (`uv lock --upgrade`; one package:
  `uv lock --upgrade-package <name>`), then `make install check` and `make docs`.
- Base images ([ADR-0110](../architecture/adr/0110-base-images-pinned-by-digest.md)): the `FROM` lines name an exact
  version and its digest (`python:3.14.<z>-slim-trixie@sha256:…`, `ghcr.io/astral-sh/uv:<x.y.z>@sha256:…`, and the
  `# syntax=docker/dockerfile:<x.y.z>@sha256:…` frontend). Look up the digest of a new tag with
  `docker buildx imagetools inspect <image>:<tag>` (the `Digest:` of the index) and edit the line. A new uv release
  goes into the `Dockerfile`, `.github/actions/setup/action.yml` and the uv step of
  `release-please.yml` together (`tests/test_reproducible_builds.py` checks that the image and CI use the same).
- GitHub Actions are pinned by commit SHA with the version as a comment (`uses: actions/checkout@<sha> # v4.4.0`;
  `tests/test_reproducible_builds.py` checks every `uses:`).
- **Dependabot** (`.github/dependabot.yml`, ADR-0110): ecosystems `uv` (`uv.lock`), `docker` (the Dockerfile's
  `FROM` lines with digests) and `github-actions` (the workflows and `.github/actions/setup`), each monthly with one
  grouped pull request (commit prefixes `build`/`ci`, so the titles pass *PR title*); security updates stay
  ungrouped. Budget: about one to three pull requests and CI runs a month. A Dependabot bump of the uv image needs the
  same version in `.github/actions/setup/action.yml` and `release-please.yml` (the test fails until then); the
  `# syntax=` frontend line is updated by hand.
- Not pinned: the Debian packages the image installs with `apt-get` (current security fixes).
- Version is maintained by release-please in `pyproject.toml`, `arcviz/__init__.py` and the project's own entry in
  `uv.lock` (a `toml` extra-file updater), so the release pull request keeps `uv sync --locked` working.

## Local workflow (`Makefile`)

| Target | Runs |
|---|---|
| `install` | `uv sync --locked --extra dev --group docs` (see *Locked dependencies*), Playwright Chromium for e2e |
| `lock` / `upgrade` | `uv lock` / `uv lock --upgrade` |
| `lint` / `format` | `ruff check`, `ruff format --check` / with fixes |
| `typecheck` | `mypy` (config in `pyproject.toml`: `check_untyped_defs`, `warn_unused_ignores`) |
| `test` | `pytest --cov=arcviz` |
| `test-js` | `node --test tests/js/*.test.mjs` (pure frontend modules such as `commands.js`, plus the module-structure rules in `modules.test.mjs`) |
| `e2e` | `npm test` in `tests/e2e` |
| `eval` | `python -m tests.evals.run $(EVAL_ARGS)`: the prompt/tool eval set against the configured real provider (needs an API key; not part of `check` or CI; design `docs/architecture/specs/agent-evals.md`) |
| `check` | lint, typecheck, test, e2e |
| `docs` / `docs-serve` | `zensical build --clean --strict` / `zensical serve` on port 8001 |
| `docs-bundle` | `python scripts/build_docs_bundle.py`: the offline site the app serves under `/docs`, into `arcviz/docs_site/` (see *Documentation site*) |
| `diagrams` | `python scripts/render_architecture_diagrams.py`: render the architecture diagrams (`docs/architecture/diagrams/*.arcviz.json`) into `*.mmd` |
| `run` / `docker` | start the server (building `arcviz/docs_site/` first when it is missing) / build the image locally |
| `vendor` | `python scripts/vendor_mermaid.py` to refresh the pinned Mermaid build |
| `vendor-icons` | `python scripts/vendor_icons.py` to rebuild the icon packs from the pinned CC0 sources |

Ruff: line length 120, rules `E F I B UP SIM W`, `E501` ignored.

## CI (`ci.yml`)

Design and rationale: [ADR-0006](../architecture/adr/0006-ci-minutes-single-job-and-milestone-branches.md).
GitHub Actions bills every job rounded up to a full minute, so CI is **one job** (`CI`) whose checks
are steps.

CI was paused from 2026-10-21 to 2026-10-26: GitHub-hosted runners did not start on the repository (each job ended after about
three seconds with `runner_id` 0 and no steps), so the automatic triggers were commented out. They are back now that the
repository is public again; a pull request is still verified locally first (`make lint typecheck test test-js e2e docs`,
CLAUDE.md, *Commands*).

Triggers: pull requests into `main` (`opened`, `synchronize`, `reopened`, `ready_for_review`; draft
pull requests skip the job), a weekly schedule (Mondays 04:17 UTC) and `workflow_dispatch`. There is
no `push` trigger: merges to `main` are not re-tested, and pull requests into other branches
(milestone branches) run no CI. Concurrency per ref with cancellation.

The step *Detect scope* runs `scripts/ci_scope.py`: on pull requests it reads the files changed by the
merge commit against its base parent (`git diff --name-only HEAD^1 HEAD`, checkout with
`fetch-depth: 2`) and prints one `scope=true|false` line per scope into `$GITHUB_OUTPUT`; on scheduled
and manual runs (`--all`) every scope is on. Any change under `.github/` or to `ci_scope.py` turns
every scope on except `full`.

| Scope | Changed paths | Steps |
|---|---|---|
| (always) | | `ruff check --output-format=github`, `ruff format --check`, `mypy`; PR title status (pull requests) |
| `python` | `arcviz/`, `tests/`, `scripts/`, `pyproject.toml`, `uv.lock`, `docs/architecture/diagrams/`, `docs/architecture/arc42/` (checked by `tests/test_architecture_docs.py`) | `pytest` with coverage on 3.14; `node --test tests/js/*.test.mjs`; Playwright Chromium + `npm test` in `tests/e2e`; JUnit, coverage XML and e2e screenshots as artifact `ci-results` |
| `docs` | `docs/`, `zensical.toml`, `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, `CONTRIBUTING.md` | `uv sync --locked --extra dev --group docs`, `zensical build --clean --strict` |
| `docker` | `Dockerfile`, `.dockerignore`, `pyproject.toml`, `uv.lock`, `arcviz/` except `arcviz/static/`, and the docs and wheel build setup (`zensical.toml`, `scripts/build_docs_bundle.py`, `scripts/check_wheel_docs.py`, `hatch_build.py`) | buildx build with GHA cache, run the container, poll `/api/config`, assert `"templates"` in the body, fetch `/docs/` and `/docs/architecture/arc42/`; then the wheel check: docs group, `build_docs_bundle.py`, `uv build --wheel`, `check_wheel_docs.py` |
| `e2e` (with `python`) | `arcviz/static/`, `tests/e2e/`, `arcviz/server.py`, `arcviz/ports.py`, `arcviz/services/`, `arcviz/store/`, `arcviz/projects.py`, `arcviz/agent.py`, `arcviz/project_agent.py`, `arcviz/agent_*.py` | the smoke test a second time with `ARCVIZ_E2E_REAL_IDS=1` (the production ids `<slug>-<random suffix>` instead of the pinned readable ones, #196), a step after the first run that reuses its browser |
| `cosmos` | `arcviz/store/`, `tests/test_store*`, `tests/store_contract.py`, `tests/cosmos_fake.py`, `scripts/wait_for_cosmos.py`, `pyproject.toml`, `uv.lock` | Starts the Cosmos DB Linux emulator (`vnext-preview`) right after checkout so it warms up during the other steps; later waits with `scripts/wait_for_cosmos.py` until real SDK requests succeed (the data plane answers 503 while `pgcosmos` is starting), then `pytest tests/test_store_cosmos_live.py`. Only that step gets `ARCVIZ_TEST_COSMOS_*` (otherwise the plain pytest steps would collect the live module). Start and test steps are `continue-on-error: true` until the emulator has proven stable on GitHub runners (ADR-0004). |
| `full` | scheduled and manual runs only | `pytest` on 3.11 and 3.13 in extra venvs under `$RUNNER_TEMP` |

Check steps run with `if: !cancelled()`, so one failing step does not hide the others; the job fails
if any non-`continue-on-error` step failed. The composite action `.github/actions/setup` installs uv,
Python 3.14 and the project with dev extras from `uv.lock` into `.venv`.

The PR title must follow Conventional Commits (`feat`, `fix`, `perf`, `refactor`, `docs`, `test`,
`build`, `ci`, `chore`, `revert`; optional scope; optional `!`). `scripts/check_pr_title.py --status`
checks it and posts the result as the commit status **`PR title`** on the PR's head commit
(`statuses: write`) instead of failing the job. The CI job runs it on every push; title edits do not
start CI, so `pr-title.yml` runs the same command on `edited` events whose payload has `changes.title`
(non-draft pull requests into `main`) and overwrites the status. The newest status wins, so the
required checks for branch protection are `CI` and `PR title`.

## Releases (`release-please.yml`)

Only squash merges are enabled on the repository, so every pull request lands on `main` as one commit
whose message is the PR title (Conventional Commits, status `PR title`). A milestone (one branch
`milestone/m<N>-<slug>` with one commit per issue, CLAUDE.md rule 6) is therefore one commit and one
changelog entry; its PR title carries the most significant type of its issues (`feat` over `fix` over
the rest, `!` if any change is breaking) so the version bump stays correct, and the squash message
lists the issue commits and the `Closes #n` lines ([ADR-0006](../architecture/adr/0006-ci-minutes-single-job-and-milestone-branches.md)).

On pushes to `main`, release-please (release type `python`, `bump-minor-pre-major`) maintains a release PR with
`CHANGELOG.md` and version bumps. Before 1.0 a `feat` bumps the minor version (0.4.1 → 0.5.0), a `fix`/`perf` the
patch version, and a breaking change (`!`) also the minor version.
Changelog sections: Features, Bug Fixes, Performance, Refactoring, Documentation, Build & CI;
`test` and `chore` are hidden. Merging the release PR creates tag `vX.Y.Z` and a GitHub release;
the workflow then:

- builds and pushes `ghcr.io/thomasniebler/arcviz:<version>`, `:<major.minor>` and
  `:latest`;
- installs the `docs` dependency group from `uv.lock`, builds the documentation site into `arcviz/docs_site/`
  (`scripts/build_docs_bundle.py`), builds the wheel with `uv build`, exports the locked runtime dependencies with
  hashes (`arcviz-agent-<version>-requirements.txt`) and attaches `dist/*` to the release;
- checks the wheel with `scripts/check_wheel_docs.py` (the build hook already refuses one without the site).

Every artifact carries the site of its version, served under `/docs` (ADR-0116). The public Pages copy of `main` is
published separately (next section); the `gh-pages` branch of earlier releases is no longer updated.

## Documentation on GitHub Pages (`docs-pages.yml`)

[ADR-0201](../architecture/adr/0201-documentation-on-github-pages.md). One job `Publish docs` on pushes to `main` that
touch `docs/**`, `zensical.toml`, `CHANGELOG.md`, `CONTRIBUTING.md`, `pyproject.toml` or `uv.lock` (the toolchain pins), and on `workflow_dispatch`: `uv sync --locked
--only-group docs --no-install-project`, `zensical build --clean --strict` (published configuration, `site_url`
`https://thomasniebler.github.io/arcviz/`), then `actions/configure-pages`, `upload-pages-artifact` (directory `site`)
and `deploy-pages` in the environment `github-pages` (permissions `pages: write`, `id-token: write`). The repository
setting Pages → Source must be "GitHub Actions". The site is one version, the state of `main`; the release workflow
does not publish docs.

Before merging the release PR, add the entry of the new version to `arcviz/whatsnew.json` (curated highlights shown as a
banner in the app, [spec](../architecture/specs/whats-new.md)); `tests/test_whatsnew.py` checks the file.

A `RELEASE_PLEASE_TOKEN` secret (PAT) is optional; without it the release PR is created with
`GITHUB_TOKEN` and CI does not run on it.

## Container

`Dockerfile` (every image pinned by version and digest, ADR-0110): a stage `uv` (`ghcr.io/astral-sh/uv:0.12.24@…`)
provides the uv binary; a stage `docs` (`python:3.14.7-slim-trixie@…`, the `docs` dependency group from
`uv.lock` into `/opt/docs`) runs `scripts/build_docs_bundle.py` on `docs/`, `zensical.toml`,
`CHANGELOG.md` and `CONTRIBUTING.md`; the runtime stage copies its `arcviz/docs_site` into the package
before installing. Runtime stage: the same Python image plus `git` (for the optional Git mirror), then
`uv sync --locked --no-dev --no-install-project` (the locked dependencies, a cached layer) and, after copying
`arcviz/`, `uv sync --locked --no-dev --no-editable` (the project) into `/opt/venv` (on `PATH`), both with
`--extra` for every name in `ARG ARCVIZ_EXTRAS` (default every runtime extra:
`anthropic,openai,azure-openai,cosmos,telemetry,mcp`; [ADR-0108](../architecture/adr/0108-the-image-installs-every-runtime-extra.md); a build argument for a slimmer image), unprivileged user `arcviz`
(uid 10001), volume `/data`, env `ARCVIZ_DATA_DIR=/data`, `ARCVIZ_HOST=0.0.0.0`,
`ARCVIZ_PORT=8000`, `HEALTHCHECK` on `/healthz` (liveness only), `CMD ["arcviz"]`. `.dockerignore` excludes
tests, `.github`, every `.venv`, `data`, the local build outputs `site`, `.cache` and `arcviz/docs_site`
(the image always builds its own) and `.zensical-bundle.yml`.

`docker-compose.yml`: one service, port 8000, provider, Git-mirror, authentication and log variables passed through,
named volume `arcviz-data` at `/data`, `restart: unless-stopped`.

## Documentation site

Built with Zensical from `docs/` (`zensical.toml`); `make docs` builds strictly, CI does the same
when a pull request changes the docs (scope `docs`). Snippets resolve from the repository root: `docs/changelog.md`
includes `CHANGELOG.md`, `docs/contributing.md` includes `CONTRIBUTING.md`, and the arc42 pages
include the rendered architecture diagrams `docs/architecture/diagrams/*.mmd`
([ADR-0040](../architecture/adr/0040-architecture-diagrams-as-arcviz-documents.md)).

### Site served by the app (`scripts/build_docs_bundle.py`)

`make docs-bundle` builds a second, offline variant of the same site into `arcviz/docs_site/`, which
the app serves under `/docs` ([design](../architecture/specs/docs-in-app.md),
[ADR-0042](../architecture/adr/0042-docs-site-built-into-the-package.md)). The script derives its
configuration from `zensical.toml`: `site_dir` `arcviz/docs_site`, no `site_url`, no repository
settings (`repo_url`, `repo_name`, `edit_uri`; the theme would query the GitHub API), no version
selector, `theme.font = false` (no Google Fonts), and `../static/vendor/mermaid.min.js` as the first
`extra_javascript` (the app's vendored Mermaid instead of unpkg). It writes the derived file as
`.zensical-bundle.yml` to the repository root (Zensical requires the site directory inside the
configuration's directory), runs `zensical build --clean --strict -f` on it and deletes it.
