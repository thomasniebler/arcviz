# Projects and sections

A **project** groups the diagrams that belong together, for example everything about one system: the
deployment, the order process as an activity diagram and the checkout as a sequence diagram. Inside a
project, **sections** work like folders: they hold diagrams and further sections. Every diagram belongs
to exactly one project and is either directly in it or in one section. The diagrams stay independent
unless you link their elements to the [shared project model](shared-model.md).

## The navigator

The column on the left is the **project navigator**. The button **Projekte** in the header shows or
hides it; the browser remembers the choice, the open project, the open diagram and which sections are
collapsed.

- **Projekt** switches between projects. **+** creates a project; the **⋯** menu next to it renames
  it, edits its description (shown below the selector) and deletes it.
- **+ Abschnitt** creates a section at the top level.
- The tree shows the sections first, then the diagrams, in their order. Click a diagram to open it; the
  open diagram is highlighted. The arrow in front of a section folds it.
- The **+** on a section creates a new diagram in that section.
- Every row has a **⋯** menu:

| Row | Actions |
|---|---|
| Section | **Neues Diagramm hier…**, **Unterabschnitt…**, **Umbenennen…**, **Nach oben**, **Nach unten**, **Abschnitt löschen** |
| Diagram | **Umbenennen…**, **Verschieben…** (to another section or project), **Nach oben**, **Nach unten**, **Diagramm löschen** |

Renaming a diagram is an ordinary change: it is saved as a new version and can be undone.

While the model works on a chat turn, or while a proposal waits for confirmation, the navigator is
locked like the header.

## Creating diagrams in a project

**Neu** in the header (or **Neues Diagramm anlegen** in an empty project) creates the diagram in the
open project; the dialog has a **Abschnitt** field for the section. **Importieren** also imports into
the open project.

## The project chat

**Projekt-Chat** in the navigator switches the chat on the left from the open diagram's conversation to
one conversation about the **whole project**. A bar above the messages shows *Projekt-Chat · <project>*;
**Zum Diagramm-Chat** (or the button again) switches back. The project chat has its own history, which
**Chat leeren** clears while it is shown; the diagrams' own chats are not touched.

In the project chat the model knows the project's sections and diagrams (type and a short summary of
each) and can:

- list and read diagrams (nothing changes);
- **open** a diagram: the canvas switches to it, and the model edits it exactly as in its own chat;
- **create** a diagram of a type in an existing section, then fill it.

One request can therefore touch several diagrams, for example *"Lege ein Sequenzdiagramm Checkout an und
ergänze im Deployment einen Payment-Service"*. The tool lines show what happens (*Neues
Sequenzdiagramm „Checkout“ anlegen*, *Diagramm „deployment“ öffnen*, …), the canvas follows the diagram
being edited, and the reply lists every changed diagram with its new version (*Geändert: Checkout (neu,
v2) · Deployment (v7)*); click one to open it. Changed diagrams that are not open are marked
**geändert** in the tree until you open them.

Every changed diagram gets its own version, so **Undo** works per diagram: to take back the whole
request, undo it in each diagram the reply lists. With **Vorschau** switched on, a project request can
change one existing diagram and creates none; confirm or discard that proposal before asking for the
next diagram. The model asks back when it is unclear which diagram you mean. In an empty project the
project chat is the way to start: ask it to create the first diagram.

## Architecture

**Architektur** in the navigator opens the project's drivers, quality scenarios with their what-if analysis,
architecture decisions and stakeholders: see [Architecture](architecture.md).

## Exporting and importing a project

**Exportieren** in the project's **⋯** menu downloads the whole project as one file
(`<id>.arcviz-project.json`): title, description, sections and every diagram (without versions and
chat, like the export of a single diagram). **Projekt importieren…** reads such a file and always
creates a **new** project; its diagrams get new ids where the old ones are taken, so nothing existing
is changed. The file is checked completely first; if anything is wrong, every problem is listed and
nothing is created.

## Deleting

- **Abschnitt löschen** never deletes diagrams: the section's diagrams and subsections move one level
  up, to where the section was.
- **Projekt löschen** is only possible for an empty project; move or delete its diagrams first. If the project was
  changed while you were reading the question, you are asked again (and not at all if it has diagrams by then).
- **Diagramm löschen** (and **Löschen** in the header) removes the diagram with all versions and its
  chat, as before.

## The default project

After an upgrade from a version without projects, all existing diagrams are in the project
**Standardprojekt**; move them into your own projects from there. Diagrams that end up without a
project for any reason (for example after an interrupted move) also land there. When you delete it
while it is empty, it comes back the next time it is needed.

Administrators can also run `arcviz-migrate --projects <store>` (see
[Configuration](../reference/configuration.md)).
