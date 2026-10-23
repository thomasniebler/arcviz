# ADR-0150: Generated id suffixes hold a digit and are replaced, not extended

- Status: Accepted
- Date: 2026-10-11
- Spec: `docs/architecture/specs/robustness.md`
- Refines: [ADR-0131](0131-server-generated-ids.md)

## Context

Since ADR-0131 every new diagram and project id is `<base>-<10 random base32 characters>`, and a requested id is only
the base. Importing an export uses the document's id as the base, so each round trip appends another suffix
(`zahlungen-<s1>-<s2>`, #200) until the 64-character cap cuts the slug. Base32 is `a–z` plus `2–7`: a plain rule
"strip a trailing `-` and ten base32 characters" also strips ordinary ten-letter words (`order-processing`,
`k8s-deployment`), which are common in requested ids and in ids from before M23.

## Options

1. **Strip any trailing ten base32 characters.** Simple; turns `order-processing` into `order-<suffix>`.
2. **Strip only a suffix that holds a digit, and generate only such suffixes.** New suffixes are drawn again until
   one holds a digit (about 49.8 instead of 50 bits). Words practically never match; a digit-free suffix from M23–M25
   (about one in eight) stays once and is followed by one new suffix, then the id is stable.
3. **Strip only on import, matching the document's title slug.** Misses ids whose base was a requested id.

## Decision

Option 2: `ports.id_suffix()` always holds a digit, `ports.id_base()` strips one trailing suffix of that shape, and
`new_ids()` starts from it, for every create path.

## Consequences

Re-imports keep one suffix; requested ids that end in a generated suffix get a fresh one. Tenant isolation (ADR-0131)
is unchanged: the answer never depends on other tenants' ids. ADR-0131 stays in force; this refines its id shape.
