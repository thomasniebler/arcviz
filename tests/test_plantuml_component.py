"""PlantUML export of component diagrams (spec: docs/architecture/specs/component-diagrams.md).

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


def components(*nodes: tuple, flows: tuple = ()) -> Diagram:
    """Nodes `(id, kind[, parent[, ports]])`, edges `(source, target, kind[, label[, fields]])`; names = ids."""
    d = Diagram(id="c", title="C", type="component")
    for node_id, kind, *more in nodes:
        args = {"name": node_id, "kind": kind, "id": node_id}
        if more and more[0]:
            args["parent"] = more[0]
        if len(more) > 1:
            args["fields"] = {"ports": more[1]}
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


def test_components_subsystems_interfaces_and_packages():
    lines = body(
        components(
            ("p", "package"),
            ("s", "subsystem", "p"),
            ("a", "component", "s"),
            ("i", "interface", "s"),
            ("b", "component"),
        )
    )
    assert lines == [
        'package "p" as n_p {',
        '  component "s" as n_s <<subsystem>> {',
        '    component "a" as n_a',
        '    interface "i" as n_i',
        "  }",
        "}",
        'component "b" as n_b',
    ]


def test_ports_are_portin_with_an_alias():
    lines = body(components(("a", "component", None, ["orders-in: OrderApi", "out"])))
    assert lines == [
        'component "a" as n_a {',
        '  portin "orders-in" as n_a_p0',
        '  portin "out" as n_a_p1',
        "}",
    ]


def test_relationships_and_ports_as_ends():
    lines = body(
        components(
            ("a", "component", None, ["in", "out"]),
            ("b", "component"),
            ("i", "interface"),
            flows=(
                ("a", "b", "connection", "ruft", {"source_port": "out"}),
                ("b", "a", "connection", None, {"target_port": "in"}),
                ("a", "i", "realization", None, {"source_port": "in"}),
                ("b", "i", "usage"),
                ("b", "a", "dependency"),
            ),
        )
    )
    assert lines[-5:] == [
        "n_a_p1 --> n_b : ruft",
        "n_b --> n_a_p0",
        "n_a_p0 ..|> n_i",
        "n_b ..> n_i : <<use>>",
        "n_b ..> n_a",
    ]


def test_hostile_texts_are_plain():
    d = components(("a", "component", None, ['<b>p</b> "q" %date()']), ("b", "component"))
    apply(d, "update_node", {"id": "a", "name": '<img src=x onerror=alert(1)> "q"'})
    apply(d, "add_edge", {"source": "a", "target": "b", "label": "<i>x</i> & %date()"})
    text = "\n".join(body(d))
    assert "<b>" not in text and "<i>" not in text and "<img" not in text and "%date" not in text


def test_the_template_exports_every_element():
    d = create_from_template("webshop-components", "x", "X")
    lines = body(d)
    assert "".join(lines).count("{") == "".join(lines).count("}")
    assert sum(line.lstrip().startswith(("component ", "interface ", "package ")) for line in lines) == len(d.nodes)
    arrows = (" --> ", " ..> ", " ..|> ")
    assert sum(any(a in line for a in arrows) for line in lines) == len(d.edges)


def test_too_deep_nesting_is_refused():
    d = Diagram(id="c", title="C", type="component")
    parent = None
    for n in range(exchange.MAX_PLANTUML_NESTING + 5):
        args = {"name": f"p{n}", "id": f"p{n}", "kind": "package", **({"parent": parent} if parent else {})}
        apply(d, "add_node", args)
        parent = f"p{n}"
    with pytest.raises(exchange.ExchangeError, match="nested more than"):
        exchange.export_plantuml(d)


@st.composite
def diagrams(draw) -> Diagram:
    """Random valid component diagrams: packages, subsystems, components with ports, interfaces, the edges the rules allow."""
    d = Diagram(id="g", title="G", type="component")
    containers: list[str | None] = [None]
    for i in range(draw(st.integers(0, 3))):
        kind = draw(st.sampled_from(["package", "subsystem"]))
        parent = draw(st.sampled_from(containers))
        try:
            apply(
                d, "add_node", {"name": f"c{i}", "id": f"c{i}", "kind": kind, **({"parent": parent} if parent else {})}
            )
            containers.append(f"c{i}")
        except Exception:  # noqa: BLE001 - a package inside a subsystem
            continue
    ids: list[str] = []
    for i in range(draw(st.integers(1, 8))):
        kind = draw(st.sampled_from(["component", "component", "interface"]))
        args = {"name": f"n{i}", "id": f"n{i}", "kind": kind}
        parent = draw(st.sampled_from(containers))
        if parent:
            args["parent"] = parent
        if kind == "component" and draw(st.booleans()):
            args["fields"] = {"ports": draw(st.sampled_from([["in"], ["in", "out: T"], ['<b>"p"</b>']]))}
        apply(d, "add_node", args)
        ids.append(f"n{i}")
    for _ in range(draw(st.integers(0, 12))):
        kind = draw(st.sampled_from(["connection", "realization", "usage", "dependency"]))
        source, target = draw(st.sampled_from(ids)), draw(st.sampled_from(ids))
        args = {"source": source, "target": target, "kind": kind}
        ports = [p.split(":")[0].strip() for p in d.nodes[source].properties.get("ports", "").split("\n") if p]
        if ports and draw(st.booleans()):
            args["fields"] = {"source_port": ports[0]}
        try:
            apply(d, "add_edge", args)
        except Exception:  # noqa: BLE001 - an edge the rules refuse
            continue
    return d


@settings(max_examples=300, deadline=None)
@given(diagrams())
def test_every_component_diagram_exports(diagram):
    """Balanced braces, every node declared once, every edge written once, never an exception."""
    text = exchange.export_plantuml(diagram)
    assert text.count("{") == text.count("}")
    lines = text.splitlines()
    heads = ("component ", "interface ", "package ")
    assert sum(1 for line in lines if line.lstrip().startswith(heads)) == len(diagram.nodes)
    arrows = (" --> ", " ..> ", " ..|> ")
    assert sum(1 for line in lines if any(a in line for a in arrows)) == len(diagram.edges)


JAR = os.environ.get("ARCVIZ_PLANTUML_JAR")


@pytest.mark.skipif(not JAR or not shutil.which("java"), reason="needs ARCVIZ_PLANTUML_JAR and java")
def test_plantuml_renders_the_exports(tmp_path):
    sample: list[Diagram] = []

    @settings(max_examples=40, deadline=None, database=None, derandomize=True)
    @given(diagrams())
    def collect(diagram):
        sample.append(diagram)

    collect()
    all_diagrams = [*EXPORTED, create_from_template("webshop-components", "x", "X"), *sample]
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
