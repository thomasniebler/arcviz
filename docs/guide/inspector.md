# Element inspector

Small corrections should not need a chat round-trip. Clicking an element opens the **inspector**,
a panel on the right of the canvas that edits the element directly. Every change goes through the
same validated operations as the chat (`POST /api/diagrams/{id}/ops`), so it is checked, versioned
and highlighted exactly like a model edit.

## Opening

| Action | Effect |
|---|---|
| Click a leaf element (service, database, …) | Opens the inspector for it |
| **Bearbeiten** in the view row | Opens the inspector for the focused container (clicking a container title focuses it instead) |
| ++esc++, **×**, switching diagram | Closes the inspector |

## Fields

- **Name**, **Art** (kind) and **Container** (parent; *oberste Ebene* for the top level). The parent
  list offers every container except the element itself and its descendants. Changing the parent
  moves the element with all its edges.
- **Notiz**: a free-text note (up to 2000 characters): what the element is for, decisions, open points.
  The assistant reads it with the diagram (long notes shortened) and can write it too ("Notiere bei
  orders-db, dass sie täglich gesichert wird"). Empty the field to remove the note. Lifelines of sequence
  diagrams have the same field. Notes are not drawn in the diagram.
- **Eigenschaften**: key/value table. Remove a row to delete the property, **+ Eigenschaft** adds
  one. Empty keys are ignored.
- **Speichern** applies the changes: `update_node` (name, kind, note, properties) and `move_node` if the
  parent changed; both together are one all-or-nothing write. Each save becomes one version, labelled
  *«id» bearbeitet* (or *«id» verschoben* when only the container changed).

## Edges

The **Kanten** table lists every edge touching the element (`→ target` or `← source`). Changing a
label or kind applies immediately; **−** removes the edge. The **⋯** button opens the edge details:
the **Richtung** (standard of the kind, → forward, ↔ both, — none; applies immediately) and the
edge's properties such as `protocol` and `port`, saved with **Übernehmen**. Once set, the button
shows the direction and the number of properties instead of the dots. Adding edges is left to the
chat.

## Deleting

**Element löschen** asks for confirmation and removes the element and all its edges. For
containers, **Inhalt mitlöschen** removes the whole subtree; without it the children move up one
level.

## Validation errors

When a change is rejected (for example moving a service into another service, or changing a
container with children to a leaf kind), the message from the server appears in red inside the
panel and stays visible while the panel reloads the current state. Operations before the failing
one in the same save are kept. Edge edits only refresh the edge table, so unsaved changes to name,
kind, container or properties are not lost when you change an edge in between.

## Sequence diagrams

In a [sequence diagram](sequence-diagrams.md) every element can be clicked: lifelines, messages,
combined fragments (frame or corner label), operands (the strip along their top edge), interaction uses,
activation bars and destructions. The inspector then shows the fields of that element (for example
name, kind, sender and receiver of a message) and a delete button; **Speichern** applies only what you
changed, as a new version.
