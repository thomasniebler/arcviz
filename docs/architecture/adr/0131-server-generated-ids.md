# ADR-0131: New diagram and project ids carry a random suffix

- Status: Accepted
- Date: 2026-10-09
- Spec: `docs/architecture/specs/hardening.md`
- Supersedes: [ADR-0089](0089-taken-ids-get-a-free-variant.md)

## Context

Diagram and project ids are global (one store, many tenants). Since ADR-0089 a taken id got the first free variant
(`<id>-2`, …), answered the same whether or not the caller could see the holder; but the variant still told the
caller that *some* diagram or project with that id existed, possibly in another tenant's workspace (#167, arc42 R20).
Reads of an invisible id already answer 404 like a missing one, so creates were the only oracle.

## Options

1. **Server-generated ids with a random suffix, existing ids kept.** Every new diagram and project gets
   `<slug>-<10 random base32 characters>` (50 bits); the slug comes from the requested `id`, else the title (or the
   imported document's id). A taken candidate (practically never) draws a new suffix internally. No migration:
   existing ids, storage keys (Cosmos partition keys, file paths), the locator (M22), URLs and exports stay.
2. **Tenant-scoped namespaces** (`<workspace>/<id>`): readable, client-chosen ids, but every storage key changes
   (Cosmos partition key values cannot be updated in place: copy and delete every item), all URLs and the locator
   change, and shared projects need the owner's namespace in readers' URLs.
3. **Pure opaque ids** (UUID/ULID): as option 1 for migration, but unreadable in exports, API and the Git mirror,
   with no security gain over option 1.
4. **Keep ADR-0089** and accept the residual.

## Decision

Option 1 (the owner's decision, M23). An explicit `id` from a client is the slug prefix; an invalid one is still 400.
The deterministic system ids (`default`, `home_<hash>`, `user_<hash>`) stay reserved and unchanged; a squatted
personal default project falls back to `home_<hash>-<suffix>`. The generator is `ports.new_ids`; every create path
(`DiagramService.create`/`import_new` — REST create, templates and the demo, imports, project bundles, the project
chat's `create_diagram` — and `ProjectService.create`/`create_with_sections`, including the squatted-default fallback
and a create whose leftovers cannot be cleared now) goes through it.

## Consequences

- **Breaking for API clients**: a requested `id` is no longer the id, even when free; clients must read the id from
  the response (the UI always did).
- No create answer depends on other tenants' data; `tests/test_ids.py` shows every create path answering identically
  whether or not another tenant holds the requested slug.
- No data migration; old ids keep working.
- The start-up migration of M20 data (system context, no caller) still renames squatting projects to `<id>-2`, … as
  before; it runs once on legacy data and answers nobody.
- The test suite pins readable ids (`tests/conftest.py`); tests of the scheme are marked `real_ids`.
