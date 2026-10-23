# ADR-0023: One finding schema for rule checks and the LLM review

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/rule-checks.md`, `docs/architecture/specs/llm-review.md`

## Context

Milestone M12 produces findings from two sources: deterministic rule checks (#95) and an LLM review
(#96) that should say what is already good, what the risks are and what to improve. The UI lists
the findings and highlights the elements they concern on the canvas, and #97 turns a suggestion
into a propose-mode change. The UI is German; code, docs and the model's tool interface are English.

## Options

1. **One schema for both sources**: `id`, `source` (`rule`/`llm`), `rule`, `category`
   (`strength`/`risk`/`suggestion`), `severity` (`info`/`low`/`medium`/`high`), `message`, node ids
   (`elements`), edge ids (`edges`), optional `suggestion`. The UI renders one list; the LLM is asked
   for exactly this shape.
2. **Separate shapes** (rule results with a rule id and severity; LLM output as free text sections
   "good / risks / suggestions"). Less validation work, but the UI cannot highlight free text and
   needs two renderers.
3. **Severity only, no category** (strengths as severity `info`). Simpler, but "what is already
   good" and "a low-severity risk" become indistinguishable in the list.

## Decision

Option 1, so rules and the LLM feed one list that the UI can group, sort and highlight the same
way. Strengths always carry severity `info`. Node ids and edge ids are separate lists because
they are different namespaces (`e1` could be a node id). Finding messages and suggestions are
German because they are shown as written.

## Consequences

- The LLM output must be validated against this schema (ADR-0024); ids it invents are dropped.
- German rule texts live in `arcviz/rules.py`; a translated UI would need message keys instead
  (follow-up if the UI ever gets a second language).
- The `suggestion` text is what #97 sends to the model, so it is phrased as an instruction.
