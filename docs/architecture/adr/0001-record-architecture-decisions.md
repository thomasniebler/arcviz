# ADR-0001: Record architecture decisions

- Status: Accepted
- Date: 2026-10-04

## Context

The project started as a single-author prototype. Decisions (file-based store, closed kind sets,
provider abstraction) were made in commits and chat, not written down. New work now changes
foundational parts (persistence, context handling), and the reasons for the old choices are needed
to judge the new ones.

## Options

1. **Keep decisions in commit messages and PR descriptions.** No extra files; hard to find later,
   impossible to see which decisions are still in force.
2. **Lightweight ADRs in `docs/architecture/adr/`.** One Markdown file per decision, numbered,
   append-only, with status. A table in `adr/index.md` as index.
3. **A single long design document.** Easier to read front to back, but edits erase history and the
   document decays.

## Decision

Option 2. ADRs are written for every decision that has a real alternative, in the same PR as the
spec or code that depends on them. The implementation workflow (`.claude/skills/implement/SKILL.md`)
makes this a required step.

## Consequences

- Each spec in `docs/architecture/specs/` links the ADRs it relies on.
- Reversing a decision means a new ADR that supersedes the old one, not editing the old one.
- Slight overhead per change; in return, "why is it like this" has an answer in the repo.
