# ADR-0022: Rule checks as plain functions registered per diagram type

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/rule-checks.md`

## Context

Issue #95 asks for deterministic architecture checks (single points of failure, a public entry
without a gateway, unconnected elements, external systems talking directly to databases) whose
findings name severity, message and element ids, with no LLM involved. Every diagram is a
deployment diagram today; milestone M13, worked on in parallel, adds a diagram `type`
(deployment, activity, sequence), and a check that makes sense for a deployment diagram (a
database without replication) is meaningless for a sequence diagram. The model has a closed set of
built-in kinds that the checks can rely on, plus optional configured kinds.

## Options

1. **Rule functions in a Python module, registered in a dictionary keyed by diagram type.** Each
   rule is a small function `Diagram -> findings` with a stable id; `run_checks` picks the list for
   the diagram's type, falling back to `deployment` while the type field does not exist. Easy to
   test one by one, no new format, M13 only adds registrations.
2. **A declarative rule format** (JSON/YAML conditions over kinds, edges and properties) that
   operators can extend, like the kinds file. Flexible, but needs a small query language, its own
   validation and documentation, and the four rules asked for are graph conditions (paths through a
   gateway, edge directions) that a simple format expresses badly.
3. **One fixed check function with no notion of diagram type.** Smallest, but M13 would have to
   change every rule or wrap the call, which is the coupling the milestone plan asks to avoid.

## Decision

Option 1: rules are Python functions registered per diagram type, with `deployment` as the
default type, because it keeps the checks testable and lets M13 add types without touching them.

## Consequences

- Adding a rule is a code change with a test; operators cannot add their own rules (possible
  follow-up: a declarative format on top of the registry if there is demand).
- `diagram_type()` reads `diagram.type` defensively (`getattr`), so this code works before and
  after M13 merges; M13 should keep `deployment` as the type of existing diagrams.
- Rules know the built-in kinds only; elements of configured kinds are not checked by
  kind-specific rules.
