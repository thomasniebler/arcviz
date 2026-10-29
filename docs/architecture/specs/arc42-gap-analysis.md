# arc42 gap analysis

- Status: Implemented
- Issues: #13
- ADRs: [0211 The gap analysis is a deterministic check per arc42 section, with an optional model pass that only advises](../adr/0211-arc42-gap-analysis.md)
- Builds on: [arc42 export](arc42-export.md) (the twelve sections and their `gaps`), [Project review](project-review.md),
  [ADR testability](adr-testability.md)

## Summary

"Your vision is still missing, add one!" For every one of the twelve arc42 sections the project says whether it is
**complete**, **partial** or **missing**, lists the concrete gaps in plain German/English with a severity, and offers the
**next step** as a button that jumps to the place that fixes it (the Vision tab, the Treiber tab, a new diagram, a
documentation note). The check is deterministic and free. An optional second pass asks a model whether the sections that
*do* have content are thin or contradict each other; its remarks are shown, never stored, and never counted.

## Goals

- A completeness view orientated on arc42, ordered the way arc42 is ordered, with a next action for every gap.
- Reuse what `arc42.build` already knows (a section without source says so) instead of a second opinion.
- Deep checks beyond emptiness: quality goals without scenarios, accepted decisions without assumptions
  ([ADR testability](adr-testability.md)), a vision without goals.
- An optional model pass for content quality.

## Non-goals

- Blocking anything (nothing is required), or changing the project (the analysis only reads).
- Grading prose (the deterministic pass never judges text quality; the model pass advises only).
- A steward integration (a possible follow-up: gaps as steward proposals).

## Design

### Deterministic analysis (`arc42_gaps.py`, pure)

`analyse(doc: arc42.Document, project, requirements) -> GapReport` over the document `arc42.build` assembled:

```python
class Gap(BaseModel):
    id: str          # stable code, e.g. "vision-missing", "quality-goal-without-scenario"
    section: str     # "01".."12"
    severity: str    # "high" | "medium" | "low"
    message: str     # request language
    action: dict     # {"tab": "vision"} | {"tab": "drivers"} | {"view": "diagrams", "type": "sequence"} | {"tab": "documentation", "section": "08"}
    items: list[str] # ids of the affected objects (a quality goal, a decision), for the UI to name
class SectionStatus(BaseModel):
    id, title, status ("complete"|"partial"|"missing"), gaps: list[Gap]
class GapReport(BaseModel):
    sections: list[SectionStatus]; complete: int; total: 12
```

`arc42.Section.gaps` becomes a list of `arc42.Gap(code, text)` with `gaps` texts exposed unchanged to the writers
(`Section.gap_texts`); the codes are the ids below. Status: `missing` when the section has no content and no note,
`partial` when it has content but at least one gap, else `complete`. A note of the section (`arc42_notes`) counts as
content for sections 02–12 as in `arc42.build`.

| Section | Gap id (severity) | Condition |
|---|---|---|
| 01 | `vision-missing` (high) | no vision; action: Vision tab |
| 01 | `vision-without-goals` (medium) | a statement but no goals |
| 01 | `quality-goals-missing` (high) | no driver of kind `quality_goal`; action: Treiber |
| 01 | `stakeholders-missing` (high) | none; action: Stakeholder |
| 02 | `constraints-missing` (medium) | no driver/requirement of kind constraint |
| 03 | `context-missing` (medium) | no overview diagram; action: new diagram |
| 04 | `strategy-missing` (medium) | no accepted decision and no note |
| 05 | `building-blocks-missing` (high) | no project element and no component diagram |
| 06 | `runtime-missing` (medium) | no sequence diagram and no core scenario |
| 07 | `deployment-missing` (medium) | no deployment diagram and no `deployed_on` relation |
| 08 | `crosscutting-missing` (low) | no note; action: Dokumentation, section 08 |
| 09 | `decisions-missing` (high) | no accepted decision |
| 09 | `decision-without-assumptions` (low) | an accepted decision without assumptions |
| 10 | `scenarios-missing` (high) | no quality scenario |
| 10 | `quality-goal-without-scenario` (medium) | an active quality goal no scenario serves |
| 11 | `risks-missing` (medium) | no risk driver and no open finding |
| 12 | `glossary-missing` (low) | no described element and no note |

Gaps of one section are sorted by severity, then by id. `GET /api/projects/{pid}/arc42/gaps` (viewer) answers the report.

### Model pass (`arc42_gap_review.py`)

`POST /api/projects/{pid}/arc42/gaps/review` (rate class `review`, viewer): one `complete_tool` call offering only
`submit_gap_review` over a budgeted digest of the document (per section its Markdown cut to a limit, its gaps). The
model may return per section at most `MAX_REMARKS` remarks of kind `thin | inconsistent | outdated` with a one-sentence
German/English text and optionally a section it conflicts with. Validated per item (unknown sections dropped, texts bounded,
counted in `dropped`), never stored, never raises (`llm.ok = false`). The report's status is not changed by it.

### UI (`gapspanel.js`, pure `gaps.js`; tab **Lücken** on the Architektur page, route `#/architecture/gaps`)

A progress line (`7 von 12 Abschnitten vollständig`), then one card per section in arc42 order: number, title, status chip,
its gaps each with a **next step** button (`gaps.js` maps `action` to a hash route or the navigator's create-diagram flow),
and below the cards a button **Inhaltlich prüfen (Assistent)** that adds the remarks to the cards (marked as advice).
The Dokumentation tab links to the tab.

## Testing

Pure unit tests for every gap id (present and absent), the status rule, notes covering sections, retired objects not
counting, determinism; route tests (access matrix, viewer access, JSON shape); model pass with `ScriptedProvider`
(valid, hostile and failing answers, budget); JS tests for `gaps.js`; e2e: open the tab, follow a next step.

## Migration / compatibility

No stored data. `Section.gaps` strings are unchanged for exports and the JSON route.

## Open questions

None.

## Decisions

- Deterministic core plus an advisory model pass (ADR-0211).
