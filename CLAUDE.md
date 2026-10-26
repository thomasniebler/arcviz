# CLAUDE.md

Guidance for Claude Code (and other readers) when working in this repository.

## What this is

A FastAPI app where a chat message is turned by an LLM (tool calling) into validated operations on a
UML 2.5.1 diagram of one of several types (deployment, activity, sequence), which is rendered live in the browser
(Mermaid, or an own SVG renderer per type). Python 3.11+, package
`arcviz/`, vanilla JS frontend in `arcviz/static/` (no build step). Persistence is pluggable
(`arcviz/store/`): by default state is JSON files under `ARCVIZ_DATA_DIR` (default `data/`); `sqlite` and
`cosmos` (Azure Cosmos DB) backends exist. User documentation and the technical specification live
under `docs/` (Zensical site, English); the UI is German.

## Rules

1. **Keep the tech spec current.** Any change to the model, operations, HTTP API, LLM layer, agent
   behaviour, persistence, rendering, configuration, CI or release process must update the matching
   page under `docs/spec/` in the same PR. New environment variables also go into
   `docs/reference/configuration.md`, new or changed endpoints into `docs/reference/api.md`,
   user-visible behaviour into `docs/guide/`. Reviewers treat a missing spec update like a missing test.
2. All mutations go through `arcviz/operations.py`. Never write to a `Diagram` from elsewhere.
3. Before pushing run `make lint typecheck test` and `make docs`; run `make e2e` when the frontend or
   the SSE protocol changed. Ruff also formats Python code blocks inside Markdown files.
4. Commit messages and PR titles follow Conventional Commits; release-please generates the changelog
   and version bumps from them. Do not edit `CHANGELOG.md` or version fields by hand. The one hand-written part
   of a release is `arcviz/whatsnew.json` (user-facing highlights shown as a banner in the app, spec
   `docs/architecture/specs/whats-new.md`): add the new version's entry on the release PR before merging it.
5. Do not add runtime dependencies without a reason stated in the PR; the Docker image and the wheel
   must stay self-contained.
6. Issues are grouped in GitHub milestones. A milestone is worked on **one** branch
   `milestone/m<N>-<slug>` cut from `main`: one Conventional Commit per issue (footer `Refs #<n>`),
   pushed to the milestone branch without per-issue PRs, and one PR into `main` whose body lists
   `Closes #<n>` for every issue. The repository allows squash merges only, so the milestone PR becomes
   one commit and one changelog entry: give it a Conventional title that summarises the milestone with
   the most significant type (`feat` if any issue adds a feature, `!` if any is breaking) and list the
   issue commits plus the `Closes` lines in the squash message. A change outside any milestone gets its
   own branch and PR.
7. **Keep GitHub Actions usage minimal** (see *CI minutes* below). Every push to a PR into `main` costs
   billed minutes; CI is a final gate, not a test runner.

## CI minutes

**CI is paused** (2026-10-21): the hosted runners do not start, so the triggers of `ci.yml` and `pr-title.yml` are
commented out (see `docs/spec/build-and-release.md`, "CI"). Run the full checks locally (`make lint typecheck test
test-js e2e docs`, and `ARCVIZ_E2E_REAL_IDS=1 make e2e` for frontend changes) and merge on those results; the rules
below apply again when the triggers are restored.

GitHub Actions bills every job rounded **up** to a full minute, so a 7-second job costs as much as a
59-second one, and every push to an open PR into `main` starts a new run. Treat each run as expensive:

- Run the checks locally before every push (rule 3). Never push to "see whether CI passes", and never
  push to re-trigger CI (no empty commits, no close/reopen). Reproduce a CI failure locally before
  pushing a fix, and re-run a failed job only for a demonstrable infrastructure failure.
- Push in batches. Commit locally as often as you like, push once the work is done and checked. On a
  branch with an open PR into `main`, every push is a full CI run.
- Prefer the milestone branch (rule 6): CI runs only for PRs into `main` (`pull_request: branches:
  [main]`), so pushes to `milestone/*` and other branches cost nothing until the milestone PR. Open PRs
  as drafts while work is in progress; CI skips drafts and runs when the PR is marked ready.
- When changing workflows: add steps to the existing `ci` job instead of new jobs (each job costs at
  least one minute per run), gate expensive steps on the changed paths, never add `push` triggers for
  branches other than `main`, keep `schedule` triggers at weekly or rarer, and do not add per-event
  bot workflows without a stated budget in the PR.

## Commands

Everything assumes a `.venv` in the repo root (the Makefile hard-codes `.venv/bin/...`).

```bash
make install                      # uv venv + pip install -e ".[dev]" + docs toolchain + e2e npm deps + Playwright Chromium
make lint                         # ruff check . && ruff format --check .
make format                       # ruff check --fix . && ruff format .
make typecheck                    # mypy (configured in pyproject: packages = ["arcviz"])
make test                         # pytest --cov=arcviz --cov-report=term-missing
make test-js                      # node --test tests/js (pure frontend modules + module-structure rules)
make e2e                          # cd tests/e2e && npm test  (Playwright smoke test, scripted LLM, no API key)
make check                        # lint typecheck test test-js e2e  (CI additionally runs the Docker and docs builds)
make eval                         # prompt/tool eval set (tests/evals/) against the REAL provider; needs a key, never in CI
make docs                         # zensical build --clean --strict
make docs-serve                   # preview the docs on http://127.0.0.1:8001
make docs-bundle                  # offline docs site into arcviz/docs_site/, served by the app under /docs
make diagrams                     # render docs/architecture/diagrams/*.arcviz.json into *.mmd / *.svg (arc42 views)
make vendor                       # refresh the pinned Mermaid build in arcviz/static/vendor
make vendor-icons                 # rebuild the CC0 icon packs in arcviz/static/vendor/icons (cloud-icons.md)
make run                          # .venv/bin/arcviz -> http://127.0.0.1:8000

.venv/bin/pytest tests/test_store.py                      # one file (store contract, all backends)
.venv/bin/pytest tests/test_agent.py -k disconnect        # one test by name
```

Unit tests need no API key or network: `tests/conftest.py` provides `ScriptedProvider`, an `LLMProvider`
that replays a fixed list of `ModelReply` objects and records every request it was sent. Use it for any
test that goes through the agent (`ScriptedProvider(..., stream=True)` also emits text deltas). The eval set
in `tests/evals/` (`cases.json`, `harness.py`, `run.py`) is the only thing that talks to a real model; pytest
never collects it, `tests/test_evals.py` checks it offline. `tests/test_properties.py` holds Hypothesis
property tests (operations → integrity → Mermaid structure, exchange round trip, comparison) driven by
`tests/strategies.py`, which draws operations against the current diagram; profile `ci` (derandomised) when
`CI` is set, else `dev` (spec `docs/architecture/specs/property-tests.md`, ADR-0021). When adding an
operation or a rendering rule, extend the strategy and the structural checks there. `tests/conftest.py` also turns `ResourceWarning` into an error (#182): close what a test opens (`SqliteStore.close()`, the
`store` fixture does; event loops handed to live streams come from `new_loop()`, closed after the test).
`tests/e2e/server.py` boots the real app with the same scripted
provider; `CHROMIUM_PATH` points Playwright at a preinstalled browser. The smoke test never names a created id (it reads
ids from the navigator by title or from responses): it must pass with the pinned readable ids and with
`ARCVIZ_E2E_REAL_IDS=1 make e2e` (production ids), which CI runs as a second step on frontend/e2e changes (#196). Mermaid is vendored
(`arcviz/static/vendor/`, refreshed with `make vendor`), so neither the app nor the tests need a CDN.

Lint rules: ruff with `E, F, I, B, UP, SIM, W`, line length 120, `E501` ignored. CI runs `ruff format
--check`, so run `make format` before pushing.

## Architecture

The data flow is a straight pipeline, and each stage is one module (details: `docs/spec/`):

```
chat message -> LLMProvider.run_turn (arcviz/llm/base.py)
             -> DiagramAgent.chat (arcviz/agent.py) executes tool calls via
             -> operations.apply (arcviz/operations.py) mutating a
             -> Diagram (arcviz/model.py), persisted through
             -> DiagramService (arcviz/services/) over the ports (arcviz/ports.py) by
             -> DiagramRepository (arcviz/store/), rendered by
             -> render.payload (arcviz/render.py: the type's renderer, e.g. mermaid.render), streamed as SSE by
             -> server.py -> static/app.js + ES modules (vendored Mermaid 11 in the browser)
```

**Service layer** (spec `docs/architecture/specs/application-services.md`, ADR-0050): entry points
(`server.py` routes, `agent.py`, `proposal.py`) call `arcviz/services/` (`DiagramService`), which owns every
read-modify-write with conditional writes and depends only on domain types and `arcviz/ports.py`
(`DiagramRepositoryPort` protocol, `VersionInfo`/`Change`/`Updated`, `StoreError`/`ConflictError`, `check_id`,
opaque `Revision`). Only `arcviz/store/` knows backends and layouts; `server.py` imports just `create_store` and
hands the repository to the services (it never calls it). `tests/test_layering.py` fails on violations, so a new
persistence need is a new service method, never a store call in a route or in business logic.

**Users, personal workspaces and sharing** (M20, reworked in M21; spec `docs/architecture/specs/personal-workspaces.md`,
ADR-0080, ADR-0084, ADR-0086, ADR-0088..0091, ADR-0100..0103; M20 design `workspaces-and-sharing.md`, superseded for
workspaces; domain `arcviz/workspaces.py`): `auth.AuthMiddleware` puts the caller's `Identity` (id, name, e-mail) into
`scope["state"]["arcviz_identity"]`: proxy mode reads configurable headers (`ARCVIZ_AUTH_PROXY_PRESET`
`easyauth`/`oauth2-proxy`) and answers 401 without the id header; `off`/`token` mode use the admin identity
(`ARCVIZ_ADMIN_USER`, default `admin`). Every user has exactly **one personal workspace** (keyed document of kind
`workspace`, id `personal_workspace_id(user)` = `user_` + 32 hex of the SHA-256, `personal: true`), created
idempotently by `services/access.ensure_personal_workspace` on the first request (`ServiceRoot.signed_in`, cached per
process, never fails a request) and again before a project is created, together with the user's own **default
project** (`personal_project_id(user)` = `home_` + the same hash, *Standardprojekt*, `ProjectService.ensure_own_default`;
diagrams created without a project go there; `home_<32 hex>` ids are reserved like `default`). There are no team
workspaces, no members, no transfers; a project has exactly one owner. Every `Project` names its owner's `workspace`;
`Project.roles` are the **shares** (user → `viewer|editor`; an M20 `owner` entry grants nothing). Effective role (`workspaces.project_role`): owner of the project's personal
workspace → owner; a share → its role; else None (404). Only a project owner shares (`ProjectService.share(pid, user,
role)` by id or trusted unambiguous e-mail, `UserService.find`, ADR-0091) and revokes (`revoke`); both publish a
`project` event, so a revoked recipient's live stream ends with `gone`. **Authorization is enforced only in the
services**: `services/access.py` (`AccessPolicy`, `Guard` with `own_workspace`, `project`, `diagram`, `role_of`,
`owner_of`; `Forbidden` → 403, `DiagramNotFound`/`ProjectNotFound`/`WorkspaceNotFound` → 404 with the store's message)
and `ServiceRoot.for_actor(identity)` (`services/context.py`) build the services bound to the caller; every route
takes `svc: Svc` (the `services_of` dependency), the agents run on `agent.bound(...)` copies. Guards of ordinary requests (`services_of`, `for_actor(…, per_request=True)`) reuse the
project documents their viewer checks read (#166, ADR-0121); chat turns and live streams take `svc: TurnSvc`
(`turn_services_of`, no cache). A new service method must
call the guard (`self.require(...)`) before it reads or writes, and a new route must take `svc: Svc` (or `TurnSvc` when
it lives long) and appear in
`tests/test_access_matrix.py` (it fails for unclassified routes and checks that every project/diagram route of another
user answers 404); `tests/test_access.py` lists every public service method. Unguarded services (guard `None`) are the
system context: tests, the CLI, the start-up migration (`migrate_to_personal_workspaces`, `services/workspaces.py`:
v0.1.8 data → the admin's personal workspace; M20 `default` and team projects → their workspace owner's, or their
explicit M20 owner's (the creator; the former workspace owner becomes an editor; `PersonalMigration.reowned`), every
other M20 effective role becomes a share; M20 workspace documents are kept and give nobody access, ADR-0103). Ids are global,
but new diagram and project ids are `<slug>-<10 random base32, at least one digit>` (`ports.new_ids`; a generated suffix
ending the requested base is replaced, `ports.id_base`, #200, ADR-0150; tried at most `CREATE_ATTEMPTS` times
through `ports.id_attempts`, #195; a requested `id` is only the prefix;
no create answer depends on other tenants' data; #167, ADR-0131 superseding ADR-0089; the test suite pins readable
ids in `tests/conftest.py`, tests of the scheme are marked `real_ids`); the project
id `default` is reserved for the admin's project of legacy data and repaired orphans (other callers get 404). A chat turn whose caller loses editor mid-turn gets tool errors (`write_through` outcomes `deleted`/`forbidden`; both
agents then change nothing more, the link take-back is the one helper `agent.take_back_invalid_link`, #195),
keeps the version of what it wrote, and writes no transcript (ADR-0090). The start-up migration is best effort (never
stops the start); `arcviz-migrate --workspaces [--admin U] [--reassign MISSING=USER]` repeats it and recovers
stranded projects. Versions and transcript entries record `author`/`author_name` (omitted when unknown, so old JSON
is unchanged; `tests/test_compat_v018.py` checks v0.1.8 stores, `tests/test_compat_m20.py` M20 stores written by
`tests/fixtures/m20/generate.py`). UI: the project select groups **Meine Projekte** / **Mit mir geteilt**
(`static/access.js` pure: `projectGroups`, `shareRows`; `static/workspaces.js`: the user and the **Freigeben…**
dialog), read-only mode via `ui.applyReadOnly` (`[data-edit]`, `[data-edit-restore]` + `access.editLock`,
`[data-edit-zone]`, `body.readonly`).

**Collaboration** (M10; spec `docs/architecture/specs/collaboration.md`, ADR-0092..0099, ADR-0104..0105): **notes**: optional
`description` on `Node` and `Lifeline` (≤ `MAX_DESCRIPTION` 2000, omitted from dumps while unset, set by
`add_node`/`update_node`/`add_lifeline`/`update_lifeline`, budgeted in `Diagram.summary` before containers collapse).
**Optimistic concurrency**: diagram GETs answer `ETag: "<revision>"`, details carry `revision`; the `IfMatch`
dependency passes `If-Match` as `expected` to `DiagramService`/`ModelService` and the store's `update(…, expected=)`
(one attempt, `ports.RevisionMismatch` → 412). **Live updates**: `arcviz/live.py` (no package imports) holds
`LiveHub` (thread-safe in-process bus + in-memory presence registry, caps from `limits.LimitConfig`), `LiveConfig`
(`ARCVIZ_LIVE_POLL_SECONDS`, `ARCVIZ_LIVE_MAX_SECONDS`) and `run_stream` (async; re-checks access on every bus event
and poll via `LiveService.recheck`, over reads shared by all streams through `LiveHub.shared` (invalidated by the
events, at most half a poll old, LRU-bounded; a role change on another replica applies within about 1.5 × poll); the
role is computed per caller; `gone` ends it). The services publish hints (`EventSink`) after
every write; a new write path must publish too. `GET /api/diagrams/{id}/live`, `GET /api/projects/{id}/live`,
`POST /api/diagrams/{id}/presence` go through `svc.live` (`services/live.py`; streams only via `open_diagram`/
`open_project`, re-check reads shared per change through `LiveHub.shared`). The app's lifespan wraps the SIGTERM/SIGINT
handlers so the hub closes before uvicorn drains connections (streams never block shutdown). Presence is in memory for
file/sqlite; with Cosmos one relay thread per process (`LiveHub.sync_presence`) writes one expiring item per project and
replica (`presence_channel()`, partition `presence:<project>`) and reads the others' once per poll through
`LiveHub.shared` (#173, ADR-0104). Watches are versioned (`update_watch(based_on=)`, #176, ADR-0105). UI: `live.js` (stream, refresh,
presence drawing), pure `conflicts.js` and `presence.js`; the inspector keeps a base revision and resolves 412s; deletes
of diagrams and projects send `If-Match` of the revision the user was asked about and ask again after a 412
(`conflicts.confirmedDelete`, #186).
Tests: `tests/test_notes.py`, `tests/test_live.py`, `tests/test_server_live.py`, e2e step 18 (two browser contexts).

**Operations** (M9; spec `docs/architecture/specs/operations.md`, ADR-0106..0115): every start-up variable is read with
`envconfig.Env` (records a problem naming variable, format and value, returns the default); each subsystem's
`from_env` raises a `ConfigError` with all its problems and `create_app`'s `StartupConfig` merges them into one (a new
variable goes through `Env`, never a bare `int()`). The LLM SDKs, `azure-identity`, Cosmos and OpenTelemetry are
extras (`anthropic`, `openai`, `azure-openai`, `cosmos`, `telemetry`; the image installs all of them via
`ARCVIZ_EXTRAS`); `llm.check_provider` checks the configured SDK at start-up. `arcviz/observability.py`: opt-in JSON
logs (`ARCVIZ_LOG_FORMAT=json`), `RequestContextMiddleware` (outermost: request id, trace id, `request` records by
route template; catches unhandled exceptions, never re-raised: one record, a 500 with the request id before the
response started, else `aborted` and the body left incomplete so uvicorn drops the connection; only the disconnect
itself is INFO with `aborted` and 499), `ContextFilter` (server request id,
`client_request_id`, trace id, `user` = HMAC pseudonym with `ARCVIZ_LOG_PSEUDONYM_KEY`, none without; exceptions as
type/code/frames, never the message unless `ARCVIZ_LOG_EXCEPTION_DETAILS`), `llm_turn`/`llm_call`
records from `llm/base.py`; structured fields go through `observability.fields(...)` (or `extra={..., "fields":
names}`); never log raw paths, e-mails, secrets, messages or diagram content (`tests/test_log_privacy.py`), never an
exception object as a log argument (only `type(exc).__name__`, checked by `tests/test_operations_review3.py`), never a
Git remote unless through `gitmirror.redacted`.
`ARCVIZ_OPENAI_AUTH=entra`: `llm/entra.py` `BearerTokenProvider` over `DefaultAzureCredential`.
The app's own spans and metrics live in `arcviz/telemetry.py` (no package imports, no-ops until `start_telemetry`;
#187, ADR-0134): spans `arcviz.llm.call`/`arcviz.tool`/`arcviz.store.write` inside chat turns, metrics
`arcviz.http.server.request.duration` (route templates only), `arcviz.llm.duration`/`tokens`, `arcviz.live.streams`,
`arcviz.store.request_units` (Cosmos DB charge per request through `cosmos._MeteredContainer`, attributes
`db.operation.name`/`arcviz.store.document` only; #201, ADR-0154);
every attribute must be in `telemetry.SPAN_ATTRIBUTES`/`METRIC_ATTRIBUTES` and hold no id, path, user or content (a tool
name only if the turn offered it). `ARCVIZ_TELEMETRY_AUTH=entra`: keyless Azure Monitor export.

**Projects** (spec `docs/architecture/specs/projects.md`, as-implemented `docs/spec/projects.md`, ADR-0051..0054):
`arcviz/projects.py` holds `Project` (section tree with `parent` links, ordered `Membership`s of diagram ids),
its operations through `projects.apply` (signature check via `operations.validate_arguments`, rollback on any
exception, integrity asserted; `ProjectError` subclasses `OperationError`); `add_diagram`/`remove_diagram` are
internal. Diagram documents do not know their project. `ProjectService` (`services/projects.py`) owns project
read-modify-writes (conditional on the project revision) and the cross-document invariant "every diagram in
exactly one project": ordered writes (create diagram → place; move: add to target → point the locator → remove from
source; delete diagram → unplace) plus `reconcile()` (duplicates keep the newest `added_at`, dangling memberships
dropped after `exists()`, orphans re-checked then adopted into the project recorded before their creation (keyed
document `placement`, ADR-0088), legacy ones into `default`/"Standardprojekt", others quarantined, then the locator
repaired), which only the system context runs: start-up and `arcviz-migrate --projects [--adopt D=P]` (`arcviz/cli.py`), never a caller's read.
**Diagram locator** (M22, #181, ADR-0120, spec `docs/architecture/specs/scale-and-robustness.md`): keyed document
`location` per diagram (`services/locations.py`) lists every project that may hold it (a superset: listed before the
membership is added, dropped after it is gone); `AccessPolicy.locate`/`ProjectService.locate` read it and those
projects (the newest membership wins), fall back to a scan only for a diagram that `exists()` (an orphan at most once
per `SCAN_TTL`), and heal the entry; it is a hint, the guard decides. A failed read is never "absent" (only the store's
definite "not found" is): vacate, placements and deletes fail closed (a failed vacate makes the create take the next
free id); a writer's `pending` token keeps a project listed until its membership is written (no clocks). Tokens name
their writer incarnation (`<boot>.<random>`, `locations.Writer`); each process renews a `writer` keyed document
(`services/writers.py` `Heartbeat`, started by the lifespan, `ARCVIZ_WRITER_HEARTBEAT_SECONDS`), and the tokens of a writer
that stopped, or that another process saw standing still for a lease on its own monotonic clock and fenced with a
conditional write, are settled (`Reaper`, own thread, only while its own heartbeat is fresh); a fenced writer issues no
tokens (checked again inside the membership write) and fails `/readyz` and `/healthz`; M22 tokens only via
`arcviz-migrate --settle-pending --all` (#189, ADR-0133). A
delete removes every membership the entry lists (`memberships` before, `unplace` after), a create first `vacate`s the
id. Per-request paths never list the store: `GET /api/diagrams`
and project views use the store primitive `diagram_infos(ids)`; `tests/test_locations.py` asserts no Cosmos
cross-partition query. Stores implement five project primitives (file
`projects/<id>.json`, sqlite table `projects`, Cosmos item `project` in partition `project:<id>`).

**Shared project model** (M18; spec `docs/architecture/specs/shared-project-model.md`, case study
`shared-project-model-case-study.md`, as-implemented `docs/spec/projects.md#shared-model`, ADR-0070..0078):
`Project.elements` (`project_model.Element`: id, name, abstract kind of `ELEMENT_KINDS`, description, parent;
at most `MAX_ELEMENTS`) and `Section.element`; deployment/activity `Node.ref` and sequence `Lifeline.ref` link a
view element to an element. Unset `ref`/`element` are **omitted from dumps** (wrap serializers) so data written
before M18 keeps its JSON and fingerprints (`tests/test_compat_v017.py` checks stores written by v0.1.7,
fixtures in `tests/fixtures/v0_1_7/`). `project_model.linkable_kinds(type, view_kind)` says which view
elements may stand for which kinds (behaviour is never linked). Diagram operations `link_element`,
`unlink_element`, `sync_element`, `clear_element` (`operations.MODEL_OPERATIONS`); the first, third and fourth
are `INTERNAL_OPERATIONS` (they carry the element's name/kind): refused by REST `/ops`, batches, tool calls and
proposals; `update_node`/`update_lifeline` refuse renaming a linked element. Project element operations are
internal (`projects.ELEMENT_OPERATIONS`). `ModelService` (`services/model.py`) orders the cross-document writes:
rename = project, then `sync_element` per referencing diagram (versioned, `DiagramService.apply_if_changed`);
delete = `clear_element` per diagram, then the project; `sync()` repairs drift (one version per diagram). Element ids are only unique per project, so a diagram moving to another project (`DiagramService.move`) or imported
alone (`import_carrying`: the export embeds the referenced model under `model`, `exchange.parse_document_model`; a
document without it loses its links, ADR-0079) carries the elements along (#153, ADR-0161:
`projects.carry_model` via `ProjectService.carry`; same id+kind+name maps, a free id copies, a taken one copies under a
new id, relations get ids from `relation_seq`, what does not fit is dropped as before and reported as `carried`; unlink
version, membership, relink version). No store changes, no
migration. Both agents get the element tools of `arcviz/agent_model.py` (create/update/link refused in
propose mode) and `prompts/shared/model.txt` plus the elements when the project has any; the project review
has seven model rules; project bundles are `schema_version` 2 only with a model. UI: `modelpanel.js`
(**Modell**), the inspector's **Projektelement** section, pure helpers `sharedmodel.js`.

**Model relations** (M25; spec `docs/architecture/specs/model-relations.md`, ADR-0135..0141; extends the shared model):
`project_model.Relation` (id `r<n>`, kind of `RELATION_KINDS` = communicates/deployed_on/data_flow, source/target element
ids, `properties` protocol/port/direction/mode per `RELATION_RULES`, description ≤ 300) in `Project.relations` (omitted
while empty; ≤ `MAX_RELATIONS` 500; size arithmetic in the spec); containment stays `Element.parent`. Internal project
operations `add/update/remove_relation` (`projects.RELATION_OPERATIONS`); `remove_element` removes its relations,
`update_element` refuses a kind a relation forbids. Deployment `Edge.ref` and sequence `Message.ref` link a connection
to a relation (`relation_kinds_for`, `ends_fit`: data_flow keeps its orientation; activity edges never link); diagram
operations `link_relation`/`sync_relation`/`clear_relation`/`set_relation_hidden` (internal) and `unlink_relation`,
`set_completeness` (public, `operations.RELATION_OPERATIONS`); links are cleared when a connection's ends change.
**Open world**: `Diagram.complete` (kinds declared complete) and `Diagram.hidden_relations` (omitted while empty).
`ModelService` relation methods (`link_relation`, `hide_relation`, `update_relation` clears links that no longer fit,
`remove_relation` clears first, `sync` repairs drift); REST `/api/projects/{pid}/relations[/{rid}]`,
`/api/diagrams/{id}/link-relation|unlink-relation|hidden-relations`. **Views stay primary**: nothing but link validity
propagates; `project_review` correspondence rules (relation-not-shown, connection-without-relation,
complete-view-missing-relation, relation-property-conflict, deployed-on-several-nodes, containment-conflict,
invalid-relation-link; `message-without-connection` keeps its id) attach `resolutions` that the review panel offers
as buttons and never applies itself. Agents: `create/update_relation`, `link/unlink_relation` (`agent_model`,
refused in propose mode except unlink); they link matching relations and propose missing ones (ADR-0140). Bundles are
`schema_version` 3 with relations. UI: pure `static/relations.js`, the model panel's relation list and **Matrix**, the
inspector's **Projektbeziehung**, the **Vollständigkeit** toolbar menu. `tests/test_compat_v040.py` checks v0.4.0
stores (`tests/fixtures/v0_4_0/generate.py`).

**Architecture drivers** (spec `docs/architecture/specs/architecture-drivers.md`, ADR-0163): `project_model.Driver` (id
`d<n>` from `Project.driver_seq`, title, kind of `DRIVER_KINDS` quality_goal/constraint/requirement/risk, ATAM priority
`importance` × `risk` of `DRIVER_LEVELS` high/medium/low, description, concerned `elements`) in `Project.drivers`
(omitted while empty, ≤ `MAX_DRIVERS` 100); public project operations `add/update/move/remove_driver`
(`projects.DRIVER_OPERATIONS`), REST `/api/projects/{pid}/drivers[/{did}]`, bundles `schema_version` 4 with drivers.
Both agents get them in the prompt (`prompts/shared/drivers.txt`, `drivers_summary`, most important first, after the
model part; `ModelTools.context()` reads the project once) and the tools `list/create/update/remove_driver` (writes only
on request, refused in propose mode). UI: pure `static/drivers.js`, the **Treiber** tab `static/driverpanel.js` of the **Architektur** dialog.

**Graded deletion** (spec `docs/architecture/specs/graded-deletion.md`, ADR-0198): drivers and scenarios have `status`
`active|retired` (omitted while active); `remove_driver`/`remove_scenario` refuse an active one and `remove_decision` an accepted
one with `projects.ProjectStateError` (REST 409); retired objects leave the prompts, review rules, coverage and the default agent
lists (`project_model.active`/`is_retired`), agents never set `status`. Requirements delete directly, in bulk through
`POST …/requirements/delete` (`RequirementService.remove_many`). UI: pure `static/lifecycle.js`. A new consumer that lists drivers
or scenarios for an assistant or a rule must filter retired ones.

**Decision support** (M29; spec `docs/architecture/specs/decision-support.md`, ADR-0166..0168): `arcviz/decision_model.py`
holds `Stakeholder` (`s<n>`), `Scenario` (`q<n>`, the six ATAM parts, one `driver`, `core`), `Decision` (`adr<n>` shown as
ADR-n, status draft/accepted/superseded/rejected, options with pros/cons, `chosen`, texts, refs), their bounds, the prompt
lines (`drivers_with_core_scenarios`, `stakeholder_lines`, `decision_lines`: accepted only) and `decision_markdown`; all in
the project document (omitted while empty), public operations `add/update/remove_<stakeholder|scenario|decision>`
(`projects.DECISION_SUPPORT_OPERATIONS`; cascades from `remove_driver`/`remove_element`), generic REST routes
(`support_routes` in `server.py`), `GET …/decisions/{id}/markdown`, bundles `schema_version` 5. `arcviz/scenario_analysis.py`:
one `complete_tool` call (`submit_scenario_analysis`) over `project_review.digest_lines` + the architecture context,
validated, never stored (ADR-0167; `POST …/scenarios/{id}/analysis`, rate class `review`). Accepted decisions bind by
prompt (ADR-0168): `prompts.architecture_part` (drivers with core scenarios, stakeholders, `shared/decisions.txt`) in both
chats via `agent_model.architecture_context`, and both reviews (`review.review(…, decisions)`, `project_review.review_input`).
Agent tools `list_scenarios/list_decisions/list_stakeholders`, `create_scenario` (on request). UI: the **Architektur**
page `static/archview.js` (layer 6; a main view addressed by hash routes, `routes.js` pure, `shell.js` layer 7 switches views, spec `app-shell.md`, ADR-0194) with the tabs `driverpanel.js`, `scenariopanel.js`, `decisionpanel.js`,
`stakeholderpanel.js`, each a filterable card list with one editor (`mdpane.js` helpers, pure `listnav.js`, shared classes in `components.css`; spec `architecture-workspace.md`, ADR-0196); pure helpers `static/decisions.js` (`decisionFromAnalysis`).

**Project vision** (spec `docs/architecture/specs/project-vision.md`, ADR-0203): `Project.vision` (`project_model.Vision`: statement, goals, non-goals, omitted
while unset) is replaced by the public operation `set_vision`, `PUT /api/projects/{pid}/vision`; agents have no tool for it (they may only
propose it, `PROPOSABLE_OPERATIONS`). `prompts.architecture_part(…, vision=)` puts it first (`prompts/shared/vision.txt`, goals as `[g1]…`);
rule `vision-missing`; bundles `schema_version` 9. UI: tab **Vision** (`visionpanel.js`, pure `vision.js`).

**ISO 25010 qualities** (spec `docs/architecture/specs/iso25010-qualities.md`, ADR-0202): `Scenario.qualities` holds one or at most two of the nine
characteristics of `quality_model.QUALITY_CHARACTERISTICS` (omitted while empty; operations refuse anything else); a driver's are computed
(`driver_qualities`), never stored. `quality_suggestion.suggest` (one `complete_tool` call, `POST …/scenarios/qualities`, rate class `review`) only
suggests: output goes through `clean_qualities`, nothing is stored, the editor pre-ticks and the person decides. Bundles that use it are `schema_version` 8;
review rule `scenario-no-quality`.

**Requirements** (M31; spec `docs/architecture/specs/requirements.md`, as-implemented `docs/spec/requirements.md`, ADR-0186,
ADR-0188..0190; design of the MCP tools and the workshop: `architecture-objects-over-mcp.md`, `requirements-workshop.md`):
`arcviz/requirement_model.py` (pure) holds `Requirement` (`req<n>`; `raw` immutable, `statement`, kind, MoSCoW priority, measure,
theme, source, `external_id`, stakeholders, elements, issues, `duplicate_of`, status `raw|refined|rejected|duplicate`),
`Suggestion` (what an assistant proposes; `rev` fingerprint, `accepted` fields) and `RequirementBox`, the content of the keyed
document `requirements` (one per project, **not** in the project document: its own byte guard and revision, incarnation like the
proposals). Operations go through `requirement_model.apply` (`apply_each` for bulk, one snapshot); `accept_suggestion` needs the
`rev` the user saw (`StaleSuggestion`), nothing but a person's UI/REST act sets `statement`/`status`/accepts: `check_agent`
allows agents (MCP credentials, chat assistants) only `add_requirement`, `update_requirement` of `source/theme/priority/
stakeholders/elements` and `set_suggestion`, never delete or decide (ADR-0186). `RequirementService` (`services/requirements.py`,
`svc.requirements`, `Services` and the access matrix know it) is the only writer; references to stakeholders/elements are checked
on write and tolerated when dangling on read (cascades from `ProjectService.apply`, repaired by `sync`). Traceability is stored on
`Driver`/`Scenario`/`Decision.requirements` (≤ 20 ids, omitted while empty; `Scenario`/`Decision.stakeholders`; `origin` for the
idempotent proposal apply), replaced by `add_/update_*`, extended by the additive `link_requirements`; `ProjectService` checks
that the ids exist; **coverage is computed** (`coverage_map`: uncovered/driver/scenario/decision), never stored. Nine
deterministic project review rules (`requirement-*`, `quality-requirement-no-measure`, `scenario-no-requirement`,
`decision-no-basis`, `stakeholder-no-requirement`) read `ProjectSnapshot.requirements`. Bundles are `schema_version` 7 whenever a
requirement or a requirement/stakeholder link is present (`exchange._parse_requirements` renumbers `req1…`).

**Requirements workshop** (M31; spec `docs/architecture/specs/requirements-workshop.md`, ADR-0191..0193): pour in (`requirement_import.py`,
pure: lines/Markdown/CSV, `POST …/requirements/import` with `dry_run`), refine (`requirement_refine.py`: one chunk of ≤ 25, one
`complete_tool` call, result = **suggestions**), derive (`requirement_derive.py`: ≤ 30 refined requirements → items of a **project
proposal**, client `derive`, reuse of existing titles as `link_requirements`), both orchestrated by `requirement_workshop.py` (routes
and chat share it; the client loops chunks until `remaining` is 0, ADR-0192). The requirements chat (`requirements_agent.py`) is the
`AgentTools` set with `client="chat"` plus the two chunk tools; its transcript is the keyed document `reqchat`
(`services/requirement_chat.py`, ADR-0193, not the project transcript). Routes `/requirements/chat` must stay registered before
`/requirements/{requirement_id}`. UI: the main view `static/requirementview.js` (pure `requirements.js`), `state.mainView`,
`chat.setMainView`, chat scope `requirements`; proposal items in `proposalpanel.js` (`proposal.selectionClosure`). `requirementpicker.js` (view layer) is the multi-select of requirements in the driver, scenario and decision forms. Repair: `arcviz-migrate --requirements STORE`. Eval cases: `"scope": "requirements"`.

**Agent access** (spec `docs/architecture/specs/agent-access.md` and `agent-authentication.md`, as-implemented
`docs/spec/agents.md`, ADR-0170..0173): with `ARCVIZ_MCP=1` (extra `mcp`: the official SDK 2.x and PyJWT) `POST /mcp` serves MCP
statelessly. `agent_tools.AgentTools(svc, scope)` holds the tools (bounded reads; `add_code_ref`/`remove_code_ref`;
`propose_change` stores a proposal) on the caller's services, so the access policy decides; **no tool applies a proposal**
(`tests/test_agent_tools.py`). M31 (ADR-0186, ADR-0187): agents also read stakeholders, drivers, scenarios, decisions and requirements, write requirements/stakeholders
directly (add and edit, suggestions only for the normalised form) and propose drivers, scenarios and decisions as a **project proposal**
(`StoredProposal.kind == "project"`, `items` with `@key` references, applied in part by a person over REST, `origin` makes a repeat
idempotent, a proposed decision is always a draft); no tool deletes, accepts or applies (`tests/test_architecture_tools.py`). `Element.code_refs` (`project_model.CodeRef`, `owner_of_path`; ops `add_code_ref`/`remove_code_ref`)
link components to repository paths as free text. Stored proposals are one keyed document `proposal` per project
(`proposals.py`, `services/proposals.py`: dry run on a copy, `apply` replays through `apply_proposal`, stale on a changed
diagram); the replay and `diagram_diff` live below `proposal.py`, which re-exports them. Credentials work **only on the MCP path**
(`agentauth.AgentGate`, called by `AuthMiddleware`; proxy headers and the session cookie never count there; REST refuses them):
personal access tokens `avp_…` (keyed document `token` per user, only the SHA-256 stored, `services/tokens.py`) and OIDC access
tokens (`oidc.OidcValidator`: RS256/ES256, issuer, audience, expiry; RFC 9728 metadata). A new tool belongs to `AgentTools`, is
registered in `mcp_server.build_mcp` (no `from __future__ import annotations` there: the SDK reads the annotations), is listed in
`TOOL_NAMES` and in `tests/test_mcp_server.py`.

**Deployment traits** (spec `deployment-traits.md`, ADR-0169): a kinds file kind may name a `role` (the built-in kind the rules
treat it as, `model.role_of`) and `traits` (`redundancy: built_in`, `model.kind_trait`); the node property `redundancy`
(`built_in`/`none`) overrides it. The shipped packs give Cosmos DB, DynamoDB and Spanner `built_in`, so
`database-without-replication` does not fire for them or for a `database` with the property.

**Project elements by default** (spec `docs/architecture/specs/elements-by-default.md`, ADR-0164): structural views
(`project_model.adoptable`: deployment leaves and `node`/`cluster`, lifelines, activity partitions and data stores; not
grouping containers or behaviour) become project elements when an operation creates them: `services/adoption.py`
(`Adopter.prepare` matches by name or loose key and creates missing elements, project first; `steps` links them inside
the same diagram write; `finish` takes back unused ones) on every path that creates views: `ModelService.apply_operation`
(the `/ops` route), both agents (`ModelTools.adopting` around `write_through`, not in propose mode), confirmed proposals
(`apply_proposal(adopter=)`). Templates, imports and moves are adopted only on request (`ModelService.adopt_all`,
`/api/projects/{pid}/adoption`, **Unverknüpfte übernehmen…**). The model prompt block is always there (`NO_ELEMENTS`)
with the reuse rule; the review rule `similar-element-names` reports near-duplicates. A new write path that creates views
must adopt too.

**Single mutation path.** Both the LLM tools and the REST endpoint `POST /api/diagrams/{id}/ops` go
through `operations.apply(diagram, op, args)`. It dispatches on the `OPERATIONS` dict, validates `args`
against the operation function's signature (`check_arguments`: unknown/missing names, JSON types from the
annotations; spec `docs/architecture/specs/operation-arguments.md`, ADR-0045), takes a deep copy, and
restores every model field on *any* exception. `OperationError` is bad input (REST 400, tool error for
the model); anything else is a defect and propagates unchanged (REST 500, agent "Internal error" tool
result), so the annotations of an operation function are its argument contract. Error messages are written to be shown to the LLM (they list known ids
and allowed kinds) so the model can self-correct. Adding an operation means: a function in
`operations.py`, an entry in `OPERATIONS`, a matching `ToolSpec` in `agent.TOOLS`, and the spec page.
`apply_batch` (spec `docs/architecture/specs/batch-operations.md`) is itself an operation: it runs a flat
list of `{op, args}` steps through `apply()` and restores the pre-batch state on *any* exception.

**Node kinds are built-in sets plus an optional kinds file.** `model.py` holds the built-in sets and the
process-wide `KindCatalog` (`active_kinds()` / `use_kinds()`); `server.create_app` activates the catalogue
that `kinds.load_kind_catalog` reads from `ARCVIZ_KINDS_FILE` (extend-only, strict; spec
`docs/architecture/specs/configurable-kinds.md`). Validation, tool enums (`agent.build_tools`), the prompt,
`/api/config` and Mermaid read the active catalogue at call time; tests get the built-ins back from an
autouse fixture. Built-in: `CONTAINER_KINDS` may have children (rendered as Mermaid
subgraphs), `LEAF_KINDS` may not, `EDGE_KINDS` map to a line style and default arrow heads, which an
edge's optional `direction` (`forward|both|none`) overrides (`mermaid.edge_arrow`); edges also carry
`properties`, of which `protocol`/`port` are drawn next to the label. The same sets feed the tool JSON
schemas (`enum`), the system prompt, `/api/config`, and the Mermaid `classDef` styles in `mermaid._STYLES`,
so a new built-in kind must be added in `model.py`, given a style (and optionally a shape) in `mermaid.py`,
and listed in `docs/guide/elements.md`.

**Diagram types** (spec `docs/architecture/specs/diagram-types.md`, ADR-0030..0033, 0037): `Diagram.type`
(`model.DIAGRAM_TYPES`, default `deployment`, immutable) selects a row in a per-type table in each layer:
`model.kinds_for(type)` (deployment = the configurable catalogue, others fixed UML vocabularies; kind
names are disjoint across types), `operations.TYPE_OPERATIONS` (`apply` rejects other types' operations),
`render.RENDERERS` (Mermaid or SVG; `render.payload()` gives `renderer` + `mermaid`/`svg` + `mermaid_ids`,
which every rendering response and SSE event carries), `agent.build_tools(catalog, type)` /
`agent_types.py` (tools of non-deployment types), `arcviz/prompts/` (system prompts: shared blocks plus
`types/<type>.txt`, spec `assistant-prompts.md`, ADR-0065), `exchange._rule_errors`,
`exchange.PLANTUML_TYPES`, `diff.SUPPORTED_TYPES`, `templates.TEMPLATE_TYPES`.
`tests/test_diagram_types.py` checks every table covers every type. A feature a type cannot support
answers 400 with the follow-up issue and is disabled in the UI (`types.js`, `ui.applyFeatures`).
Fingerprints omit `type` for deployment; JSON exports are `schema_version` 1 for deployment, 2 otherwise.
Node/edge types with a fixed UML vocabulary register a `graph_types.GraphProfile` (spec `docs/architecture/specs/uml-graph-types.md`,
ADR-0183, 0185, 0177: default kinds, edge/node/whole-diagram rules, typed `fields`; activity is the first, state machine,
class, use case and component diagrams follow); operations, importer and tools ask the profile, never `if diagram.type ==
"activity"`; kind names shared by several types are listed in `model.SHARED_KINDS`. State machine diagrams (spec
`state-machine-diagrams.md`, ADR-0178): rules in `state_machine.py`, findings in `state_rules.py`, PlantUML in `plantuml_graph.py`
(helpers in `plantuml_text.py`); their states are never linked to project elements (`project_model.NOT_LINKED_TYPES`). Class diagrams (spec `class-diagrams.md`,
ADR-0179): rules in `class_diagram.py`, findings in `class_rules.py`, members as list `fields` (`graph_types.Field`), aggregation and
composition stored part → whole, classes linkable (`data`) but never adopted at creation; a kind named like a Mermaid keyword
needs `mermaid._style_name`. Use case diagrams (spec `use-case-diagrams.md`, ADR-0180): rules in `use_case.py`, findings in
`use_case_rules.py`, the specification of a use case is its note, actors and subjects are linked and adopted
(`project_model.adoptable`), a new diagram starts left to right (`model.TYPE_DIRECTIONS`). Component diagrams (spec
`component-diagrams.md`, ADR-0181): rules in `component_diagram.py`, findings in `component_rules.py`, ports are a list field that
edges name (a port in use cannot be removed), interfaces are circles with realization/usage edges, components and subsystems are
linked and adopted; the notation of a shared kind may depend on the diagram type (`mermaid._node_line(…, diagram_type)`). Their `connection`/`dependency` edges link to model relations (`communicates`, `data_flow`) like deployment edges; `usage`/`realization` never.
Activity diagrams (spec `activity-diagrams.md`, ADR-0034) reuse the node/edge operations with UML kinds,
a `guard` argument stored as the edge property `guard`, and rules (`model.activity_edge_problem`) checked
by the operations and the importer; Mermaid draws them with `@{ shape: … }` nodes (`mermaid.shape_label`
also escapes `\`).
Sequence diagrams (spec `sequence-diagrams.md`, ADR-0035) keep their content in `Diagram.interaction`
(`arcviz/sequence.py`: ordered lifelines, a fragment tree of messages / combined fragments with operands /
interaction uses / destructions, execution specifications; `integrity_errors` is part of the diagram's);
their operations live in `sequence_ops.py`, imported at the end of `operations.py` and registered in
`OPERATIONS`; `sequence_svg.py` draws them as SVG with `data-id`/`data-element` on every element (all text
through `escape()`); the UI inspects them via `elements.js`.
Kind packs (`arcviz/kindpacks/*.json`, `ARCVIZ_KIND_PACKS`) are kinds files loaded before `ARCVIZ_KINDS_FILE`;
a leaf kind's `icon` (`"<pack>:<icon>"`, checked against `arcviz/static/vendor/icons/`, CC0 subsets built
by `scripts/vendor_icons.py`, ADR-0036) makes `mermaid.py` emit `@{ icon: … }` nodes (`g.icon-shape`).

**Node references are resolved loosely.** `Diagram.find_node` accepts an id, a case-insensitive name, or
the slugified name; operations always store the resolved `node.id`. Ids are slugs made unique by
`Diagram.unique_id`; edge ids are `e1, e2, ...`.

**Provider abstraction.** `LLMProvider` (`arcviz/llm/base.py`) owns the agentic loop (`run_turn`: call
model, execute tool calls, feed results back, repeat up to `max_rounds`) and yields `AgentEvent`s. A
provider implements only five hooks that translate between the neutral dataclasses (`ToolSpec`,
`ToolCall`, `ToolResult`, `ChatMessage`, `ModelReply`) and its wire format, plus optionally `_stream`, which
yields `TextDelta`s and then the `ModelReply` (streamed to the browser as `text_delta` SSE events; the
round's `text` event stays authoritative; `ARCVIZ_STREAM=0` disables it). `ModelReply.native` carries
the raw assistant content so the provider can echo it back verbatim (Anthropic needs this for tool_use
blocks). Two providers exist: `anthropic_provider.py` (default model `claude-opus-5-5`, prompt caching on
the system block, optional `output_config.effort`, server-side fallback beta) and `openai_provider.py`
(any OpenAI-compatible chat-completions endpoint). `factory.create_provider` picks one from
`ARCVIZ_PROVIDER` and related env vars. The persisted transcript is text only; tool calls and results
are not replayed into history on later turns.

**Agent turn semantics** (`DiagramAgent.chat`), which the tests pin down:
- The system prompt (`build_system_prompt` → `prompts.system_prompt`) is the shared blocks of
  `arcviz/prompts/shared/` plus the type's expert part (stable prefix, cached) followed by the current
  `Diagram.summary(budget)` (notes shortened, containers collapsed, then edges/leaves cut with counts: at most
  `budget + model.SUMMARY_SLACK` for any shape, #115), interpolated every turn; the model also has
  a read-only `get_diagram` tool that is handled in the agent, not in `operations`.
- After every successful tool call `diagrams.write_through(...)` saves latest at once, conditional on the
  turn's revision (a conflict re-loads latest and re-applies the call, #65), and the `tool_result` event carries the
  intermediate `diagram` and `mermaid` (with changed nodes highlighted) for live rendering.
- Finalization (`diagrams.commit_turn`: latest + auto version atomically if the content changed, conditional on
  the last revision, skipped on a conflict; transcript append) happens in a `finally`
  block, so a client disconnect mid-stream still persists what was applied. The generator being closed
  by the server is the disconnect signal.
- The stream ends with a `final` event containing reply, error, applied operations, new version, and
  the full diagram + Mermaid. `server.py` wraps the sync generator in a `TurnResponse` (`iterate_in_threadpool`,
  SSE `data:` lines) that closes it on a worker thread however the response ends, so a disconnect finalizes at once
  (#112, ADR-0126); the route's checks before the stream run in `run_in_threadpool` (no store call on the loop).
- **Propose mode** (spec `docs/architecture/specs/propose-confirm.md`): `chat(..., propose=True)` (request
  `mode: "propose"`) is a dry run on the in-memory diagram (no `save_latest`, no version, transcript
  entry marked `proposed`); `final.proposal` carries the operations, base/result fingerprints and a
  `diagram_diff`. `arcviz/proposal.py` replays a confirmed proposal through `operations.apply`
  (`POST …/proposal/apply`, 409 on a stale base or mismatching result) or records a discard.
- **Chat compaction** (spec `docs/architecture/specs/chat-compaction.md`): after the transcript append,
  `_maybe_compact` checks `len(chat) > compact_after` and runs `compact()` on a single-worker executor
  (inline when `background=False`, which the tests use; `drain()` waits). `compact()` folds everything
  before the newest `keep` entries (boundary moved back to a `user` entry, at least one full turn) into a
  `{"role": "summary", "covers": {...}}` entry via `provider.complete_text(SUMMARY_SYSTEM_PROMPT, …)` and
  `store.compact_chat(…, generation)`, guarded by the transcript generation read before the transcript
  (`store.chat_generation`; clear/import/delete replace it, #67, ADR-0046). Summaries are cumulative. `_history()` emits the summary as a user message plus an
  assistant "Understood." before the tail. Failures are logged and never affect the turn.
  `POST /api/diagrams/{id}/chat/compact` runs it inline; the UI renders summaries as a collapsed `<details>`.

**Project chat** (`arcviz/project_agent.py`, spec `docs/architecture/specs/project-chat.md`, ADR-0057..0061):
`ProjectAgent(provider, diagrams, projects)` subclasses `DiagramAgent` for history/compaction with
`self.transcripts = projects` (the project transcript: store primitives `*_project_chat`, `ProjectService.chat`/
`append_chat`/`chat_generation`/`compact_chat`/`clear_chat`) and `summary_prompt = PROJECT_SUMMARY_SYSTEM_PROMPT`.
`chat(project_id, message, active, focus, propose)`: the prompt is `prompts.project_prompt_prefix()` (the shared
blocks plus `prompts/project/chat.txt`), then `project_structure()` (budgeted briefs per diagram), then the active
diagram's `prompts.diagram_part` (expert part and state); tools are `PROJECT_TOOLS` (`list_diagrams`,
`read_diagram`, `open_diagram`, `create_diagram`) plus the active type's `build_tools`, passed to `run_turn` as a
callable re-evaluated every round. The active diagram is per-turn state seeded from the request. Every edited
diagram keeps its own revision: `write_through` per call, `commit_turn` per changed diagram in `finally` (no
cross-diagram atomicity), one project transcript entry with `operations` (tagged `diagram`) and `diagrams`.
Propose mode: one existing diagram per turn, `create_diagram` refused; confirmed via
`POST /api/projects/{pid}/proposal/apply` (`apply_proposal(note=…)` writes the note into the project transcript).
UI: `state.chatScope`, the **Projekt-Chat** button, `chat.setChatScope`, pure helpers in `static/projectchat.js`.

**Store** (`arcviz/store/`, as-implemented spec `docs/spec/persistence.md`, design
`docs/architecture/specs/persistence.md`): `base.DiagramRepository` is an ABC with abstract *primitives*
(`read_latest`, `create_latest` (latest + version 1), `commit_latest` (conditional write on the latest
*revision*, atomic with an optional version; `ConflictError`), `write_latest` (migration only), atomic
`append_version`, list/load versions,
`append_chat`/`chat_generation`/`compact_chat`/`clear_chat`/`import_chat`, `purge`, the project primitives and the
project transcript `*_project_chat`, and the keyed documents `list/read/create/commit/delete_document` of kind
`workspace`/`user`: file `workspaces/`, `users/`, sqlite table `documents`, Cosmos partition `<kind>:<key>`) and concrete *derived operations*
(`create`, `load`, `load_with_revision`, `save_latest`, `commit`, `update` (read-modify-write retried on
conflicts; every server mutation uses it; spec `docs/architecture/specs/concurrent-mutations.md`,
ADR-0044), `snapshot`, `snapshot_latest`, `restore`, `undo`/`redo`, `delete`, `undo_target`,
`redo_target`) that hold all versioning semantics: `source` is `initial | auto | manual | restore | undo |
redo` plus a content `fingerprint`; restore, undo and redo are recorded as *new* versions (history stays
linear); `undo_target` walks back to the most recent version whose fingerprint differs, skipping manual
snapshots and following `restored_from`; `redo_target` replays the versions as a stack (undo pushes, redo
pops, manual is transparent, anything else clears; spec `docs/architecture/specs/redo.md`);
`save_latest` refuses a diagram with `integrity_errors()`. `history()` lists the version metadata once (never a
snapshot; file sidecars `NNNN.meta.json`, Cosmos a projection) and derives both targets (pure functions in
`arcviz/history.py`); responses carry the newest `ARCVIZ_VERSIONS_PAGE` versions; opt-in retention
(`ARCVIZ_VERSIONS_KEEP`, `delete_versions`) never prunes the first/latest version, what undo/redo need or manual
snapshots, runs every `SLACK` versions (`history.prune_due`) with a `KEEP + SLACK` grace, and a target pruned meanwhile
is `VersionGone` (409); `put_version` lets the backend migration keep numbers across gaps (#114, ADR-0129, ADR-0130). Chat entries get a per-diagram `seq` that is
never reused, and `load_chat` hides entries older than the newest `summary` entry. `ping()` (concrete,
overridden by `FileStore`) is the cheap probe behind `GET /readyz`. Backends:
`file.FileStore` (`diagrams/<id>/latest.json`, `versions/NNNN.json` claimed with `O_EXCL`, `chat.json`),
`sqlite.SqliteStore` (WAL, `BEGIN IMMEDIATE`), `cosmos.CosmosStore` (one container, partition key
`/diagram_id`, deterministic ids + 409 retry; tested against `tests/cosmos_fake.py`).
`factory.create_store` reads `ARCVIZ_STORE*` and attaches the optional `GitMirror` (`ARCVIZ_GIT_DIR`),
which `snapshot(details=…)` and `delete` feed. `tests/store_contract.py` is the contract every backend
must pass. `DiagramStore` is a deprecated alias of `FileStore`.

**Mermaid rendering** (`mermaid.py`): node ids become `n_<sanitized>` identifiers, de-duplicated per
diagram by `id_map` (sanitizing is not injective). All user/model text in labels goes through
`escape_text`, which uses Mermaid entity codes (`#lt;` etc.) because the frontend renders with
`htmlLabels: true` and `securityLevel: "strict"`. In the browser Mermaid's SVG is parsed into an inert `<template>` and
sanitized like every other SVG (`canvas.parseMermaidSvg`, `svgsafe.sanitizeSvg(root, {htmlLabels: true})`: label HTML
only `div/span/p/br`; #197, ADR-0132); `tests/js/sinks.test.mjs` admits no trusted markup sink. Container labels are
single-line on purpose (Mermaid clips multi-line nested subgraph titles). The e2e test asserts that hostile names render
literally.

**Import/export** (`exchange.py`, spec `docs/architecture/specs/diagram-exchange.md`): a versioned JSON
envelope (`format: arcviz-diagram`, `schema_version`) both ways and a PlantUML export for every type (#107: activity
via `_ActivityWriter`, a structured walk with `if`/`switch`/`fork` closed at post-dominators, swimlanes and UML
connectors `(n)` for jumps, at most `MAX_PLANTUML_NESTING` deep for every type (else `ExchangeError`, 400; other export exceptions stay 500s);
sequence with `group <operator>` for operators without keyword and activate/deactivate from executions; all text via
`_plain(text, reserved)`, `%` included; checked against PlantUML 1.2026.8, `ARCVIZ_PLANTUML_JAR` renders the
constructs in `tests/test_plantuml_behaviour.py`).
`parse_document` collects every problem (schema, closed kind sets, container parents, unique edge ids,
`integrity_errors()`); `POST /api/diagrams/import` only ever creates a new diagram (`DiagramService.import_new`).
Project bundles (`format: arcviz-project`, spec `project-exchange.md`, ADR-0056) embed the diagram documents;
`exchange.parse_project` validates everything, `ProjectExchangeService` (`services/bundles.py`) imports into a new
project with free ids and rolls back on failure (`GET /api/projects/{id}/export`, `POST /api/projects/import`).

**Frontend** (`arcviz/static/`, spec `docs/architecture/specs/frontend-modules.md`, ADR-0020): native ES
modules, no build step. The top bar is slim: a search field opens the **command palette** (`Strg+K`; pure registry `palette.js`, view `paletteview.js`, spec `command-palette.md`, ADR-0197; a new action reachable from it is one registry entry for an existing control id) and the diagram/version actions live in one **⋯** menu. `app.js` is the only module with top-level side effects (binds elements, calls each
feature's `init…()` in a fixed order, boots). Layers: pure (`geometry`, `sse`, `model`, `format`, `commands`,
`speech`, `reply`, `proposal`, `findings`, `skins`, `projects`, `projectchat`, `svgsafe` (the SVG allow-list sanitizer
every server-rendered SVG goes through), `markdown` (assistant replies as a syntax tree, rendered by `chatlog.js` with
DOM APIs, ADR-0162); no DOM, tested in `tests/js/`) → base (`dom`, `state`, `api`) → views (`canvas`,
`chatlog`, `ui`) → hub `diagram.js` → navigator `navigator.js` (project switcher and section tree; owns
`loadDiagramList`/`refreshDiagramListLabel`, spec `project-navigator.md`, ADR-0055) → features (`inspector`, `versions`, `export`, `voice`, `splitter`, `review`, `appearance`, `topbar`, `driverpanel`) →
`chat.js` → `app.js`. A module imports only lower layers; features react to a newly shown diagram through
`diagram.js` hooks (`onDiagramShown`, `onDiagramSwitched`), never by being imported from below.
`tests/js/modules.test.mjs` enforces this (add a new module to its `LAYERS` and to the spec; M20 added the pure
`access.js` and the feature `workspaces.js`). Put new pure
logic in a pure module with a test. UI skins (`data-skin`, spec `ui-skins.md`, ADR-0049) restyle the chrome only;
the diagram stage uses its own `--stage-*` tokens that no skin may override.

**Internationalization** (spec `docs/architecture/specs/internationalization.md`, ADR-0199): UI and server messages are German or English;
the **German source text is the message key** (no symbolic keys). Server: `_("Deutscher Text {x}", x=1)` from `arcviz/i18n.py` (literal first
argument, `{placeholders}`, never f-strings; `N_()` marks a text in a module-level table, `_()` it when produced), language per request from
`Accept-Language` (`LanguageMiddleware`, German without header), catalog `arcviz/locales/en/<area>.json`. Frontend: `t("…")`/`N_("…")` from
`static/i18n.js` (never `t()` at module top level), static markup `data-i18n*`, catalog `static/locales/en/<area>.json` listed in
`static/locales/index.json`, language stored in `localStorage` (`arcviz.lang`), switched in the settings (reload). A new user-visible text
needs an English entry (`tests/test_i18n.py`, `tests/js/i18n.test.mjs` fail otherwise); tests run in German; e2e uses locale `de-DE`.

**Architecture review** (`rules.py`, `review.py`; specs `docs/architecture/specs/rule-checks.md` and
`llm-review.md`, ADR-0022–0026): `rules.run_checks(diagram)` runs the pure rule functions registered in
`rules.RULES` for `rules.diagram_type(diagram)` (`getattr(diagram, "type", None) or "deployment"`, so a
diagram type field selects its own rules) and returns `Finding`s (category strength/risk/suggestion,
severity, German message, node ids `elements`, edge ids `edges`, optional `suggestion`); served by
`GET /api/diagrams/{id}/checks`. Every type has rules: deployment (architecture patterns), activity (flow:
start, reachability, guards, fork/join, final nodes) and sequence (calls and replies matched in time order with
`alt` operands as alternatives, `_match_replies`, memoised per `run_checks` via `_memo`; findings name interaction
element ids); behaviour rules are low suggestions except an unguarded decision and a reply without call, and linear
in nodes and flows (reverse reachability, SCCs; flow helpers shared in `model.py`) (#123). `review.review(provider, diagram)` adds one
`LLMProvider.complete_tool(...)` call offering only `submit_review`, validates the arguments finding by
finding (`parse_review`: unknown ids dropped, invalid findings counted) and never raises: a failed call
returns the rule findings with `llm.ok = false` (`POST /api/diagrams/{id}/review`, nothing stored). The
panel `static/review.js` highlights a selected finding on the SVG (`findings.edgeSvgKeys` mirrors
Mermaid's `L_<from>_<to>_<n>` edge ids; `canvas.onRendered` re-applies it). **Als Vorschlag umsetzen** sends
`findings.suggestionPrompt(finding)` as an ordinary propose-mode chat turn (`initReview({onApply})` wired to
`chat.sendMessage(text, {propose: true})` in `app.js`; spec `review-suggestions.md`, ADR-0026); no server change.
**Project review** (`arcviz/project_review.py`, spec `project-review.md`, ADR-0062..0064): on a
`ProjectSnapshot` from `ProjectService.load_members`, `run_project_checks` = `PROJECT_RULES` (empty section,
section without overview (by title), missing diagram types, duplicate titles, empty diagram) + every diagram's
`rules.run_checks` with `id = "<diagram>:<id>"`; `ProjectFinding(Finding)` adds `diagram`, `diagrams`, `section`.
`review_project` makes one `complete_tool` call (`submit_project_review`) over a budgeted digest, validates per
finding (diagram by id or unique title, elements via `review._finding`), never raises (`llm.ok = false`), stores
nothing (`GET /api/projects/{pid}/checks`, `POST …/review`). `review.js` has a project mode (**Projekt-Review**
in the navigator): selecting a finding opens its diagram (`navigator.openInProject`) and highlights it there.

**Version comparison** (`diff.py`, spec `docs/architecture/specs/version-comparison.md`): `compare(old, new)`
plus `merged()` build a render-only diagram (new state + removed elements, removed edges as `-<id>`, more dashes while taken) and
marks that `mermaid.render(marks=, edge_marks=)` turns into `classDef`s and `linkStyle <n>` lines;
served by `GET /api/diagrams/{id}/compare`. The merged diagram is never persisted. Sequence diagrams (#108):
`added/removed/changed_elements` by interaction id, matched by (kind, id) (changed = own attributes, container, or
moved outside an LCS of the kept siblings, computed as an O(n log n) increasing run), and `_merged_interaction`
restores removed elements after their nearest surviving former predecessor, else at the start of their former
container, renaming one whose id the newer state reuses to `-<id>` (references follow); marks go to `sequence_svg`.

## Architecture docs and workflow

Two documentation sets with different jobs, both part of the Zensical site:

- `docs/spec/` is the technical specification **as implemented** (rule 1 above): updated in the same
  PR as the code it describes.
- `docs/architecture/` is the **design** side: `index.md` with the module/dependency table,
  `specs/` with the design of a change written before it is implemented, `adr/` with the decision
  records (append-only; a reversed decision gets a new ADR). Any change that moves a module boundary,
  an interface or a recorded decision updates these in the same PR.
- `docs/architecture/arc42/` is the **arc42 architecture documentation** (spec
  `docs/architecture/specs/architecture-documentation.md`, ADR-0040/0041): it links to the above instead of
  repeating it and holds the assessment (section 10 quality scenarios, section 11 risks with issues); a PR
  that fixes a listed risk updates its row. Its C4, deployment and runtime views are Arcviz exports
  (`docs/architecture/diagrams/*.arcviz.json`, C4 kinds from `c4-kinds.json`; the runtime views are sequence
  diagrams whose lifeline ids name server components, rendered to `.svg`, #120) rendered with `make diagrams`;
  `tests/test_architecture_docs.py` fails when a render is stale or when the server component view no
  longer matches the imports between its modules (edit the view when you change such an import).

The standard workflow for features and refactors is the `/implement` skill
(`.claude/skills/implement/SKILL.md`): design spec and ADRs first, one issue per topic, tests before
code, implementation along the module table, verification, then `docs/spec/` and the PR.

## Conventions

- PR titles must follow Conventional Commits (`feat:`, `fix:`, `ci:`, ...), enforced by
  `scripts/check_pr_title.py`, which the CI job (every push) and `pr-title.yml` (title edits) post as the
  commit status `PR title`; squash merges turn the title into the commit that release-please reads. Version bumps and `CHANGELOG.md` are generated by release-please; do not edit the version in
  `pyproject.toml` / `arcviz/__init__.py` by hand.
- CI (`.github/workflows/ci.yml`) is one job `CI` on pull requests into `main` (required checks: `CI`, `PR title`),
  weekly and on demand: lint, format and mypy always; pytest (3.14), the JS and Playwright tests, the
  strict docs build, the Docker build+smoke test and the non-blocking Cosmos emulator contract only when
  `scripts/ci_scope.py` finds matching changed paths; pytest on 3.11/3.13 only weekly and on demand
  (ADR-0006). A new check is a new step in that job, not a new job. The Dockerfile
  copies only `arcviz/`, `pyproject.toml`, `uv.lock`, `README.md`, `LICENSE`, `hatch_build.py` (plus the docs site its
  `docs` stage builds into `arcviz/docs_site/`, ADR-0042), so new runtime files must live under `arcviz/`. Every
  image, wheel and sdist contains the documentation site; `main` is also published on GitHub Pages by `docs-pages.yml` (ADR-0201): `hatch_build.py`
  refuses a non-editable build without it, `scripts/check_wheel_docs.py` checks a wheel, `make run` builds it.
- The app serves its documentation under `/docs` (spec `docs/architecture/specs/docs-in-app.md`, ADR-0043);
  FastAPI's Swagger UI is at `/api/docs`. Without a built site `/docs` answers 404 with build instructions.
- Authentication is optional and off by default (`arcviz/auth.py`, `ARCVIZ_AUTH=off|token|proxy`, spec
  `docs/architecture/specs/authentication.md`, ADR-0047; identities, workspaces and roles: see *Users, personal
  workspaces and sharing* above): the outermost ASGI middleware, then
  `arcviz/limits.py` (body sizes, optional rate limits; `request-limits.md`, ADR-0048); the probes and
  `/login`/`/logout` are public; the UI sends every request through `api.js` `apiFetch`, which turns a 401
  into a redirect to `/login` (proxy mode answers 401 without a redirect). Proxy mode trusts the identity headers:
  the app must be reachable only through the proxy. The `file` and `sqlite` stores are single-writer (one process per data
  dir; the file store's per-diagram locks are per process), only `cosmos` is safe with several replicas.
  Concurrent edits of one diagram never lose updates (conditional writes on a revision, #65); a new write
  path must go through a service method that uses `update`/`commit` with the revision it read, never write
  unconditionally. Live streams and presence work across replicas as far as the revision poll reaches (presence:
  Cosmos items read in the poll, ADR-0104).
