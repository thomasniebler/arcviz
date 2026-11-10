# Model first

As implemented; design and decisions: [Model first](../architecture/specs/model-first.md) (ADR-0213..0220).

The project model is the source of truth. A diagram of a structure type (deployment, class, use case, component) is a
**view**: its stored document keeps only `shows` (the ids of the project elements it shows, in order),
`hidden_connections` and its own settings (title, direction, completeness, hidden relations). Names, kinds,
properties, notes and connections live in the project document:

| Model | Field | What |
|---|---|---|
| `Element` | `notation` | diagram type → view kind (`{"deployment": "database"}`), omitted while empty |
| `Element` | `properties` | the properties every view shows, omitted while empty |
| `Element` | `description` | the note of every view (≤ 2000 characters) |
| `Project` | `connections` | `Connection` (`id` `e<n>`, `kind`, `source`, `target`, `label`, `direction`, `properties`, `relation`) |
| `Project` | `connection_seq` | highest `e<n>` issued (ids are never reused) |
| `Project` | `agent_writes` | `propose` (default, omitted) or `direct` |

A view draws every connection of a kind its type knows between two shown elements, unless it hides it. Behaviour
diagrams (activity, state machine, sequence) keep their content; lifelines, partitions and data stores always stand
for elements and show the element's name and note.

## Repository

`store.modelbacked.ModelBackedStore` wraps every backend (`create_store`). `read_latest` resolves the stored document
against the project that holds the diagram (`views.resolve`); `create_latest`/`commit_latest` split the diagram
(`views.decompose`), write the project first (conditional on its revision; `ConflictError` makes the caller's `update`
retry) and then the view, and replace the version's snapshot by the resolved diagram. The revision is
`<backend revision>~<fingerprint of the resolved diagram>`; a conditional write checks both parts. Diagrams outside
every project pass through unchanged.

## Operations

- `remove_node`, `remove_edge`, `remove_lifeline`: `scope` `view` (default; hide in this diagram) or `model` (delete
  the element or connection from the project model and so from every diagram).
- `add_node` reuses the element of the same name that the view does not show; `add_edge` shows a hidden connection
  again; `show_element(element, parent?)` shows an existing element (structure types).
- Renaming a view element renames the element.

## Linking and removing

- `link_element` makes a view element that element; unlinking is refused in a project for people and assistants
  (`operations.unlink_refusal`), the inspector offers to point a node at another element instead.
- Removing an element from the model is refused while a lifeline, partition or data store stands for it.
- A rename writes no diagram; `add_element` with an existing name returns that element.
- Moving a diagram into another project keeps it whole (`Diagram._detach`) and then takes it into the target model.

## Agents

The chat assistants may remove with scope `model` only in a preview turn. MCP tools `create_component`,
`update_component`, `create_relation`, `show_in_diagram`, `hide_in_diagram` write when the project's `agent_writes` is
`direct`, else store proposals; `PUT /api/projects/{pid}/agent-writes` (owner) sets it.

## Migration

At every start `services/model_first.migrate_to_model` writes each member diagram that is still a full document once
through the repository (version label *Ins Projektmodell übernommen*): nodes become elements (merged by link, id or
name), edges become connections, and connections the document did not draw are hidden in it.
