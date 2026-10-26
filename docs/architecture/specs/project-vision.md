# Project vision

- Status: Accepted (design agreed, not implemented)
- Issue: none yet
- ADR: [0202 The vision is a structured object of the project document](../adr/0202-vision-is-a-structured-project-object.md)
- Related: [Architecture drivers](architecture-drivers.md), [Project steward](project-steward.md),
  [arc42 export](arc42-export.md), [Projects](projects.md)

## Summary

A project can say what the product is for. Today the nearest things are `Project.description` (free text, for people) and
the drivers (quality goals, constraints, requirements, risks). Neither answers "where should this architecture go?",
which the [project steward](project-steward.md) needs to propose next steps and the [arc42 export](arc42-export.md)
needs for section 1. This change adds one small structured object, `Project.vision`.

## Goals

- One place for the product vision, readable by both assistants, the review, the steward and the arc42 export.
- Written by people; assistants may only propose a change.
- Data written before this change stays byte-identical (the field is omitted while empty).

## Non-goals

- A roadmap, milestones or a backlog.
- Versioned vision history beyond the project document's own revisions.
- Linking vision goals to drivers or requirements (a possible follow-up once the steward shows what it needs).

## Design

### Model (`arcviz/project_model.py`)

```
Vision:
  statement: str          # ≤ MAX_VISION_STATEMENT (2000): what the product is and for whom
  goals: list[str]        # ≤ MAX_VISION_GOALS (10) entries of ≤ MAX_VISION_ITEM (200): what success looks like
  non_goals: list[str]    # same bounds: what the product deliberately is not
```

`Project.vision: Vision | None = None`, omitted from dumps while unset or empty (wrap serializer, like the other optional
fields). Texts are stripped; control characters are refused like in other free-text fields.

### Operation (`arcviz/projects.py`)

`set_vision(statement, goals, non_goals)` replaces the object as a whole; an all-empty call clears it. It is a public
project operation (`projects.VISION_OPERATIONS`), validated through `validate_arguments`, rolled back on any exception.
Because the vision is a person's statement, `projects.AGENT_REFUSED_OPERATIONS` contains it: agents (chat assistants, MCP
credentials, the steward) cannot call it directly. It is however in `PROPOSABLE_OPERATIONS`
([steward spec](project-steward.md#proposable-operations)), so an agent may **propose** a vision and a person confirms.

### HTTP

`GET /api/projects/{pid}` already returns the project: it gains `vision` (absent while unset).
`PUT /api/projects/{pid}/vision` (body = the three fields; editor or owner; `If-Match` honoured) calls the operation
through `ProjectService.apply`. Both go into `docs/reference/api.md` and the access matrix.

### Where it is used

- **Prompts:** `prompts/shared/vision.txt` plus a `vision_summary` line block, placed before the drivers part of
  `prompts.architecture_part`. Absent vision: no block and no placeholder text (an empty hint would invite invention).
- **Review:** a new deterministic project rule `vision-missing` (low suggestion) fires for a project with at least one
  diagram or driver and no vision; `project_review.review_input` hands the vision to the LLM review as context.
- **Steward and arc42:** see their specs.
- **UI:** a **Vision** tab in the **Architektur** dialog (`static/visionpanel.js`, view layer, one editor form with the
  statement and two list fields, pure helpers in `static/vision.js`).

### Internationalization

All new UI texts and the rule message get English catalog entries (`tests/test_i18n.py`, `tests/js/i18n.test.mjs`).

## Testing

- Unit: bounds, omission from dumps, `set_vision` round trip, clear, agent refusal, proposable.
- Compatibility: a project JSON without `vision` loads and dumps unchanged (`tests/test_compat_*` pattern).
- Server: `PUT …/vision` (viewer 404/403 per the matrix, editor 200, `If-Match` 412), access matrix entry.
- Prompt: block present only with a vision.
- JS: pure `vision.js` tests; e2e: edit the vision in the dialog.

## Migration / compatibility

None: the field is optional and omitted while unset. Bundles: a vision is a bundle field, so a bundle carrying one is
`schema_version` 8 (a bundle without stays at its current version, like drivers and requirements before).

## Open questions

- Should the vision be exported into the project bundle in all cases, or only when present? (Proposed: only when present.)

## Decisions

- Structured object, not a driver kind, not free text in `description` (ADR-0202).
- Agents may propose, never set (ADR-0202).
- The vision is a bundle field only when present (open question resolved as proposed).
