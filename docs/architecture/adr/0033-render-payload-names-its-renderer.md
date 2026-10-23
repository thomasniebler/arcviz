# ADR-0033: Render payload names its renderer

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/diagram-types.md`

## Context

Responses and SSE events carry the rendering under the key `mermaid` (detail, `/ops`, versions,
comparison, chat `tool_result` and `final`). Sequence diagrams are rendered as SVG (ADR-0032). The
frontend must know which engine to use; existing clients, the e2e test and the parallel milestone M12
read `mermaid`.

## Options

1. **Rename to a neutral key** (`render: {renderer, source}`) everywhere. Clean, but a breaking API
   change for every client and every test.
2. **Keep `mermaid` and put SVG in it for sequence diagrams.** No new key, but the name lies.
3. **Add `renderer` and a key per format**: `renderer` is `"mermaid"` or `"svg"`, `mermaid` holds
   Mermaid source or `null`, `svg` holds the SVG document or `null`.

## Decision

Option 3. Deployment responses are unchanged apart from two added keys, the name of each key says
what it holds, and a further format adds one key.

## Consequences

- `render.payload()` builds the three keys in one place; the server and the agent spread it into
  their responses.
- The frontend reads the rendering through one helper (`viewOf` in `types.js`).
- `GET /api/diagrams/{id}/render` returns the payload; `GET …/mermaid` stays for Mermaid types.
