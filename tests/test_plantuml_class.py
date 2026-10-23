"""PlantUML export of class diagrams (spec: docs/architecture/specs/class-diagrams.md).

One test per construct. With `ARCVIZ_PLANTUML_JAR` pointing at a PlantUML jar (and `java` on the path) the exports are
also rendered by PlantUML itself and must not report a syntax error (checked with 1.2026.8).
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from arcviz import exchange
from arcviz.model import Diagram
from arcviz.operations import apply
from arcviz.templates import create_from_template

EXPORTED: list[Diagram] = []


def classes(*nodes: tuple, flows: tuple = ()) -> Diagram:
    """Nodes `(id, kind[, parent[, fields]])`, edges `(source, target, kind[, label[, fields]])`; names = ids."""
    d = Diagram(id="c", title="C", type="class")
    for node_id, kind, *more in nodes:
        args = {"name": node_id, "kind": kind, "id": node_id}
        if more and more[0]:
            args["parent"] = more[0]
        if len(more) > 1:
            args["fields"] = more[1]
        apply(d, "add_node", args)
    for source, target, kind, *more in flows:
        args = {"source": source, "target": target, "kind": kind}
        if more and more[0]:
            args["label"] = more[0]
        if len(more) > 1:
            args["fields"] = more[1]
        apply(d, "add_edge", args)
    return d


def body(diagram: Diagram) -> list[str]:
    EXPORTED.append(diagram)
    lines = exchange.export_plantuml(diagram).splitlines()
    assert lines[0] == "@startuml" and lines[1].startswith("title ") and lines[-1] == "@enduml"
    return lines[3:-1]  # without the direction line


def test_classifiers_with_members():
    lines = body(
        classes(
            ("a", "class", None, {"attributes": ["- id: UUID"], "operations": ["+ run(): void"], "abstract": True}),
            ("i", "interface", None, {"operations": ["+ go()"]}),
            ("e", "enumeration", None, {"literals": ["OFFEN", "ZU"]}),
            ("t", "data_type", None, {"attributes": ["betrag: Decimal"]}),
            ("plain", "class"),
        )
    )
    assert lines == [
        'abstract class "a" as n_a {',
        "  - id: UUID",
        "  + run(): void",
        "}",
        'interface "i" as n_i {',
        "  + go()",
        "}",
        'enum "e" as n_e {',
        "  OFFEN",
        "  ZU",
        "}",
        'class "t" as n_t <<dataType>> {',
        "  betrag: Decimal",
        "}",
        'class "plain" as n_plain',
    ]


def test_packages_nest():
    lines = body(classes(("p", "package"), ("q", "package", "p"), ("a", "class", "q")))
    assert lines == ['package "p" as n_p {', '  package "q" as n_q {', '    class "a" as n_a', "  }", "}"]


def test_relationships():
    d = classes(
        ("a", "class"),
        ("b", "class"),
        ("i", "interface"),
        flows=(
            (
                "a",
                "b",
                "association",
                "kennt",
                {"source_multiplicity": "1", "target_multiplicity": "0..*", "target_role": "bs"},
            ),
            ("a", "b", "association"),
            ("b", "a", "association"),
            ("a", "i", "realization"),
            ("a", "b", "dependency"),
            ("b", "a", "generalization"),
        ),
    )
    apply(d, "update_edge", {"id": d.edges[1].id, "direction": "forward"})
    lines = body(d)[-6:]
    assert lines == [
        'n_a "1" -- "0..*" n_b : kennt (bs)',
        "n_a --> n_b",
        "n_b -- n_a",
        "n_i <|.. n_a",
        "n_a ..> n_b",
        "n_a <|-- n_b",
    ]


def test_whole_part_relationships_are_written_whole_first():
    d = classes(
        ("whole", "class"),
        ("part", "class"),
        flows=(
            ("part", "whole", "composition", "enthält", {"source_multiplicity": "1..*", "target_multiplicity": "1"}),
            ("part", "whole", "aggregation", None, {"source_role": "teile"}),
        ),
    )
    lines = body(d)[-2:]
    assert lines == ['n_whole "1" *-- "1..*" n_part : enthält', "n_whole o-- n_part : (teile)"]


def test_hostile_texts_are_plain():
    d = classes(
        ("a", "class", None, {"attributes": ['- <b>x</b> "q" ~~: %date()']}),
        ("b", "class"),
        flows=(("a", "b", "association", "<i>n</i> & co", {"source_role": "<u>r</u>"}),),
    )
    text = "\n".join(body(d))
    assert "<b>" not in text and "<i>" not in text and "<u>" not in text and "%date" not in text


def test_the_template_exports_every_classifier_and_relationship():
    d = create_from_template("webshop-domain", "x", "X")
    lines = body(d)
    assert "".join(lines).count("{") == "".join(lines).count("}")
    relationships = [
        line for line in lines if any(a in line for a in (" -- ", " --> ", "*--", "o--", "<|--", "<|..", "..>"))
    ]
    assert len(relationships) == len(d.edges)
    declared = [
        line for line in lines if line.lstrip().split(" ")[0] in ("class", "abstract", "interface", "enum", "package")
    ]
    assert len(declared) == len(d.nodes)


def test_too_deep_nesting_is_refused():
    d = Diagram(id="c", title="C", type="class")
    parent = None
    for n in range(exchange.MAX_PLANTUML_NESTING + 5):
        args = {"name": f"p{n}", "id": f"p{n}", "kind": "package", **({"parent": parent} if parent else {})}
        apply(d, "add_node", args)
        parent = f"p{n}"
    with pytest.raises(exchange.ExchangeError, match="nested more than"):
        exchange.export_plantuml(d)


@st.composite
def diagrams(draw) -> Diagram:
    """Random valid class diagrams: packages, classifiers with members, the relationships the rules allow."""
    d = Diagram(id="g", title="G", type="class")
    packages: list[str | None] = [None]
    for i in range(draw(st.integers(0, 3))):
        parent = draw(st.sampled_from(packages))
        apply(
            d, "add_node", {"name": f"p{i}", "id": f"p{i}", "kind": "package", **({"parent": parent} if parent else {})}
        )
        packages.append(f"p{i}")
    ids: list[str] = []
    for i in range(draw(st.integers(1, 8))):
        kind = draw(st.sampled_from(["class", "class", "interface", "enumeration", "data_type"]))
        fields = {}
        if kind in ("class", "data_type") and draw(st.booleans()):
            fields["attributes"] = [draw(st.sampled_from(["- a: int", "+ b(x: int)", "<b>#lt;", 'q"uote']))]
        if kind == "class" and draw(st.booleans()):
            fields["abstract"] = True
        parent = draw(st.sampled_from(packages))
        apply(
            d,
            "add_node",
            {"name": f"n{i}", "id": f"n{i}", "kind": kind, "fields": fields, **({"parent": parent} if parent else {})},
        )
        ids.append(f"n{i}")
    kinds = ["association", "aggregation", "composition", "generalization", "realization", "dependency"]
    for _ in range(draw(st.integers(0, 12))):
        args = {
            "source": draw(st.sampled_from(ids)),
            "target": draw(st.sampled_from(ids)),
            "kind": draw(st.sampled_from(kinds)),
        }
        if args["kind"] in ("association", "aggregation", "composition") and draw(st.booleans()):
            args["fields"] = {"source_multiplicity": draw(st.sampled_from(["1", "*", "0..1"])), "target_role": "r"}
        try:
            apply(d, "add_edge", args)
        except Exception:  # noqa: BLE001 - a relationship the rules refuse
            continue
    return d


@settings(max_examples=300, deadline=None)
@given(diagrams())
def test_every_class_diagram_exports(diagram):
    """Balanced braces, every node declared once, every relationship written once, never an exception."""
    text = exchange.export_plantuml(diagram)
    assert text.count("{") == text.count("}")
    lines = text.splitlines()
    heads = ("class ", "abstract class ", "interface ", "enum ", "package ")
    assert sum(1 for line in lines if line.lstrip().startswith(heads)) == len(diagram.nodes)
    arrows = (" -- ", " --> ", " *-- ", " o-- ", " <|-- ", " <|.. ", " ..> ")
    assert sum(1 for line in lines if any(a in line for a in arrows) and not line.lstrip().startswith(heads)) == len(
        diagram.edges
    )


JAR = os.environ.get("ARCVIZ_PLANTUML_JAR")


@pytest.mark.skipif(not JAR or not shutil.which("java"), reason="needs ARCVIZ_PLANTUML_JAR and java")
def test_plantuml_renders_the_exports(tmp_path):
    sample: list[Diagram] = []

    @settings(max_examples=40, deadline=None, database=None, derandomize=True)
    @given(diagrams())
    def collect(diagram):
        sample.append(diagram)

    collect()
    all_diagrams = [*EXPORTED, create_from_template("webshop-domain", "x", "X"), *sample]
    for n, diagram in enumerate(all_diagrams):
        (tmp_path / f"d{n:03}.puml").write_text(exchange.export_plantuml(diagram), encoding="utf-8")
    result = subprocess.run(
        ["java", "-jar", str(JAR), "-tsvg", "-failfast2", str(tmp_path / "*.puml")],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(list(tmp_path.glob("*.svg"))) == len(all_diagrams)
