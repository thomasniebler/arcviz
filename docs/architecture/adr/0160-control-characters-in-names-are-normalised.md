# ADR-0160: Control characters in names, titles and labels are normalised, never refused

- Status: Accepted
- Date: 2026-10-11
- Spec: `docs/spec/operations.md` (dispatcher), `docs/spec/rendering.md` (labels and escaping)

## Context

The operations accepted names, titles, edge labels and guards with control characters, line breaks included. A raw
line break then reached the Mermaid source inside a quoted label: a flowchart shows a break, a `sequenceDiagram`
fails to parse (renderer case study), and the system prompt summary of the diagram gets lines the model reads as
structure. The property-test generators excluded control characters, so nothing pinned the behaviour (#103).

## Options

1. **Refuse them** with an `OperationError`. The model can act on it, but a pasted name with a trailing newline or
   a tab from a spreadsheet fails for no visible reason, and data stored before stays unrenderable.
2. **Normalise them**: line breaks and tabs become a space, other control characters are dropped.
3. **Map line breaks to `<br/>`** in node labels. Only Mermaid flowcharts could show it; container titles must stay
   single-line, the sequence renderer, PlantUML and the summary would need their own mapping, and the stored text
   would still hold a character the next renderer may choke on.

## Decision

Option 2, in the operations, so the stored data, the model's view of it and the UI agree: `model.single_line` turns
a run of CR, LF, TAB, U+2028 or U+2029 into one space and drops every other C0/C1 control character and DEL.
`operations.validate_arguments` applies it to the string arguments named in `ONE_LINE_ARGUMENTS` (`name`, `title`,
`label`, `guard`, `type`, `refers_to`) of every diagram and project operation; new diagram and project titles get
it too (stripped; an interactive create refuses an empty one like a rename, an import names it "Unbenanntes
Diagramm" with a warning, so stored diagrams and bundles with a blank title stay importable). The importer applies it to every document, bundle and embedded
model (`exchange._one_line`; user `properties` maps are data and stay exactly as they are), and both renderers apply it again (`mermaid.escape_text`, `sequence_svg.escape`, whose
XML has no form for most control characters), so data stored before still renders.

## Consequences

- Never an error: a text that is empty after normalisation is refused as empty, like a blank one.
- Descriptions and notes keep their line breaks; they are not labels.
- The property-test generators draw control characters (only lone surrogates stay excluded, no JSON request carries
  them), and every state they reach has one-line names, titles and labels.
- Stored data is not migrated. Comparisons treat a stored name as its normalised form: a linked view whose name equals
  it is no rename (`update_node`, `update_lifeline`) and no drift (`element-name-drift`), and `ModelService.sync`
  normalises stored element names (one project write; a name another element already holds stays as it is).
  Element names compare one line everywhere (`project_model.name_key`: uniqueness, lookup by name, the carry's
  mapping), so a stored "Order\nService" is the same name as "Order Service".
