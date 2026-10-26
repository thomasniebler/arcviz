# Project steward

- Status: Implemented (with the deviations listed under *As implemented*)
- Issue: #8
- ADRs: [0204 The steward runs in process, on change, with a lease](../adr/0204-steward-runs-on-change-with-a-lease.md),
  [0205 A different model verifies what the steward proposes](../adr/0205-independent-verifier-model.md),
  [0206 Project proposals carry the operations that fix findings](../adr/0206-project-proposals-carry-fix-operations.md)
- Related: [Project review](project-review.md), [Project vision](project-vision.md), [arc42 export](arc42-export.md),
  [Agent access](agent-access.md), [Requirements workshop](requirements-workshop.md)

## Summary

Reviews are on demand today. The steward re-checks a project **in the background, after it changed**, and puts what it
finds into the existing proposal panel: **fixes** for current findings, and **next steps** that follow the project's
[vision](project-vision.md), drivers and requirements. It never writes to the project: its only outputs are one pending
project proposal and its own bookkeeping document. A person applies, in part or whole, or discards.

Because an LLM can be confidently wrong, nothing the model authors reaches the panel unless a **second, different
model** has confirmed it against the cited model objects (ADR-0205).

## Goals

- Surface new problems and sensible next steps without anybody asking, in the proposal panel the user already knows.
- Every proposal item says why (rationale), and cites the model objects it is about.
- No nagging: a dismissed item never comes back, a run costs bounded tokens, the panel never floods.
- Safe by construction: same single mutation path, no new write privilege (`tests/test_steward.py` pins it).

## Non-goals

- Applying anything automatically, not even "obviously correct" fixes.
- Editing diagrams from LLM output (follow-up; see open questions). Diagram-level findings keep their per-finding
  resolution buttons.
- A scheduler framework or external job runner.
- Notifications (mail, push). The panel and the existing live `project` events are the only surfaces.

## Design

### Configuration (`arcviz/steward.py` `StewardConfig.from_env`, via `Env`)

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_STEWARD` | `0` | Master switch. Off: no worker, routes answer 404-like "disabled" (409) |
| `ARCVIZ_STEWARD_DEBOUNCE_SECONDS` | `300` | A project is checked this long after its last change |
| `ARCVIZ_STEWARD_SWEEP_SECONDS` | `3600` | Every enabled project is checked at the latest this long after its last check (what other replicas changed, a lost hint) |
| `ARCVIZ_STEWARD_MAX_RUNS_PER_DAY` | `4` | Per project, UTC day; manual runs count |
| `ARCVIZ_STEWARD_MAX_ITEMS` | `15` | Items per run (hard cap `MAX_ITEMS` 60 of a proposal) |
| `ARCVIZ_VERIFIER_PROVIDER` | unset | Provider of the verifier (`anthropic`, `openai`); with `ARCVIZ_VERIFIER_MODEL` |
| `ARCVIZ_VERIFIER_MODEL` | unset | Model id; must differ from the main `(provider, model)` pair or the start stops |

Credentials of the verifier reuse the provider's usual variables (`ANTHROPIC_API_KEY`, `OPENAI_*`) so a deployment needs
no second secret for the same vendor. Without a verifier the steward still runs but only produces **deterministic**
items (below); it says so in its status. New variables go into `docs/reference/configuration.md`.

### Per-project state: keyed document `steward` (`arcviz/steward.py`, `services/steward.py`)

```
StewardBox:
  incarnation: str           # of the project, like proposals: another incarnation → ignore the box
  enabled: bool              # opt-in per project (default false), set by the owner
  last_fingerprint: str      # project + member diagrams when the last run started
  last_run: {at, outcome, items, tokens, verified, dropped}
  runs: {day: count}         # last 7 days only
  dismissed: list[str]       # origin keys of discarded items, ≤ 500 (oldest dropped)
  lease: {holder, until}     # ADR-0204
```

Stores need no new primitive: it is a keyed document of kind `steward` (file `steward/`, sqlite `documents`, Cosmos
partition `steward:<project>`). Writes are conditional on its revision.

### Trigger and placement (ADR-0204)

`StewardWorker` (own thread, started by the lifespan next to `Heartbeat`, off unless `ARCVIZ_STEWARD=1`) subscribes to the
`LiveHub` project and diagram events (the services already publish after every write). A hint marks the project dirty (in memory; a restart forgets it,
the sweep finds it again); a project is due when `now − last change ≥ debounce`, it is enabled, its day count is
below the cap, and the worker holds its lease. Lease: conditional write of `lease` (`holder = <boot>.<random>` as in
`locations.Writer`, `until = now + 2 × run timeout`), renewed during a run; an expired lease is taken over, so several
replicas (Cosmos) never run the same project at once and a crashed replica blocks nothing for long. A change that arrives
during a run only marks it dirty again. The steward's own writes (proposal, box) publish no change hint for the project
document, so it never triggers itself; applying one of its proposals does (that is a change).

`POST /api/projects/{pid}/steward/run` (editor or owner, rate class `review`) runs immediately, ignoring the debounce but
not the day cap, and answers 200 with the report of the run (it runs in the request, on a worker thread) and the status; `GET /api/projects/{pid}/steward` returns the status (`enabled`,
`last_run`, `runs_today`, `verifier: configured|missing`); `PUT …/steward` sets `enabled` (owner only).

### Identity and authorization

The steward runs in the **system context** (unguarded services, like the migration), because no user is present. It may do
exactly three things: read the project snapshot, call `ProposalService.create_project(..., client="steward")` and
`discard` its own superseded proposal, and write its box. The proposal author is the fixed identity id `steward` (name
**Steward**). It never calls `ProjectService.apply` or any diagram write; `tests/test_steward.py` runs a full cycle against
a recording service double and fails on any other call, like `tests/test_agent_tools.py` for the MCP tools.

### One run (`steward.run_project`)

1. Load the snapshot (`ProjectService.load_members`) and fingerprint it; equal to `last_fingerprint` and no manual run:
   stop (`outcome: unchanged`).
2. **Deterministic stage** (always): `project_review.run_project_checks`. Each finding whose resolutions map to a
   proposable operation (ADR-0206) becomes an item with `derived_from=[]`, `title` = the finding message, rationale
   = the rule id and message, `origin` key `rule:<rule>:<sorted element/diagram ids>`. These need no verification: a rule is
   reproducible evidence.
3. **LLM stage** (only if a verifier is configured and the token budget allows): one `complete_tool` call on the main
   provider offering `submit_steward_findings` over `project_review.digest_lines`, the architecture context (vision,
   drivers with core scenarios, accepted decisions, requirements coverage) and the open rule findings. Two kinds:
   - **fix** candidates for what the rules cannot see (the LLM review's findings), and
   - **next steps** (`kind: next_step`) that serve a named vision goal or driver (`serves: ["vision:g2", "d3"]`).

   Each candidate must name `cites` (ids that exist in the snapshot; unknown ids drop the candidate) and carry the
   operation items that would realise it (only `PROPOSABLE_OPERATIONS`; validated with `validate_arguments`). A next step
   with no `serves` is dropped: "follow the vision" must be demonstrable.
4. **Verification** (ADR-0205): for every candidate one `complete_tool` call on the verifier offering `submit_verdict`
   (`supported | unsupported | unclear`, `reason` ≤ 300), given the candidate, the cited objects dumped in full, the vision
   and the digest, **not** the main model's reasoning. Only `supported` passes; the reason is appended to the item's
   rationale prefixed "Prüfung:". Calls run with bounded concurrency and a per-run token budget; failures count as
   `unclear` (never pass, never raise).
5. **Select**: drop items whose `origin` is in `dismissed`, already `pending` in another proposal, or applied (matching
   `origin` in the project's objects); order fixes before next steps, rule findings by severity; cap at
   `ARCVIZ_STEWARD_MAX_ITEMS`.
6. **Publish**: the steward keeps at most **one** pending project proposal. The previous one is `discard`ed (its pending
   items become `discarded`; their origins are *not* added to `dismissed`, only a person's discards are), then the new one is
   created through `create_project`. No items: no proposal, `outcome: clean`. A person's partially applied proposal is left
   alone and counts as decided.
7. Record the run in the box.

### Dismissal memory

`ProposalService.discard` for a proposal with `client == "steward"` and a person as actor adds the discarded items'
origin keys to `StewardBox.dismissed`. An applied item changes the project, so it vanishes from findings on its own.

### Proposable operations (ADR-0206) {#proposable-operations}

`PROPOSABLE_OPERATIONS` grows by the operations that fix model-level findings and by the vision:
`set_vision`, `add_element`, `update_element`, `add_relation`, `update_relation`, `remove_relation` (not
`remove_element`: deleting stays a person's act, ADR-0198), plus `update_scenario`/`update_driver` as today. Internal
element operations that carry derived data (`link_element`, `sync_element`, `clear_element`) stay refused. `AGENT_REFUSED_FIELDS` is kept
and gains the vision's refusal for direct calls only. `projects.apply` still validates every argument at apply time, so
a stale or wrong item fails alone (`state: failed|stale`), as for the derivation today.

`MAX_PENDING_PROJECT` stays 5; the steward's single pending proposal counts against it. If the cap is reached by people's
proposals the run ends with `outcome: blocked` and nothing is written.

### Prompts and guards against bad advice

- `prompts/steward/findings.txt` and `verdict.txt`, English instructions with German output like the other review prompts.
- The findings prompt forbids advice without citation and instructs "propose nothing rather than something weak"; the
  verifier prompt is adversarial ("find the reason this is wrong or not supported by the cited objects") and answers only
  with a verdict.
- All text that came from a model is shown in the panel as text (existing DOM rendering), never as markup.
- Logging: only counts, outcomes and ids hashed by the existing helpers; never diagram content, never the exception
  (`tests/test_log_privacy.py` extended).

### UI

The proposal panel (`proposalpanel.js`) shows items with `client == "steward"` under a **Steward** badge, grouped
**Korrekturen** / **Nächste Schritte**, with the rationale and "Prüfung" lines and the cited objects as links. A small
status line (last run, enabled switch for owners, **Jetzt prüfen**) sits in the **Architektur** dialog next to the
review. No new panel (user decision: same proposal panel).

### Observability

Spans `arcviz.steward.run` (attributes: outcome only), metrics `arcviz.steward.runs`, `arcviz.steward.items`
(`verdict` attribute), `arcviz.llm.tokens` as usual with the verifier's role as an allow-listed attribute value.

## Testing

- `ScriptedProvider` for main and verifier (separate instances; the test asserts the verifier saw no main-model text).
- Deterministic stage: a seeded project → expected items and origins; same project twice → no second proposal.
- Verification: `unsupported`/`unclear`/error → dropped; `supported` → in the proposal with the reason.
- Dismissal: discard by a person → next run proposes nothing for those origins.
- Lease: two workers over one store, one run; expired lease taken over; debounce; day cap; self-trigger absent.
- Safety: recording double, only the three allowed calls; layering test for `steward.py`; access matrix for the three routes.
- Config: verifier equal to the main model stops the start with a message.
- Evals (`"scope": "steward"`, how often the verifier rejects) are a follow-up: the harness has no steward scope yet.
- e2e: enable, run now, see the badge, apply one item, discard another.

## Migration / compatibility

New keyed document kind and new optional route family; stores need no migration. Proposals gain no field: `client` already
exists (`"steward"` is a new value), the `StoredProposal` shape is unchanged, so old data and clients are unaffected.

## As implemented

Where the code differs from the design above (all deliberate):

- **Modules:** `steward_model.py` (pure: `StewardConfig`, `StewardBox`, `finding_key`), `steward.py` (prompts, candidate parsing,
  rule items, selection, `Steward.run`, `StewardWorker`), `services/steward.py` (`StewardStore`: the document, the lease,
  `dismiss`; `StewardService`: the guarded face of the routes) and `ServiceRoot.system_services()` (`SystemServices`: unguarded
  `projects`, `requirements`, `proposals` and `steward_store` bound to the identity `steward`). `LiveHub.add_listener` is the
  only change to the hub.
- **`ProposalItem`** gained four optional fields, omitted from dumps while empty: `finding` (the key a discard remembers), `group`
  (`fix` | `next_step`), `rationale` (≤ 600 characters, with the verifier's remark after "Prüfung:") and `cites` (≤ 12 ids).
  `ProposalService` takes a `DismissalSink` (the steward's store): a **person's** discard of items of a `steward` proposal
  remembers their `finding` keys; the steward's own withdrawal of its previous proposal does not.
- **Model items:** `add_element`, `update_element`, `add_relation`, `update_relation` and `remove_relation` apply through
  `ModelService` (`ProposalService._apply_model`); an interrupted `add_*` is recognised by what it would create (an element's name,
  a relation's kind and ends), since these carry no `origin`. `remove_relation` is proposable by a person's tools, but
  `steward.LLM_OPERATIONS` leaves it out: a model never proposes a deletion.
- **Deterministic stage:** an item only for a finding with exactly one model-level resolution among `create_relation`,
  `update_relation` and `move_element`; never `remove_relation`, never a choice between alternatives. Most findings keep their
  buttons.
- **Run budget:** `steward_model.MAX_TOKENS_PER_RUN` (200 000 tokens of main and verifier calls); candidates beyond it are dropped.
  `capped` and `disabled` runs write nothing.
- **Verifier at start-up:** `ARCVIZ_VERIFIER_PROVIDER`/`_MODEL` are read as a `ProviderConfig` (the verifier's variables on top
  of the usual ones, `steward_model.verifier_environ`); equal to the chat's provider and model stops the start
  (`steward.verifier_problems`), the SDK is checked like the chat provider's.
- **UI:** a status line (switch, last run, **Jetzt prüfen**) under the heading of the **Architektur** page (`stewardpanel.js`, pure
  `steward.js`), hidden while `ARCVIZ_STEWARD` is off; the proposal panel groups steward items under **Korrekturen** and
  **Nächste Schritte** and shows each item's rationale and cited ids.

## Open questions

1. **Diagram-level fixes.** Items that edit a diagram (from LLM output) would need one diagram proposal per diagram with
   its own freshness check and would eat the `MAX_PENDING` budget. v1 leaves them out; the existing per-finding buttons stay.
2. **Cost visibility.** A per-project token budget shown in the status line, or only counters in telemetry?
3. **Verifier not configured.** v1 drops all LLM candidates and says so. Alternative: allow the main model to self-verify
   with a visible "ungeprüft" badge. Proposed: no, a second opinion from the same model is the weak point we want to avoid.

## Decisions

- Same proposal panel, steward items marked (user, session decision).
- Different model verifies (user, ADR-0205).
- Opt-in per project; on change with debounce, not on a clock (ADR-0204).
- System context with exactly three permitted calls (ADR-0204).
- Open questions 1 to 3 resolved as proposed: no LLM-written diagram edits in v1, counters in telemetry only, and no
  self-verification without a configured verifier (user: "things are fine").
