# ADR-0203: The vision is a structured object of the project document

- Status: Accepted
- Date: 2026-10-26
- Spec: `docs/architecture/specs/project-vision.md`

## Context

The steward and the arc42 export need to know what the product is for. Today there is `Project.description` (free text for people, no bounds that suit prompts) and drivers (quality goals, constraints, requirements, risks). The user asked for a vision object.

## Options

1. **Free text in `description`.** No new field; but the text is unstructured, mixed with other uses, and a prompt cannot cite a goal.
2. **A driver kind `vision`.** Reuses drivers; but drivers are scored (importance, risk), have scenarios and coverage, and a vision is neither.
3. **A structured `Project.vision` (statement, goals, non-goals).** One small bounded object, omitted while unset, citable by id (`vision:g2`).

## Decision

Option 3: a dedicated object, set by people only; agents may propose it (`set_vision` is proposable, refused for direct agent calls).

## Consequences

One more optional project field, one operation, one prompt block, one review rule, one UI tab. Old data is unchanged. Linking goals to drivers is a possible follow-up.
