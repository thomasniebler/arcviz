# ADR-0075: Cross-diagram consistency rules over element references

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/shared-project-model.md` (case study: `shared-project-model-case-study.md`)

## Context

The project review (M17, ADR-0062/0063) could only compare titles and structure. References give the
review a precise basis for checks across diagrams.

## Options

1. **Leave consistency to the LLM review.** Not deterministic, not cheap, no stable finding ids.
2. **Deterministic project rules over refs**, registered in `PROJECT_RULES` like the M17 rules.

## Decision

Option 2 with these rules (German messages, `ProjectFinding.model_element` names the element):

- `dangling-element-ref` (risk, medium): a view references an element the project lacks.
- `element-name-drift` (risk, low): a linked view shows another name than its element.
- `incompatible-element-link` (risk, medium): the view's kind cannot represent the element's kind.
- `unused-element` (suggestion, low): no view and no section references the element.
- `element-not-in-structure` (suggestion, low): a non-actor element appears in behaviour (lifelines,
  partitions) but in no deployment diagram, while the project has deployment diagrams with linked nodes.
- `message-without-connection` (risk, medium): a sequence message between lifelines of two elements
  that both appear in deployment diagrams, while no deployment edge connects them (a node stands for the
  elements it and its ancestors reference).
- `unlinked-namesake` (suggestion, low): an unlinked, linkable view element named like an element of a
  compatible kind.

## Consequences

- The rules are conservative: they only fire where the model gives evidence, so projects without a model
  get no new findings.
