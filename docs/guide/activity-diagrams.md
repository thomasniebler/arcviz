# Activity diagrams

An activity diagram (type **Aktivitätsdiagramm**) shows a process: what happens in which order,
where it branches, what runs in parallel and who is responsible. It follows the *Activities* chapter
of UML 2.5.1. Create one with **Neu → Typ: Aktivitätsdiagramm**, either empty or from the template
**Bestellprozess** (`order-process`).

## Elements

| Kind | Drawn as | Use it for |
|---|---|---|
| `opaque_action` | rounded box with the name | a step, named with a verb phrase ("Bestellung prüfen") |
| `initial_node` | filled black dot | where the flow starts |
| `activity_final_node` | bull's eye | the end of the whole activity |
| `flow_final_node` | circle with a cross | the end of one flow only (other flows continue) |
| `decision_node` | diamond with the question | a branch; the conditions go on the outgoing edges as guards |
| `merge_node` | empty diamond | where alternative branches meet again |
| `fork_node` / `join_node` | black bar | split into parallel flows / wait for all of them |
| `central_buffer_node`, `data_store_node` | box «centralBuffer» / «datastore» | objects or data passed between actions |
| `activity_partition` | box around its contents | a swimlane: who does it (customer, shop, warehouse) |

## Flows and guards

Edges are **control flows** (order) or **object flows** (objects or data). They always point from
source to target. A **guard** is the condition under which a flow is taken, drawn as `[ja]`; put
guards on the edges leaving a decision. Ask in the chat ("wenn verfügbar, versenden, sonst den Kunden
informieren") or edit the guard in the inspector: it is the edge property `guard`.

Arcviz keeps the diagram well-formed: flows cannot start or end at a swimlane, nothing flows into a
start node and nothing leaves a final node. A request that breaks a rule is refused with the reason.

## What works as for deployment diagrams

Swimlanes can be focused (view selector, click), the layout direction can be changed, versions can be
compared, and the diagram can be exported as SVG, PNG, Mermaid source, JSON and PlantUML.

The PlantUML file uses PlantUML's activity syntax: swimlanes, `if`/`switch` for decisions, `fork` for parallel
flows. That syntax is block-structured, so a flow that jumps back (a loop) or into another branch is drawn as a pair
of numbered connector circles, as UML allows; a flow that ends without a final node ends with `detach`.
