# Offline prompt/tool eval set

- Status: Implemented (milestone M7)
- Issue: #76
- ADR: [0016 Eval set as JSON cases with a standalone runner outside pytest and CI](../adr/0016-eval-set-outside-ci.md)

## Summary

Unit tests pin down the agent's mechanics with scripted model replies, but nothing measures whether
the system prompt and the tool descriptions make a real model do the right thing. This adds a small
eval set: each case is a starting diagram, one chat instruction and loose expectations about the
resulting diagram (and, for questions, the reply). A runner sends every case through the real
`DiagramAgent` with the configured provider and reports a score per case. It runs on demand with a real
API key (`make eval`) and never in the default CI job.

## Goals

- A versioned set of representative requests (German and English): building, nesting, moving,
  renaming, properties, edges, removal with and without cascade, read-only questions, clarifying
  questions.
- Loose scoring: every expectation is a separate check, elements are found by id or name like the
  agent does (`Diagram.find_node`), property values and labels are compared case-insensitively; the
  score of a case is the share of checks that passed, so prompt changes show partial progress.
- One command, a readable table, an optional JSON report for comparing runs.
- The harness itself is unit-tested offline (`ScriptedProvider`); the real-provider run is never part
  of `pytest` or CI.

## Non-goals

- Statistical rigour (repetitions, confidence intervals), LLM-as-judge grading, a dashboard.
- A CI job or scheduled workflow (costs API money and CI minutes; see ADR-0016).
- Testing the UI or streaming.

## Design

### Files

| File | Purpose |
|---|---|
| `tests/evals/cases.json` | The cases (list of objects, schema below). |
| `tests/evals/harness.py` | `load_cases()`, `check_case()` (scoring), `run_case()` (one case through the agent), `run_all()`. |
| `tests/evals/run.py` | CLI: `python -m tests.evals.run [--case ID ...] [--json FILE] [--min-score X]`. |
| `tests/test_evals.py` | Offline tests of the harness and of `cases.json` (no provider calls). |
| `Makefile` | `make eval` → `.venv/bin/python -m tests.evals.run`. |

Neither `harness.py` nor `run.py` matches pytest's `test_*.py` pattern, so `pytest` never collects
them. Nothing lives under `arcviz/` (not shipped in the wheel or the image).

### Case schema

```json
{
  "id": "vnet-move",
  "template": "demo",
  "setup": [{"op": "add_node", "args": {"name": "audit-db", "kind": "database", "parent": "prod-deployment"}}],
  "request": "Lege im prod-deployment ein VNet prod-vnet mit CIDR 10.0.0.0/16 an …",
  "expect": {
    "title": "Shop Produktion",
    "nodes": [{"ref": "prod-vnet", "name": "prod-vnet", "kind": "vnet", "parent": "prod-deployment", "properties": {"cidr": "10.0.0.0/16"}}],
    "absent": ["events"],
    "edges": [{"source": "service-c", "target": "orders-db", "label": "sql", "kind": "connection"}],
    "absent_edges": [{"source": "service-b", "target": "events"}],
    "unchanged": true,
    "reply_contains": ["service-b"],
    "reply_contains_any": ["orders-db", "audit-db"],
    "reply_max_chars": 300,
    "max_operations": 6
  }
}
```

`template` is a key of `arcviz.templates.TEMPLATES`; the optional `type` is the diagram type of the
`empty` template (default deployment; milestone M19, for design-help cases of every type), and the
starting diagram comes from `templates.create_from_template(template, …, type)`; `setup` operations are applied with
`operations.apply` before the turn. Every `expect` key is optional. `"scope": "project"` (M17) runs the
case through the project chat ([project chat](project-chat.md)): the diagram (titled `title`) is in a
project next to a second diagram (`Checkout`, sequence) and not open, so the model has to open it (or
create a diagram); the checks still score the eval diagram, plus `created_diagrams`. `"scope": "requirements"` (M31) runs the requirements chat ([workshop](requirements-workshop.md)) on a project whose
raw requirements are the case's `requirements` list (imported before the turn) and scores the requirements instead of a diagram
(`harness.run_requirements_case`, keys below). `model` (M25) puts the
diagram into a project with a shared model first: `elements` (`id`, `name`, `kind`, `views` of the eval diagram to
link) and `relations` (`kind`, `source`, `target`, `properties`), and gives the agent the model tools.

### Checks (one point each)

| Expectation | Check |
|---|---|
| `title` | diagram title equals (case-insensitive, trimmed) |
| `nodes[i]` | one check that `ref` resolves (`find_node`), then one per given attribute: `name` (case-insensitive), `kind` (exact), `parent` (`null` = top level, otherwise resolved with `find_node` and compared by id), each `properties` key (case-insensitive, trimmed) |
| `absent[i]` | `find_node(ref)` is `None` |
| `edges[i]` | an edge from the resolved source to the resolved target exists; `label` and `kind` are one further check each on that edge (label: case-insensitive substring) |
| `absent_edges[i]` | no edge between the resolved endpoints (either direction) |
| `unchanged` | content fingerprint equals the start state |
| `reply_contains` | each string occurs in the reply (case-insensitive), one check each |
| `reply_contains_any` | at least one occurs (one check) |
| `reply_max_chars` | the trimmed reply has at most this many characters (milestone M19: short refusals, [topic-scope.md](topic-scope.md)) |
| `max_operations` | number of applied operations ≤ the limit |
| `created_diagrams[i]` | project scope: a diagram with that `title` (case-insensitive) was created by the turn; `type` one further check |
| `requirements_added` | scope `requirements` (M31): this many requirements were added by the turn |
| `raw_contains[i]` | scope `requirements`: some requirement's original wording contains the text |
| `nothing_in_force` | scope `requirements`: no existing requirement got a statement or left `raw` (people decide, ADR-0186) |
| `suggested` | scope `requirements`: at least this many requirements carry a suggestion |
| `edges[i].relation` | that edge shows the project relation with this id (`ref`) |
| `relations[i]` | the project model has a relation of that `kind` between `source` and `target` (either orientation) |
| `relation_count` | the project model holds exactly this many relations after the turn |
| (always) | the turn ended without an error |

`score = passed / total`; a case *passes* with score 1.0. The result records the checks with their
outcome, the applied operations, the reply, tokens (`usage`), wall-clock seconds and the error.

### Running a case

`run_case(provider, case)` creates a `FileStore` in a temporary directory, creates the diagram from the
template, applies `setup`, runs `DiagramAgent(provider, store, compact_after=0, background=False).chat()`
(project scope: `ProjectAgent(...).chat(project, request)` without an active diagram) to the end, loads the stored diagram and scores it. A provider exception becomes the turn's error
(score drops by the error check; other checks still count against the stored state).

### CLI

`python -m tests.evals.run` builds the provider with `create_provider()` (the normal `ARCVIZ_*`,
`ANTHROPIC_*`, `OPENAI_*` variables; `ARCVIZ_STREAM` etc. apply), runs all or the selected cases
sequentially, prints one line per case (`id`, score, passed checks, operations, tokens, seconds, failed
checks) and a summary (mean score, cases passed, total tokens). `--json FILE` writes the full results;
`--min-score X` exits 1 if the mean score is below `X` (0 otherwise; default no threshold). A provider
that cannot be built exits 2 with the reason.

## Testing

`tests/test_evals.py` (offline, part of `make test`): every case in `cases.json` loads, ids are unique,
templates exist, setup operations apply, every expectation key is known and every `ref` in `nodes`,
`edges`, `absent` that must exist at the start does so where stated; `check_case` scoring for passing
and failing expectations of each kind; `run_case` with a `ScriptedProvider` that solves the
`vnet-move` case scores 1.0, one that does nothing scores below 1 and names the failed checks; the CLI
`main()` with an injected provider factory prints the table and honours `--min-score` and `--json`.

## Migration / compatibility

None; new files only.

## Open questions

None open; decisions taken without the maintainer are *Proposed* in ADR-0016.

## Decisions

- JSON cases plus a standalone runner under `tests/evals/`, invoked with `make eval`; no pytest marker,
  no CI workflow (ADR-0016, Proposed).
- Loose, check-based partial scoring without an LLM judge (Proposed).
