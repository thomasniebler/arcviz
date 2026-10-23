# ADR-0066: Topic scope enforced in the system prompt, without a classifier or a switch

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/topic-scope.md`

## Context

The diagram chat answers any request, including recipes and chit-chat (#124). Every turn already sends a
system prompt composed from shared blocks (ADR-0065), cached as a stable prefix by the Anthropic provider.
A turn costs one model call per round; there is no per-turn pre-processing. The issue proposes prompt-level
scoping, rejects a classifier for now and leaves a strictness switch (`ARCVIZ_TOPIC_SCOPE=strict|off`)
open.

## Options

1. **Scope block in the shared prompt blocks, no switch.** One text, part of every type's cached prefix;
   no extra latency or cost; leakage is measured by eval cases. Deployments cannot turn it off without a
   code change.
2. **Option 1 plus `ARCVIZ_TOPIC_SCOPE=strict|off`.** Operators can run an unrestricted assistant. Adds
   an environment variable, a second prompt variant (and its tests and evals) and a configuration path
   into the agent, for a need nobody has stated yet.
3. **A classifier call before each turn.** Enforces the scope independently of the main prompt, but adds
   a model call (latency and tokens) to every turn and a second failure mode.

## Decision

Option 1: the scope is one shared prompt block (`prompts.TOPIC_SCOPE`) without a classifier and without a
switch; the eval set (off-topic, borderline, disguised, other type) shows whether it holds.

## Consequences

- Off-topic requests get a one-sentence refusal without tool calls; architecture questions stay allowed.
- An off-topic request disguised as a change is declined as a whole; the diagram stays unchanged.
- Adding the switch later stays backwards compatible (default `strict`); a classifier is the next step
  only if the evals show leakage.
- Status *Proposed*: decided without the issue author while implementing M19.
