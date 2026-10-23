# Applying a review suggestion via propose mode

- Status: Implemented (milestone M12)
- Issue: #97
- ADR: [0026 A review suggestion becomes an ordinary propose-mode chat turn composed in the browser](../adr/0026-suggestion-as-propose-turn-composed-in-the-browser.md)
- Builds on: [Rule-based architecture checks](rule-checks.md) (#95), [LLM architecture review](llm-review.md) (#96),
  [Propose-then-confirm mode](propose-confirm.md) (M7, ADR-0015)

## Summary

Risk and suggestion findings of the architecture review carry a `suggestion`: an instruction, in
German, that names the elements by id (for example *"Füge für die Datenbank „orders-db“ (id
orders-db) eine Replik hinzu und verbinde beide mit einer Kante der Art replication."*). This
change adds a button **Als Vorschlag umsetzen** to such findings. It sends the suggestion, with the
finding as context, as a chat turn in **propose mode**, regardless of the *Vorschau* checkbox: the
model turns it into operations on an in-memory copy, the canvas and the reply bubble show the
proposed change as a highlighted diff, and the user confirms (**Übernehmen**) or discards
(**Verwerfen**) it exactly as for any other proposal. Nothing about propose mode changes.

## Goals

- One click from a finding to a previewed, unsaved proposal; the diagram changes only after
  **Übernehmen**, as one version.
- Reuse the existing chat endpoint and propose mode unchanged: no new endpoint, no new server state,
  all mutations through `operations.apply` (via the agent and `proposal.apply_proposal`).
- The chat transcript shows what was asked (the composed instruction), so the model and the user
  see later turns in context.
- After confirming, the open review panel re-runs the rule checks (already the case when the
  diagram changes, #96), so a fixed rule finding disappears.

## Non-goals

- Applying several suggestions at once, or applying without preview.
- Deterministic fixes without the LLM (a rule could carry operations); the model decides how to
  realise the suggestion, so the same flow serves rule and LLM findings.
- Changing propose mode (partial confirmation, editing a proposal).

## Design

### Instruction: `suggestionPrompt(finding, nodes)` (`arcviz/static/findings.js`, pure)

Returns `null` for a finding without `suggestion`, else

```text
Setze diesen Vorschlag aus dem Architektur-Review um: <suggestion>
Befund (<category label singular>, <severity label>): <message>
Betroffene Elemente: <name> (<id>), … · Betroffene Kanten: e3, …
```

The category labels are *Risiko* and *Verbesserung*; the element line lists the finding's nodes that
exist in `nodes` as `name (id)`; the edge part is added when the finding has edges; lines without
content are left out. The text is what the user would otherwise type; the agent's system prompt
and tools are unchanged.

### Chat: `sendMessage(text, { propose })` (`arcviz/static/chat.js`)

`sendMessage` is exported and takes an option `propose` (default: the *Vorschau* checkbox). The rest
of the turn, the proposal handling and the lock are unchanged.

### Review panel (`arcviz/static/review.js`)

- `initReview({ onApply })`: `app.js` passes `onApply: (text) => sendMessage(text, { propose: true })`
  (callback injection like `initCanvas({ onClick })`, because a feature module must not import
  `chat.js`).
- Every finding with a `suggestion` gets a button `.finding-apply` **Als Vorschlag umsetzen** (title:
  the suggestion). Clicking it (not selecting the finding) does nothing while `state.busy`; otherwise
  it calls `onApply(suggestionPrompt(finding, state.nodes))`.
- The button is disabled while the UI is busy (`setBusy`, which also covers a pending proposal) and
  for a stale LLM finding (the diagram changed since the review; its ids may be outdated). The
  title then says *Diagramm geändert – KI-Review erneut ausführen*.

### Server

No change. The turn is a normal `POST /api/diagrams/{id}/chat` with `mode: "propose"`; confirming
is `POST …/proposal/apply`. The transcript records the composed instruction as the user entry and
the proposal outcome as before.

### Error handling, concurrency

As for any propose turn: an error ends the turn without a proposal; a diagram changed between the
proposal and the confirmation gives 409 and nothing is written.

## Testing

- `tests/js/findings.test.mjs`: `suggestionPrompt` (rule and LLM finding, unknown ids skipped, edges,
  no suggestion → `null`, strength → `null`).
- `tests/test_review.py`: the round trip through the API with `ScriptedProvider`: a rule finding's
  suggestion sent as a propose-mode turn reaches the model verbatim, nothing is written before the
  confirmation, confirming creates one version, and the rule check no longer reports the finding.
- e2e: **Als Vorschlag umsetzen** on the database finding starts a propose turn (diff listed,
  banner, UI locked, nothing saved), **Übernehmen** saves one version, the open panel then shows
  the database as a strength and marks the LLM findings as outdated.

## Migration / compatibility

None: frontend only, `sendMessage` keeps its behaviour for the composer.

## Open questions

None open; decisions taken without the maintainer are *Proposed* in ADR-0026.

## Decisions

- A suggestion is applied as a propose-mode chat turn, never directly (ADR-0026, Proposed).
- The instruction is composed in the browser from the finding; no new endpoint (ADR-0026, Proposed).
- Applying is always a preview, independent of the *Vorschau* checkbox (Proposed).
- Stale LLM findings cannot be applied until the review is run again (Proposed).
