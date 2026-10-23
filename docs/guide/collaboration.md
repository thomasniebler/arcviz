# Working together

Several people can open the same diagram at once (see [Own and shared projects](workspaces.md) for who may). Arcviz
keeps everybody's view current and makes sure nobody overwrites a change they have not seen.

## Changes appear at once

When someone else changes the diagram you are looking at (in the inspector, through the chat, by undoing or
restoring a version), your canvas re-renders by itself; so do the version list and, when it changed, the chat
transcript. Changes to the project (new or moved diagrams, sections, renamed elements) update the navigator.

If you are busy at that moment (your own chat turn is running, a proposal waits for your decision, or you look at a
version preview or a comparison), the update waits until you are done.

If a diagram you have open is deleted, moved to a project you cannot see, or you lose access to it, the status line
says *Dieses Diagramm ist nicht mehr verfügbar* and the navigator is reloaded.

!!! note "Several servers"
    On a single server changes arrive immediately. When the operator runs several instances (Azure Cosmos DB),
    a change made on another instance arrives after a few seconds (`ARCVIZ_LIVE_POLL_SECONDS`).

## Editing the same element

While you edit an element in the [inspector](inspector.md), what you have typed is never replaced:

- If someone else changes a field you have **not** touched, the field shows the new value.
- If someone else changes a field you **have** changed, your value stays and a note below the field says
  *Inzwischen von Bob geändert: „…“*.
- When you save, Arcviz only writes if nobody changed the element since you opened it. Otherwise the inspector
  shows, for every field both of you changed, **Ihr Wert** and **Aktuell**, and you choose:
  **Meine Werte speichern** writes your values over the current ones (every other change stays),
  **Aktuelle Werte übernehmen** discards your edit.

Changes to other elements of the diagram never get in your way. Edge edits, the layout direction and linking to the
project model apply at once; if the diagram changed in the same moment, the view is refreshed and you are asked to
try again.

## Who else is here

Next to the diagram title, a round badge with initials stands for every other person who has the same diagram open
(in the project chat: the same project). Hover it for the name. A ring around a badge means that person is chatting
with the assistant right now; the line above your chat input says so too (*Bob chattet gerade mit dem
Assistenten.*), and their changes appear on your canvas as the assistant makes them.

When someone opens an element in their inspector, it gets a dashed outline in their colour with their initials on
your canvas. You only ever see people who may view the diagram, and they only see you there.

!!! note "Several servers"
    When the operator runs several instances on Azure Cosmos DB, you see everybody, whichever instance they are
    connected to; badges and outlines of people on another instance appear after a few seconds (about one poll
    interval). With the file or SQLite store there is only one instance.
