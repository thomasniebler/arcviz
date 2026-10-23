# ADR-0086: Authors as user id plus display name, omitted when unknown

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/workspaces-and-sharing.md`

## Context

Versions and transcript entries should show who caused them (#162). Data written before has no author and must
load unchanged; the v0.1.7 compatibility test compares version metadata byte for byte.

## Options

1. **Store the user id only** and resolve names from the directory when reading: always current names, but every
   version list needs a directory lookup and names of users never seen again are lost.
2. **Store id and the display name at write time.** No lookup; the name is that of the moment.

## Decision

Option 2: `author` and `author_name` on `VersionInfo` and on transcript entries; both omitted from dumps when
unset, so old data keeps its JSON and new data without an author (system writes, migration) looks the same.

## Consequences

- A renamed user keeps the old name on old versions.
- Summary entries of the chat compaction carry no author.
