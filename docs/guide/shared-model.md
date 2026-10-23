# Shared project model

The diagrams of a project often show the same things: the *Order Service* is a node in the deployment
diagram, a lifeline in the checkout sequence and a swimlane in the order process. The **project model**
holds such things once, as **project elements**, and lets the diagrams show them as **views**. Rename an
element once and every diagram that shows it follows; describe it once and the description appears
wherever it is shown.

Using the model is optional. Diagrams without links work exactly as before.

## Project elements

**Modell** in the navigator opens the model of the open project. Every element has

- a **name** (unique in the project, at most 100 characters),
- a **kind**: *Akteur* (actor), *System*, *Teilsystem* (subsystem), *Komponente* (component),
  *Knoten* (node, an execution environment) or *Daten* (data),
- an optional **description** (at most 300 characters) and an optional **part of** (*Teil von*) another element, for example a
  component that belongs to a subsystem,
- the list of diagrams that show it.

**Anlegen** creates an element, **Speichern** saves the changes of a row and **Löschen** removes the
element from the model. A project holds at most 500 elements. Elements saved before these limits existed keep
their longer name or description until you change it; importing a project file shortens longer names and descriptions
(ending in "…") and says how many it shortened.

- **Renaming** an element renames every node and lifeline that shows it, in every diagram of the project.
  Each of these diagrams gets a new version *Element „…“ umbenannt*, so the rename can be undone per
  diagram.
- **Deleting** an element from the model keeps the nodes and lifelines in the diagrams; only their link
  ends (a new version *Element „…“ entfernt* per diagram). Deleting a node or lifeline from a diagram
  never deletes the element.
- **Abgleichen** brings every linked node and lifeline back to the name in the model, for example after an
  undo in one diagram restored an old name. It changes nothing when everything matches.

## Building blocks become project elements automatically

Every building block you add to a diagram of a project becomes a project element at once, whether you add it in the
chat, through the API or by confirming a proposal: deployment nodes such as services, components, databases,
queues, gateways, external systems and the execution nodes *node* and *cluster*, every lifeline of a sequence
diagram, and activity partitions and data stores. The components and subsystems of a component diagram become *component* and *subsystem* elements, so the *Bestellservice*
of the deployment diagram and of the component diagram is one element. The actors and the systems (subjects) of a use case
diagram become *actor* and *system* elements the same way, so
"Kunde" is one element in every diagram. Classes of a class diagram can be linked to *data* elements and
interfaces to *component* elements, but they are not taken over automatically (use **Unverknüpfte übernehmen…**). When the project already has an element with the same name (also
written differently, e.g. *OrderService* for *Order Service*), the new node is linked to it and takes its exact
name; otherwise a new element is created. Grouping containers (*deployment*, *vnet*, *subnet*, *region*, *group*)
and behaviour (actions, messages) stay in their diagram. If the existing element is of a kind the node cannot show
(e.g. an actor for a database), the node stays unlinked.

So **reuse names**: a node called like an existing element is the same thing; a variant name creates a second
element. The assistant knows this and builds new diagrams from the existing elements first; when it creates new
elements it names them in its reply (*"Neu im Projektmodell: …"*). The project review reports elements whose names
are almost the same (**Projektelemente mit fast gleichem Namen**).

Diagrams created before, from a template or by an import are not changed by themselves. **Unverknüpfte
übernehmen…** in the model dialog shows what would happen to their unlinked building blocks (linked to an existing
element, created, left as is) and, after you confirm, does it: one version *Projektelemente übernommen* per diagram.
Undoing such a version removes the links, not the elements.

## Linking a node or lifeline

Select a node or a lifeline: the inspector shows the section **Projektelement**.

- Pick an element and click **Verknüpfen**: the node or lifeline now shows that element and takes its name.
- **Als Projektelement anlegen** creates an element named like the node or lifeline (of the chosen kind)
  and links it.
- **Verknüpfung lösen** ends the link; the node or lifeline keeps its name.
- A linked node or lifeline shows the element's description under **Beschreibung im Projektmodell**, read-only
  (edit it in **Modell**). The node's own **Notiz** stays separate: it belongs to this diagram.

Which diagram elements can show which kinds of element:

| Diagram element | Can show |
|---|---|
| Lifeline (sequence diagram) | every kind |
| Activity partition (swimlane) | component, actor, system, subsystem, node |
| Data store, central buffer (activity diagram) | data |
| Container of a deployment diagram (deployment, vnet, subnet, node, cluster, group, region) | node, system, subsystem |
| Database, storage, queue | data, component |
| External system | system, actor, component |
| Other deployment elements | component, system, subsystem |

Actions, decisions, messages and flows belong to their diagram and cannot be linked.

The name of a linked node or lifeline comes from the model: changing it in the inspector renames the element,
so every diagram follows. Renaming it through an operation of the diagram itself is refused.

## Code links

An element can say where its code lives: **Modell → Element → Code** takes a path in the repository (`src/orders`,
a file or a directory; a directory covers everything below it), and optionally a repository label (when the system has
several repositories), a symbol (class or function) and a note. Arcviz does not read the repository; the links are text
that you, the chat assistant or a [coding agent](agents.md) maintain, and they stay with the element when it is renamed
or shown in more diagrams. Ask for example: *"Verknüpfe den Order Service mit src/orders."* A coding agent can ask which
element owns a path.

## Relations

Besides the elements, the model can hold **relations** between them (**Modell** → *Beziehungen*):

| Kind | Meaning | Between |
|---|---|---|
| *kommuniziert mit* (communicates) | one calls or talks to the other | any two elements |
| *läuft auf* (deployed_on) | something runs on an execution environment | a component, subsystem, system, data or node on a **node** |
| *Datenfluss zu* (data_flow) | data flows in this direction | actors, systems, subsystems, components and data |

A relation can carry a **protocol**, a **port**, a **direction** and whether calls are **synchronous or
asynchronous**, plus a short description. A project holds at most 500 relations; there is only one relation of a kind
between the same two elements. Deleting an element deletes its relations.

Diagrams show relations, they do not have to: an edge of a deployment diagram or a message of a sequence diagram can
**link** to a relation when both of its ends are linked to the relation's elements. Select the node, open the details
of the edge (the button next to it) or select the message: the section **Projektbeziehung** offers the relations
that fit, **Verknüpfen** links, **Verknüpfung lösen** ends the link. Edges of kind *connection*, *dependency*,
*association* and *replication* show *kommuniziert mit* (a connection also a data flow), *deploys* edges show *läuft
auf*. Activity diagrams do not link relations. The relation's protocol or port is never written into the diagram:
when the diagram says something else, the project review reports the contradiction and you decide.

When a relation changes its elements or its kind, links that no longer fit end (a new version in the diagrams
concerned); deleting a relation ends all its links.

**Matrix** in the model dialog shows which diagram shows which relation: ● linked, ○ shown by a connection that is not
linked yet, – intentionally hidden, ! missing in a diagram declared complete. Clicking ● or ○ opens the diagram and
marks the connection.

## Complete diagrams and hidden relations

A diagram that leaves a relation out says nothing about it: it may simply not be about it. Only a diagram that
**declares itself complete** for a kind of relation is expected to show every relation of that kind between the
elements it shows. Use **Vollständigkeit** in the toolbar of a deployment, component or sequence diagram and tick the kinds
(*Kommunikation*, *Verteilung*, *Datenfluss*; sequence diagrams only *Kommunikation*, component diagrams *Kommunikation* and *Datenfluss*). Each change is a new version.
The assistant can do the same (`set_completeness`) when you ask it.

If a complete diagram leaves a relation out on purpose, select one of its two elements: the inspector's
**Projektelement** section lists the relations of the element whose other end the diagram shows but that it does
not draw. Tick **hier bewusst ausgeblendet** and the project review no longer reports it for this diagram. The mark
belongs to the diagram (it is versioned with it and moves with the diagram and its relation to another project).

## Sections and subsystems

A section can stand for an element of the model (usually a subsystem); its title then follows the
element's name. Use the API (`PATCH /api/projects/{pid}/sections/{sid}` with `element`) or the chat for that.

## In the chat

When the diagram belongs to a project, the assistant can list the project elements, create them, rename or
describe them and link nodes and lifelines to them. Ask for example: *"Lege den Order Service als
Projektelement an und verknüpfe ihn in diesem Diagramm."* In preview mode (**Vorschau**) the assistant only
proposes diagram changes; it does not change the model.

With relations in the model, the assistant links an edge or message it draws between two linked elements to
the matching relation. When there is none, it tells you which relation it would add and creates it only when you
ask (for example *"Lege die Beziehung an, dass Service C mit der Orders DB kommuniziert."*). It can also declare a
diagram complete when you ask it to.

## In the project review

The project review checks the links across diagrams (and, with relations, gaps and contradictions between the
diagrams; see [Review](review.md)): links to elements that no longer exist, diagrams that
still show an old name, links whose diagram element cannot show the element's kind, unused elements,
elements that take part in flows but appear in no deployment diagram, sequence messages between two
elements that no deployment diagram connects, and diagram elements named like an element but not linked.
A finding names the element it concerns.

## Export, import and moves

The project export (**Exportieren** in the project menu) contains the model and the links; importing it
creates a new project with the same elements.

Elements belong to one project. Moving a diagram to another project brings its linked elements and relations
along: an element the target already has (same id, kind and name) is used, any other is copied, under a new id if
its id is taken there. The links stay; the status line says what was copied or used. What the target cannot take (a
name another element already has, a full project) loses its link, as before: the nodes and lifelines keep their
names, and the status line lists them. Importing a single diagram works the same way: its export file contains
the elements and relations it links to. A file exported before this version contains none, so its links end.
Link them again to elements of the new project if needed.
