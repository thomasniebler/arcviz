# Export and import of whole projects

- Status: Implemented (milestone M16)
- Issue: #134
- ADR: [0056 A project bundle embeds the diagram documents; import always creates a new project](../adr/0056-project-bundle-embeds-diagram-documents.md)
- Builds on: [Diagram import and export](diagram-exchange.md), [Projects](projects.md)

## Summary

A diagram can be exported as a versioned JSON document and imported as a new diagram (#79). With
projects (#131) users want to move or back up a whole project: its title, description, section tree,
the order of its diagrams and every diagram. This change adds a **project bundle**: one JSON file with a
versioned envelope that holds the project structure and the diagram documents in the existing format.
Importing a bundle always creates a **new** project; its diagrams get unique ids, so nothing existing is
overwritten.

## Goals

- `GET /api/projects/{id}/export` downloads the bundle; `POST /api/projects/import` creates a new project
  from one.
- The bundle reuses the diagram document format, so every diagram inside it is validated by the same
  parser, including the per-type `schema_version` rules.
- The import validates the whole bundle before anything is written and lists every problem.
- Navigator menu entries to export the open project and to import a bundle.

## Non-goals

- Versions and chat transcripts (the diagram export leaves them out as well).
- Merging a bundle into an existing project, or updating diagrams in place.
- PlantUML or image exports of a project.

## Design

### Format

```json
{
  "format": "arcviz-project",
  "schema_version": 1,
  "exported_at": "2026-10-07T12:00:00+00:00",
  "project": {
    "id": "shop",
    "title": "Shop",
    "description": "",
    "sections": [{"id": "backend", "title": "Backend", "parent": null}],
    "diagrams": [{"diagram": "orders", "section": "backend"}]
  },
  "diagrams": [
    {"format": "arcviz-diagram", "schema_version": 1, "exported_at": "…", "diagram": {"id": "orders", "…": "…"}}
  ]
}
```

`project.diagrams` is the membership in order (without `added_at`); every entry names a diagram
document in `diagrams` by the id inside it. `project.id` is informational (the import picks a free id).

### `exchange.py`

- `export_project(project, diagrams) -> dict`: the envelope above; `diagrams` are exported with
  `export_document` in membership order, members whose diagram is missing are left out.
- `parse_project(bundle) -> ProjectBundle(title, description, sections, members, diagrams)`: checks the
  envelope (`format`, `schema_version` 1, `project` and `diagrams` present), parses every diagram
  document with `parse_document` (problems prefixed with `diagram <n> (<id>)`), rejects duplicate
  diagram ids, validates the project structure with `Project.integrity_errors()` (sections, parents,
  cycles, members in unknown sections, duplicate members) and requires slug section ids and requires that the membership and the
  diagram documents name the same diagrams. All problems are collected into one `ExchangeError`.
  `PROJECT_FORMAT = "arcviz-project"`, `PROJECT_SCHEMA_VERSION = 1`.

`exchange.py` may now import `projects` (the module table changes accordingly).

### Service

`ProjectExchangeService(diagrams: DiagramService, projects: ProjectService)` in
`services/bundles.py`:

- `export(project_id) -> dict`: reads the project and its member diagrams.
- `import_bundle(bundle, title=None) -> Project`: `parse_project` first (nothing written on a 400),
  then `ProjectService.create_with_sections(title or bundle title, description, sections)` with a free
  id (`<slug>`, `<slug>-2`, …), then every diagram in membership order through
  `DiagramService.import_new(diagram, its title, project=new id, section=its section)` (free ids
  derived from the document ids). If any write fails, the diagrams created so far and the new project
  are deleted again (best effort) and the error is raised.

### HTTP

| Route | Answer |
|---|---|
| `GET /api/projects/{pid}/export` | 200 `application/json`, `Content-Disposition: attachment; filename="<pid>.arcviz-project.json"`; 404 |
| `POST /api/projects/import` `{bundle, title?}` | 201 project view of the new project; 400 `Invalid bundle: …` listing every problem |

The import route is declared before `/api/projects/{pid}` and gets the import body limit
(`ARCVIZ_MAX_IMPORT_BYTES`) like `POST /api/diagrams/import`.

### UI

The navigator's project menu gets **Exportieren** (downloads the bundle of the open project) and
**Projekt importieren…** (file picker; the new project opens). Errors are shown with `alert()`.

## Testing

- `tests/test_exchange.py`: round trip (export, parse, same structure and diagrams), every validation
  error collected, newer `schema_version` refused, mismatching membership.
- `tests/test_properties.py`: a project built from random operations round-trips through the bundle.
- `tests/test_project_bundles.py`: import creates a new project with unique ids next to an existing one,
  order and sections kept, cleanup after a failing write.
- `tests/test_server_projects.py`: export download headers, import 201/400, the import body limit.
- e2e: export the open project and import it again as a new project.

## Migration / compatibility

New format, nothing changes for existing files. A future schema change bumps
`PROJECT_SCHEMA_VERSION`; newer bundles are refused with a hint to update.

## Open questions

None open; ADR-0056 is *Proposed*.

## Decisions

- One JSON file that embeds the diagram documents; import always creates a new project with fresh
  diagram ids (ADR-0056, Proposed; "import always creates a new project with unique ids" is the issue's
  requirement).
- No versions or transcripts in the bundle, as for single diagrams.
