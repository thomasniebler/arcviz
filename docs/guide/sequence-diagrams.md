# Sequence diagrams

A sequence diagram (type **Sequenzdiagramm**) shows an interaction over time: participants side by
side, and the messages they exchange from top to bottom. It follows the *Interactions* chapter of
UML 2.5.1 and uses its terms. Create one with **Neu → Typ: Sequenzdiagramm**, either empty or from the
template **Checkout** (`checkout`), which shows every construct below.

## Elements

| Element | Drawn as | Notes |
|---|---|---|
| **Lifeline** | box `name : Typ` with a dashed line, or a stick figure for an actor | left to right in the order you add them; ask to move one ("setze die Datenbank ganz nach rechts") |
| **Message** | arrow between lifelines, its name above | see the kinds below; a message to the sender itself loops back |
| **Execution specification** | narrow bar on a lifeline | from the message that starts it to the one that finishes it, e.g. from a call to its reply |
| **Combined fragment** | frame with the operator in the corner | `alt`, `opt`, `loop`, `par`, `break`, `critical`, `neg`, `assert`, `strict`, `seq`, `ignore`, `consider`; its operands are separated by dashed lines and carry guards such as `[ok]` or `[else]` |
| **Interaction use** | frame with `ref` and a name | refers to another interaction, e.g. "Versand beauftragen" |
| **Destruction** | X at the end of a lifeline | the participant ends here |

Message kinds (UML *messageSort*):

| Kind | Line |
|---|---|
| `synchCall` | solid, filled arrowhead (the caller waits) |
| `asynchCall`, `asynchSignal` | solid, open arrowhead |
| `reply` | dashed, open arrowhead |
| `createMessage` | dashed, open arrowhead to the head of the new lifeline, which starts at that height |
| `deleteMessage` | solid, filled arrowhead ending in an X: the receiver is destroyed |

A **found** message (sender unknown) starts at a black dot; a **lost** message (receiver unknown) ends
at one.

## Working with the chat

Describe the scenario in your words, for example "Der Kunde ruft checkout beim Shop auf, der Shop
fragt die Zahlung an; wenn sie scheitert, zeigt er einen Fehler, sonst legt er eine Bestellung an".
The assistant adds lifelines, messages and fragments and can wrap existing messages in a fragment
("pack die Zahlung in eine Schleife"). Ids such as `m3` or `cf1` are shown in the chat and the
inspector; you can refer to them.

## Editing directly

Click any element (lifeline, message, frame, the guard strip of an operand, an activation bar, an X)
to open the inspector for it: rename, change the message kind, sender or receiver, the operator or the
covered lifelines of a frame, the guard of an operand, or delete it. Deleting a lifeline also deletes
its messages and activations; deleting a message also deletes activations that start or end at it.

## What works as for other diagrams

Sequence diagrams have no focus/drill-down and no layout direction. SVG, PNG, JSON and PlantUML export,
import, versions, version comparison ([Versions](versions.md#compare)), undo/redo and the preview mode work as
for other diagrams.

The PlantUML file is a PlantUML sequence diagram: participants and actors, the messages with the same arrows,
found and lost messages from and to a dot, `create`/`destroy`, the frames (`neg`, `assert`, `strict`, `seq`,
`ignore` and `consider` have no PlantUML keyword and become a `group` named after the operator), `ref over` and
the activations as `activate`/`deactivate`. PlantUML sizes a frame by its messages, so the lifelines a frame
covers are not kept.
