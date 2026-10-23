# LLM architecture review with findings linked to elements

- Status: Implemented (milestone M12)
- Issue: #96
- ADRs: [0023 One finding schema for rule checks and the LLM review](../adr/0023-one-finding-schema.md),
  [0024 The LLM review is one validated tool call, neither streamed nor stored](../adr/0024-llm-review-as-one-validated-tool-call.md),
  [0025 Review panel highlights findings on the rendered SVG](../adr/0025-review-highlights-on-the-rendered-svg.md)
- Builds on: [Rule-based architecture checks](rule-checks.md) (#95)

## Summary

The rule checks (#95) find known weaknesses but cannot judge a design as a whole. This change adds
an on-demand review of the current diagram by the LLM: a short overall assessment plus findings
that say what is already good, what the risks are and what could be improved, in the same
`Finding` schema as the rule checks (category, severity, message, node and edge ids, optional
suggestion). The model gets the diagram and the rule findings and returns its findings through one
tool call that the server validates finding by finding; ids the model invents are dropped, and an
unusable answer degrades to the rule findings plus an error message. A review panel next to the
canvas lists the findings grouped by category, and selecting one highlights its elements and edges
on the canvas.

## Goals

- `POST /api/diagrams/{id}/review` returns the rule findings and the LLM findings of the current
  diagram in one sorted list, the model's summary and the outcome of the model call.
- Structured model output: one call offering the single tool `submit_review` with a JSON schema;
  the arguments are validated, never trusted. No free-text parsing beyond a JSON object as fallback.
- Invalid or partial model output never fails the request: invalid findings are dropped and
  counted; a refusal, truncation, provider error or missing tool call yields `llm.ok = false` with
  the reason, and the rule findings are still returned.
- Testable with `ScriptedProvider`: no network or API key in tests.
- UI: a **Review** button opens a panel with **Regeln prüfen** (rule checks only, instant) and
  **KI-Review** (rules plus LLM). Findings are grouped (*Risiken*, *Verbesserungen*, *Stärken*), show
  severity, message and the names of the elements; selecting a finding highlights its nodes and
  edges on the canvas; the highlight survives re-renders while the finding is selected.
- Works for every diagram type: the prompt names the type, and a type without rules still gets an
  LLM review.

## Non-goals

- Reviewing images or anything but Arcviz diagrams (decided for the milestone).
- Persisting reviews, review history, or comparing reviews over time.
- Streaming the review text; the call returns one JSON answer.
- Reviewing past versions; the review is of the current state.
- Applying a suggestion; that is #97 ([Applying a review suggestion](review-suggestions.md)).

## Design

### LLM layer: `LLMProvider.complete_tool`

A second plain completion next to `complete_text`, in `arcviz/llm/base.py`:

```python
def complete_tool(self, system: str, user_text: str, tool: ToolSpec) -> tuple[dict[str, Any], dict[str, int]]:
    """One model call offering only `tool`; returns the arguments of the model's call to it and the usage."""
```

It calls `_complete(system, [user message], [tool])`. Refusal and `max_tokens` raise `RuntimeError`
like `complete_text`. If the reply contains a call of `tool.name`, its arguments are returned. If
not, the reply text is tried as a JSON object (optionally inside a ```` ```json ```` fence) as a
fallback for providers that answer in text; anything else raises `RuntimeError("The model did not
call submit_review.")`. No provider changes: tools are already part of every `_complete` call, and
the tool is not forced (`tool_choice` is not in the provider hooks); the prompt asks for exactly one
call.

### Module `arcviz/review.py`

Imports `model`, `llm` (neutral types only), `rules`. This makes it the second module after the
agent that sees both diagrams and the LLM layer; the module table and its rule are updated.

```python
REVIEW_TOOL: ToolSpec           # "submit_review"
REVIEW_SYSTEM_PROMPT: str
REVIEW_CONTEXT_BUDGET = 100_000  # characters of Diagram.summary() in the prompt
MAX_LLM_FINDINGS = 20
MESSAGE_LIMIT = 600             # characters per message / suggestion
SUMMARY_LIMIT = 1_500

def review_input(diagram: Diagram, rule_findings: list[Finding]) -> str
def parse_review(data: Any, diagram: Diagram) -> tuple[str | None, list[Finding], int]  # summary, findings, dropped
def review(provider: LLMProvider, diagram: Diagram) -> dict[str, Any]
```

`REVIEW_TOOL.parameters`:

```json
{
  "type": "object",
  "properties": {
    "summary": {"type": "string", "description": "Overall assessment in two or three German sentences."},
    "findings": {
      "type": "array", "maxItems": 20,
      "items": {
        "type": "object",
        "properties": {
          "category": {"type": "string", "enum": ["strength", "risk", "suggestion"]},
          "severity": {"type": "string", "enum": ["info", "low", "medium", "high"]},
          "message": {"type": "string"},
          "elements": {"type": "array", "items": {"type": "string"}},
          "edges": {"type": "array", "items": {"type": "string"}},
          "suggestion": {"type": "string"}
        },
        "required": ["category", "severity", "message", "elements"],
        "additionalProperties": false
      }
    }
  },
  "required": ["summary", "findings"],
  "additionalProperties": false
}
```

The system prompt tells the model it reviews a software architecture diagram of the given type,
to cover strengths, risks and improvement suggestions, not to repeat the rule findings it is
given, to reference elements by the ids in square brackets and edges by their `eN` ids, to write
`message` and `suggestion` in German, to phrase `suggestion` as an instruction to the diagram
assistant that changes the diagram (or omit it), and to call `submit_review` exactly once.
`review_input` contains the diagram type, `diagram.summary(budget=REVIEW_CONTEXT_BUDGET)` and the
rule findings as `- [severity] message (elements: …)` lines (or "none"). The review offers no
`get_diagram`, so a diagram whose full summary exceeds the budget (`is_partial`) is shown collapsed
with `PARTIAL_NOTE` telling the model not to judge the hidden contents, and the result reports
`llm.partial = true` (shown in the panel's status line).

`parse_review` validates:

- `data` must be an object with a list `findings`; otherwise `ValueError` (the call counts as failed).
- `summary`: a non-empty string, stripped and cut to `SUMMARY_LIMIT`; else `None`.
- Per finding (anything invalid drops the finding and increments `dropped`): an object; `category`
  one of the three; `message` a non-empty string (cut to `MESSAGE_LIMIT`); `severity` one of the four,
  where a strength is always `info` and `info` on a risk or suggestion becomes `low`.
- `elements`: each entry resolved with `Diagram.find_node` (ids, names or slugs) to a node id;
  unresolved entries are dropped, duplicates removed, order kept. `edges`: entries that are edge
  ids of the diagram; others dropped. A finding may end up with no elements (a remark about the
  diagram as a whole).
- `suggestion`: a non-empty string (cut to `MESSAGE_LIMIT`) for risks and suggestions, `None` for
  strengths.
- At most `MAX_LLM_FINDINGS` findings are kept (the rest count as dropped); ids are `llm-1`, `llm-2`, …
  in the model's order; `source = "llm"`, `rule = None`.

`review(provider, diagram)` runs `rules.run_checks`, then `provider.complete_tool(...)` and
`parse_review`, and returns

```json
{
  "diagram_type": "deployment",
  "summary": "…",
  "findings": ["…rule and llm findings, sorted with rules.sort_key…"],
  "llm": {"ok": true, "error": null, "dropped": 0, "usage": {"input_tokens": 0, "output_tokens": 0}, "partial": false}
}
```

Any exception from the provider or from `parse_review` is caught: `llm.ok = false`,
`llm.error = "<ExceptionType>: <message>"`, `summary = null`, the findings are the rule findings.

### Endpoint

`POST /api/diagrams/{id}/review` (no body) → 200 with the object above. 404 unknown diagram; 503 when
the provider cannot be created (same as the chat). A failed model call is a 200 with `llm.ok = false`
(the rule findings are still useful). Nothing is written. The endpoint is a plain `def`, so FastAPI
runs the blocking model call in its thread pool.

### Frontend

New pure module `arcviz/static/findings.js` (layer pure, tested in `tests/js/findings.test.mjs`):

- `CATEGORY_LABELS` (`risk` → *Risiken*, `suggestion` → *Verbesserungen*, `strength` → *Stärken*),
  `SEVERITY_LABELS` (`high` → *hoch*, `medium` → *mittel*, `low` → *niedrig*, `info` → *Info*).
- `groupFindings(findings)` → `[{category, label, findings}]` in the order risk, suggestion,
  strength, empty groups left out, order inside a group kept.
- `elementNames(finding, nodes)` → names of the finding's existing nodes (unknown ids skipped).
- `edgeSvgKeys(edgeIds, edges, mermaidIds, visible = null)` → the Mermaid edge ids
  `L_<source>_<target>_<n>` of the given edges. Mermaid 11 numbers the edges between the same two
  nodes in drawing order `0, 2, 3, …` (the k-th earlier edge gives `k + 1`; ADR-0025). Only drawn
  edges count: both endpoints are in `mermaidIds` and, with a `visible` set (the focused subtree), at
  least one endpoint is visible, exactly as `mermaid.render` emits edge lines.
- `diagramKey(diagram)` → a string that changes when nodes, edges, title or direction change
  (timestamps excluded), used to notice a stale review.
- `mergeFindings(ruleFindings, llmFindings)` → both lists, stably sorted by severity, then category.

`format.js` gains `reviewMeta(result)`: the status line under the summary (*n Befunde · KI-Review
fehlgeschlagen: … · m ungültige KI-Befunde verworfen · Tokens*).

New DOM module `arcviz/static/review.js` (layer features), `initReview()`:

- `#btn-review` in the canvas toolbar toggles the panel `aside#review` (an overlay on the left of the
  stage, like the inspector on the right). Opening it runs the rule checks.
- **Regeln prüfen** (`#review-run-checks`) → `GET /checks`; **KI-Review** (`#review-run-llm`) →
  `POST /review` (button shows *KI prüft …* meanwhile and stays disabled until its own request ends,
  even when another operation calls `setBusy(false)`; one model call at a time). Errors appear in `#review-status`.
- The list `#review-list` renders `groupFindings`; each finding is a `li.finding` with
  `data-finding=<id>`, a severity badge `.sev-<severity>`, the message, the element names and, for
  LLM findings, the mark *KI*. Clicking a finding selects it (again: deselects).
- Highlight: for the selected finding the module adds the class `review-hit` to `g.node`/`g.cluster`
  elements whose id maps to one of its nodes (`nodeIdForSvgId`, as clicks do) and to edge paths whose
  id ends with one of `edgeSvgKeys(...)`. `canvas.js` gains a hook `onRendered(fn)` (runs after each
  successful render) so the highlight is re-applied after live renders, focus changes and theme
  changes. A selected finding whose elements are not in the current view highlights nothing.
- Staleness: `onDiagramShown` while the panel is open compares `diagramKey`; on a change it re-runs
  the rule checks and keeps the LLM findings marked `.stale` with the note *Diagramm geändert –
  KI-Review erneut ausführen*. `onDiagramSwitched` clears the panel.
- `setBusy` disables the panel's buttons like every other control during a turn.

### Error handling, concurrency

The review reads one diagram snapshot; a change while the model runs is noticed by the staleness
check in the UI. The provider object is shared with the chat; providers are already used from
the thread pool concurrently (the SDK clients are thread-safe).

## Testing

- `tests/test_review.py`: `complete_tool` (tool call returned, JSON text fallback incl. fence,
  no call → error, refusal/max_tokens → error, only the review tool offered); `parse_review`
  (valid findings; names resolved to ids; unknown ids and edges dropped; invalid category, empty
  message, non-object → dropped and counted; severity normalisation; strengths lose their
  suggestion; cap; non-object or missing `findings` → `ValueError`); `review()` with
  `ScriptedProvider` (rule and LLM findings merged and sorted, the prompt contains ids and rule
  findings, provider exception and invalid output → `llm.ok = false` with rule findings);
  endpoint 200, 404, 503 without provider, nothing written.
- `tests/js/findings.test.mjs`: grouping, labels, element names, edge keys with parallel edges,
  diagram key, meta line. `tests/js/modules.test.mjs`: the new modules in their layers.
- e2e: open the panel, rule finding listed, KI-Review adds the scripted LLM finding, selecting a
  finding highlights the node (and an edge) on the canvas.

## Migration / compatibility

Additive: a new endpoint, a new public method on `LLMProvider` (concrete, no provider changes), new
frontend modules and a hook in `canvas.js`. No stored data changes; no new configuration.

## Open questions

None open; decisions taken without the maintainer are *Proposed* in ADR-0024 and ADR-0025.

## Decisions

- One tool call validated finding by finding; failure degrades to rule findings (ADR-0024, Proposed).
- The review is not streamed and not stored (ADR-0024, Proposed).
- The LLM sees the rule findings and is asked not to repeat them; both lists are returned
  together (Proposed).
- Highlighting is done in the browser on the rendered SVG, not by a server re-render (ADR-0025,
  Proposed).
- Review texts are German (ADR-0023, Proposed).
