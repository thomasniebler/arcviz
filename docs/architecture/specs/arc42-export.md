# arc42 export

- Status: Accepted (design agreed, not implemented)
- Issue: none yet
- ADRs: [0206 arc42 is assembled deterministically and rendered on the server](../adr/0206-arc42-assembled-deterministically-and-rendered-on-the-server.md),
  [0207 Prose for the sections the model does not cover](../adr/0207-arc42-prose-for-unmodelled-sections.md)
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
- Editing arc42 text on the page (only the notes of ADR-0207 in the existing dialog).

## Design

### Section tree (`arcviz/arc42.py`, pure, no I/O)

`build(snapshot) -> Document`: a list of 12 `Section(id, title, blocks, gaps)` where `blocks` are `Markdown(text)`,
`Table(headers, rows)`, `Figure(diagram_id, caption)`. Pure over a `ProjectSnapshot` plus the architecture objects, so it is
unit-testable without a store. Mapping:

| § | Source |
|---|---|
| 1 Introduction and goals | `Project.title/description`, **vision** (statement, goals), top quality goals (drivers of kind `quality_goal` by priority), stakeholders table |
| 2 Constraints | drivers of kind `constraint`; requirements of kind constraint |
| 3 Context and scope | note (ADR-0207) + diagrams in sections titled like a context/overview (`project_review` already finds them by title) |
| 4 Solution strategy | accepted decisions' summary lines + the note |
| 5 Building block view | element tree with kinds and descriptions; component and deployment-structure figures; relations table |
| 6 Runtime view | sequence diagrams (figures) with their notes; core scenarios |
| 7 Deployment view | deployment diagrams; `deployed_on` relations as a table |
| 8 Crosscutting concepts | note (ADR-0207); accepted decisions tagged crosscutting are listed (follow-up: tags) |
| 9 Architecture decisions | `decision_model.decision_markdown` per decision (accepted first, superseded marked) |
| 10 Quality requirements | quality tree from drivers, scenarios table (ATAM parts, importance × risk) |
| 11 Risks and technical debt | risk drivers, open review findings of high/medium severity (deterministic rules only; no LLM call) |
| 12 Glossary | element names with descriptions, alphabetically; note |

Retired drivers/scenarios are excluded (`project_model.active`), rejected/draft decisions marked or left out per §9 rules in the
module docstring. A section whose source is empty carries a `gap` (`"arcviz: kein Inhalt"` text on the page, a comment in
Markdown/AsciiDoc), never filler text. Ordering is deterministic so two builds of one project state are byte-identical.

### Service (`services/arc42.py`, `svc.arc42`)

`Arc42Service.document(project_id) -> Document` takes the **viewer** guard, loads the snapshot once (members and
architecture objects, via the same bounded reads the review uses; no per-request store listing) and calls `arc42.build`.
Size is bounded by the project's own bounds; the writers stream. A new service method calls `self.require(...)` before it reads
(CLAUDE.md), and its routes are in `tests/test_access_matrix.py` and the list of service methods in `tests/test_access.py`.

### Routes

| Route | Answer |
|---|---|
| `GET /project/{id}/arc42` | HTML page (full document, anchors per section), the live view |
| `GET /project/{id}/arc42/{section}` | one section (`01`…`12`), same page chrome |
| `GET /api/projects/{id}/arc42` | JSON: `{sections: [{id, title, markdown, gaps}], figures: […]}` |
| `GET /api/projects/{id}/arc42.md` | Markdown, figures as relative `figures/<diagram>.svg` references |
| `GET /api/projects/{id}/arc42.zip?format=asciidoc\|markdown` | zip: `index.adoc` or `index.md`, one file per section, `figures/` |
| `GET /api/projects/{id}/arc42.docx` | docx (extra `docx`); 501 with the install hint if the extra is missing |

HTML pages are not under `/api`, so they answer 401/404 like the SPA's pages do under proxy auth (`auth.AuthMiddleware`
treats them like any non-public path; token mode redirects to `/login`). Ids are checked with `check_id`; an unknown or
unreadable project is 404 with the store's message (existing guard behaviour).

### Rendering (ADR-0206)

The section's Markdown goes through one renderer in `arcviz/arc42_html.py` using **markdown-it-py** with `html=False`
(raw HTML in model text is escaped, never passed), `linkify` off, links limited to `http(s)`, `mailto` and in-page anchors, and
tables enabled. The page template is a small Jinja-free string template with the app's own CSS tokens (`static/arc42.css`) and,
for figures, the vendored Mermaid (`static/vendor/mermaid.min.js`, no CDN) for `renderer: mermaid` diagrams and the inline
sanitised SVG for the SVG renderers (`svgsafe` rules mirrored server-side by `render` output being escaped at the source). Every
user or model text reaches the page through the Markdown renderer or `escape()`; `tests/test_arc42_html.py` renders hostile names
and descriptions and asserts that no tag or attribute survives. A strict `Content-Security-Policy` (no inline script except the
vendored Mermaid initialiser, loaded as a file) is set on these responses.

### Writers (`arc42_export.py`)

- **Markdown/zip:** the section Markdown, figures as `.svg` for SVG renderers and as ```` ```mermaid ```` blocks for
  Mermaid renderers (so Zensical/GitHub/MkDocs render them without conversion).
- **AsciiDoc:** a small writer over the same blocks (`= `, `|===` tables, `[source,mermaid]` blocks, `image::` for SVG);
  escaping through one helper (like `plantuml_text`), property-tested for round-safe text.
- **docx:** `python-docx` behind the `docx` extra, lazy import with a clear error; headings, tables, text; Mermaid
  figures as their source in a code style (open question 2).

Dependencies (CLAUDE.md rule 5): `markdown-it-py` (pure Python; required by the page, no further runtime dependencies besides
`mdurl`) is a **core** dependency, because the page is the main surface; `python-docx` is the extra `docx`, installed in the image
via `ARCVIZ_EXTRAS` like the others. The reasons go into the PR body.

### Completeness and the steward

A deterministic project rule `arc42-section-empty` (low suggestion, one finding per empty §3, §4, §8, §12 with a note-able
resolution `set_arc42_note`) feeds the [steward](project-steward.md), which can then **propose** a draft note (a proposal
item, verified and confirmed like any other). The export itself never calls a model.

### UI

A **arc42** entry in the project menu and the command palette (one registry entry for an existing control id, `palette.js`)
opens `/project/<id>/arc42` in a new tab; the **Architektur** dialog gets a **Dokumentation** tab with the notes editor
(ADR-0207) and the download links. The page's header offers the downloads and a "stand: <revision, time>" line.

## Testing

- `arc42.build`: golden test on a seeded project (all sections), empty project (all gaps), retired/rejected exclusions,
  determinism (two builds byte-equal), property test: any project produced by the existing strategies builds without error.
- HTML: hostile text escaping (as above), links limited, anchors stable.
- Writers: AsciiDoc and Markdown goldens; docx opens with `python-docx` and has the expected headings; missing extra → 501.
- Server: every route, viewer allowed, other user 404, `If-None-Match`/`ETag` from the project revision (304), large project.
- Access matrix and `test_access.py` entries; layering test for the new modules; i18n catalog entries.
- e2e: open the page from the menu, see §9 with a created decision; download the zip.

## Migration / compatibility

New routes and a new optional project field (ADR-0207). No data migration.

## Open questions

1. **Caching:** build per request (simple, bounded) or cache by project revision? Proposed: per request first, `ETag` from the
   revision handles repeat views.
2. **Figures in docx/AsciiDoc:** Mermaid diagrams cannot be rendered server-side without a browser. Options: ship the Mermaid
   source (proposed for v1), or add an optional headless rendering step later. Which is acceptable for your docx use?
3. **Language:** the page follows `Accept-Language` for the headings (German source text is the key, ADR-0199); user content is
   not translated. Should exports offer an explicit `?lang=`?

## Decisions

- Live page at `/project/<id>/arc42`, rendered **server-side** from Markdown (user decision; ADR-0206).
- Markdown + AsciiDoc zip first; docx behind an optional extra (user decision).
- The export never calls a model (ADR-0206).
- Open questions resolved as proposed: build per request with `ETag`, Mermaid source in docx and AsciiDoc in v1, language
  from `Accept-Language` (user: "things are fine").
