# ADR-0177: Typed node and edge fields are stored as properties

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/uml-graph-types.md`

## Context

ADR-0034 stored the activity `guard` as the edge property `guard`, with a top-level `guard` argument, to avoid changing the
serialised `Edge`. The new types need more typed values: state machines `effect`, `entry`, `do`, `exit`; classes
`attributes`, `operations`, `literals`, `abstract`, multiplicities and roles; use cases `condition`, `extension_point`;
components `ports`, `source_port`, `target_port`. Some are lists. `Node` and `Edge` fingerprints must not change for existing
types, and unset values are omitted from dumps.

## Options

1. **One top-level argument per field** (the `guard` pattern). Explicit, but `add_node` and `add_edge` grow a dozen
   arguments that exist for one type each, and `check_arguments` (the argument contract, ADR-0045) has to list them all.
2. **One optional `fields` object** on the four node/edge operations, validated by the type's profile, stored as properties
   (lists newline-joined); the per-type tool schema lists exactly the allowed keys per kind. `guard` stays as it is.
3. **New model fields** on `Node`/`Edge` (`members`, `trigger`, …). Typed, but changes the JSON of every node and edge
   (needs wrap serializers and fingerprint special cases) and the model for every type.

## Decision

Option 2. Typed fields are properties with reserved keys; `properties`/`remove_properties` refuse a reserved key and name the
field; the inspector, diff, summary, fingerprint and exports see ordinary properties. Bounds per field are in the profile
(lines per list, characters per line). `guard` keeps its place for activity (and state machines), so tool schemas of existing
types do not change.

## Consequences

- No change to `Node`/`Edge` serialisation; stored data of existing types is untouched.
- A list stored as newline-joined text cannot hold a line break inside an item (by design: items are one line, `single_line`).
- Two ways to pass a typed value exist (`guard`, `fields`); the guide and the tool descriptions say so. A later cleanup can move
  `guard` into `fields` for new schema versions.
