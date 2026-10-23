# Project review: rule checks across a project and one LLM review of the whole

- Status: Implemented (milestone M17)
- Issue: #136
- ADRs: [0062 Project findings extend the finding schema with diagram references](../adr/0062-project-findings-reference-diagrams.md),
  [0063 Project rules and what counts as an overview diagram](../adr/0063-project-rules-and-overview-diagrams.md),
  [0064 The project LLM review is one tool call over a budgeted project digest](../adr/0064-project-llm-review-one-call-budgeted-digest.md)
- Builds on: [Rule checks](rule-checks.md), [LLM review](llm-review.md) (M12), [Projects](projects.md) (M16)

## Summary

The architecture review (M12) judges one diagram. A project (M16) holds several diagrams in sections, and
some problems only show at that level: an empty section, a subsystem described by a deployment diagram
but no behaviour, two diagrams with the same title. The **project review** aggregates the rule findings
of every member diagram, adds **project rules** over the project structure, and asks the model once for a
review of the project as a whole. Every finding names the diagram it concerns (and, within it, element
and edge ids), so the UI can open that diagram and highlight the elements. Like the diagram review it
never fails because of the model and stores nothing.

## Goals

- `arcviz/project_review.py`: `ProjectFinding`, a registry of project rules, `run_project_checks`,
  `review_project` (one `complete_tool` call, never raising, `llm.ok = false` on failure).
- `GET /api/projects/{pid}/checks` (rules only) and `POST /api/projects/{pid}/review` (rules + LLM).
- The review panel gets a project mode: entered from the navigator, findings show their diagram or
  section, selecting one opens the diagram and highlights its elements.

## Non-goals

- Cross-diagram consistency of shared elements (M18): diagrams shared no elements yet. M18 added these rules,
  see [Shared project model](shared-project-model.md) and ADR-0075.
- Storing reviews or findings, applying project-level suggestions without a diagram.
- New per-diagram rules.

## Design

### Findings (ADR-0062)

```python
class ProjectFinding(Finding):
    diagram: str | None = None  # the diagram `elements`/`edges` belong to and the UI jumps to
    diagrams: list[str] = []  # every diagram the finding concerns (includes `diagram`)
    section: str | None = None  # the section it concerns, if any
```

Aggregated diagram findings keep everything of the rule finding, with `id = "<diagram>:<id>"`,
`diagram`/`diagrams` set to the member. Sorting is `rules.sort_key`, then diagram order in the project.

### Project rules (ADR-0063)

Registered in `project_review.PROJECT_RULES` (`ProjectRule(id, title, category, check)`), pure functions of
a `ProjectSnapshot(project, members: list[(Membership, Diagram)])`. Messages are German.

| Rule | Category / severity | Fires when |
|---|---|---|
| `empty-section` | suggestion / low | a section has no diagram in it or below it; reported for the outermost such section only |
| `section-without-overview` | suggestion / low | a section holds two or more diagrams directly and none of them is an overview diagram (title contains Übersicht, Overview, Kontext, Context, Landschaft, Landscape, Gesamtbild or Big Picture, case-insensitive) |
| `missing-diagram-types` | suggestion / low | a section's direct diagrams show only structure (deployment) or only behaviour (activity, sequence) |
| `duplicate-title` | risk / medium (same normalised title) or low (similarity ≥ 0.85 by `difflib`, titles of at least 4 characters) | two member diagrams have the same or nearly the same title |
| `empty-diagram` | suggestion / low | a member diagram has no elements |

Normalised title: case-folded, every run of non-alphanumerics a single space, stripped.

### LLM review (ADR-0064)

`review_project(provider, snapshot)` runs the checks, then one `provider.complete_tool(...)` call with
`PROJECT_REVIEW_SYSTEM_PROMPT` and only the tool `submit_project_review`:

```text
summary: string (German, two or three sentences)
findings: [{category, severity, message, diagram?, section?, elements?, edges?, suggestion?}]  (at most 20)
```

The user message is the **project digest**: title, description, the section tree with every diagram
(`[id] «type» title`), then each diagram's `summary()` with an equal share of
`PROJECT_REVIEW_BUDGET` (100 000 characters, at least 2 000 per diagram), the rule findings ("already
reported, do not repeat"), and a note when a diagram summary is collapsed (no tool to fetch details).

Validation (`parse_project_review`), finding by finding as in `review.parse_review`: category, severity
and message are required; `diagram` must name a member (id, or a unique title); with a known diagram the
`elements`/`edges` are validated against it with the diagram review's rules (`review._finding`), without
one they are dropped; an unknown `section` is dropped; findings beyond 20 and invalid ones count as
`dropped`. A finding that names no diagram is a project-level finding. Any exception (provider error,
no tool call, unusable arguments) gives `llm = {ok: false, error}` and the rule findings.

### HTTP API

| Route | Answer |
|---|---|
| `GET /api/projects/{pid}/checks` | `{project, rules: [{id, title, category}], findings, diagrams}`; 404 unknown project |
| `POST /api/projects/{pid}/review` | `{project, summary, findings, diagrams, llm: {ok, error, dropped, usage, partial}}`; 404; 503 without provider |

`diagrams` maps every member id to `{title, type, section}` so the UI can label findings. Both read the
members through `ProjectService.load_members`; nothing is written. The limits middleware classifies
`…/review` as `review`.

### Frontend

`review.js` gets a project mode next to the diagram mode: **Projekt-Review** in the navigator's action
row opens the panel for the open project (the **Review** button in the canvas toolbar keeps the
diagram mode). In project mode:

- **Regeln prüfen** calls `…/checks`, **KI-Review** calls `…/review` of the project;
- each finding shows a chip with its diagram title or section title (`findings.findingTarget`, pure);
- selecting a finding of another diagram opens it (`navigator.openInProject`), then highlights the
  elements once rendered (the existing `onRendered` path); findings of the shown diagram highlight at
  once; project-level findings only select;
- **Als Vorschlag umsetzen** appears for findings with a diagram and a suggestion: it opens the diagram,
  then sends the suggestion as a propose-mode turn (as in the diagram review);
- switching projects closes project mode; switching diagrams keeps the project findings.

## Testing

- `tests/test_project_review.py`: every rule (fires / does not fire), aggregation and ids, sorting,
  digest contents and budget, parsing (unknown diagram/section/elements dropped, title lookup, limits),
  provider failure → `ok: false` with rule findings, ScriptedProvider for the model.
- `tests/test_server_projects.py`: both routes, 404, 503 without provider, nothing stored.
- `tests/js/findings.test.mjs`: `findingTarget`, project suggestion prompts.
- e2e: project review lists a project finding and a diagram finding; selecting the diagram finding opens
  the diagram and marks the element.

## Migration / compatibility

Additive. `Finding` is unchanged; diagram review responses are unchanged.

## Open questions

None open; the rule heuristics are a Proposed ADR (0063) for review.

## Decisions

- Project findings subclass `Finding` with `diagram`, `diagrams`, `section` (ADR-0062).
- Five project rules; an overview diagram is recognised by its title (ADR-0063).
- One tool call over a budgeted digest, validated per finding (ADR-0064).
