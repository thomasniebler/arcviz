# Cloud icons and kind packs

- Status: Implemented (milestone M13)
- Issue: #100
- ADR: [0036 Vendored CC0 icon subsets as Mermaid icon packs](../adr/0036-vendored-cc0-icon-subsets-as-mermaid-icon-packs.md),
  [0037 Configurable kinds extend deployment diagrams only](../adr/0037-configurable-kinds-extend-deployment-only.md)
- Rendering: [ADR-0032 Rendering of diagram types in M13](../adr/0032-rendering-of-diagram-types-in-m13.md), implementing [ADR-0038](../adr/0038-renderer-per-diagram-type.md)
- Builds on: [Configurable element kinds](configurable-kinds.md), [Diagram types](diagram-types.md)

## Summary

Deployment diagrams of cloud systems read better with the vendors' service icons. The kinds file gets
an optional `icon` per node kind (`"<pack>:<icon>"`), drawn with Mermaid's icon shape
(`@{ icon: … }`, Mermaid ≥ 11.3, `mermaid.registerIconPacks`). Three icon packs (`aws`, `azure`, `gcp`)
are vendored as Iconify JSON under `arcviz/static/vendor/icons/`, so rendering stays offline, and three
ready-made **kind packs** (kinds files with icons) are shipped under `arcviz/kindpacks/` and switched on
with `ARCVIZ_KIND_PACKS=aws,azure,gcp`. Only a subset of icons is vendored, all under CC0 (ADR-0036).

## Goals

- `icon` in the kinds file, validated at startup against the vendored packs.
- Icons drawn in the canvas, in SVG/PNG exports and in focus stubs; clicks, highlights and comparison
  marks keep working.
- Ready-made Azure, AWS and GCP kind packs that can be combined with each other and with a kinds file.
- Every icon's source, licence and the size added are documented; a script rebuilds the packs.

## Non-goals

- The vendors' official architecture icon sets (licence, ADR-0036).
- Icons for containers (Mermaid draws containers as subgraphs, which cannot carry an icon) or for
  activity and sequence diagrams.
- Operator-provided icon packs at runtime (possible follow-up).
- Icons in the PlantUML export.

## Design

### Vendored icon packs

`arcviz/static/vendor/icons/<pack>.json` is an Iconify JSON icon set (`{"prefix", "icons": {name:
{"body", "width"?, "height"?}}, "width", "height"}`) with prefix `aws`, `azure` or `gcp`, built by
`scripts/vendor_icons.py` from pinned npm packages:

| Source | Version | Licence | Used for |
|---|---|---|---|
| `@iconify-json/logos` (svg-logos by Gil Barbara) | 1.2.15 | CC0-1.0 | AWS service icons, Kubernetes, Redis, PostgreSQL, Google Cloud product icons, the Azure logo |
| `@iconify-json/simple-icons` (Simple Icons) | 1.2.99 | CC0-1.0 | monochrome Azure and Google Cloud service icons, recoloured with the vendor colour |

`scripts/vendor_icons.py` downloads the two tarballs from the npm registry, extracts the icons listed in
its table, writes the three packs and `arcviz/static/vendor/icons/README.md` (sources, versions,
licences, trademark note, sizes). The logos remain trademarks of their owners; they are used to name the
service they stand for (nominative use).

Size added: the three packs together stay below 100 kB (measured and listed in the README).

### Kinds file

```json
{"node_kinds": {"aws_lambda": {"icon": "aws:lambda", "style": "fill:#fff4e5,stroke:#ff9900"}}}
```

- `icon`: `"<pack>:<icon>"`; the pack must be a vendored pack and the icon must exist in it
  (`kinds.ICON_DIR`); otherwise `KindConfigError` names the file, the kind and the available icons.
- Only leaf kinds may have an icon (`container: true` with `icon` is an error).
- `KindCatalog.node_icons: Mapping[str, str]` holds the icons; `/api/config` reports `icon_packs` (the
  vendored pack names) and `diagram_types.deployment.node_icons`.

### Kind packs (`ARCVIZ_KIND_PACKS`)

`arcviz/kindpacks/{aws,azure,gcp}.json` are kinds files. `ARCVIZ_KIND_PACKS` is a comma-separated list
of pack names; `server.create_app` loads the packs and then `ARCVIZ_KINDS_FILE` into one catalogue
(`kinds.load_kind_catalog(*sources)`). A kind defined twice (in two packs, or in a pack and the kinds
file) is an error naming both sources; an unknown pack name is an error listing the available packs.
Kind names carry the vendor prefix (`aws_lambda`, `azure_functions`, `gcp_cloud_run`), so the packs
combine.

### Rendering (`mermaid.py`)

A leaf whose kind has an icon is drawn as
`n_x@{ icon: "aws:lambda", form: "rounded", label: "«aws_lambda»<br/>name", pos: "b", h: 46 }`, the
label escaped with `shape_label`. Its kind's `classDef` still applies. The frontend registers the
vendored packs once (`canvas.registerIcons(names)`, loaders fetch `/static/vendor/icons/<pack>.json`
lazily, so only used packs are loaded); click mapping also accepts `g.icon-shape`.

## Testing

- `tests/test_icons.py`: kinds file `icon` validation (unknown pack, unknown icon, container, format),
  every icon referenced by a kind pack exists, packs combine, duplicates and unknown packs fail,
  `/api/config` reports packs and icons, Mermaid icon lines and escaping, the vendored files are valid
  Iconify JSON with a CC0 source and stay under the size budget.
- e2e: with `ARCVIZ_KIND_PACKS` set, a node of an icon kind renders as an icon shape with the SVG icon
  inline (no network), and a click opens the inspector.

## Migration / compatibility

None. Kinds files without `icon` behave as before; no pack is active unless configured.

## Open questions

None open; see Decisions.

## Decisions

- CC0 subsets from Iconify's `logos` and `simple-icons` sets instead of the official AWS/Azure/GCP
  architecture icons, whose terms allow use in diagrams and documentation but not redistribution in a
  package (ADR-0036, Proposed).
- Azure services without an openly licensed icon use the Azure logo; the README lists which.
- Kind packs are kinds files selected with `ARCVIZ_KIND_PACKS` and extend deployment diagrams only
  (ADR-0037, Proposed).
