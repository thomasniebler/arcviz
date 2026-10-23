# ADR-0138: Views stay primary; findings never pick a winner

- Status: Accepted
- Date: 2026-10-11
- Spec: `docs/architecture/specs/model-relations.md` (M25)

## Context

Owner decisions for M25 (#203): views stay primary (people and the agent edit diagrams, the model is reconciled with
them, views are never generated from the model), and findings never pick a winner automatically: a person resolves them
through a proposal, by marking the relation hidden in that view, or by fixing the model (#207).

## Decision

- The model never overwrites what a view draws. Relations have no name; a relation's properties are not pushed into
  linked edges or messages. What propagates from a relation change is only link validity: links that no longer fit a
  relation's kind or ends are cleared (one version per diagram), as are links of a removed relation.
- Every correspondence finding carries `resolutions` (propose a view change, hide in this view, change, create or
  remove the relation, link, sync). The review stores and applies nothing; the panel runs the chosen resolution through
  the ordinary routes: a proposal is a propose-mode turn the person confirms (ADR-0026, ADR-0060).
- A property contradiction offers one model change per value seen and a proposal per view, never a default.

## Consequences

- Contradictions stay visible until somebody decides; `sync` repairs only drift (links, names), never content.
- Element name propagation (ADR-0071) is unchanged.
