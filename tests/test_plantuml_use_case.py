"""PlantUML export of use case diagrams (spec: docs/architecture/specs/use-case-diagrams.md).

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


def cases(*nodes: tuple, flows: tuple = ()) -> Diagram:
    """Nodes `(id, kind[, parent[, description]])`, edges `(source, target, kind[, label[, fields]])`; names = ids."""
    d = Diagram(id="u", title="U", type="use_case")
    for node_id, kind, *more in nodes:
        args = {"name": node_id, "kind": kind, "id": node_id}
        if more and more[0]:
            args["parent"] = more[0]
        if len(more) > 1:
            args["description"] = more[1]
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
    return lines[2:-1]


def test_actors_use_cases_and_the_subject():
    lines = body(
        cases(
            ("s", "subject"),
            ("a", "actor"),
            ("u", "use_case", "s"),
            ("v", "use_case"),
            flows=(("a", "u", "association"),),
        )
    )
    assert lines[0] == "left to right direction"
    assert lines[1:] == [
        'rectangle "s" as n_s {',
        '  usecase "u" as n_u',
        "}",
        'actor "a" as n_a',
        'usecase "v" as n_v',
        "n_a -- n_u",
    ]


def test_the_direction_follows_the_diagram():
    d = cases(("a", "actor"))
    d.direction = "TB"
    assert body(d)[0] == "top to bottom direction"


def test_relationships():
    d = cases(
        ("a", "actor"),
        ("b", "actor"),
        ("u", "use_case"),
        ("v", "use_case"),
        ("w", "use_case"),
        flows=(
            ("a", "u", "association"),
            ("a", "v", "association", "startet"),
            ("u", "v", "include"),
            ("w", "u", "extend", None, {"condition": "Gutschein", "extension_point": "Zahlung"}),
            ("w", "v", "extend"),
            ("b", "a", "generalization"),
            ("v", "u", "generalization"),
        ),
    )
    apply(d, "update_edge", {"id": d.edges[0].id, "direction": "forward"})
    lines = body(d)[-7:]
    assert lines == [
        "n_a --> n_u",
        "n_a -- n_v : startet",
        "n_u ..> n_v : <<include>>",
        "n_w ..> n_u : <<extend>> [Gutschein] (Zahlung)",
        "n_w ..> n_v : <<extend>>",
        "n_a <|-- n_b",
        "n_u <|-- n_v",
    ]


def test_the_specification_is_a_note():
    d = cases(("u", "use_case", None, "Ziel: kaufen.\nVorbedingung: angemeldet."), ("v", "use_case", None, "x" * 300))
    lines = body(d)
    assert "note right of n_u : Ziel: kaufen. Vorbedingung: angemeldet." in lines
    note = next(line for line in lines if line.startswith("note right of n_v : "))
    assert note.endswith("…") and len(note.split(" : ", 1)[1]) == 200


def test_hostile_texts_are_plain():
    d = cases(
        ("a", "actor"),
        ("u", "use_case", None, "<b>Ziel</b> %date() ~~x~~"),
        ("v", "use_case"),
        flows=(("u", "v", "extend", None, {"condition": '<i>c</i> "q"', "extension_point": "<u>p</u>"}),),
    )
    d.nodes["a"].name = '<img src=x onerror=alert(1)> "q" %date()'
    text = "\n".join(body(d))
    assert "<b>" not in text and "<i>" not in text and "<u>" not in text and "<img" not in text and "%date" not in text


def test_the_template_exports_every_element():
    d = create_from_template("webshop-use-cases", "x", "X")
    lines = body(d)
    assert "".join(lines).count("{") == "".join(lines).count("}")
    assert sum(line.lstrip().startswith(("actor ", "usecase ", "rectangle ")) for line in lines) == len(d.nodes)
    arrows = (" -- ", " --> ", " ..> ", " <|-- ")
    assert sum(any(a in line for a in arrows) for line in lines) == len(d.edges)
    assert sum(line.startswith("note right of ") for line in lines) == sum(
        bool(n.description) for n in d.nodes.values()
    )


@st.composite
def diagrams(draw) -> Diagram:
    """Random valid use case diagrams: subjects, actors, use cases with notes, the relationships the rules allow."""
    d = Diagram(id="g", title="G", type="use_case")
    subjects = [None]
    for i in range(draw(st.integers(0, 2))):
        apply(d, "add_node", {"name": f"s{i}", "id": f"s{i}", "kind": "subject"})
        subjects.append(f"s{i}")
    ids: list[str] = []
    for i in range(draw(st.integers(1, 9))):
        kind = draw(st.sampled_from(["actor", "use_case", "use_case"]))
        args = {"name": f"n{i}", "id": f"n{i}", "kind": kind}
        if kind == "use_case":
            parent = draw(st.sampled_from(subjects))
            if parent:
                args["parent"] = parent
            args["description"] = draw(st.sampled_from(["", "Ziel: x", '<b>a</b>\nb "q" %date()', "x" * 250]))
        apply(d, "add_node", args)
        ids.append(f"n{i}")
    for _ in range(draw(st.integers(0, 12))):
        kind = draw(st.sampled_from(["association", "include", "extend", "generalization"]))
        args = {"source": draw(st.sampled_from(ids)), "target": draw(st.sampled_from(ids)), "kind": kind}
        if kind == "extend" and draw(st.booleans()):
            args["fields"] = {"condition": "c <i>", "extension_point": "p"}
        try:
            apply(d, "add_edge", args)
        except Exception:  # noqa: BLE001 - a relationship the rules refuse
            continue
    return d


@settings(max_examples=300, deadline=None)
@given(diagrams())
def test_every_use_case_diagram_exports(diagram):
    """Balanced braces, every node declared once, every relationship written once, never an exception."""
    text = exchange.export_plantuml(diagram)
    assert text.count("{") == text.count("}")
    lines = text.splitlines()
    assert sum(1 for line in lines if line.lstrip().startswith(("actor ", "usecase ", "rectangle "))) == len(
        diagram.nodes
    )
    arrows = (" -- ", " --> ", " ..> ", " <|-- ")
    assert sum(1 for line in lines if any(a in line for a in arrows)) == len(diagram.edges)
    assert sum(1 for line in lines if line.startswith("note right of ")) == sum(
        bool((n.description or "").strip()) for n in diagram.nodes.values()
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
    all_diagrams = [*EXPORTED, create_from_template("webshop-use-cases", "x", "X"), *sample]
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
