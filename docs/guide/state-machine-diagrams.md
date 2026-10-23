# State machine diagrams

A state machine diagram (type **Zustandsdiagramm**) shows the **lifecycle of one thing**: an order, a ticket, a session. It
lists the states the thing can be in and the events that move it from one state to the next. It follows the *State Machines*
chapter of UML 2.5.1. Create one with **Neu → Typ: Zustandsdiagramm**, either empty or from the template
**Bestellung** (`order-lifecycle`).

## Elements

| Kind | Drawn as | Use it for |
|---|---|---|
| `state` | rounded box with the name | a situation the thing stays in ("Bezahlt"); optional lines `entry / …`, `do / …`, `exit / …` below the name |
| `composite_state` | box around its contents | a state that contains states, or parallel regions |
| `state_region` | dashed box inside a composite state | one of several parts that run in parallel |
| `initial_pseudostate` | filled black dot | where the machine (or a composite state) starts |
| `final_state` | bull's eye | the end |
| `terminate_pseudostate` | circle with a cross | stops the machine at once |
| `choice_pseudostate` | diamond | a decision made on arrival; the conditions go on the outgoing transitions |
| `junction_pseudostate` | small dot | merges or splits transition paths |
| `fork_pseudostate` / `join_pseudostate` | black bar | split into / merge parallel regions |
| `shallow_history_pseudostate`, `deep_history_pseudostate` | circle `H` / `H*` | resume the last state of a composite state |

## Transitions

A transition points from a state to the next one and is drawn as `trigger [guard] / effect`; every part is optional. The
**trigger** is the transition's label (the event that fires it: "bezahlen"), the **guard** the condition in brackets
("Betrag ok"), the **effect** what happens on the way. Set them in the inspector (the effect appears in the property table
of the edge) or ask in the chat ("von Neu nach Bezahlt, wenn der Betrag stimmt, mit dem Effekt Rechnung senden").

A state's `entry`, `do` and `exit` lines are properties of the state with these names; they are drawn below its name.

Arcviz keeps the diagram well-formed: nothing leaves a final state, nothing enters a start dot, a region lives only in a
composite state (which then holds regions only), and a transition out of a start dot has no trigger and no condition. A
request that breaks a rule is refused with the reason. To turn a state into a composite state, change its kind.

## Review

**Prüfen** checks the diagram: a scope without a start state, several start states, unreachable states, states without a way
out, transitions on the same event without conditions, choices without an *else* branch, and transitions between parallel
regions.

## What works as for other diagrams

Composite states and regions can be focused, versions compared, the layout direction changed, and the diagram exported as
SVG, PNG, Mermaid source, JSON and PlantUML. In the PlantUML file start and end states, choices, forks, joins and history
states are states with the matching stereotype. A *terminate* state has no PlantUML counterpart and is exported as an end
state with a comment, a *junction* as a choice.
