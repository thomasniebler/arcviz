# Architecture review

Source: `arcviz/rules.py`, `arcviz/review.py`. Tests: `tests/test_rules.py`, `tests/test_review.py`.
Design: [Rule-based architecture checks](../architecture/specs/rule-checks.md) (ADR-0022, ADR-0023),
[LLM architecture review](../architecture/specs/llm-review.md) (ADR-0024, ADR-0025).

The architecture review reports *findings* about a diagram: what is already good, what is risky and
what could be improved. Deterministic rule checks produce findings without an LLM; the LLM review
adds the model's findings in the same schema. The UI is described in
[Frontend](frontend.md#architecture-review).

## Findings

```python
class Finding(BaseModel):
    id: str  # unique within one result
    source: Literal["rule", "llm"]
    rule: str | None  # rule id for rule findings
    category: Literal["strength", "risk", "suggestion"]
    severity: Literal["info", "low", "medium", "high"]
    message: str  # German, shown as written
    elements: list[str]  # node ids
    edges: list[str]  # edge ids
    suggestion: str | None  # how to improve it, phrased as an instruction
```

Strengths have severity `info`; risks and suggestions `low`, `medium` or `high`. `sort_key` orders
findings by severity (`high` first), category (`risk`, `suggestion`, `strength`), rule id, element ids
and edge ids. Rule finding ids are `<rule>:<element ids joined by +>[:<edge ids joined by +>]`.

## Rule checks

`RULES` maps a diagram type to its rules (`Rule(id, title, category, check)`, `check: Diagram ->
Iterable[Finding]`); `register(type, rule)` appends one (a duplicate id for the same type is a
`ValueError`). `diagram_type(diagram)` is `diagram.type` if the diagram has a non-empty `type`
attribute, else `deployment` (diagrams have no type field yet). `rules_for(type)` is the list for a
type (empty for an unknown type); `run_checks(diagram)` runs all rules of the diagram's type and
sorts the findings. Rules are pure: they read the diagram and never change it.

Traffic along an edge: edges of kind `deploys` carry none; otherwise traffic flows from source to
target, and also back when the effective direction (`mermaid.edge_direction`) is `both` or `none`.

Rules for `deployment` diagrams, in registration order:

| Rule | Category | Severity | Finding for | Elements | Edges |
|---|---|---|---|---|---|
| `database-without-replication` | risk | high | each database (kind `database`, or a configured kind whose `role` is `database`) without a `replication` edge, without a replication property and without built-in redundancy (the node's `redundancy=built_in`, or its kind's trait unless the node says `redundancy=none`) | the database | – |
| `public-entry-without-gateway` | risk | medium | each flow from an `external` node into a `service`, `function` or `component` (by role: a configured kind counts as the kind of its `role`) | external, target | the edge |
| `external-to-datastore` | risk | high | each non-`deploys` edge between an `external` node and a `database`, `storage` or `queue` (either direction) | external, data store | the edge |
| `unconnected-element` | suggestion | low | each leaf node (not a container kind, no children) that no edge touches | the node | – |
| `replicated-database` | strength | info | each `database` node with a `replication` edge or a replication property | the database | its `replication` edges |
| `gateway-entry` | strength | info | each flow from an `external` node into a `gateway` | external, gateway | the edge |

A replication property is one of `replicas`, `replication`, `ha`, `high_availability`,
`zone_redundant`, `geo_redundant` whose value, trimmed and lower-cased, is not one of `""`, `0`, `1`,
`false`, `no`, `none`. Kinds from a kinds file are not known to the rules; their nodes are only
relevant to `unconnected-element`. Risk and suggestion findings carry a German `suggestion` that
names the elements with their ids.

Rules for `activity` diagrams (#123), in registration order. Flows are all edges; ActivityPartitions are
not nodes for these rules. A *decision* is a `decision_node` with at least two outgoing flows; a guard is
the edge property `guard`, `else` (or `[else]`, any case) is the else branch.

| Rule | Category | Severity | Finding for | Elements | Edges |
|---|---|---|---|---|---|
| `decision-without-guards` | risk | medium | each decision with an outgoing flow without guard | the decision | the unguarded flows |
| `missing-initial-node` | suggestion | low | a diagram with ActivityNodes but no `initial_node` | the nodes without incoming flow, or all nodes if every node has one | – |
| `unreachable-node` | suggestion | low | each ActivityNode not reachable from an `initial_node` (none when there is no `initial_node`) | the node | – |
| `decision-without-else` | suggestion | low | each decision whose outgoing flows all have guards, none `else` | the decision | its outgoing flows |
| `fork-without-join` | suggestion | low | each `fork_node` from which no `join_node` is reachable | the fork | – |
| `flow-without-final-node` | suggestion | low | each group of `opaque_action`s from which no final node is reachable and that the flow does not leave for another such action (a dead end, or a loop without exit) | the actions | – |

Rules for `state_machine` diagrams (`arcviz/state_rules.py`, [spec](../architecture/specs/state-machine-diagrams.md)), in registration
order. A *scope* is the top level, a region, or a composite state without regions; `state`, `composite_state` and the other
vertices are nodes, regions are not. A trigger is the edge label compared case-insensitively; a guard is the edge property
`guard`, `else` the else branch. Reachability starts at the top-level `initial_pseudostate`s; entering a node enters its
enclosing composite states and regions and so their initial pseudostates.

| Rule | Category | Severity | Finding for | Elements | Edges |
|---|---|---|---|---|---|
| `state-no-initial` | suggestion | low | each scope with a state and no `initial_pseudostate` | the scope node, or the first top-level state | – |
| `state-multiple-initial` | risk | medium | each scope with several initial pseudostates | the initial pseudostates | – |
| `state-initial-targets` | risk | medium | each initial pseudostate without exactly one outgoing transition | the pseudostate | its transitions |
| `state-unreachable` | risk | medium | each node not reachable (none without a top-level initial state; nodes inside an unreachable composite state are not repeated) | the node | – |
| `state-dead-end` | suggestion | low | each simple `state` without outgoing transition, in a machine with a final or terminate state, unless an enclosing state has one | the state | – |
| `state-nondeterministic` | risk | medium | each state with several transitions on one trigger (or several without trigger) of which one has no guard | the state | those transitions |
| `state-choice-without-else` | risk | medium (low if all are guarded) | each choice without an `else` branch | the choice | its unguarded transitions, else all |
| `state-crosses-regions` | risk | low | each transition between two regions of one composite state (not from a fork or into a join) | both ends | the transition |
| `state-final-unreachable` | suggestion | low | each `final_state` without incoming transition | the state | – |

Rules for `class` diagrams (`arcviz/class_rules.py`, [spec](../architecture/specs/class-diagrams.md)), in registration order.
Edges point from the referring element to the element it refers to, so a composition runs from the part to its whole. A
member's name is the text between its visibility sign and the first `:`, `(` or space; operations are the same when name and
parameter list are (spaces and case ignored).

| Rule | Category | Severity | Finding for | Elements | Edges |
|---|---|---|---|---|---|
| `class-package-cycle` | risk | medium | each group of top-level packages whose members refer to each other in a cycle (an edge from a node in one package to a node in another) | the packages | the edges between them |
| `class-multiple-wholes` | risk | medium | each part with more than one outgoing `composition` | the part and its wholes | the compositions |
| `class-composition-cycle` | risk | medium | each cycle of compositions (also a class composing itself) | the classes | the compositions |
| `class-duplicate-member` | risk | low | each attribute or operation line that occurs twice in a compartment | the classifier | – |
| `class-multiple-inheritance` | suggestion | low | each class with more than one outgoing `generalization` | the class | the generalizations |
| `class-interface-unrealized` | suggestion | low | each interface that no `realization` or `generalization` points at | the interface | – |
| `class-abstract-leaf` | suggestion | low | each abstract class that no `generalization` points at | the class | – |
| `class-isolated` | suggestion | low | each classifier without any edge, in a diagram with at least three classifiers | the classifier | – |
| `class-empty` | suggestion | low | each class without attributes and operations, each interface without operations | the classifier | – |
| `class-association-without-multiplicity` | suggestion | low | each association, aggregation or composition with neither multiplicity | both ends | the edge |
| `class-enumeration-empty` | suggestion | low | each enumeration without literals | the enumeration | – |

Rules for `use_case` diagrams (`arcviz/use_case_rules.py`, [spec](../architecture/specs/use-case-diagrams.md)), in registration
order. A *user goal* is a use case that no `include` points at and that is no `extend` source; an actor takes part when an
association joins them.

| Rule | Category | Severity | Finding for | Elements | Edges |
|---|---|---|---|---|---|
| `usecase-without-actor` | risk | medium | each user goal without an association to an actor | the use case | – |
| `usecase-extend-cycle` | risk | medium | each group of use cases that extend each other in a cycle | the use cases | the extends |
| `usecase-duplicate-name` | risk | low | each group of use cases, and each group of actors, with the same name (case-insensitive) | the group | – |
| `usecase-actor-unused` | suggestion | low | each actor without any edge | the actor | – |
| `usecase-without-specification` | suggestion | low | each use case with an empty note ("Spezifikation fehlt") | the use case | – |
| `usecase-extend-without-condition` | suggestion | low | each `extend` without `condition` | both ends | the extend |
| `usecase-include-used-once` | suggestion | low | each use case that exactly one use case includes | the included use case | the include |
| `usecase-outside-subject` | suggestion | low | each use case outside every subject, when the diagram has one | the use case | – |
| `usecase-subject-empty` | suggestion | low | each subject without a use case | the subject | – |

Rules for `component` diagrams (`arcviz/component_rules.py`, [spec](../architecture/specs/component-diagrams.md)), in registration
order. A *dependency* of a component is a `connection` or `dependency` edge to another component or subsystem, and a `usage`
of an interface together with every `realization` of that interface (the user depends on its providers).

| Rule | Category | Severity | Finding for | Elements | Edges |
|---|---|---|---|---|---|
| `component-interface-unprovided` | risk | medium | each interface that is used and not provided | the interface and its users | the usages |
| `component-dependency-cycle` | risk | medium | each group of components or subsystems that depend on each other in a cycle | the group | the edges that make the dependencies |
| `component-interface-unused` | suggestion | low | each interface that is provided and not used | the interface | the realizations |
| `component-fan-out` | suggestion | low | each component with more than 7 distinct dependencies | the component | – |
| `component-isolated` | suggestion | low | each component without an edge, in a diagram with at least three components | the component | – |
| `component-port-unconnected` | suggestion | low | each port that no edge names, when other ports of the component are named | the component | – |
| `component-subsystem-empty` | suggestion | low | each subsystem without a child | the subsystem | – |

Rules for `sequence` diagrams (#123), in registration order; `elements` are interaction element ids.
Replies are matched in time order: a complete `reply` from B to A answers the most recent unanswered complete
`synchCall` or `asynchCall` from A to B (self messages are ignored); each operand of an `alt` with several
operands starts from the calls open before it, and afterwards a call is open only if no operand answered it;
the operands of other operators are read in order.

| Rule | Category | Severity | Finding for | Elements |
|---|---|---|---|---|
| `reply-without-call` | risk | medium | each reply that answers no call | the reply |
| `call-without-reply` | suggestion | low | each `synchCall` still unanswered at the end | the call |
| `alt-with-single-operand` | suggestion | low | each `alt` with one operand | the fragment |
| `fragment-after-destruction` | suggestion | low | each lifeline a combined fragment or interaction use covers after the lifeline's destruction (destruction or `deleteMessage` received earlier in time order) | the fragment, the lifeline |
| `unfinished-execution` | suggestion | low | each execution without `finish` on the receiver of its start message, when that call has exactly one matching reply (not one per `alt` operand) | the execution, the reply |

Messages after a lifeline's destruction are an integrity error (`sequence.integrity_errors`), not a finding.

## LLM review

`review.review(provider, diagram)` runs `rules.run_checks(diagram)`, then one
`provider.complete_tool(REVIEW_SYSTEM_PROMPT, review_input(diagram, rule_findings), REVIEW_TOOL)`
call, validates the arguments with `parse_review` and returns
`{diagram_type, summary, findings, llm: {ok, error, dropped, usage, partial}}` with rule and LLM findings
merged and sorted by `sort_key`. Any exception of the call or the validation is caught:
`llm.ok = false`, `llm.error = "<Type>: <message>"`, `summary = null`, only the rule findings.

- `REVIEW_SYSTEM_PROMPT` asks for strengths, risks and suggestions, not repeating the rule findings,
  references by the ids shown in the diagram, German texts, suggestions phrased as instructions to
  the diagram assistant naming elements by id, at most 12 findings, and exactly one `submit_review`
  call.
- `review_input` is `Diagram type: <type>`, `Diagram:` with `diagram.summary(budget=100_000)`, then the
  rule findings as `- [severity] message (elements: ids)` lines or `Rule findings: none`. When the
  full summary is longer than the budget (`is_partial`), containers are collapsed and `PARTIAL_NOTE`
  follows the diagram: `get_diagram` is not available in the review, so the model must not judge the
  hidden contents; `llm.partial` is then `true`.
- With accepted decisions in the diagram's project (`review.review(provider, diagram, decisions)`, the route passes
  `decision_model.decision_lines` of the project; M29, ADR-0168), `review_input` adds `Accepted architecture decisions
  of the project (binding):` and those lines before the rule findings, and `REVIEW_SYSTEM_PROMPT` asks for a risk finding
  (medium or high) starting "Widerspricht ADR-<n>:" when the diagram contradicts one. The project review does the same
  (`project_review.review_input` after `digest_lines`, and its system prompt).
- `REVIEW_TOOL` (`submit_review`): `summary` (string) and `findings` (at most 20 objects with
  `category`, `severity`, `message`, `elements`, optional `edges` and `suggestion`; no other keys).

`parse_review(data, diagram)` → `(summary, findings, dropped)`; `ValueError` when `data` is not an
object with a list `findings`. Summary: non-empty string, stripped, cut to 1500 characters, else
`None`. A finding is dropped (and counted) unless it is an object with a valid `category`, a valid
`severity` and a non-empty string `message` (cut to 600). Strengths get severity `info` and no
suggestion; `info` on a risk or suggestion becomes `low`. `elements` entries are resolved with
`Diagram.find_node` (id, name, slug) and deduplicated, unresolved or non-string entries removed;
`edges` keep only existing edge ids; a non-list counts as empty. `suggestion` is a non-empty string
cut to 600, else `None`. After 20 accepted findings the rest are dropped. Ids are `llm-1`, `llm-2`, …
in the model's order.

## Applying a suggestion

The `suggestion` of a risk or suggestion finding can be applied from the review panel: the browser
sends it, with the finding as context, as a propose-mode chat turn (`POST /chat` with
`mode: "propose"`) and the user confirms or discards the previewed proposal. There is no
review-specific endpoint; see [Frontend](frontend.md#architecture-review) and
[Propose mode](agent.md#propose-mode).

## HTTP

`GET /api/diagrams/{id}/checks` → `{diagram_type, rules: [{id, title, category}], findings}`;
`POST /api/diagrams/{id}/review` → `{diagram_type, summary, findings, llm}` (503 without a
provider). See the [API reference](../reference/api.md#architecture-review). Neither writes anything.

## Project review

Design: [Project review](../architecture/specs/project-review.md), ADR-0062–0064. Tests:
`tests/test_project_review.py`, `tests/test_server_projects.py`, the project-review step of the e2e test.
`arcviz/project_review.py` works on a `ProjectSnapshot(project, members)` from
`ProjectService.load_members` (the existing member diagrams in membership order).

**Findings.** `ProjectFinding(Finding)` adds `diagram` (the diagram its `elements`/`edges` belong to),
`diagrams` (every diagram it concerns), `section`, `model_element` (the shared-model element it concerns) and
`relation` (the model relation it concerns, M25). `run_project_checks(snapshot)` returns the
findings of `PROJECT_RULES` plus the rule findings of every member diagram (`rules.run_checks`), the
latter with `id = "<diagram>:<id>"` and `diagram`/`diagrams` set; sorted by `rules.sort_key`, then the
diagram's position in the project. Project rule findings have `id = "<rule>:<subject>"` and no suggestion.

| Rule | Category / severity | Fires when |
|---|---|---|
| `empty-section` | suggestion / low | no diagram in the section or below it; only the outermost such section (`section`) |
| `section-without-overview` | suggestion / low | two or more diagrams directly in the section and none `is_overview` (title contains one of `OVERVIEW_WORDS`: Übersicht, Overview, Kontext, Context, Landschaft, Landscape, Gesamtbild, Big Picture; case-insensitive) |
| `missing-diagram-types` | suggestion / low | the section's direct diagrams are all deployment (no behaviour) or all activity/sequence/state machine/use case (no structure); deployment, class and component diagrams count as structure |
| `duplicate-title` | risk / medium | diagrams whose `normalise_title` (case-folded, non-alphanumerics to single spaces) is equal: one finding per group |
| `duplicate-title` | risk / low | two normalised titles of at least 4 characters with a `difflib` ratio ≥ 0.85 |
| `empty-diagram` | suggestion / low | no nodes (sequence: no lifelines and no fragments) |
| `dangling-element-ref` | risk / medium | nodes or lifelines of a diagram reference elements the project does not have (one finding per diagram, `elements` = the view ids) |
| `element-name-drift` | risk / low | a linked view element's name differs from its element's, both compared one line (`single_line`, #103) (per diagram and element; fix: **Abgleichen**, which also normalises stored element names) |
| `incompatible-element-link` | risk / medium | the view element's kind cannot stand for its element's kind (`linkable_kinds`) |
| `unused-element` | suggestion / low | no view, no section and no other element (as parent) references the element |
| `element-not-in-structure` | suggestion / low | a non-actor element is shown by lifelines or partitions but by no deployment node, while some deployment diagram shows linked elements |
| `message-without-connection` | risk / medium | messages of a sequence diagram between lifelines of two elements that both appear in deployment diagrams, while no deployment edge connects nodes standing for them, neither contains the other (a node stands for its own element and its ancestors') and no `communicates` relation of the model relates them (M25; the id is unchanged); one finding per diagram and pair, `elements` = the message ids |
| `unlinked-namesake` | suggestion / low | unlinked view elements named like an element (case-insensitive) that could stand for its kind; one finding per element over all diagrams |
| `similar-element-names` | suggestion / low | two project elements whose names are the same apart from case, spaces and punctuation, the same stem with suffixes of one class (service/svc/server/dienst, db/database/datenbank, app/application, component/komponente, system), or one typo inside a word (a substitution or a swap of neighbours between letters, never the last letter or a digit: *service-a*/*service-b* are different); at most 50 ([elements by default](../architecture/specs/elements-by-default.md)) |

The model rules (ADR-0075, [shared project model](../architecture/specs/shared-project-model.md)) only fire
where views reference elements, so projects without a model get no new findings.

Correspondence rules over model relations ([model relations](../architecture/specs/model-relations.md), ADR-0141;
tests `tests/test_correspondence.py`, `tests/test_m25_review.py`). A diagram *shows* a relation by a connection linked
to it, an unlinked connection that `fits` it, or (deployment, `deployed_on`) by nesting the source's node under the
target's as its nearest ancestor standing for a `node` element (`project_model.shown_views`/`shows`/`nested`).
Containment compares the nearest linked ancestor (unless it is a node) with `Element.parent`. Edge directions are
compared relative to the relation (an edge drawn backwards turns `forward` into "ein Pfeil entgegen der
Modellrichtung"). A message between two
linked elements without relation is reported once: by `message-without-connection` when both appear in deployment
diagrams, else by `connection-without-relation`. The first three
run only when the project has relations.

| Rule | Category / severity | Fires when |
|---|---|---|
| `relation-not-shown` | suggestion / low | no deployment diagram shows the relation (`project_model.shows`); `diagrams` = the deployment diagrams showing both ends that do not hide it; none, or one of them complete for the kind: no finding |
| `connection-without-relation` | suggestion / low | a deployment edge or message between views of two (different) elements links no relation: one finding per diagram and pair (`elements` = the connections), with `relation` set when a relation fits (link it), else none (create one); a message pair that neither a relation nor a deployment connection relates is left to `message-without-connection` |
| `complete-view-missing-relation` | risk / medium | a diagram declares the relation's kind complete, shows both ends, does not show the relation and does not mark it hidden |
| `relation-property-conflict` | risk / medium | the relation's properties and the values its linked views state (edge `protocol`, `port`, explicit `direction`; message sort → `mode`: synchCall sync, asynchCall/asynchSignal async) disagree after trimming and case-folding; one finding per relation and key, naming the model and every diagram with its value |
| `deployed-on-several-nodes` | risk / medium | an element is deployed on two or more node elements by `deployed_on` relations and nesting in deployment views; `model_element` set |
| `containment-conflict` | risk / medium | a linked node nested (not in a node) inside an element that is not among the element's ancestors (`Element.parent` chain), or, without a parent in the model, inside different elements in different views |
| `invalid-relation-link` | risk / medium | connections of a diagram link relations that no longer exist or that they no longer fit, or the diagram hides relations that no longer exist (one finding per diagram; **Abgleichen** repairs) |
| `requirement-unrefined` | suggestion / low | requirements still `raw` more than a day after they were created (one finding, the first 40 ids in `requirements`) |
| `requirement-uncovered` | risk / medium | refined requirements that no driver, scenario or decision names, one finding per theme |
| `requirement-no-stakeholder` | suggestion / low | refined requirements without an existing stakeholder |
| `quality-requirement-no-measure` | risk / medium | refined requirements of kind `quality` without `measure` |
| `requirement-conflict-open` | risk / medium | an unresolved `conflict` issue on a requirement that is not rejected or a duplicate (one finding per issue) |
| `requirement-duplicate-open` | suggestion / low | an unresolved `duplicate` issue, or two refined statements whose word sets are at least 70 % alike (one finding per pair) |
| `scenario-no-requirement` | suggestion / low | scenarios that name no requirement while the project has refined ones |
| `scenario-no-quality` | suggestion / low | active scenarios without an ISO 25010 characteristic ([spec](../architecture/specs/iso25010-qualities.md)) |
| `decision-no-basis` | risk / medium | an accepted decision that names neither a driver nor a requirement (one finding per decision) |
| `stakeholder-no-requirement` | suggestion / low | stakeholders of influence `high` that no requirement names, while requirements exist |

Connection ids of a deployment diagram are in a finding's `edges` (highlighted as edges), message ids in `elements`.
**Resolutions** (ADR-0138, tests `tests/test_resolutions.py`): every correspondence finding carries
`resolutions: list[Resolution]` (`action`, German `label`, and `diagram`, `relation`, `view`, `model_element`, `prompt`,
`args` as the action needs; the request table is in the [API reference](../reference/api.md#get-apiprojectspidchecks)).
`relation-not-shown`: propose (draw it in the first deployment diagram showing both ends), remove_relation;
`connection-without-relation`: link_relation (the first fitting relation) or create_relation (the first kind the
connection can show whose rules allow the ends), propose (remove the connection); `complete-view-missing-relation`:
propose, hide, remove_relation; `relation-property-conflict`: update_relation for every value a view states (never a
default), and when the model states a value, a proposal per diagram whose views differ; `deployed-on-several-nodes`:
remove_relation per `deployed_on` relation, propose per diagram; `containment-conflict`: move_element per container
seen, propose per diagram when the model names a parent; `invalid-relation-link`: sync. The M18 rules carry none.

**LLM review.** `review_project(provider, snapshot)` runs the checks and one
`provider.complete_tool(PROJECT_REVIEW_SYSTEM_PROMPT, review_input(...), PROJECT_REVIEW_TOOL)` call
(`submit_project_review`: `summary`, up to 20 `findings` with `category`, `severity`, `message` and
optional `diagram`, `section`, `elements`, `edges`, `suggestion`). `review_input` is the digest: project
title and description, the section tree with `[id] «type» title` per diagram, the model elements (`- [id]
«kind» name (part of p) — description (in: diagram ids)`, when the project has any), the model relations
(`Model relations:`, `- [r1] communicates: web -> api {protocol=HTTPS} — description`, within
`RELATION_DIGEST_BUDGET` 6 000 characters: descriptions dropped first, then counted), every diagram's
`summary(budget=…)` with a share of `PROJECT_REVIEW_BUDGET` (100 000 characters / number of diagrams, at
least 2 000), `PARTIAL_NOTE` when a summary is collapsed (`is_partial`), and the rule findings as
already reported. `parse_project_review` validates finding by finding: `diagram` resolves by id or by a
unique case-insensitive title (unknown: project-level finding, `elements`/`edges` dropped); with a
diagram the finding goes through the diagram review's validation (`review._finding`: unknown element and
edge ids dropped, strengths have no suggestion); an unknown `section` is dropped; invalid findings and
those beyond 20 count as `dropped`. Any exception gives `llm = {ok: false, error}` and only the rule
findings. Answer: `{project, summary, findings, diagrams, model, llm: {ok, error, dropped, usage, partial}}`,
where `diagrams` maps every member id to `{title, type, section}` and `model` every element id to `{name, kind}`
(`GET …/checks` answers `{project, rules, findings, diagrams, model}`). Nothing is stored.

`GET /api/projects/{pid}/checks` → `checks(snapshot)` = `{project, rules: [{id, title, category}], findings,
diagrams}`; `POST /api/projects/{pid}/review` → the answer above (404 unknown project, 503 without a
provider). The panel's project mode is described in [Frontend](frontend.md#architecture-review).

## Steward

The project steward ([design](../architecture/specs/project-steward.md), ADR-0204 to 0206) re-checks an opted-in project after it
changed (`steward.StewardWorker`: live-hub hints, debounce, sweep, a lease in the keyed document `steward`) and stores
**one** pending project proposal of the client `steward` (it discards its own earlier one): the deterministic rule findings that
have exactly one model-level fix, and the candidates of one `complete_tool` call (`submit_steward_findings`) that a second
model confirmed (`submit_verdict`, `supported` only). A candidate needs `cites` that exist, a next step needs `serves` (an active
driver or `vision:g<n>`), its items must apply to a copy of the project, and a model never proposes a deletion
(`steward.LLM_OPERATIONS`). Items carry `finding`, `group` (`fix` | `next_step`), `rationale` and `cites`
(`proposals.ProposalItem`); a person's discard remembers the `finding` so it does not come back. The steward runs in the system
context (`ServiceRoot.system_services`, identity `steward`) and writes nothing but its proposal and its document. Configuration:
[reference](../reference/configuration.md#project-steward-optional); routes `GET|PUT /api/projects/{pid}/steward`,
`POST …/steward/run`. Telemetry: span `arcviz.steward.run`, metrics `arcviz.steward.runs` and `arcviz.steward.items`.
