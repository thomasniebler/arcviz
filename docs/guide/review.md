# Architecture review

The architecture review looks at the current diagram and tells you what is already good, what is
risky and what could be improved. Each finding names the elements it is about, and selecting it
highlights them on the canvas.

## Opening the review

Click **Review** in the canvas toolbar. A panel opens on the left of the canvas and the **rule
checks** run at once. Click **Review** again or **×** to close it.

| Button | What it does | Cost |
|---|---|---|
| **Regeln prüfen** | Runs the built-in rule checks again | none, instant, no AI |
| **KI-Review** | Rule checks plus a review by the language model; shows *KI prüft …* while it runs | one model call |

## Findings

Findings are grouped into **Risiken** (risks), **Verbesserungen** (suggestions) and **Stärken**
(strengths). Each shows its severity (*hoch*, *mittel*, *niedrig*, *Info*), the elements it concerns
and a short message; findings of the AI review carry the mark **KI**. The coloured bar on the left
repeats the severity: red high, yellow medium, blue low, green for strengths. After an AI review its
summary is shown above the list, and the status line says how many findings there are, how many
tokens the review used, and how many invalid AI findings were discarded.

Click a finding to highlight its elements (a glow) and edges (magenta) on the canvas; click it
again to remove the highlight. The highlight stays while the diagram is re-rendered (for example
after you focus a container) and disappears when you close the panel. Elements outside the current
focus are not highlighted.

## Rule checks

The rule checks know these patterns of a deployment diagram:

| Finding | Severity | When |
|---|---|---|
| Datenbank ohne Replikation | high | A database has no `replication` edge, no property such as `replicas=3`, `ha=true`, `zone_redundant=true`, and the platform does not replicate it (`redundancy=built_in`, or a managed kind such as Cosmos DB, DynamoDB or Spanner from a [kind pack](elements.md#cloud-icons-and-kind-packs)) |
| Externer Zugriff ohne Gateway | medium | Traffic goes from an `external` element straight to a `service`, `function` or `component` |
| Externes System direkt an einem Datenspeicher | high | An `external` element is connected to a `database`, `storage` or `queue` |
| Element ohne Verbindungen | low | A leaf element has no edge at all |
| Replizierte Datenbank (strength) | – | A database is replicated, by replicas or by the platform |
| Externer Zugriff über ein Gateway (strength) | – | Traffic from an `external` element enters through a `gateway` |

Which way traffic flows follows the arrow of the edge: an edge drawn without arrow heads or with
both counts in both directions, `deploys` edges are not traffic. Element kinds added through a
kinds file are not checked by the kind-specific rules.

For activity diagrams the rule checks look at the flow:

| Finding | Severity | When |
|---|---|---|
| Verzweigung mit Flüssen ohne Bedingung | medium | A decision has two or more outgoing flows and one of them has no guard |
| Aktivität ohne Startknoten | low | The diagram has no start node |
| Knoten vom Start aus nicht erreichbar | low | No path leads from a start node to the node |
| Verzweigung ohne else-Zweig | low | Every outgoing flow of a decision has a guard, but none is `else` |
| Parallelisierung ohne Zusammenführung | low | No join node follows a fork |
| Ablauf ohne Endknoten | low | From an action (or a loop of actions) no path leads to a final node |

For state machine diagrams they look at states and transitions:

| Finding | Severity | When |
|---|---|---|
| Bereich ohne Anfangszustand | low | A scope (top level, composite state, region) has states but no start state |
| Bereich mit mehreren Anfangszuständen | medium | A scope has more than one start state |
| Anfangszustand ohne genau einen Übergang | medium | A start state has no or several transitions |
| Zustand vom Anfang aus nicht erreichbar | medium | No path leads from the start to the state |
| Zustand ohne ausgehenden Übergang | low | A state has no way out, though the machine has an end state |
| Übergänge auf dasselbe Ereignis ohne Bedingung | medium | Two transitions out of one state react to the same event and not all have a guard |
| Entscheidung ohne else-Zweig | medium / low | A choice has no `else` branch (medium when transitions have no condition) |
| Übergang zwischen parallelen Regionen | low | A transition connects two parallel regions without a fork or join |
| Endzustand ohne eingehenden Übergang | low | An end state is never entered |

For class diagrams they look at the relationships and members:

| Finding | Severity | When |
|---|---|---|
| Pakete mit zyklischen Abhängigkeiten | medium | Classes of several packages refer to each other in a cycle |
| Teil mit mehreren Ganzen | medium | A class is part (composition) of more than one whole |
| Kompositionen im Kreis | medium | Compositions form a cycle |
| Doppeltes Attribut oder doppelte Operation | low | A member is listed twice |
| Klasse mit mehreren Oberklassen | low | A class specializes more than one class |
| Schnittstelle ohne realisierende Klasse | low | No class realizes an interface |
| Abstrakte Klasse ohne Unterklasse | low | An abstract class is never specialized |
| Klasse ohne Beziehung | low | A class has no relationship (in diagrams with at least three) |
| Klasse ohne Attribute und Operationen | low | A class or interface has no members yet |
| Beziehung ohne Multiplizitäten | low | An association has no multiplicity at either end |
| Aufzählung ohne Werte | low | An enumeration has no literals |

For use case diagrams they look at actors and specifications:

| Finding | Severity | When |
|---|---|---|
| Anwendungsfall ohne Akteur | medium | A use case that nobody includes or extends has no actor |
| Erweiterungen im Kreis | medium | Use cases extend each other in a cycle |
| Gleiche Namen | low | Two use cases, or two actors, have the same name |
| Akteur ohne Anwendungsfall | low | An actor has no edge |
| Anwendungsfall ohne Spezifikation | low | A use case has an empty note ("Spezifikation fehlt") |
| Erweiterung ohne Bedingung | low | An `extend` has no condition |
| Eingebundener Anwendungsfall nur einmal genutzt | low | Only one use case includes it |
| Anwendungsfall außerhalb des Systems | low | A use case stands outside the subject while the diagram has one |
| System ohne Anwendungsfall | low | A subject contains no use case |

For component diagrams they look at interfaces and dependencies:

| Finding | Severity | When |
|---|---|---|
| Benötigte Schnittstelle ohne Anbieter | medium | A component requires an interface that no component provides |
| Komponenten mit zyklischen Abhängigkeiten | medium | Components depend on each other in a cycle (also through interfaces) |
| Bereitgestellte Schnittstelle ohne Nutzer | low | An interface is provided and nobody requires it |
| Komponente mit vielen Abhängigkeiten | low | A component depends on more than seven others |
| Komponente ohne Beziehung | low | A component has no edge (in diagrams with at least three) |
| Port ohne Verbindung | low | No edge names a port, while other ports of the component are used |
| Teilsystem ohne Komponente | low | A subsystem is empty |

For sequence diagrams they look at calls and replies:

| Finding | Severity | When |
|---|---|---|
| Antwort ohne Aufruf | medium | A reply answers no earlier call between the same two lifelines |
| Synchroner Aufruf ohne Antwort | low | A synchronous call between two lifelines gets no reply |
| alt-Fragment mit nur einer Alternative | low | An `alt` has a single operand (use `opt`) |
| Fragment überdeckt eine zerstörte Lebenslinie | low | A fragment or `ref` after a lifeline's destruction still covers it |
| Ausführung ohne Ende trotz Antwort | low | An activation has no end although its call is answered |

UML leaves much of this open (a reply may be omitted, a fork may end in flow ends), so most of these are
low suggestions: ignore them where the diagram is meant that way. The alternatives of an `alt` are read
separately, so a call answered differently in each alternative counts as answered.

## AI review

**KI-Review** sends the diagram and the rule findings to the language model, which adds its own
strengths, risks and suggestions without repeating the rules. It only judges what the diagram
shows, so give elements meaningful kinds, properties (engine, sku, replicas, …) and edge details
(protocol, port) for a better review. The review is not saved: switching to another diagram or
reloading the page discards it (closing and reopening the panel keeps it). If the model fails or
answers with something unusable, the panel says *KI-Review fehlgeschlagen* and the rule findings
remain.

When the diagram changes while the panel is open (a chat turn, an edit in the inspector, undo), the
rule checks run again by themselves; AI findings stay but are greyed out with the note
*Diagramm geändert – KI-Review erneut ausführen*.

## Applying a suggestion

Risks and suggestions that come with a concrete improvement have a button **Als Vorschlag
umsetzen**. It sends the suggestion to the chat as an instruction (you see it as your message,
starting with *Setze diesen Vorschlag aus dem Architektur-Review um*) and always runs it as a
[preview](chat.md#preview-before-saving), whether or not **Vorschau** is ticked: the model makes the
change on a copy, the canvas highlights it and the reply lists it. Click **Übernehmen** to save it
as one version or **Verwerfen** to drop it. After saving, the open review panel checks the diagram
again, so a fixed rule finding disappears.

The button is disabled while a turn or a proposal is pending, and on AI findings that are out of
date because the diagram changed; run **KI-Review** again first.

## Reviewing a whole project

**Projekt-Review** in the navigator opens the same panel for the **open project** instead of the shown
diagram. It collects the rule findings of every diagram of the project and adds checks that only make
sense for the project as a whole:

| Check | Finding |
|---|---|
| Empty section | a section (with its subsections) holds no diagram |
| Section without overview | a section holds two or more diagrams and none of them is an overview; a diagram counts as overview when its title contains *Übersicht*, *Overview*, *Kontext*, *Context*, *Landschaft*, *Landscape*, *Gesamtbild* or *Big Picture* |
| Missing diagram types | a section shows only the structure (deployment diagrams) or only processes (activity, sequence, state machine and use case diagrams); class and component diagrams count as structure |
| Duplicate titles | two diagrams have the same or almost the same title |
| Empty diagram | a diagram has no elements yet |

When the project has a shared model ([Shared project model](shared-model.md)), the review also compares the
diagrams with it and with each other. With **relations** in the model it finds *gaps*: a relation that no deployment
diagram shows, a connection between two project elements that no relation stands behind (link it, or create the
relation), and a diagram declared **complete** that leaves out a relation between elements it shows. And it finds
*contradictions*: a relation whose protocol, port, direction or sync/async differs between the model and a diagram or
between two diagrams (the message names both), an element that runs on two different nodes, and an element placed
inside different elements in different diagrams or inside another one than in the model. A diagram that leaves
something out says nothing about it, unless it declares itself complete.

When the project has **requirements** (they are entered through the API for now; the **Anforderungen** view follows), the
review also traces them: a requirement that stayed raw for more than a day, refined requirements that no driver,
scenario or decision names (one finding per theme), requirements without a stakeholder, quality requirements without a
measure, open conflicts and near-duplicates between requirements, scenarios that name no requirement, an accepted
decision that names neither a driver nor a requirement, and influential stakeholders that no requirement names.
These findings carry no resolution buttons; they say what to do next.

Such a finding shows the ways to resolve it as buttons; the review never picks one for you:

- **… (Vorschlag)** opens the diagram and lets the assistant propose the change (for example drawing the missing
  connection or adapting a protocol); you see the preview and click **Übernehmen** or **Verwerfen**.
- **Hier bewusst ausblenden** marks the relation as intentionally left out of a complete diagram.
- **Im Modell …**, **Beziehung anlegen**, **Mit Beziehung … verknüpfen** and **Beziehung aus dem Modell entfernen**
  change the project model (removing asks first).
- **Modell abgleichen** repairs links that no longer fit their relation.

The findings are checked again afterwards. Viewers see the findings but no buttons.

**KI-Review** asks the language model once about the whole project: whether structure and processes fit
together, whether names are consistent across diagrams, and what is missing (one model call, also for
large projects; very large diagrams are shortened, which the status line says).

Each finding shows what it is about: *Diagramm: …*, *Abschnitt: …* or *Projekt*. Selecting a finding of
a diagram opens that diagram and highlights the elements; **Als Vorschlag umsetzen** opens the diagram
and previews the change there. The project review keeps its findings while you switch between the
project's diagrams; it closes when you switch projects. When you change a diagram of the project, the rule
checks run again and the AI findings are greyed out as outdated; run **KI-Review** again. Nothing is stored.

## The steward

On the **Architektur** page the owner can switch on the *Steward* for a project (when the installation allows it). After you
changed the project and a few minutes passed, the steward looks at it again: it runs the review rules and asks the assistant for
fixes the rules cannot see and for **next steps** that serve the vision or a driver. A **second model** checks every proposal of
the assistant against the objects it names; only what it confirms reaches you. The result is one entry in the proposal list
(**Vorschläge**) with **Korrekturen** and **Nächste Schritte**, each with its reason and the objects it is about. Nothing is applied
without you; what you discard does not come back. **Jetzt prüfen** checks at once (editors).
