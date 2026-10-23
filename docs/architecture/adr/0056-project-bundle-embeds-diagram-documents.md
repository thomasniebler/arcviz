# ADR-0056: A project bundle embeds the diagram documents; import always creates a new project

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/project-exchange.md`

## Context

Diagrams have a versioned JSON exchange document (ADR-0018). Projects (ADR-0051) add a structure around
several diagrams that users want to export and import as a whole. The issue asks for a versioned
envelope with the project, its sections and all diagrams, and that an import always creates a new
project with unique ids.

## Options

1. **One JSON file embedding the diagram documents unchanged.** Every diagram is validated by the
   existing parser with its own `schema_version`; the envelope only adds the structure. Large projects
   produce one large file (bounded by the import body limit).
2. **A ZIP archive** with one file per diagram and a manifest. Closer to a file tree, but a new
   dependency path (multipart upload, zip handling in the browser and server) for no gain at today's
   sizes.
3. **A new flat format** with the diagrams' content inlined into the project. One schema, but it
   duplicates the diagram format and its rules.

## Decision

Option 1: it reuses the diagram format and parser as they are, is a single download/upload like the
diagram export, and keeps the two formats evolving independently. Imports never update anything:
the project gets a free id and every diagram a free id derived from its document id.

## Consequences

- `exchange.export_project`/`parse_project`, a small service that writes the project and its diagrams
  with a best-effort rollback, two routes and two menu entries.
- Versions and transcripts are not part of a bundle, as for a single diagram.
- Bundles larger than `ARCVIZ_MAX_IMPORT_BYTES` need the limit raised.
