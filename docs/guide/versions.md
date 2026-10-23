# Versions

Every diagram keeps a linear history of immutable snapshots. Nothing is ever overwritten in that
history; "going back" creates a new version that contains the old state.

## How versions are created

| Source | When |
|---|---|
| `initial` | When the diagram is created. |
| `auto` | After every chat turn that changed something (label: first line of your instruction) and after every direct operation through the API. |
| `manual` | When you press **Version sichern** and enter a label. |
| `restore` | When you restore a version with **Wiederherstellen**. |
| `undo` | When you press **Undo** (or ++ctrl+z++). |
| `redo` | When you press **Redo** (or ++ctrl+shift+z++ / ++ctrl+y++). |

The **Versionen** selector lists all versions, newest first, as
`v<number> · <label> (<source>, <time>)`.

## Preview

Selecting a version in the selector shows that state on the canvas with a banner
*Vorschau v<n>*. Nothing is changed yet; the current state is still the newest version. Any other
action (a chat message, Undo, choosing the diagram again) returns to the current state.

## Compare

**Vergleichen** shows what changed between two versions, or between a version and the current
state, in one picture. Choose **Von** (the older version; preselected is the version picked in
the selector) and **Bis** (*Aktueller Stand* by default) and confirm. The canvas then shows the
newer state plus everything that was removed, with a coloured border:

| Colour | Meaning |
|---|---|
| Green | New element or edge |
| Red, dashed and faded | Removed element or edge, drawn where it used to be (at the top level if its container is gone) |
| Yellow | Changed: name, kind, container or properties of an element; label or kind of an edge |

The banner above the canvas sums it up (*Vergleich v3 → aktueller Stand: +1 neu · −2 entfernt ·
~1 geändert*) and mentions a changed title or layout direction. The comparison respects the focus,
so you can compare just one container. While it is shown, clicking elements does nothing; SVG,
PNG and Mermaid exports contain the comparison. **Beenden**, or any other action (a chat message,
an edit, choosing a version or a focus), returns to the current state.

Sequence diagrams are compared the same way: new, removed and changed lifelines, messages, frames, operands and
activations get the colours. A removed element is drawn where it used to be: after the element that came before
it, or at the start of its former frame section. A message that only moved (to another place or into another
frame) counts as changed.

## Restore

**Wiederherstellen** makes the previewed version the current state. A new version with source
`restore` is recorded, labelled *Restored version n (…)*, so the history stays linear and you can
always get back to where you were before the restore.

## Undo

**Undo** restores the most recent version whose content differs from the current state. Manual
snapshots duplicate the state they were taken from and are skipped, so Undo always undoes a real
change. After an undo the next Undo continues backwards from the version that was restored, so
repeated presses walk back through the history one change at a time. The button is disabled when
there is nothing to undo.

## Redo

**Redo** re-applies the state that the last Undo replaced, again as a new version (source `redo`).
After several Undos, Redo walks forward again one step per press. As in any editor, a new change
after an Undo (a chat turn, an inspector edit, a restored version) ends the possibility to redo;
saving a manual version does not. The button is disabled when there is nothing to redo.

Keyboard: ++ctrl+z++ (++cmd+z++ on macOS) is Undo, ++ctrl+shift+z++ or ++ctrl+y++ is Redo. They
act only while the cursor is not in a text field, so undoing typing in the chat box still works
as usual.

!!! info "Undo and redo are restores"
    Both are implemented as a restore of a computed target version, so they appear in the history
    as `undo` and `redo` entries. Undos made before Redo existed are recorded as `restore` and
    cannot be redone; restore the newest `auto` version in the selector instead.

## Editing in several tabs at once

Two tabs, two people, or a running chat turn plus an edit in the inspector can change the same
diagram at the same time. No change is lost: when another change was saved in between, Arcviz applies
yours again on top of it (an undo then undoes the newest change). A chat turn that is still running
continues on the combined state; if the diagram changed after the turn's last operation, the turn
creates no version of its own, and its changes are part of the next version. Only when the diagram
keeps changing for several attempts in a row does an action fail with *try again*; nothing of it was
saved.

## Git history

Optionally, Arcviz mirrors the history into a Git repository (set `ARCVIZ_GIT_DIR`, see
[Configuration](../reference/configuration.md#git-mirror-optional)). Every version becomes a commit:

- `diagrams/<id>/diagram.json` holds the model, `diagrams/<id>/diagram.mmd` the Mermaid source, so
  `git diff` shows what changed in readable form.
- The commit subject is the version label, i.e. your chat instruction; the body lists the applied
  operations and the Arcviz version number.
- Manual snapshots of an unchanged state create a version but no commit; restores and deletions
  are committed too.
- With `ARCVIZ_GIT_PUSH=1` every commit is pushed. A failed push never blocks a chat turn; the
  error is shown in `GET /api/config`.

The JSON files under the data directory remain the source Arcviz reads from; the Git repository is
a second record for review, diffing and sharing.

## Where versions live

```
data/diagrams/<id>/versions/0001.json
data/diagrams/<id>/versions/0002.json
...
```

Each file contains the metadata (number, label, source, time, element and edge counts, a content
fingerprint) and the full model. Copying the directory is a complete backup.

A diagram's version list shows the newest 50 versions; **Ältere Versionen laden …** at its end loads the next older
ones. An administrator can let Arcviz delete old versions (`ARCVIZ_VERSIONS_KEEP`, off by default): then a diagram
keeps its newest versions, its first one, everything undo and redo still need, and your manual snapshots.
