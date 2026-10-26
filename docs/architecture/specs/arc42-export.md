# arc42 export

- Status: Implemented
- Issue: #9
- ADRs: [0207 arc42 is assembled deterministically and rendered on the server](../adr/0207-arc42-assembled-deterministically-and-rendered-on-the-server.md),
  [0208 Prose for the sections the model does not cover](../adr/0208-arc42-prose-for-unmodelled-sections.md)
- Related: [Architecture documentation](architecture-documentation.md), [Project vision](project-vision.md),
  [Project steward](project-steward.md), [Decision support](decision-support.md), [Docs in app](docs-in-app.md)

## Summary

A project holds most of what an arc42 document says: goals and constraints (drivers, requirements, stakeholders),
building blocks (elements, component diagrams), runtime (sequence diagrams), deployment (deployment diagrams), decisions,
quality scenarios and risks. This change assembles it into the twelve arc42 sections and offers it as a **live page**
at `/project/<id>/arc42`, always current, plus downloads: Markdown (with figures), an **AsciiDoc zip**, and an optional **docx**.

## Goals

- A readable, shareable, always-current arc42 view of a project, with no build step and no stale copy.
- Everything derivable from the model is derived; what is missing is visible as a gap, not invented.
- The same assembly feeds page, Markdown, AsciiDoc and docx (one section tree, several writers).
- Read-only: viewers may read it; it changes nothing.

## Non-goals

- Round-tripping (importing arc42 text back into the model).
- Replacing the hand-written arc42 of Arcviz itself (`docs/architecture/arc42/`).
- Server-side rasterised diagrams (see open question 2).
- Editing arc42 text on the page (only the notes of ADR-0208 in the existing dialog).

## Design

### Section tree (`arcviz/arc42.py`, pure, no I/O)

`build(snapshot) -> Document`: a list of 12 `Section(id, title, blocks, gaps)` where `blocks` are `Markdown(text)`,
`Table(headers, rows)`, `Figure(diagram_id, caption)`. Pure over a `ProjectSnapshot` plus the architecture objects, so it is
unit-testable without a store. Mapping:

| § | Source |
|---|---|
| 1 Introduction and goals | `Project.title/description`, **vision** (statement, goals), top quality goals (drivers of kind `quality_goal` by priority), stakeholders table |
| 2 Constraints | drivers of kind `constraint`; requirements of kind constraint |
| 3 Context and scope | note (ADR-0208) + diagrams in sections titled like a context/overview (`project_review` already finds them by title) |
| 4 Solution strategy | accepted decisions' summary lines + the note |
| 5 Building block view | element tree with kinds and descriptions; component and deployment-structure figures; relations table |
| 6 Runtime view | sequence diagrams (figures) with their notes; core scenarios |
| 7 Deployment view | deployment diagrams; `deployed_on` relations as a table |
| 8 Crosscutting concepts | note (ADR-0208); accepted decisions tagged crosscutting are listed (follow-up: tags) |
| 9 Architecture decisions | `decision_model.decision_markdown` per decision (accepted first, superseded marked) |
| 10 Quality requirements | quality tree from drivers, scenarios table (ATAM parts, importance × risk) |
| 11 Risks and technical debt | risk drivers, open review findings of high/medium severity (deterministic rules only; no LLM call) |
| 12 Glossary | element names with descriptions, alphabetically; note |

Retired drivers/scenarios are excluded (`project_model.active`), rejected/draft decisions marked or left out per §9 rules in the
module docstring. A section whose source is empty carries a `gap` (`"arcviz: kein Inhalt"` text on the page, a comment in
Markdown/AsciiDoc), never filler text. Ordering is deterministic so two builds of one project state are byte-identical.

### Document assembly (no service of its own)

The routes build the document in `server.arc42_document`: the same `project_snapshot` as the review (`load_members` and the
requirements box, under the viewer guard of those service methods), the review's deterministic findings
(`project_review.run_project_checks`) and `arc42.build`. There is no `Arc42Service`: it would only repeat
what `ProjectService.load_members` and `RequirementService.box` already guard, and `tests/test_layering.py` keeps services
below `project_review`. Every route takes `svc: Svc` and is in `tests/test_access_matrix.py`. Size is bounded by the project's own
bounds and by `arc42.MAX_ROWS` per table.

### Routes

| Route | Answer |
|---|---|
| `GET /project/{id}/arc42` | HTML page (full document, anchors per section), the live view |
| `GET /project/{id}/arc42/{section}` | one section (`01`…`12`), same page chrome |
| `GET /api/projects/{id}/arc42` | JSON: per section `id`, `title`, `markdown`, `gaps`, `figures` |
| `GET /api/projects/{id}/arc42.md` | Markdown, SVG figures as `figures/<diagram>.svg` references |
| `GET /api/projects/{id}/arc42.zip?format=asciidoc\|markdown` | zip: `arc42/index.adoc` or `arc42/index.md`, `arc42/figures/` |
| `GET /api/projects/{id}/arc42.docx` | docx (extra `docx`); 501 with the install hint if the extra is missing |
| `GET /api/projects/{id}/arc42/figures/{diagram}.svg` | the SVG rendering of an SVG-drawn diagram, `Content-Security-Policy: sandbox`; 400 for a Mermaid diagram |

HTML pages are not under `/api`, so they answer like any other non-public path of `auth.AuthMiddleware` (401, or in token mode a
redirect to `/login`). An unknown or unreadable project is 404 with the store's message. Responses are never cached
(`Cache-Control: no-store`): a diagram change does not change the project revision, so there is no `ETag`.

### Rendering (ADR-0207)

The section tree holds plain text only, so the page needs no Markdown library: `arc42_html.page` writes every block with
`html.escape` (paragraph, list with depth classes, table, figure). **Deviation from the first design:** `markdown-it-py` is not
a dependency; escaping structured blocks is strictly safer than disabling raw HTML in a Markdown renderer and adds nothing the
tree does not already have. The page loads only files of the app: `static/arc42/page.css`, the vendored Mermaid and
`static/arc42/page.js` (no inline script); a Mermaid figure is `<pre class="mermaid">` with the escaped source, an SVG
figure an `<img>` of the sandboxed figure route, so a defect in a rendering can never run in the page. The response carries
`Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; base-uri 'none';
form-action 'none'; frame-ancestors 'none'` (Mermaid sets inline styles). `tests/test_arc42.py` renders hostile names,
descriptions, visions and notes and asserts that no tag or script survives.

### Writers (`arc42_export.py`)

- **Markdown/zip:** the section Markdown (`md()` escapes the special characters, also in table cells), figures as `.svg` files for
  SVG renderers and as fenced `mermaid` blocks for Mermaid renderers (the fence outgrows any run in the source).
- **AsciiDoc:** a small writer over the same blocks (`= `, `|===` tables, `[source,mermaid]` blocks, `image::` for SVG); every
  special character is written as a character reference (`adoc()`), which is safe in text and in table cells.
- **docx:** `python-docx` behind the `docx` extra, lazy import, 501 with the install hint without it; headings, tables, text;
  Mermaid figures as their source in a monospaced paragraph.

Dependencies (CLAUDE.md rule 5): none in the core. `python-docx` is the optional extra `docx` (also in `dev` and in the
image's `ARCVIZ_EXTRAS`). Notes are plain text (ADR-0208): blank lines separate paragraphs, no markup.

### Completeness and the steward

A deterministic project rule `arc42-section-empty` (low suggestion, one finding per empty §3, §4, §8, §12 with a note-able
resolution `set_arc42_note`) feeds the [steward](project-steward.md), which can then **propose** a draft note (a proposal
item, verified and confirmed like any other). The export itself never calls a model.

### UI

The **Architektur** page gets a **Dokumentation** tab (`documentationpanel.js`) with the link that opens `/project/<id>/arc42` in a
new tab, the download links and the notes editor (ADR-0208); the navigation and the command palette have an entry for it. The page's header offers the downloads and a "stand: <revision, time>" line.

## Testing

- `arc42.build`: golden test on a seeded project (all sections), empty project (all gaps), retired/rejected exclusions,
  determinism (two builds byte-equal), property test: any project produced by the existing strategies builds without error.
- HTML: hostile text escaping (as above), links limited, anchors stable.
- Writers: AsciiDoc and Markdown goldens; docx opens with `python-docx` and has the expected headings; missing extra → 501.
- Server: every route, viewer allowed, other user 404, `no-store`, the figure route (SVG 200, Mermaid 400, unknown 404).
- Access matrix and `test_access.py` entries; layering test for the new modules; i18n catalog entries.
- e2e: open the page from the menu, see §9 with a created decision; download the zip.

## Migration / compatibility

New routes and a new optional project field (ADR-0208). No data migration.

## Open questions

1. **Caching:** resolved: built per request, `no-store`; no `ETag` (a diagram change does not change the project revision).
2. **Figures in docx/AsciiDoc:** Mermaid diagrams cannot be rendered server-side without a browser. Options: ship the Mermaid
   source (proposed for v1), or add an optional headless rendering step later. Which is acceptable for your docx use?
3. **Language:** the page follows `Accept-Language` for the headings (German source text is the key, ADR-0199); user content is
   not translated. Should exports offer an explicit `?lang=`?

## Decisions

- Live page at `/project/<id>/arc42`, rendered **server-side** from Markdown (user decision; ADR-0207).
- Markdown + AsciiDoc zip first; docx behind an optional extra (user decision).
- The export never calls a model (ADR-0207).
- Open questions resolved as proposed: build per request without `ETag`, Mermaid source in docx and AsciiDoc in v1, language
  from `Accept-Language` (user: "things are fine"). The Markdown library of the first design was dropped during implementation (ADR-0207, amendment).
