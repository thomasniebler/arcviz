# ADR-0161: Linked elements and relations come along when a diagram moves or is imported

- Status: Accepted
- Date: 2026-10-11
- Spec: `docs/architecture/specs/shared-project-model.md`, `docs/spec/projects.md` (shared model)
- Supersedes: [ADR-0079](0079-links-dropped-when-a-diagram-leaves-its-project.md) (its behaviour remains for what does not fit and for documents without a model)

## Context

Element and relation ids are project-scoped. ADR-0079 drops every link of a diagram that moves to another project, so
that a ref never names an unrelated element there; the views keep their names and have to be linked again by hand,
one action per view (#153). The source project holds every element and relation the diagram refers to, so a move can
bring them along. A single-diagram document carried only the ref ids, so an import had nothing to bring. Open points
were id collisions and what happens when the target cannot take everything (the element
limit, and since M26 the element bounds and the size guard of the project document).

## Options

1. **Keep ADR-0079.** Safe, but every move loses the model work done in the diagram.
2. **Map by name.** Elements of different projects share no identity; a name match is a guess.
3. **Map what is provably the same, copy the rest, drop what does not fit.** Same id, same kind and the same name
   (case-insensitive) count as the same element; anything else is copied, under its own id when that is free.

## Decision

Option 3:

- An element the diagram refers to (by a view, or as an end of a relation it refers to), and its ancestors, is mapped
  to the target's element with the same id, kind and name; else copied under the same id when the id is free there,
  else copied under a new id (the slug of its name, made unique) and the views are linked to that.
- A relation the diagram refers to (a connection or a hidden marker), and every relation between carried elements of
  which one end was copied, is mapped to the same relation between the carried ends (`same_relation`), else copied with
  an id from the target's `relation_seq`, never one used before.
- What the target refuses is left out and keeps ADR-0079's behaviour, its links are dropped: a name another element of
  the target holds (names are unique per project), `MAX_ELEMENTS`/`MAX_RELATIONS`, the element bounds and the size
  guard of the project document (checked per element and relation, so whatever fits still comes along), a child of an
  element left out.
- Order of the writes: the target project (one conditional write, planned again on a conflict), then one diagram
  version that unlinks every view whose id is not the same in both projects, then the membership, then one version
  that links those views to their new ids (and gives mapped views the target element's spelling of the name). Every
  state in between links nothing wrongly; a crash leaves views unlinked (ADR-0079's state) and, at worst, unreferenced
  copies in the target.
- The move answer carries `carried`: copied and mapped (`{id, as}`) and dropped (`{id, reason}`) elements and
  relations; the UI shows it as a status line.
- **Imports**: a single-diagram export embeds the referenced elements, their ancestors and the relations between
  referenced elements under an optional `model` key. Every release reads only `format`, `schema_version` and
  `diagram`, so the schema version stays and older releases import such a document as before. The import validates
  the model like a bundle's, runs the same planner against the target project, then creates the diagram linked to the
  target's ids and answers `carried` too. A document without `model` (exported before) drops its links as in
  ADR-0079 and says so. The embedded model is only what a viewer of the project could read anyway.

Review of M28 (same milestone):

- **Kept links are checked after the membership**: the relink version reads the target after the membership write and
  unlinks every link (kept or new) whose element or relation is gone or no longer fits (an element kind the view cannot
  show, a relation the connection does not fit: an old version's edge, a change meanwhile), so a copy another editor removed in between never
  leaves a ref that a later element with that id would silently take; a removal after that read reaches the diagram
  through the model service, which now sees it as a member. `ModelService.sync` clears dangling element refs too.
  Imports check once more after the create.
- **A failure loses nothing; an orphan copy is harmless** (second review): when `place` raises, the target is read
  first. It is read fresh, never through the request's cached copy (`ProjectService.fresh`; so are the
  source for a restore and the target's links for `uncarry`), because the write that failed may have landed. If it
  holds the membership (a later step of `place` failed), the move counts as done: it is finished against the target,
  the source membership is withdrawn best-effort (else left to the repair), and the failure is still raised. A
  definite "not found" or "forbidden" means nothing moved; only a failed read ("can't tell") compensates nothing. Only when
  nothing moved do the views get their links back, against the source project as read then (to elements and relations
  that still exist and fit; the rest stays unlinked), and `ProjectService.uncarry` takes back a copy only while it is
  still exactly what the carry added (every field, so an edit made meanwhile is never lost), no member diagram
  links to it and no relation, child or section it did not add hangs on it; everything else is left in the target and
  logged. An import whose create fails uncarries the same way; after the create every linked view is checked again.
- **Version exports** embed only elements whose current name and kind still match every view of them in that version
  (and the relations between those): a version may stem from another project, and a view must not be paired with an
  unrelated element. The other refs stay in the document; an import reports them as dropped.
- **Names compare one line** (`name_key`): a stored pre-#103 name maps to the same element instead of being copied.
- **The size guard is measured incrementally**: one `document_size` before and one after, in between the serialised
  length of each added element or relation; if the final measure still exceeds the limit, the last additions are taken
  back. A document already above the limit takes nothing.

## Consequences

- Moving a diagram keeps its links in the usual cases (an empty target, the same model, a colliding id).
- The source project is never changed; elements are copied, not moved, so other diagrams of the source keep theirs.
- A move into a full project degrades to ADR-0079 for the part that does not fit, and says so.
- Project bundles keep their refs as before (ADR-0077); their documents carry no `model` (the bundle has the whole
  model once).
- An import whose diagram create fails removes its copies again (review above).
