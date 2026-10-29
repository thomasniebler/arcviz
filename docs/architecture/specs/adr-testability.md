# ADR testability and the MADR export

- Status: Implemented
- Issues: #12
- ADRs: [0209 Assumptions and a falsification criterion are stored on the decision, suggested by the model and confirmed by a person](../adr/0209-adr-assumptions-stored-and-suggested.md),
  [0210 The exported ADR follows MADR 4](../adr/0210-adr-export-follows-madr.md)
- Builds on: [Decision support](decision-support.md) (decisions, options, the Markdown ADR),
  [ISO 25010 qualities](iso25010-qualities.md) (the suggestion pattern)

## Summary

Every architecture decision rests on assumptions ("the second region fails over in under 60 s", "the team can operate
Kubernetes"). Today an ADR records the options and the choice but not **how cheaply those assumptions could be tested**,
so nobody knows which part of a decision is a safe bet and which should be validated first. A decision gets

- `assumptions`: what the choice rests on, each with *how to test it*, the *effort* of that test, and what is *known so
  far* (untested / confirmed / refuted), and
- `falsification`: the observation that would make the team reverse the decision.

In the editor a model **suggests** assumptions, tests and a falsification criterion from the decision text; the person
edits and saves (nothing is stored unconfirmed). The Markdown ADR export is rewritten to follow **MADR 4** and gains an
**Assumptions and testability** section.

## Goals

- A fixed, documented ADR schema (MADR 4) for the export.
- A testability viewpoint per decision: assumptions, appropriate tests, test effort, evidence, a falsification criterion.
- Help writing it (model suggestion, same pattern as ISO 25010), without trusting the model.
- Old data, bundles and fingerprints stay valid.

## Non-goals

- Running the tests, or linking to a CI system.
- A project-wide testability dashboard (the [gap analysis](arc42-gap-analysis.md) only counts decisions without assumptions).
- Agent tools that write assumptions: agents already propose decisions as proposal items; assumptions are part of the
  same arguments and are checked like every other argument (ADR-0209), but no new tool is added.

## Design

### Model (`decision_model.py`)

```python
ASSUMPTION_STATUSES = ("untested", "confirmed", "refuted")  # omitted from dumps while "untested"


class Assumption(BaseModel):
    statement: str  # ≤ MAX_ASSUMPTION (300), one line
    test: str = ""  # how to find out cheaply and what result would count (≤ MAX_TEST 600)
    effort: str = "medium"  # DRIVER_LEVELS: low | medium | high (cost of running that test)
    status: str = "untested"  # ASSUMPTION_STATUSES
    evidence: str = ""  # what is known so far, a result or a link (≤ MAX_TEST)
```

`Decision.assumptions: list[Assumption]` (at most `MAX_ASSUMPTIONS` = 8) and `Decision.falsification: str`
(≤ `MAX_LONG_TEXT`). Both are omitted from dumps while empty (`_without_empty`), so stored data and fingerprints of
existing decisions do not change.

### Operations (`projects.py`)

`add_decision` and `update_decision` take `assumptions: list[dict]` (replaces the list; the same validation as options:
`ProjectError` for a bad shape, an empty statement, an unknown effort or status, too many) and `falsification: str`.
No new operation, so REST (`POST`/`PATCH …/decisions`), agent proposals and bundles carry them unchanged.

### Suggestion (`decision_testability.py`)

One `LLMProvider.complete_tool` call offering only `submit_testability` over the decision as typed (title, context,
options with pros/cons, the decision, consequences; bounded). The answer is validated per item (`clean_assumptions`:
at most `MAX_ASSUMPTIONS`, empty statements and unknown efforts dropped, texts bounded), and the system prompt asks for
tests that are **cheap and decisive**: a spike, a load test with a stated threshold, a failure-injection experiment, a
cost estimate, an interview, a prototype. `POST /api/projects/{pid}/decisions/testability` (rate class `review`, viewer
access like `scenarios/qualities`) answers `{assumptions, falsification, llm: {ok, error, dropped, usage}}`; it never
stores anything and never raises because of the model.

### Markdown ADR (`decision_model.decision_markdown`)

MADR 4 (<https://adr.github.io/madr/>), headings in the request language, empty sections written as `–`:

```
# ADR-3: Title
- Status: Angenommen (2026-10-01)            (decided date; superseded by …)
- Szenario / Treiber / Elemente / Anforderungen / Stakeholder: …   (only those that are set)

## Kontext und Problemstellung
## Entscheidungstreiber                      (the linked drivers, one bullet each with its title)
## Betrachtete Optionen                      (a numbered list of the option titles)
## Entscheidung
Gewählte Option: „B“, weil … (the decision text)
### Konsequenzen
## Vor- und Nachteile der Optionen           (per option: description, Pro, Contra; the chosen one marked)
## Annahmen und Testbarkeit                  (table: assumption | test | effort | status | evidence; then the falsification criterion)
```

The section is omitted when a decision has neither assumptions nor a falsification criterion and the decision is a draft;
for an accepted decision it is always written and says `–` (that is the visible gap).

### Bundles

`schema_version` 10 whenever a decision has assumptions or a falsification criterion (`PROJECT_SCHEMA_VERSION` = 10);
older versions refuse such a bundle instead of dropping the fields.

### UI (`decisionpanel.js`, pure `testability.js`)

A **Testbarkeit** group in the decision editor: a list of assumption rows (statement, test, effort, status, evidence;
add / remove), the falsification field, and **Vorschlag vom Assistenten**, which fills new rows marked as suggestions
(the person edits, then saves; the existing rows are only replaced after one confirmation). A chip `n Annahmen` on the card
and a muted `ungetestet` chip when an accepted decision has no assumptions.

## Testing

Unit tests for the model and operations (bounds, omission while empty, fingerprints unchanged), `clean_assumptions`
(hostile and malformed model output), the MADR layout (golden structure, escaping, language), the route (access,
rate class, failed call), the bundle round trip and version, the property tests are unaffected; JS tests for
`testability.js`; the e2e smoke test fills the group.

## Migration / compatibility

None: optional fields, omitted while empty; bundle version only when used.

## Open questions

None.

## Decisions

- Stored fields with a confirmed model suggestion (ADR-0209), not computed at export time.
- MADR 4 as the export schema (ADR-0210).
