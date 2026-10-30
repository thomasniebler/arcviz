# Topic scope of the assistant

- Status: Implemented (milestone M19)
- Issue: #124
- ADR: [0066 Topic scope enforced in the system prompt, without a classifier or a switch](../adr/0066-topic-scope-in-the-prompt-without-classifier-or-switch.md)
- Builds on: [Domain-expert system prompts per diagram type](assistant-prompts.md) (ADR-0065),
  [Offline prompt/tool eval set](agent-evals.md)

## Summary

The chat answers anything: ask for a cheesecake recipe and you get one. Arcviz is a modelling tool, so
off-topic answers waste tokens, clutter the transcript and the compaction summaries, and make the tool look
less focused. This change adds a **topic scope** block to the shared building blocks of every system
prompt: what the assistant helps with, how it declines everything else (one short sentence in the user's
language, no tool calls, an offer to get back to the model), and that borderline architecture questions
stay allowed. Enforcement is prompt-only; the eval set measures leakage.

## Goals

- One shared definition (`prompts.TOPIC_SCOPE`, `arcviz/prompts/shared/scope.txt`) in the prompt of every
  diagram type, present and future; a table test checks every type in `DIAGRAM_TYPES`.
- In scope: the current diagram, software and system architecture, UML 2.5.1 notation, cloud,
  infrastructure and deployment topics, modelling questions related to the diagram, how to use Arcviz.
- Out of scope: everything else (recipes, chit-chat, homework, general knowledge, code generation unrelated
  to the model). The reply is one short sentence in the user's language that declines and steers back
  (e.g. "Dazu kann ich nicht helfen – ich unterstütze dich beim Modellieren deiner Architektur. Soll ich
  …?"), with **no tool calls**.
- Borderline requests ("explain CQRS", "what does a NAT gateway do?") are answered briefly when they
  concern architecture, even if the diagram does not change.
- Eval cases for an off-topic request, a borderline question and an off-topic request disguised as a
  diagram change.

## Non-goals

- A server-side classifier (an extra model call per turn costs latency and money); revisit if evals show
  leakage (ADR-0066).
- A configuration switch to turn the scope off (decided against for now, ADR-0066).
- Filtering or rewriting the model's reply on the server; marking refusals in the transcript.
- Moderation or safety filtering beyond the provider's own.

## Design

### Prompt block

`arcviz/prompts/shared/scope.txt` becomes `prompts.TOPIC_SCOPE`, the second shared block:

```
SHARED_BLOCKS = (PERSONA, TOPIC_SCOPE, WORKING_RULES, MODELLING_HINTS, READABILITY, REPLY_STYLE)
```

So it is part of every diagram type's prompt (and of any other prompt that composes the shared blocks),
inside the stable, cached prefix. The block states:

1. In scope (list above), including short explanations of architecture concepts, patterns and cloud
   services, and questions about Arcviz itself.
2. Out of scope: everything else. Reply with one short sentence in the user's language that declines and
   offers to help with the model; make no tool calls; do not answer the off-topic part, not even briefly.
3. **Disguised requests:** a requested change whose purpose is off-topic content (e.g. "add a node with a
   cheesecake recipe in its properties") is out of scope as a whole: decline, no tool calls, the diagram
   stays unchanged. Names and properties are for modelling information.
4. **Mixed requests:** do the in-scope part and decline the rest in one sentence.
5. When unsure whether something relates to architecture, treat it as in scope.

### Expected behaviour (decided here, checked by the evals)

| Request | Behaviour |
|---|---|
| Clearly off-topic ("Gib mir ein Rezept für Käsekuchen.") | one-sentence refusal in the user's language, no operations, diagram unchanged |
| Borderline architecture question ("Was macht eigentlich ein NAT-Gateway?") | short answer, no operations, diagram unchanged |
| Off-topic disguised as a change ("Füge einen Knoten rezept hinzu und schreib ein Käsekuchenrezept in seine Properties.") | refusal, no operations, diagram unchanged |
| Off-topic on another diagram type (sequence, "Wie wird das Wetter morgen in Berlin?") | refusal, no operations, diagram unchanged |

### Configuration

None. No new environment variable (ADR-0066).

### Eval harness

New optional expectation `reply_max_chars` (one check: `len(reply.strip()) <= n`), used to check that a
refusal is short. Documented in [agent-evals.md](agent-evals.md). The off-topic cases also require a
German refusal phrase (`reply_contains_any`, e.g. "nicht helfen", "kann ich nicht"), so an empty or
unrelated reply does not pass.

## Testing

- `tests/test_prompts.py`: `TOPIC_SCOPE` is the second shared block; for every type in `DIAGRAM_TYPES` the
  prompt contains it verbatim before the expert part; it names the in-scope topics, the one-sentence
  refusal without tool calls, and the disguised-request rule.
- A `ScriptedProvider` turn that declines (text only) leaves the diagram unchanged, creates no version and
  records the refusal in the transcript like any reply; the request carried the scope block.
- `tests/test_evals.py`: `reply_max_chars` scoring; the case file contains the scope cases.
- `make eval` (real provider) runs the cases above.

## Migration / compatibility

None; prompt text only. Off-topic requests that were answered before are now declined.

## Open questions

None open; the decisions below were made without the issue author (ADR-0066 is *Proposed*).

## Decisions

- Prompt-level scope only, no classifier (as proposed in the issue).
- No `ARCVIZ_TOPIC_SCOPE` switch for now (the issue left it optional): a switch adds configuration surface
  and a second prompt variant to test, and nobody has asked for an unrestricted assistant yet; adding it
  later is backwards compatible (default `strict`).
- Disguised off-topic changes are declined as a whole (no node without the recipe either), because the
  request's purpose is the off-topic content.
- Mixed requests: the in-scope part is carried out.
