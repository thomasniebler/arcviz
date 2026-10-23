"""Review findings of use case diagrams (spec: docs/architecture/specs/use-case-diagrams.md)."""

from __future__ import annotations

import time

from arcviz import rules
from arcviz.model import Diagram
from arcviz.operations import apply
from arcviz.templates import create_from_template

RULE_IDS = {
    "usecase-without-actor",
    "usecase-actor-unused",
    "usecase-without-specification",
    "usecase-extend-without-condition",
    "usecase-extend-cycle",
    "usecase-include-used-once",
    "usecase-outside-subject",
    "usecase-subject-empty",
    "usecase-duplicate-name",
}


def diagram() -> Diagram:
    return Diagram(id="u", title="U", type="use_case")


def node(d: Diagram, name: str, kind: str = "use_case", described: bool = True, **args) -> str:
    if kind == "use_case" and described and "description" not in args:
        args["description"] = "Ziel: x"
    return apply(d, "add_node", {"name": name, "kind": kind, "id": name.lower(), **args})["touched"][0]


def edge(d: Diagram, source: str, target: str, kind: str = "association", **args) -> str:
    apply(d, "add_edge", {"source": source, "target": target, "kind": kind, **args})
    return d.edges[-1].id


def found(d: Diagram, rule: str) -> list[rules.Finding]:
    return [f for f in rules.run_checks(d) if f.rule == rule]


def test_every_rule_is_registered():
    assert {r.id for r in rules.rules_for("use_case")} == RULE_IDS


def test_the_template_is_clean():
    assert rules.run_checks(create_from_template("webshop-use-cases", "x", "X")) == []


def test_a_user_goal_needs_an_actor():
    d = diagram()
    s = node(d, "Webshop", "subject")
    goal, included, extension = (node(d, n, parent=s) for n in ("Bestellen", "Zahlen", "Gutschein"))
    edge(d, goal, included, "include")
    edge(d, extension, goal, "extend", fields={"condition": "x"})
    [finding] = found(d, "usecase-without-actor")
    assert finding.elements == [goal] and (finding.category, finding.severity) == ("risk", "medium")
    edge(d, node(d, "Kunde", "actor"), goal)
    assert found(d, "usecase-without-actor") == []  # the included and the extending use case need none


def test_unused_actors():
    d = diagram()
    a, b = node(d, "A", "actor"), node(d, "B", "actor")
    [first, second] = found(d, "usecase-actor-unused")
    assert {first.elements[0], second.elements[0]} == {a, b}
    edge(d, b, a, "generalization")
    assert found(d, "usecase-actor-unused") == []


def test_missing_specifications():
    d = diagram()
    s = node(d, "S", "subject")
    bare = node(d, "Bare", parent=s, described=False)
    node(d, "Done", parent=s)
    [finding] = found(d, "usecase-without-specification")
    assert finding.elements == [bare] and "Spezifikation fehlt" in finding.message
    apply(d, "update_node", {"id": bare, "description": "   "})
    assert [f.elements for f in found(d, "usecase-without-specification")] == [[bare]]
    apply(d, "update_node", {"id": bare, "description": "Ziel: y"})
    assert found(d, "usecase-without-specification") == []


def test_extend_without_a_condition():
    d = diagram()
    a, b = node(d, "A"), node(d, "B")
    e = edge(d, a, b, "extend")
    [finding] = found(d, "usecase-extend-without-condition")
    assert finding.edges == [e] and finding.elements == [a, b]
    apply(d, "update_edge", {"id": e, "fields": {"condition": "Gutschein"}})
    assert found(d, "usecase-extend-without-condition") == []


def test_extend_cycles():
    d = diagram()
    a, b, c = node(d, "A"), node(d, "B"), node(d, "C")
    edge(d, a, b, "extend", fields={"condition": "x"})
    edge(d, b, c, "extend", fields={"condition": "x"})
    assert found(d, "usecase-extend-cycle") == []
    edge(d, c, a, "extend", fields={"condition": "x"})
    [finding] = found(d, "usecase-extend-cycle")
    assert finding.elements == [a, b, c] and len(finding.edges) == 3
    assert (finding.category, finding.severity) == ("risk", "medium")


def test_an_include_used_once():
    d = diagram()
    a, b, c = node(d, "A"), node(d, "B"), node(d, "C")
    edge(d, a, c, "include")
    [finding] = found(d, "usecase-include-used-once")
    assert finding.elements == [c] and (finding.category, finding.severity) == ("suggestion", "low")
    edge(d, b, c, "include")
    assert found(d, "usecase-include-used-once") == []


def test_use_cases_outside_the_subject():
    d = diagram()
    outside = node(d, "Outside")
    assert found(d, "usecase-outside-subject") == []  # no subject, nothing to be outside of
    s = node(d, "S", "subject")
    inside = node(d, "Inside", parent=s)
    [finding] = found(d, "usecase-outside-subject")
    assert finding.elements == [outside] and inside not in finding.elements


def test_an_empty_subject():
    d = diagram()
    s = node(d, "S", "subject")
    [finding] = found(d, "usecase-subject-empty")
    assert finding.elements == [s]
    node(d, "UC", parent=s)
    assert found(d, "usecase-subject-empty") == []


def test_duplicate_names():
    d = diagram()
    s = node(d, "S", "subject")
    a = apply(d, "add_node", {"name": "Bestellen", "kind": "use_case", "parent": s, "description": "x"})["touched"][0]
    b = apply(d, "add_node", {"name": "bestellen", "kind": "use_case", "parent": s, "description": "x"})["touched"][0]
    k1, k2 = node(d, "Kunde", "actor"), apply(d, "add_node", {"name": "KUNDE", "kind": "actor"})["touched"][0]
    findings = found(d, "usecase-duplicate-name")
    assert sorted(tuple(f.elements) for f in findings) == sorted([(a, b), (k1, k2)])
    assert all((f.category, f.severity) == ("risk", "low") for f in findings)
    apply(d, "update_node", {"id": b, "name": "Stornieren"})
    assert [f.elements for f in found(d, "usecase-duplicate-name")] == [[k1, k2]]


def test_a_use_case_and_an_actor_may_share_a_name():
    d = diagram()
    node(d, "Admin", "actor")
    node(d, "Admin verwalten")
    apply(d, "add_node", {"name": "Admin", "kind": "use_case", "description": "x"})
    assert found(d, "usecase-duplicate-name") == []


def test_findings_are_linear_in_the_size_of_the_diagram():
    d = diagram()
    s = node(d, "S", "subject")
    previous = node(d, "N0", parent=s)
    for n in range(1, 200):
        current = node(d, f"N{n}", parent=s)
        edge(d, previous, current, "include")
        previous = current
    start = time.perf_counter()
    rules.run_checks(d)
    assert time.perf_counter() - start < 2.0
