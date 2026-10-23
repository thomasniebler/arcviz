# Project elements by default

- Status: Implemented
- Issue: #225
- ADR: [0164 Structural views become project elements when they are created](../adr/0164-structural-views-become-project-elements.md)
- Extends: [Shared project model](shared-project-model.md) (ADR-0070..0078); related: [Project review](project-review.md),
  [Architecture drivers](architecture-drivers.md)

## Summary

Until now a node or lifeline was local to its diagram until someone linked it or chose **Als Projektelement
anlegen**. The same component therefore ended up as unrelated nodes in several diagrams, and a new diagram rarely
reused what the project already had. Now every **structural** view element that is created (a deployment building
block or execution environment, a sequence lifeline, an activity partition or data store) is **adopted** at once: it
is linked to the project element with the same name, or a new project element is created for it. This happens on
every write path that creates view elements (the assistants' tool calls, `POST /ops`, confirmed proposals) and never
in preview mode. The prompts tell both assistants to reuse the project's elements before creating new ones and to
name every element they create. A project review rule reports elements whose names are near-duplicates, and the
model panel adopts the unlinked views of existing diagrams on request.

## Goals

- A structural view element created in a diagram of a project stands for a project element right away.
- Reuse: an existing element with the same name (or the same name apart from case, spaces and punctuation) is linked
  instead of creating another one.
- The assistants see the project's elements, reuse them first and say which elements they created.
- Near-duplicate element names are reported.
- Existing diagrams can be brought into the model with one action, with a preview.

## Non-goals

- Adopting grouping containers (`deployment`, `vnet`, `subnet`, `region`, `group` and container kinds of a kinds file):
  they stay local; **Als Projektelement anlegen** still works for them.
- Behaviour (activity actions and control nodes, messages, fragments): never linked (ADR-0071).
- Adopting on diagram creation from a template, on import or on a diagram move: the content arrives as a whole; the
  model panel's action adopts it on request.
- Merging duplicate elements (the review reports them; merging is a follow-up).
- A switch to turn adoption off (user's decision: always on).

## Design

### Which views are adopted (`project_model.adoptable`)

`adoptable(diagram_type, view_kind) -> bool`:

| Type | Adopted | Not adopted |
|---|---|---|
| deployment | leaf kinds (`service`, `component`, `database`, `queue`, `storage`, `gateway`, `external`, `artifact`, `function` and leaf kinds of a kinds file), the execution environments `node` and `cluster` | `deployment`, `vnet`, `subnet`, `region`, `group`, container kinds of a kinds file |
| sequence | every lifeline | – |
| activity | `activity_partition`, `data_store_node` | everything else |

Only views that can be linked at all (`linkable_kinds` non-empty) qualify.

### Matching (`project_model.adoption_target`)

For a new, unlinked, adoptable view named *n*:

1. the element whose `name_key` equals *n*'s; else
2. the only element whose **loose key** (letters and digits only, case-folded: "Auth-Service" = "authservice")
   equals *n*'s;
3. none: a new element named *n* of `default_kind(diagram, view)` (an actor for an actor lifeline, else the first
   linkable kind) is created.

When the found element's kind cannot be represented by the view (`linkable_kinds`), the view stays unlinked and the
outcome says why (`skipped`: "element 'x' is a data element, which a «service» cannot represent").

### Service (`services/model.py`)

```python
@dataclass
class Adoption:
    linked: list[dict]   # {"view", "element", "name"}: linked to an existing element
    created: list[dict]  # {"view", "element", "name", "kind"}: linked to a new element
    skipped: list[dict]  # {"view", "name", "reason"}

class ModelService:
    def adopter(self, diagram_id: str) -> Adopter | None  # None: the diagram is in no project
    def project_adopter(self, project_id: str) -> Adopter  # the assistants know their project
```

`Adopter(projects, project_id)` (`services/adoption.py`, only `ProjectService` below it) works in two steps around a diagram write, like `promote` (project first):

- `prepare(before: Diagram, after: Diagram) -> Plan`: the new adoptable unlinked views of `after` (ids not in
  `before`), matched against the project read now; the elements to create are created in **one** project write
  (`apply_all` of internal `add_element` steps; names that another request created meanwhile are matched again).
- `steps(before_ids, diagram, plan) -> list[(op, args)]`: inside the diagram's change, after the operation itself: one
  internal `link_element` per planned view that still exists, is still unlinked and whose element still fits.
- `finish(plan, adoption)`: elements created by `prepare` that no view ended up linked to are removed again (best
  effort, like `promote`).

Writes:

- **REST** `POST /api/diagrams/{id}/ops`: `ModelService.apply_operation(diagram_id, op, args, label, expected)` probes
  the operation on a copy, prepares, then writes the operation **and** the links as **one** version (one
  `DiagramService.update` whose change is wrapped by `adoption.adopting`). The answer adds `adoption` (omitted when nothing was adopted).
  A diagram in no project behaves as before.
- **Assistants** (diagram chat, project chat): after a successful editing call is planned, the call's change runs
  the operation and then the link steps within the same `write_through` (no extra version; the turn's version holds
  both). The tool result adds `adoption`, so the model can name what it created. In propose mode nothing is adopted
  (the proposal is one diagram's operations); confirming it adopts as below.
- **Proposals** confirmed through `POST …/proposal/apply`: the replay adopts the new views of the result in the same
  write.

Concurrency: the project write and the diagram write are separate conditional writes (no cross-document atomicity,
as for `promote`). A conflict on the diagram re-runs the change, which recomputes the link steps on the newer state.

- **Verified after the write** (like `link_element`, #155, ADR-0128): `ModelService.adoption_problems` reads every
  adopted element again; one that was removed or re-kinded meanwhile (or cannot be read: fail closed) has its link
  taken back, only while the view still stands for it, and the entry moves to `skipped` with the reason.
  `ModelService.settle_adoption` does it for `/ops`, `adopt_all` and confirmed proposals (a version "Verknüpfung
  zurückgenommen"); the assistants run `ModelTools.settle` through the turn's write-through. A take-back that cannot be
  written leaves the link to the project review (`dangling-element-ref`, `incompatible-element-link`).
- **Cleanup never orphans another diagram's link**: `finish` removes an element it created only when no member
  diagram (and no section) refers to it, read after the write; an element a concurrent adoption or link reused stays.
  When the members cannot be read the element stays (the review reports it as unused).

### Existing diagrams: **Unverknüpfte übernehmen**

`ModelService.adoption_plan(project_id)` → per member diagram the adoptable unlinked views with their outcome
(link / create / skip); `ModelService.adopt_all(project_id)` applies it: one project write for the new elements, then
one version per diagram (`apply_if_changed`, label "Projektelemente übernommen"). Routes:

| Method | Path | Answer |
|---|---|---|
| GET | `/api/projects/{pid}/adoption` | `{"diagrams": [{"id", "title", "linked", "created", "skipped"}], "counts": {...}}` (viewer) |
| POST | `/api/projects/{pid}/adoption` | the same, as done, plus `failed` (diagram ids not written) and `model` (editor) |

UI: the model panel's **Unverknüpfte übernehmen…** button shows the counts and the list in a confirmation and then
applies it.

### Prompts

`prompts/shared/model.txt` gets the reuse rule: before creating a node or lifeline, look for a project element that is
the same thing and use exactly its name (the view is linked automatically; a new name creates a new project
element); never create a near-duplicate name; when the turn created project elements (`adoption.created` in tool
results), name them briefly in the reply ("Neu im Projektmodell: …"). The model block is now also present (with
"none yet") when the project has no elements, so the rule is always known.

### Review rule `similar-element-names`

`project_review` rule: pairs of elements whose names differ only in case, spaces or punctuation, or whose loose keys
are equal after removing a generic suffix (service, svc, server, api, app, db, database, system, component,
komponente, dienst) or differ by one edit (keys of at least five characters). Severity low, category suggestion,
message in German naming both elements, `elements` = both element ids. At most 50 findings.

## Testing

- `adoptable` table; matching (exact, loose, ambiguous loose → create, kind mismatch → skip).
- REST `/ops` adds a node: one version holding node and link; the element exists; a second diagram's node with the same
  name links to it; containers stay unlinked; outside a project nothing changes.
- Agent with `ScriptedProvider`: `add_node` → linked and created; tool result names it; propose mode adopts nothing;
  project chat the same; batch with two nodes of the same name → one element.
- Proposal apply adopts.
- Adoption plan/apply over existing diagrams; viewer may read the plan, not apply it.
- Review rule cases. Access matrix entries. e2e: a node added through the UI shows as linked in the inspector.

## Migration and compatibility

No stored data changes. Diagrams created before stay as they are until **Unverknüpfte übernehmen**. API answers only
gain fields.

## Decisions

- Always on, server side, for every write path (user's decision, 2026-10-12).
- Grouping containers stay local (user's decision).
- Existing diagrams only on request, with a preview (user's decision).
- Near-duplicates are reported by a review rule, not refused (user's decision: "Dublettenprüfung").
- Templates and imports are not adopted automatically (they arrive as a whole and could create many elements at once).
