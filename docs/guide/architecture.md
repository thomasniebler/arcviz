# Architecture: drivers, scenarios, decisions, stakeholders

**Architektur** in the navigator (or the house icon in the left rail) opens the architecture work of the open project as a page with four tabs. The address bar follows the tab (`#/architecture/scenarios`), so you can bookmark it and the back button returns to the previous tab; the chat is hidden while the page is open, and the rail's first icon brings back the diagram. Everything here belongs
to the project (not to a diagram), is exported and imported with it, and is known to both assistants. Viewers see
everything but change nothing.

Every tab works the same way: a **list of cards** on the left, the **editor** on the right. Click a card (or move with ↑/↓
in the list) to edit it; with nothing selected the editor is the form for a new item, and the **+** button next to the
filter field starts another one. The field above the list filters as you type (every word must occur). **Speichern**
stores, **Zurücksetzen** (or Esc) returns to the stored values; if you switch to another card, another tab or leave the
page with unsaved changes you are asked once whether to discard them.

## Treiber (drivers)

What the architecture has to achieve or respect. Each driver has a title, a kind (**Qualitätsziel**,
**Randbedingung**, **Anforderung**, **Risiko**), a description (best measurable: "99,9 % Verfügbarkeit pro Monat") and
the project elements it concerns. Its priority has two ratings, as in an ATAM utility tree: **Wichtigkeit** for the
business and technical **Risiko** (hoch, mittel, niedrig). The list is sorted by priority; the badge shows both
ratings (`H/M` = importance high, risk medium), ↑ and ↓ reorder drivers with the same ratings.

### Außer Kraft setzen, then delete

Drivers, scenarios and decisions shape what the assistants and the reviews say, so deleting one takes two steps. **Außer
Kraft setzen** (drivers and scenarios) keeps the object in the project but takes it out of the assistants' prompts, the
review rules, the coverage of requirements and the scenario analysis; **Wieder aktivieren** undoes it. Retired objects
are hidden in the lists until you tick **Außer Kraft anzeigen**, and a new scenario or decision cannot point to a retired
driver. Only then **Löschen** is enabled (hover for the reason while it is not). A decision is deleted like this when it
is a draft, rejected or superseded; an accepted decision must be rejected or superseded first. The assistants cannot
retire anything; they can delete a driver you retired, when you ask for it.

## Szenarien (quality scenarios)

A quality scenario makes a driver concrete, in the six parts of ATAM:

| Part | Question | Example |
|---|---|---|
| Quelle | Who or what causes it? | the cloud provider |
| Stimulus | What happens? ("Was, wenn …") | region A fails completely |
| Umgebung | Under which conditions? | normal operation, working hours |
| Artefakt | What is affected? | the whole system |
| Reaktion | How should the system respond? | traffic moves to region B |
| Reaktionsmaß | How is the response measured? | less than 5 minutes of downtime |

A scenario serves **at most one driver**; a driver usually has several. Mark the scenarios that state a driver's core
goals as **Kernszenario** (★): the assistants see them next to their driver.

**ISO-25010-Qualität**: assign each scenario to one, at most two of the nine quality characteristics of ISO/IEC 25010
(for example *Zuverlässigkeit* or *Informationssicherheit*). **Vorschlagen** asks the model to propose them from the text
of the scenario (one call, nothing is stored); it only pre-ticks the boxes, and you can always choose differently, because
a suggestion can be wrong. The characteristics appear on the scenario cards; a driver card shows the ones its scenarios
cover. The project review points out scenarios without one.

**Analysieren** plays the scenario through (one call of the model; nothing is stored): what happens today, the effect
on each driver (*unterstützt*, *gefährdet*, *verletzt*), the affected elements, sensitivity points and trade-offs, and
two to four options with pros and cons; the recommended one is framed. For each option:

- **Als Entscheidung festhalten** turns the analysis into a decision draft (below) with all options, this one chosen.
- **Als Vorschlag umsetzen** (when the option comes with an instruction) sends it to the chat as a preview, which you
  confirm or discard as usual.

## Entscheidungen (decisions)

Architecture decision records (ADRs), numbered ADR-1, ADR-2, …, listed by status: **Angenommen**, **Entwurf**,
**Ersetzt**, **Verworfen**. A decision from an analysis starts as a draft with its context (the scenario and the
analysis), the options with pros and cons, the decision ("Gewählt: … Begründung: …", complete the reasoning) and its
consequences. Edit the texts, then set the status to **Angenommen** and save. **Neue Entscheidung…** records one without
an analysis. A decision that a newer one replaces gets the status **Ersetzt** and names the newer one.
**Als Markdown** downloads the ADR as a Markdown file in the layout of [MADR 4](https://adr.github.io/madr/) (context and
problem statement, decision drivers, considered options, decision outcome, consequences, pros and cons of the options),
for example for a repository.

**Testbarkeit.** Every decision rests on assumptions ("the failover takes under a minute", "the team can operate
Kubernetes"). In the group *Testbarkeit* of the editor, list them, say for each how it could be tested cheaply and
what result would count, estimate the effort of that test (gering, mittel, hoch) and record what you know so far
(*ungetestet*, *bestätigt*, *widerlegt*, with the evidence). *Widerlegt, wenn …* names the observation that would make
you reverse the decision. **Vorschlag vom Assistenten** proposes assumptions and tests from the text of the decision;
it adds rows you can edit or remove and stores nothing until you save. The ADR export gets a section *Annahmen und
Testbarkeit*; an accepted decision without assumptions shows *ohne Annahmen* on its card.

**Accepted decisions bind.** The assistants know them: when a request would contradict one, they say which (ADR-<n>)
and ask before changing anything. The AI review of a diagram and of the project reports a finding *"Widerspricht
ADR-<n>: …"* when a diagram contradicts one. Drafts, rejected and superseded decisions do not bind.

## Stakeholder

Who has a stake in the architecture: name, role, concerns, **Einfluss** (hoch, mittel, niedrig) and the drivers they
care about. The assistants and the analysis say whose concerns an option serves or hurts.

## In the chat

The assistants know the drivers with their core scenarios, the stakeholders and the accepted decisions. They can list
scenarios, decisions and stakeholders and, when you ask, record a scenario (*"Lege ein Szenario an: Was, wenn sich die
Last verzehnfacht?"*), drivers too. Decisions are made in the dialog, not by the assistant.
