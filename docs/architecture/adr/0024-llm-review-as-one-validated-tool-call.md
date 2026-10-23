# ADR-0024: The LLM review is one validated tool call, neither streamed nor stored

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/llm-review.md`

## Context

Issue #96 asks for an on-demand LLM review of the current diagram with structured findings that
reference elements. The provider layer offers the tool loop (`run_turn`) and one plain text call
(`complete_text`); its hooks have no `tool_choice` or response-format parameter. Model output can
be malformed, reference ids that do not exist, or be refused. Tests must run with
`ScriptedProvider`, without network.

## Options

1. **One model call offering a single tool `submit_review`** whose JSON schema is the finding
   shape; the arguments are validated finding by finding on the server, with a JSON object in the
   reply text accepted as a fallback. Works with both providers without touching their hooks.
2. **Free-text answer with a JSON block** parsed from the text. No tool needed, but models wrap or
   comment JSON unpredictably and there is no schema the provider can enforce.
3. **A review agent turn through `run_turn`** with read tools (`get_diagram`) and a submit tool.
   Lets the model inspect collapsed containers, but costs several calls, needs a streaming UI and a
   transcript decision for little gain on diagrams that fit the prompt.
4. **Force the tool with `tool_choice`.** More reliable, but needs a new parameter in every
   provider hook; can be added later without changing this design.

## Decision

Option 1: `LLMProvider.complete_tool(system, user_text, tool)` plus strict validation in
`arcviz/review.py`, because it gives structured output with the existing provider hooks and
degrades gracefully. The review is a plain JSON request/response (not SSE) and is not persisted.

## Consequences

- An invalid answer never fails the request: invalid findings are dropped and counted, a failed call
  returns the rule findings with `llm.ok = false` and the reason.
- `arcviz/review.py` is a second module that sees diagrams and the LLM layer (the architecture
  overview's rule is amended).
- The UI shows a spinner instead of streamed text; reviews of huge diagrams see a collapsed summary
  (`REVIEW_CONTEXT_BUDGET`).
- Nothing records that a review happened; the chat model does not know about it unless a
  suggestion is applied (#97).
- Follow-ups if needed: `tool_choice` support in the providers, storing reviews.
