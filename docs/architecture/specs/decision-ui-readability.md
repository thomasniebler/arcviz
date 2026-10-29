# Readability of the Architektur editors

- Status: Implemented
- Issues: #14
- ADRs: none (a presentation change inside [ADR-0196](../adr/0196-master-detail-editor-and-component-layer.md))
- Builds on: [Architecture workspace](architecture-workspace.md) (card lists with one editor beside them)

## Summary

The decision, stakeholder, scenario and driver editors are hard to read when the texts are long: fixed-height text areas
that scroll inside the card, 12 px muted labels, one long form without structure, a list that scrolls away with the page,
option cards squeezed into a narrow grid. This change improves the shared components, so every two-pane list profits.

## Goals

- Long text is readable without scrolling inside a field.
- A form has visible structure; a long list stays usable next to a long editor.
- A decision can be read as the ADR it exports.

## Non-goals

- A new frontend framework or build step; changes to data or API.
- Restyling skins (they follow the tokens).

## Design

- **Auto-growing text areas** (CSS `field-sizing: content`, capped at 60 vh; `mdpane.initAutosize(root)` sets the height
  from `readability.fitHeight` where the CSS property is unsupported): the field grows to its content, then scrolls.
- **Reading typography** (`components.css`): labels 13 px in the text colour (not muted), `line-height: 1.55`, the editor's
  text measure limited to 80 ch with the form still filling the column, `overflow-wrap: anywhere`, more space between fields.
- **Structure**: the decision editor groups its fields in `fieldset.md-group` with a legend (Kontext · Optionen ·
  Entscheidung · Testbarkeit), the title has a row of its own, and the action bar is sticky so Speichern stays visible in a
  long form. The other editors (stakeholder, scenario, driver, vision) get the typography, the growing areas and the
  sticky list and bar from the shared classes; they have no groups yet.
- **List**: the left column is `position: sticky` with its own scroll; a card shows the title (up to three lines) and a
  two-line snippet of the main text (`readability.snippet` in `meta`), so long titles no longer break the layout.
- **Layout class**: the two-pane grid was called `.md`, like the container of rendered Markdown (`chatlog.setRichText`), so
  every assistant reply was laid out as that grid (a 355 px and a 0 px column). The grid is now `.md-layout`.
- **Options**: one column, each option a block with Pro and Contra side by side, the chosen one marked.
- **ADR preview**: a collapsible **Vorschau** in the decision editor renders the exported Markdown ADR with the
  chat's sanitising Markdown renderer (`chatlog.setRichText`), so what is exported is what is read.

## Testing

JS tests for `autosize` (sizes from scrollHeight, caps) and the snippet helper; the module-structure test; the e2e
smoke test opens a decision with long text and checks no inner scrollbar below the cap and the preview.

## Migration / compatibility

None.

## Open questions

None.

## Decisions

Improve the shared components instead of per-panel fixes.
