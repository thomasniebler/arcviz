"""Review findings of state machine diagrams (spec: docs/architecture/specs/state-machine-diagrams.md)."""

from __future__ import annotations

import time

from arcviz import rules
from arcviz.model import Diagram
from arcviz.operations import apply
from arcviz.templates import create_from_template

RULE_IDS = {
    "state-no-initial",
    "state-multiple-initial",
    "state-initial-targets",
    "state-unreachable",
    "state-dead-end",
    "state-nondeterministic",
    "state-choice-without-else",
    "state-crosses-regions",
    "state-final-unreachable",
}


def machine() -> Diagram:
    return Diagram(id="m", title="M", type="state_machine")


def node(d: Diagram, name: str, kind: str = "state", **args) -> str:
    return apply(d, "add_node", {"name": name, "kind": kind, **args})["touched"][0]


def edge(d: Diagram, source: str, target: str, **args) -> str:
    apply(d, "add_edge", {"source": source, "target": target, **args})
    return d.edges[-1].id


def found(d: Diagram, rule: str) -> list[rules.Finding]:
    return [f for f in rules.run_checks(d) if f.rule == rule]


def test_every_rule_is_registered():
    assert {r.id for r in rules.rules_for("state_machine")} == RULE_IDS


def test_a_clean_machine_has_no_findings():
    d = machine()
    i, a, b, f = node(d, "I", "initial_pseudostate"), node(d, "A"), node(d, "B"), node(d, "F", "final_state")
    edge(d, i, a)
    edge(d, a, b, label="go")
    edge(d, b, f, label="done")
    assert rules.run_checks(d) == []


def test_the_template_is_clean():
    assert rules.run_checks(create_from_template("order-lifecycle", "x", "X")) == []


def test_no_initial_state():
    d = machine()
    node(d, "A")
    [finding] = found(d, "state-no-initial")
    assert (finding.category, finding.severity) == ("suggestion", "low") and finding.elements == ["a"]
    comp = node(d, "C", "composite_state")
    node(d, "Inner", parent=comp)
    assert {tuple(f.elements) for f in found(d, "state-no-initial")} == {("a",), (comp,)}
    node(d, "I", "initial_pseudostate", parent=comp)
    assert {tuple(f.elements) for f in found(d, "state-no-initial")} == {("a",)}


def test_a_region_is_its_own_scope():
    d = machine()
    comp = node(d, "C", "composite_state")
    top = node(d, "Start", "initial_pseudostate")
    edge(d, top, comp)
    r1, r2 = node(d, "R1", "state_region", parent=comp), node(d, "R2", "state_region", parent=comp)
    node(d, "I", "initial_pseudostate", parent=r1)
    node(d, "A", parent=r1)
    node(d, "B", parent=r2)
    assert [f.elements for f in found(d, "state-no-initial")] == [[r2]]  # the region without an initial state


def test_multiple_initial_states_in_one_scope():
    d = machine()
    i1, i2 = node(d, "I1", "initial_pseudostate"), node(d, "I2", "initial_pseudostate")
    [finding] = found(d, "state-multiple-initial")
    assert (finding.category, finding.severity) == ("risk", "medium") and finding.elements == [i1, i2]


def test_initial_state_needs_exactly_one_transition():
    d = machine()
    i, a, b = node(d, "I", "initial_pseudostate"), node(d, "A"), node(d, "B")
    [none] = found(d, "state-initial-targets")
    assert none.elements == [i] and not none.edges
    e1, e2 = edge(d, i, a), edge(d, i, b)
    [two] = found(d, "state-initial-targets")
    assert two.edges == [e1, e2]
    apply(d, "remove_edge", {"id": e2})
    assert found(d, "state-initial-targets") == []


def test_unreachable_states():
    d = machine()
    i, a, b, c = node(d, "I", "initial_pseudostate"), node(d, "A"), node(d, "B"), node(d, "Island")
    edge(d, i, a)
    edge(d, a, b)
    edge(d, c, a)
    [finding] = found(d, "state-unreachable")
    assert finding.elements == [c] and (finding.category, finding.severity) == ("risk", "medium")


def test_nothing_is_unreachable_without_an_initial_state():
    d = machine()
    a, b = node(d, "A"), node(d, "B")
    edge(d, a, b)
    assert found(d, "state-unreachable") == []  # state-no-initial says it


def test_entering_a_composite_state_reaches_its_initial_state_and_what_follows():
    d = machine()
    i, comp = node(d, "I", "initial_pseudostate"), node(d, "C", "composite_state")
    ci, inner, last = (
        node(d, "CI", "initial_pseudostate", parent=comp),
        node(d, "In", parent=comp),
        node(d, "Last", parent=comp),
    )
    edge(d, i, comp)
    edge(d, ci, inner)
    edge(d, inner, last, label="x")
    assert found(d, "state-unreachable") == []


def test_a_transition_into_a_nested_state_enters_the_composite_state():
    d = machine()
    i, a, comp = node(d, "I", "initial_pseudostate"), node(d, "A"), node(d, "C", "composite_state")
    inner = node(d, "In", parent=comp)
    edge(d, i, a)
    edge(d, a, inner, label="x")
    assert found(d, "state-unreachable") == []


def test_dead_end_states():
    d = machine()
    i, a, b, f = node(d, "I", "initial_pseudostate"), node(d, "A"), node(d, "B"), node(d, "F", "final_state")
    edge(d, i, a)
    edge(d, a, b, label="x")
    edge(d, a, f, label="y")
    [finding] = found(d, "state-dead-end")
    assert finding.elements == [b] and (finding.category, finding.severity) == ("suggestion", "low")


def test_no_dead_ends_without_a_final_state_or_below_a_composite_that_is_left():
    d = machine()
    i, a, comp, after = (
        node(d, "I", "initial_pseudostate"),
        node(d, "A"),
        node(d, "C", "composite_state"),
        node(d, "After"),
    )
    edge(d, i, a)
    edge(d, a, comp, label="x")
    node(d, "In", parent=comp)
    assert found(d, "state-dead-end") == []  # no final state at all
    f = node(d, "F", "final_state")
    edge(d, after, f)
    edge(d, comp, after, label="leave")
    assert found(d, "state-dead-end") == []  # the composite state is left, which leaves In as well


def test_nondeterministic_transitions():
    d = machine()
    a, b, c = node(d, "A"), node(d, "B"), node(d, "C")
    e1, e2 = edge(d, a, b, label="go"), edge(d, a, c, label="go")
    [finding] = found(d, "state-nondeterministic")
    assert finding.edges == [e1, e2] and finding.elements == [a]
    apply(d, "update_edge", {"id": e1, "guard": "x > 0"})
    apply(d, "update_edge", {"id": e2, "guard": "x <= 0"})
    assert found(d, "state-nondeterministic") == []
    e3 = edge(d, a, b)
    e4 = edge(d, a, c, label="")  # two completion transitions without a trigger or a guard
    assert {tuple(f.edges) for f in found(d, "state-nondeterministic")} == {(e3, e4)}


def test_different_triggers_are_deterministic():
    d = machine()
    a, b, c = node(d, "A"), node(d, "B"), node(d, "C")
    edge(d, a, b, label="go")
    edge(d, a, c, label="Stop")
    edge(d, a, c, label="go", guard="x")  # a guarded one beside the unguarded: still the same trigger
    assert len(found(d, "state-nondeterministic")) == 1


def test_choice_without_else():
    d = machine()
    ch, a, b = node(d, "Wahl", "choice_pseudostate"), node(d, "A"), node(d, "B")
    e1, e2 = edge(d, ch, a, guard="x > 0"), edge(d, ch, b, guard="x <= 0")
    [low] = found(d, "state-choice-without-else")
    assert (low.category, low.severity) == ("risk", "low")
    apply(d, "update_edge", {"id": e2, "guard": "else"})
    assert found(d, "state-choice-without-else") == []
    apply(d, "update_edge", {"id": e2, "guard": ""})
    [medium] = found(d, "state-choice-without-else")
    assert medium.severity == "medium" and medium.edges == [e2] and e1 not in medium.edges


def test_transitions_between_regions():
    d = machine()
    comp = node(d, "C", "composite_state")
    r1, r2 = node(d, "R1", "state_region", parent=comp), node(d, "R2", "state_region", parent=comp)
    a, b, fork = node(d, "A", parent=r1), node(d, "B", parent=r2), node(d, "Fork", "fork_pseudostate", parent=r1)
    crossing = edge(d, a, b, label="x")
    [finding] = found(d, "state-crosses-regions")
    assert finding.edges == [crossing] and (finding.category, finding.severity) == ("risk", "low")
    apply(d, "remove_edge", {"id": crossing})
    edge(d, fork, b)  # a fork may fan out into other regions
    assert found(d, "state-crosses-regions") == []


def test_final_state_without_incoming_transition():
    d = machine()
    f = node(d, "F", "final_state")
    [finding] = found(d, "state-final-unreachable")
    assert finding.elements == [f] and (finding.category, finding.severity) == ("suggestion", "low")


def test_findings_are_linear_in_the_size_of_the_machine():
    d = machine()
    previous = node(d, "I", "initial_pseudostate")
    for n in range(250):
        current = node(d, f"S{n}")
        edge(d, previous, current, **({} if n == 0 else {"label": f"e{n}"}))
        previous = current
    start = time.perf_counter()
    rules.run_checks(d)
    assert time.perf_counter() - start < 2.0
