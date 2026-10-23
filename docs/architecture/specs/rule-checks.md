# Rule-based architecture checks

- Status: Implemented (milestone M12; activity and sequence rules: milestone M27)
- Issue: #95, #123 (activity and sequence rules)
- ADRs: [0022 Rule checks as plain functions registered per diagram type](../adr/0022-rule-checks-registered-per-diagram-type.md),
  [0023 One finding schema for rule checks and the LLM review](../adr/0023-one-finding-schema.md)
- Related: [LLM architecture review](llm-review.md) (#96), [Applying a review suggestion](review-suggestions.md) (#97)

## Summary

Milestone M12 adds an architecture review of a diagram. Its first, deterministic half is a set of
rule checks that look for well-known weaknesses in a deployment diagram: a database that is a single
point of failure, a public entry that bypasses the gateway, an external system that talks directly
to a data store, an element that is connected to nothing. Each check produces *findings*: a
category (strength, risk or suggestion), a severity, a message, the ids of the elements and edges it
concerns and an optional suggestion how to improve it. No LLM is involved, so the checks are fast,
free and reproducible, and they are what the LLM review (#96) builds on.

The checks are registered per diagram type. Today every diagram is a deployment diagram; milestone
M13 adds a diagram `type` (deployment, activity, sequence). The registry is keyed by that type and
falls back to `deployment` while the field does not exist, so M13 only has to register rules for
its new types (or none) and nothing here assumes one type everywhere.

## Goals

- `arcviz/rules.py`: a `Finding` model shared with the LLM review, a registry of rules per diagram
  type, `run_checks(diagram)` returning the findings of the diagram's type, sorted by severity.
- Four risk rules and two strength rules for deployment diagrams (below), each with a stable id.
- `GET /api/diagrams/{id}/checks` returns the findings plus the list of rules that ran.
- Findings reference node ids and edge ids that exist in the checked diagram, so the UI can
  highlight them.
- Pure and deterministic: the same diagram gives the same findings in the same order; nothing is
  written.

## Non-goals

- User-configurable rules, thresholds or a rule DSL (ADR-0022).
- Rules for the diagram types M13 introduces; M13 registered none, M27 (#123) adds them (see
  [Activity and sequence rules](#activity-and-sequence-rules-123)).
- Rules for kinds defined in a kinds file (`ARCVIZ_KINDS_FILE`): the rules know the built-in kinds
  only; elements of configured kinds are ignored by kind-specific rules (they still count as
  connected elements).
- Checking a past version, or checking automatically after every change (the UI runs the checks
  on demand and refreshes them while the review panel is open, see #96).

## Design

### Finding (`arcviz/rules.py`)

```python
Category = Literal["strength", "risk", "suggestion"]
Severity = Literal["info", "low", "medium", "high"]


class Finding(BaseModel):
    id: str  # stable within one result, e.g. "database-without-replication:orders-db"
    source: Literal["rule", "llm"]
    rule: str | None = None  # rule id for source "rule"
    category: Category
    severity: Severity
    message: str  # German, shown in the UI
    elements: list[str] = []  # node ids, existing in the checked diagram
    edges: list[str] = []  # edge ids, existing in the checked diagram
    suggestion: str | None = None  # how to improve it, phrased as an instruction (used by #97)
```

A `strength` always has severity `info`; risks and suggestions use `low`, `medium` or `high`.
Messages and suggestions are German because they are shown as-is in the (German) UI.
`SEVERITY_ORDER = ("high", "medium", "low", "info")` sorts findings: severity, then category
(`risk`, `suggestion`, `strength`), then rule id, then element ids.

### Registry

```python
@dataclass(frozen=True)
class Rule:
    id: str
    title: str          # German one-liner shown in the rule list
    category: Category
    check: Callable[[Diagram], Iterable[Finding]]

DEFAULT_DIAGRAM_TYPE = "deployment"
RULES: dict[str, list[Rule]]                        # diagram type -> rules, in registration order

def register(diagram_type: str, rule: Rule) -> Rule # duplicate id for the same type: ValueError
def diagram_type(diagram: Diagram) -> str            # getattr(diagram, "type", None) or "deployment"
def rules_for(diagram_type: str) -> list[Rule]       # [] for a type without rules
def run_checks(diagram: Diagram) -> list[Finding]    # all rules of the diagram's type, sorted
```

`diagram_type` reads the attribute M13 will add without depending on it. `run_checks` runs every
rule of the type; a rule that raises is a bug, so the exception propagates (tests cover every rule).
Findings whose element or edge ids are not in the diagram are a bug too and are rejected by a test,
not filtered at run time.

### Deployment rules

Traffic is read from edges whose kind is not `deploys` (a deployment relation, not a call). The
*flow* of an edge follows its effective direction: `forward` means source → target, `both` and
`none` count in both directions. The effective direction is the edge's `direction` or the default
of its kind, provided by the new public helper `mermaid.edge_direction(edge)` that `edge_arrow`
already computes internally (a configured edge kind uses its configured default).

| Rule id | Category | Severity | Fires when | Elements / edges |
|---|---|---|---|---|
| `database-without-replication` | risk | high | a `database` node has no `replication` edge and none of the properties `replicas`, `replication`, `ha`, `high_availability`, `zone_redundant`, `geo_redundant` with a value other than `"", "0", "1", "false", "no", "none"` | the database |
| `public-entry-without-gateway` | risk | medium | traffic flows from an `external` node into a `service`, `function` or `component` node (not through a `gateway`) | the external node and the target; the edge |
| `external-to-datastore` | risk | high | traffic flows between an `external` node and a `database`, `storage` or `queue` node, in either direction | the external node and the data store; the edge |
| `unconnected-element` | suggestion | low | a leaf node (not a container, no children) has no edge at all | the node |
| `replicated-database` | strength | info | a `database` node is replicated (the opposite of the first rule) | the database (and its `replication` edges) |
| `gateway-entry` | strength | info | traffic flows from an `external` node into a `gateway` node | the external node and the gateway; the edges |

An `external` leaf without edges is reported by `unconnected-element` like any other leaf. Rule findings carry a German `suggestion`, for example
*"Füge für orders-db eine Replik hinzu (z. B. orders-db-replica) und verbinde beide mit einer
replication-Kante."* Finding ids are `<rule>:<element ids joined by '+'>[:<edge id>]`, unique within
one result.

### Activity and sequence rules (#123)

Milestone M27 registers rules under `activity` and `sequence`. UML 2.5.1 is loose about most of what
they look for (an Activity may lack an InitialNode, an Action without incoming flows starts with the
Activity, a fork may end in FlowFinalNodes, replies are optional), so only two of them are risks; the
others are low suggestions, to avoid false alarms on legitimate models. For sequence diagrams `elements`
holds interaction element ids (lifelines, messages, fragments, executions), which the own SVG renderer
carries as `data-id`; `edges` stays empty.

Activity rules (flows are all ActivityEdges, control and object flows; ActivityPartitions are no nodes):

| Rule id | Category | Severity | Fires when | Elements / edges |
|---|---|---|---|---|
| `decision-without-guards` | risk | medium | a `decision_node` with at least two outgoing flows has a flow without a guard (the choice is undetermined) | the decision; the unguarded flows |
| `missing-initial-node` | suggestion | low | the diagram has ActivityNodes but no `initial_node` | the nodes without incoming flow (all nodes in a closed loop) |
| `unreachable-node` | suggestion | low | an ActivityNode is not reachable from any `initial_node` (only when one exists) | the node |
| `decision-without-else` | suggestion | low | every outgoing flow of such a decision has a guard, none is `else` (the guards may not cover every case) | the decision; its outgoing flows |
| `fork-without-join` | suggestion | low | no `join_node` is reachable from a `fork_node` | the fork |
| `flow-without-final-node` | suggestion | low | no final node is reachable from an `opaque_action`; one finding per place the flow gets stuck: an action from which only actions on a loop back to it are equally stuck (a dead end, or a loop without exit) | the action, or the actions of the loop |

Sequence rules. Replies are matched to calls in time order: a `reply` from B to A answers the most recent
unanswered `synchCall` or `asynchCall` from A to B; found, lost and self messages are not matched. The
operands of an `alt` are alternatives: each starts from the calls open before the fragment, and a call
answered in any of them counts as answered (so a call before the alt may be answered differently in each
operand); the operands of every other operator are read one after the other.

| Rule id | Category | Severity | Fires when | Elements |
|---|---|---|---|---|
| `reply-without-call` | risk | medium | a reply answers no preceding call between the same lifelines | the reply |
| `call-without-reply` | suggestion | low | a complete `synchCall` between two lifelines gets no reply | the call |
| `alt-with-single-operand` | suggestion | low | an `alt` has one operand (an `opt` says the same) | the fragment |
| `fragment-after-destruction` | suggestion | low | a combined fragment or interaction use covers a lifeline destroyed before it (by a destruction or a `deleteMessage`) | the fragment, the lifeline |
| `unfinished-execution` | suggestion | low | an execution specification without `finish` on the receiver of the call that starts it, when that call has exactly one reply (a call answered differently per `alt` operand is left alone: "until the last message" fits it; so is the caller's execution) | the execution, the reply |

Every rule is linear in nodes and flows: reachability towards join and final nodes is one reverse search from all of
them (`model.flow_distances` over `model.reverse_flows`), `flow-without-final-node` groups the stuck actions by
strongly connected components (iterative Tarjan, components reported sinks first), and analyses several rules share
(the outgoing flows, the matched replies) are computed once per `run_checks` (`rules._memo` over a context variable).
The flow helpers (`activity_outgoing`, `flow_distances`, `reverse_flows`, `edge_guard`, `is_else_guard`) live in
`model.py` next to `activity_rule_errors` and are shared with the PlantUML export.

Messages to or from a lifeline after its destruction, the issue's third sequence candidate, are not a rule:
`sequence.integrity_errors` already refuses them, so no stored diagram contains one.

### Endpoint

`GET /api/diagrams/{id}/checks` → 200

```json
{
  "diagram_type": "deployment",
  "rules": [{"id": "database-without-replication", "title": "…", "category": "risk"}],
  "findings": [{"id": "database-without-replication:orders-db", "source": "rule", "rule": "database-without-replication",
                "category": "risk", "severity": "high", "message": "…", "elements": ["orders-db"], "edges": [],
                "suggestion": "…"}]
}
```

404 for an unknown diagram. A diagram type without rules returns `rules: []`, `findings: []`.

### Module boundaries

`arcviz/rules.py` imports `model` and `mermaid` (for `edge_direction`). `server.py` imports it. The
module table in `docs/architecture/index.md` gets the row.

### Error handling, concurrency

Read-only; loads the latest diagram once. No locking needed.

## Testing

- `tests/test_rules.py`: each rule fires and does not fire (positive and negative fixtures,
  including edge directions `forward`/`both`/`none`, `deploys` edges ignored, replication by edge and
  by property, falsy property values); the demo diagram gives exactly the database finding; every
  finding of every starter template references existing ids; ordering and ids are deterministic;
  registry: duplicate ids rejected, unknown type → no rules, a diagram object with a `type`
  attribute selects that type's rules (simulating M13).
- `tests/test_rules_behaviour.py` (#123): each activity and sequence rule fires and does not fire, alt
  operands as alternatives, the templates' findings (the order process's ja/nein decision has no `else`), the
  endpoint for both types; `tests/test_properties.py`: the findings of any reachable state of every template
  name existing elements and edges.
- `tests/test_server.py`: `GET /checks` 200 with the expected finding and rule list, 404.
- `tests/test_mermaid.py`: `edge_direction` for defaults and overrides.

## Migration / compatibility

Additive: one new module, one new endpoint, one new public helper in `mermaid.py`. No stored data
changes.

## Open questions

None open; decisions taken without the maintainer are *Proposed* in ADR-0022 and ADR-0023.

## Decisions

- Rules are Python functions registered per diagram type, not configuration (ADR-0022, Proposed).
- The diagram type is read with a `deployment` fallback so the registry works before and after M13
  (ADR-0022, Proposed).
- One `Finding` schema for rules and the LLM, with a category, a severity and node and edge ids
  (ADR-0023, Proposed).
- Finding texts are German, like the rest of the UI (ADR-0023, Proposed).
- Severities of the rules as in the table; a database without replication is `high` (Proposed).
- Behaviour rules (#123): risks only where the model is undetermined or contradictory (an unguarded decision, a
  reply without call); everything UML allows but that is often unintended is a low suggestion.
