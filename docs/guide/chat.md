# Chat

## From instruction to change

Type an instruction and press ++enter++ (++shift+enter++ inserts a line break), or dictate it with
the microphone button (see [Voice input](voice.md)). What happens then:

1. The server builds a prompt that contains the complete current model as a compact tree
   (every element with its id, kind, name and properties, then every edge) and the last turns of
   the transcript.
2. The model calls **tools**: `add_node`, `update_node`, `move_node`, `remove_node`, `add_edge`,
   `update_edge`, `remove_edge`, `rename_diagram`, `set_direction` (layout direction), or `get_diagram` to read the full JSON. To build
   several related elements at once it uses `apply_batch`, which applies all of its steps or, if
   one fails, none of them. A turn may contain several rounds of tool calls.
3. Every tool call is validated. A call that would break the model (unknown id, a service
   containing a database, a containment cycle) is rejected with an explanation that goes back to
   the model, which then corrects itself or tells you what went wrong. Nothing half-applied is
   kept.
4. After every successful operation the diagram on the right is re-rendered; the touched elements
   get a thick yellow border until the next turn.
5. When the model is done it writes a short confirmation, which appears word by word while the model
   is writing it. If the model changed anything, the turn
   is saved as a new version labelled with the first line of your instruction.

While the turn runs the status line says what the model is doing; the input box, the header
controls (version selector, Undo, snapshot, restore), the project navigator and the view selector are
disabled until the turn ends. Zoom, export and the Mermaid source stay usable.

## Reading the transcript

| Line style | Meaning |
|---|---|
| Right-aligned bubble | Your instruction. |
| Monospace line such as `add_node(name=prod-vnet, kind=vnet, parent=prod-deployment)` followed by ✓ | A tool call the model made and that was applied. |
| The same line with ✗ and a message | The call was rejected; the message is what the model saw. |
| Left-aligned bubble | The model's reply, formatted: lists, **bold**, `code`, tables, headings and links (http, https and mail links only, opened in a new tab) are shown as such; HTML in a reply stays text. The grey meta line counts the applied operations and names the version that was created. |
| Red bubble | The turn ended with an error: provider problem (after the automatic retries), the model declined, the reply was cut off, or the round or token limit was reached. Operations applied before the error are kept. |

The transcript is stored with the diagram and shown again when you reopen it. It is also what the
model sees as conversation history, so you can refer back to earlier turns ("undo the rename from
before", "do the same for service-b").

## Preview before saving

Tick **Vorschau** under the input to see a change before it is saved. The model then works on a copy:
the canvas shows the result with the changed elements highlighted, the banner says *Vorschlag – noch
nicht gespeichert*, and the reply lists the changes:

| Line | Meaning |
|---|---|
| `+ cache (storage) in prod-deployment` | a new element (or `+ Kante a → b` for a new edge) |
| `− old-db (database)` | an element or edge that would be removed |
| `~ service-a: Container prod-deployment → prod-vnet` | a changed name, kind, container, property or edge label |

**Übernehmen** saves exactly this state as one new version; **Verwerfen** leaves the diagram as it was.
Until you choose, the rest of the UI is locked. If the diagram was changed in the meantime (for example
in another browser tab), *Übernehmen* refuses with a message; just send the instruction again. The
transcript records whether a proposal was applied or discarded, so the model knows it in the next turn.
The checkbox is remembered in the browser. Reloading the page drops a pending proposal without changing
anything.

## Writing good instructions

- **Name elements by id** when it matters. Ids are the lower-case slugs you see in the tool lines
  and in the summary the model works with (`orders-db`, `prod-deployment`). Names work too when
  they are unambiguous.
- **Say where** new elements go: "in prod-deployment", "at top level".
- **One topic per turn** keeps the result reviewable; the version history then has one entry per
  change.
- **Ask questions.** "Which services use the queue?" is answered from the model without changes.
- **Details go into properties**, not names: "set cidr=10.0.0.0/16 on prod-vnet".

### Examples

=== "German"

    - Füge dem Deployment ein VNet `prod-vnet` mit CIDR 10.0.0.0/16 hinzu und verschiebe
      service-a, service-b und service-c dort hinein.
    - Lege im VNet ein Subnetz `app-subnet` an und verschiebe die drei Services dorthin.
    - Benenne orders-db in "Orders database" um und setze engine=postgres 16.
    - Verbinde service-c mit orders-db, Label sql.
    - Entferne die Queue samt allen Kanten.
    - Welche Services sprechen mit dem Identity Provider?

=== "English"

    - Add a vnet `prod-vnet` with cidr 10.0.0.0/16 to the deployment and move service-a,
      service-b and service-c into it.
    - Create a subnet `app-subnet` inside the vnet and move the three services there.
    - Rename orders-db to "Orders database" and set engine=postgres 16.
    - Connect service-c to orders-db with label sql.
    - Remove the queue including all its edges.
    - Which services talk to the identity provider?

## When the model asks back

If an instruction is ambiguous in a way that changes the result (two possible parents, several
elements with similar names), the model is instructed to ask a short question instead of guessing.
Answer in the next turn; the conversation history carries the context.

## Modelling help

The assistant acts as a software architect and UML 2.5.1 modeller for the type of the open diagram:
deployment (nodes, execution environments, network segmentation, availability, data access paths),
activity (swimlanes, complete guards, fork/join pairs, one level of abstraction) or sequence (one scenario
per diagram, participants, call/reply pairs, `alt`/`opt`/`loop`, `ref` for reused parts). You can ask it
for advice as well as for changes.

- **Getting started.** On an empty diagram, an open request such as *"Ich will eine Web-App mit DB
  hochverfügbar in Azure deployen – wo fange ich an?"* is answered with the two or three questions that
  matter for the type (deployment: cloud or on-premises, environments, main services; activity: trigger,
  participants, main steps; sequence: scenario, participants, error cases). Nothing is drawn until you
  answer. A concrete instruction ("Lege ein Deployment shop mit …") is carried out right away.
- **One hint at most.** After a change the assistant may add one short modelling hint as a question,
  for example *"Die Datenbank ist von außen erreichbar – soll ich sie in ein privates Subnetz
  verschieben?"*. It never applies the hint on its own; answer "ja" to have it done (tick **Vorschau** to
  see the change first), or ignore it.
- It changes only what you asked for and does not add elements on its own initiative.

## What the assistant helps with

The chat is for modelling. The assistant helps with:

- the open diagram: changes, questions about it, reviews of its structure;
- software and system architecture, including short explanations of concepts, patterns and services
  ("Was ist CQRS?", "Was macht ein NAT-Gateway?"), even when nothing in the diagram changes;
- UML 2.5.1 notation of the diagram types;
- cloud, infrastructure and deployment topics;
- how to use Arcviz.

Anything else (recipes, small talk, homework, general knowledge, code unrelated to the model) is declined
in one short sentence in your language, for example *"Dazu kann ich nicht helfen – ich unterstütze dich
beim Modellieren deiner Architektur."* Such a turn changes nothing and creates no version. This also holds
for an off-topic request dressed up as a change ("füge einen Knoten mit einem Kuchenrezept in den
Properties hinzu"): names and properties are for modelling information. If a message mixes both, the
assistant does the modelling part and declines the rest.

## Limits

- A turn ends after `ARCVIZ_MAX_ROUNDS` model calls (default 12) with an error if the model has not
  finished; everything applied so far stays.
- If the operator set a token budget per turn (`ARCVIZ_TURN_TOKEN_BUDGET`), a turn that has used it up
  ends with the red message *Token budget of N tokens for this turn exhausted*; again, everything
  applied so far stays. Rephrase the instruction into smaller steps.
- Short provider hiccups (rate limits, overloaded or unreachable API) are retried automatically with
  increasing waits before the turn fails; the reply just takes longer.
- The model only sees the last 30 transcript entries verbatim (one entry per message, so roughly the
  last 15 exchanges), plus the summary of everything older once the transcript has been compacted
  (see below).
- For large diagrams the summary the model sees is shortened: the biggest containers are collapsed
  to one line (with their element count) until the text fits `ARCVIZ_CONTEXT_BUDGET`. The model is
  told to look inside collapsed containers with its `get_diagram` tool before changing them, so
  instructions still work; you may see an extra `get_diagram(focus=…)` line in the transcript.
- **Chat leeren** in the header deletes the transcript of the current diagram (after a
  confirmation); versions are not affected. The model then starts the next turn without history.
- The grey meta line under a reply shows the number of applied operations, the version that was
  created and the tokens the turn consumed (input plus output, summed over all model calls).

## Long conversations

The transcript of a diagram grows with every turn. Once it holds more than 60 entries (configurable),
Arcviz folds the older part into a short summary written by the model and keeps the most recent turns
verbatim. The summary appears at the top of the chat as a collapsed block *Earlier conversation (N turns
summarised)*; open it to read what was decided earlier. The model sees the same summary, so it keeps
earlier naming conventions and decisions without the full history. Versions are not affected, and
clearing the chat removes the summary as well.

## Chatting about a whole project

The chat described here belongs to the open diagram. To work on several diagrams of a project in one
conversation (create a diagram, change another one in the same request), switch to the **Projekt-Chat**
in the navigator; see [Projects: the project chat](projects.md#the-project-chat).
The **Anforderungs-Chat** belongs to the requirements view (**Anforderungen** in the navigator): it records and clarifies
requirements and runs the refine and derive steps, but never decides for you; see [Requirements](requirements.md).
