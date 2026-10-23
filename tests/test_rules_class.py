"""Review findings of class diagrams (spec: docs/architecture/specs/class-diagrams.md)."""

from __future__ import annotations

import time

from arcviz import rules
from arcviz.class_diagram import member_key, multiplicity_problem
from arcviz.model import Diagram
from arcviz.operations import apply
from arcviz.templates import create_from_template

RULE_IDS = {
    "class-package-cycle",
    "class-multiple-wholes",
    "class-composition-cycle",
    "class-multiple-inheritance",
    "class-interface-unrealized",
    "class-abstract-leaf",
    "class-isolated",
    "class-empty",
    "class-association-without-multiplicity",
    "class-duplicate-member",
    "class-enumeration-empty",
}


def diagram() -> Diagram:
    return Diagram(id="c", title="C", type="class")


def node(d: Diagram, name: str, kind: str = "class", members: bool = True, **args) -> str:
    if members and kind in ("class", "interface") and "fields" not in args:
        args["fields"] = {"operations": ["+ run()"]}
    return apply(d, "add_node", {"name": name, "kind": kind, "id": name.lower(), **args})["touched"][0]


def edge(d: Diagram, source: str, target: str, kind: str = "association", **args) -> str:
    if kind in ("association", "aggregation", "composition") and "fields" not in args:
        args["fields"] = {"target_multiplicity": "1"}
    apply(d, "add_edge", {"source": source, "target": target, "kind": kind, **args})
    return d.edges[-1].id


def found(d: Diagram, rule: str) -> list[rules.Finding]:
    return [f for f in rules.run_checks(d) if f.rule == rule]


def test_every_rule_is_registered():
    assert {r.id for r in rules.rules_for("class")} == RULE_IDS


def test_the_template_is_clean():
    assert rules.run_checks(create_from_template("webshop-domain", "x", "X")) == []


def test_package_cycles():
    d = diagram()
    p, q, r = node(d, "P", "package"), node(d, "Q", "package"), node(d, "R", "package")
    a, b, c = node(d, "A", parent=p), node(d, "B", parent=q), node(d, "C", parent=r)
    node(d, "Solo")
    edge(d, a, b)
    edge(d, b, c, "dependency")
    assert found(d, "class-package-cycle") == []
    back = edge(d, c, a, "dependency")
    [finding] = found(d, "class-package-cycle")
    assert finding.elements == [p, q, r] and back in finding.edges
    assert (finding.category, finding.severity) == ("risk", "medium")


def test_nested_packages_are_lifted_to_the_outermost_package():
    d = diagram()
    outer, inner, other = node(d, "Outer", "package"), node(d, "Inner", "package"), node(d, "Other", "package")
    apply(d, "move_node", {"id": inner, "parent": outer})
    a, b = node(d, "A", parent=inner), node(d, "B", parent=other)
    edge(d, a, b)
    edge(d, b, node(d, "C", parent=outer), "dependency")
    assert [f.elements for f in found(d, "class-package-cycle")] == [sorted([outer, other])]


def test_no_cycle_inside_one_package_or_outside_packages():
    d = diagram()
    p = node(d, "P", "package")
    a, b = node(d, "A", parent=p), node(d, "B", parent=p)
    edge(d, a, b)
    edge(d, b, a)
    c, e = node(d, "C"), node(d, "E")
    edge(d, c, e)
    edge(d, e, c)
    assert found(d, "class-package-cycle") == []


def test_a_part_with_several_wholes():
    d = diagram()
    part, w1, w2 = node(d, "Position"), node(d, "Bestellung"), node(d, "Lieferung")
    edge(d, part, w1, "composition")
    assert found(d, "class-multiple-wholes") == []
    edge(d, part, w2, "composition")
    [finding] = found(d, "class-multiple-wholes")
    assert finding.elements == [part, w1, w2] and len(finding.edges) == 2


def test_one_whole_with_several_parts_is_fine():
    d = diagram()
    whole = node(d, "Bestellung")
    for name in ("P1", "P2", "P3"):
        edge(d, node(d, name), whole, "composition")
    assert found(d, "class-multiple-wholes") == []


def test_composition_cycle():
    d = diagram()
    a, b = node(d, "A"), node(d, "B")
    edge(d, a, b, "composition")
    assert found(d, "class-composition-cycle") == []
    edge(d, b, a, "composition")
    [finding] = found(d, "class-composition-cycle")
    assert finding.elements == [a, b]
    d2 = diagram()
    c = node(d2, "C")
    edge(d2, c, c, "composition")  # a class that contains itself
    assert [f.elements for f in found(d2, "class-composition-cycle")] == [[c]]


def test_multiple_inheritance():
    d = diagram()
    a, b, c = node(d, "A"), node(d, "B"), node(d, "C")
    edge(d, c, a, "generalization")
    assert found(d, "class-multiple-inheritance") == []
    edge(d, c, b, "generalization")
    [finding] = found(d, "class-multiple-inheritance")
    assert finding.elements == [c] and (finding.category, finding.severity) == ("suggestion", "low")


def test_unrealized_interfaces():
    d = diagram()
    i, j, k = node(d, "I", "interface"), node(d, "J", "interface"), node(d, "K")
    edge(d, k, i, "realization")
    edge(d, j, i, "generalization")  # an interface that specialises another one keeps it in use
    assert [f.elements for f in found(d, "class-interface-unrealized")] == [[j]]


def test_abstract_class_without_subclass():
    d = diagram()
    a = node(d, "A", fields={"abstract": True, "operations": ["+ f()"]})
    assert [f.elements for f in found(d, "class-abstract-leaf")] == [[a]]
    edge(d, node(d, "B"), a, "generalization")
    assert found(d, "class-abstract-leaf") == []


def test_isolated_classifiers_only_in_a_diagram_of_three():
    d = diagram()
    a, b = node(d, "A"), node(d, "B")
    assert found(d, "class-isolated") == []
    c = node(d, "C")
    assert {f.elements[0] for f in found(d, "class-isolated")} == {a, b, c}
    edge(d, a, b)
    assert [f.elements for f in found(d, "class-isolated")] == [[c]]


def test_empty_classes_and_interfaces():
    d = diagram()
    c = node(d, "A", members=False)
    i = node(d, "I", "interface", members=False)
    t = node(d, "T", "data_type", members=False)  # a data type is not asked for members
    assert {f.elements[0] for f in found(d, "class-empty")} == {c, i}
    assert t not in {f.elements[0] for f in found(d, "class-empty")}
    apply(d, "update_node", {"id": c, "fields": {"attributes": ["x: int"]}})
    assert {f.elements[0] for f in found(d, "class-empty")} == {i}


def test_associations_without_multiplicity():
    d = diagram()
    a, b = node(d, "A"), node(d, "B")
    bare = edge(d, a, b, fields={})
    [finding] = found(d, "class-association-without-multiplicity")
    assert finding.edges == [bare] and finding.elements == [a, b]
    apply(d, "update_edge", {"id": bare, "fields": {"source_multiplicity": "1"}})
    assert found(d, "class-association-without-multiplicity") == []
    edge(d, a, b, "dependency")  # no multiplicities on a dependency
    assert found(d, "class-association-without-multiplicity") == []


def test_duplicate_members():
    d = diagram()
    a = node(
        d,
        "A",
        fields={
            "attributes": ["- id: UUID", "+ id: String", "- name: String"],
            "operations": ["+ f(x: int)", "+ f(y: int)", "+ f( x:int )", "+ g()"],
        },
    )
    findings = found(d, "class-duplicate-member")
    assert sorted(f.message for f in findings) == sorted(
        ["„A“ führt „- id: UUID“ mehrfach auf.", "„A“ führt „+ f(x: int)“ mehrfach auf."]
    )
    assert all(f.elements == [a] and (f.category, f.severity) == ("risk", "low") for f in findings)


def test_member_keys():
    assert member_key("- id: UUID", False) == "id" == member_key("+ id", False)
    assert member_key("/ total: Money", False) == "/total"  # a derived attribute
    assert member_key("+ place(items: Item[*]): Order", True) == "place(items:item[*])"
    assert member_key("+ place( items : Item[*] )", True) == "place(items:item[*])"
    assert member_key("run", True) == "run()"


def test_empty_enumerations():
    d = diagram()
    e = node(d, "Status", "enumeration")
    [finding] = found(d, "class-enumeration-empty")
    assert finding.elements == [e]
    apply(d, "update_node", {"id": e, "fields": {"literals": ["OFFEN"]}})
    assert found(d, "class-enumeration-empty") == []


def test_multiplicity_text():
    assert all(multiplicity_problem(v) is None for v in ("1", "*", "0..1", "1..*", "10..20", "0..0"))
    assert all(multiplicity_problem(v) for v in ("", "x", "1..", "..1", "2..1", "*..1", "1..2..3", " 1"))


def test_findings_are_linear_in_the_size_of_the_diagram():
    d = diagram()
    previous = node(d, "N0")
    for n in range(1, 200):
        current = node(d, f"N{n}")
        edge(d, previous, current)
        previous = current
    start = time.perf_counter()
    rules.run_checks(d)
    assert time.perf_counter() - start < 2.0
