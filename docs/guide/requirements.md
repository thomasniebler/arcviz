# Requirements

The **Anforderungen** view is where a list of requirements becomes quality scenarios and architecture drivers. Open it with
**Anforderungen** in the project navigator; it replaces the diagram, the chat pane switches to the **Anforderungs-Chat**, and
**Zum Diagramm** takes you back. Everything is per project.

## The pipeline

A requirement moves through four steps, shown as buttons with counts above the table (a click filters by that step):

| Step | Meaning |
|---|---|
| **Roh** | As someone said it; nothing else is known |
| **Vorschlag** | An assistant proposed a normalised form; it is not in force until you accept it |
| **Verfeinert** | You accepted a clear statement; it can be derived from |
| **Abgeleitet** | A driver, a scenario or a decision names it |

**Verworfen** and **Duplikat** are the two ways out. The original wording of a requirement never changes; the statement you accept
stands next to it.

## Pour in

- **Importieren…** takes a pasted list or a file: one requirement per line (bullets and numbers are removed, `REQ-12:` becomes the
  external id), Markdown (headings become the *Thema*), or CSV with a column *text* or *Anforderung* and optionally id, source,
  theme, priority (Muss, Soll, Kann) and stakeholder. **Prüfen** shows what would happen and writes nothing. Importing a file
  twice adds nothing: the same text or external id is skipped. Up to 500 requirements at once.
- In the **Anforderungs-Chat** you can also say what to record ("Nimm auf: …"). The assistant adds requirements as you said
  them, never rewords them, and points you to the import for long lists. Coding agents add requirements over MCP the same way
  ([Coding agents](agents.md)).

## Refine and accept

**Rohe aufbereiten** (or **Ausgewählte aufbereiten** for ticked rows) asks the model for a clear sentence, the kind (functional,
quality, constraint …), a measurable criterion, a theme, stakeholders and **Befunde** (vague wording, missing measure, a possible
duplicate or conflict). It works in chunks of 25 and goes on until nothing raw is left; **Abbrechen** stops after the running chunk.
The suggestion appears below the original text in the table and in the detail panel next to the fields in force.

You decide: **Übernehmen** takes the suggestions of the ticked rows, **Unauffällige übernehmen** all suggestions without an open
conflict or duplicate, and in the detail panel you take a single field or everything, edit the fields yourself, mark a requirement
as verfeinert, verwerfen, a duplicate of another, or delete it (after a question; the references in drivers, scenarios and decisions go with it).
Tick several rows and use **Löschen** in the toolbar to delete them at once: one question names the count and how many are referenced. If a suggestion was replaced after you looked at it (another
refinement, an agent), accepting says so and takes nothing: look at the new one first.

## Derive and apply

**Ableiten** takes refined requirements that nothing covers yet and asks the model for drivers and quality scenarios (with their six
parts) that serve them, reusing drivers and scenarios that exist by title and respecting the accepted decisions. The result is not
written to the project: it appears under **Vorschläge** as a proposal with one card per item. Tick the items you want and
**Ausgewählte übernehmen** (an item that needs another, a scenario for a new driver, pulls it in), or take everything; discard what
you do not want. Applying links the new objects to the requirements they came from.

## Link requirements to drivers, scenarios and decisions

Derivation sets the links for you. By hand, the driver rows, the scenario form and the decision form of the **Architektur** page have
a multi-select **Anforderungen**; the lists then show "deckt: req1, req3". The detail panel of a requirement edits its stakeholders
and elements and shows which drivers, scenarios and decisions name it.

## Coverage

The column **Abdeckung** shows how far a refined requirement is covered: **offen** (nothing names it), **Treiber**, **Szenario** or
**Entscheidung** (the highest). **Matrix** lists the refined requirements with the drivers, scenarios and decisions that name them.
The project review ([Architecture review](review.md)) reports requirements that stay raw, uncovered requirements per theme,
quality requirements without a measure, open conflicts and near-duplicates, scenarios without a requirement, accepted decisions
without a basis, and influential stakeholders no requirement names.

## Who may do what

Viewers see the table, the detail and the matrix; editors change them. An assistant or a coding agent can add requirements and
suggest refinements, but it cannot accept a suggestion, delete a requirement, change its status or apply a proposal: that is always
you.
