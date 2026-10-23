"""PlantUML export of state machine diagrams (spec: docs/architecture/specs/state-machine-diagrams.md).

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


def machine(*nodes: tuple, flows: tuple = ()) -> Diagram:
    """Nodes `(id, kind[, parent])`, transitions `(source, target[, trigger[, guard]])`; names = ids."""
    d = Diagram(id="m", title="M", type="state_machine")
    for node_id, kind, *parent in nodes:
        args = {"name": node_id, "kind": kind, "id": node_id, **({"parent": parent[0]} if parent else {})}
        apply(d, "add_node", args)
    for source, target, *more in flows:
        args = {"source": source, "target": target}
        for key, value in zip(("label", "guard"), more, strict=False):
            if value:
                args[key] = value
        apply(d, "add_edge", args)
    return d


def body(diagram: Diagram) -> list[str]:
    EXPORTED.append(diagram)
    lines = exchange.export_plantuml(diagram).splitlines()
    assert lines[0] == "@startuml" and lines[1].startswith("title ") and lines[-1] == "@enduml"
    return lines[3:-1]  # without the direction line


def test_states_transitions_and_the_start_and_end():
    lines = body(
        machine(
            ("i", "initial_pseudostate"),
            ("a", "state"),
            ("e", "final_state"),
            flows=(("i", "a"), ("a", "e", "go", "x > 1")),
        )
    )
    assert lines == [
        'state "i" as n_i <<start>>',
        'state "a" as n_a',
        'state "e" as n_e <<end>>',
        "n_i --> n_a",
        "n_a --> n_e : go [x > 1]",
    ]


def test_terminate_and_junction_are_marked_on_a_line_of_their_own():
    lines = body(
        machine(
            ("i", "initial_pseudostate"),
            ("j", "junction_pseudostate"),
            ("t", "terminate_pseudostate"),
            flows=(("i", "j"), ("j", "t")),
        )
    )
    assert lines.index("' junction: j") + 1 == lines.index('state "j" as n_j <<choice>>')
    assert lines.index("' terminate: t") + 1 == lines.index('state "t" as n_t <<end>>')


def test_choice_fork_join_and_history():
    lines = body(
        machine(
            ("c", "composite_state"),
            ("h", "shallow_history_pseudostate", "c"),
            ("d", "deep_history_pseudostate", "c"),
            ("s", "state", "c"),
            ("ch", "choice_pseudostate"),
            ("f", "fork_pseudostate"),
            ("j", "join_pseudostate"),
            flows=(("s", "h"), ("d", "s"), ("ch", "c", "", "a"), ("f", "j")),
        )
    )
    assert '  state "h" as n_h <<history>>' in lines and '  state "d" as n_d <<history*>>' in lines
    assert 'state "ch" as n_ch <<choice>>' in lines
    assert 'state "f" as n_f <<fork>>' in lines and 'state "j" as n_j <<join>>' in lines
    assert "n_c.n_s --> n_c.n_h" in lines and "n_c.n_d --> n_c.n_s" in lines


def test_regions_are_separated_and_their_states_qualified():
    lines = body(
        machine(
            ("c", "composite_state"),
            ("r1", "state_region", "c"),
            ("r2", "state_region", "c"),
            ("i1", "initial_pseudostate", "r1"),
            ("a", "state", "r1"),
            ("i2", "initial_pseudostate", "r2"),
            ("b", "state", "r2"),
            ("out", "state"),
            flows=(("i1", "a"), ("i2", "b"), ("b", "out", "x"), ("out", "a", "y")),
        )
    )
    assert "  --" in lines and "  ' region: r1" in lines and "  ' region: r2" in lines
    assert "n_c.n_i1 --> n_c.n_a" in lines and "n_c.n_i2 --> n_c.n_b" in lines
    assert "n_c.n_b --> n_out : x" in lines and "n_out --> n_c.n_a : y" in lines


def test_nested_composites_use_the_outermost_name():
    lines = body(
        machine(
            ("c", "composite_state"),
            ("d", "composite_state", "c"),
            ("e", "state", "d"),
            ("x", "state"),
            flows=(("x", "e"), ("e", "x")),
        )
    )
    assert "n_x --> n_c.n_e" in lines and "n_c.n_e --> n_x" in lines


def test_behaviours_and_hostile_texts():
    d = machine(("a", "state"), ("b", "state"), flows=(("a", "b", '<b>x</b> "q" ~~', "a & b"),))
    apply(d, "update_node", {"id": "a", "fields": {"entry": "<i>on</i> %date()"}})
    lines = body(d)
    assert any(line.startswith("n_a : entry / ") and "<i>" not in line and "%date" not in line for line in lines)
    assert not any("<b>" in line or '"q"' in line for line in lines if " --> " in line)


def test_the_template_exports_every_state_and_transition():
    d = create_from_template("order-lifecycle", "x", "X")
    lines = body(d)
    assert sum(" --> " in line for line in lines) == len(d.edges)
    assert sum(line.lstrip().startswith("state ") for line in lines) == sum(
        n.kind != "state_region" for n in d.nodes.values()
    )
    assert "".join(lines).count("{") == "".join(lines).count("}")


def test_too_deep_nesting_is_refused():
    d = Diagram(id="m", title="M", type="state_machine")
    parent = None
    for n in range(exchange.MAX_PLANTUML_NESTING + 5):
        apply(
            d,
            "add_node",
            {"name": f"c{n}", "id": f"c{n}", "kind": "composite_state", **({"parent": parent} if parent else {})},
        )
        parent = f"c{n}"
    with pytest.raises(exchange.ExchangeError, match="nested more than"):
        exchange.export_plantuml(d)


@st.composite
def machines(draw) -> Diagram:
    """Random valid state machines: composite states with or without regions, pseudostates, transitions the rules allow."""
    d = Diagram(id="g", title="G", type="state_machine")
    containers: list[tuple[str | None, str]] = [(None, "top")]
    for i in range(draw(st.integers(0, 3))):
        parent = draw(st.sampled_from([c for c, kind in containers if kind in ("top", "plain", "region")]))
        apply(
            d,
            "add_node",
            {"name": f"c{i}", "id": f"c{i}", "kind": "composite_state", **({"parent": parent} if parent else {})},
        )
        if draw(st.booleans()):
            for k in range(draw(st.integers(1, 3))):
                apply(
                    d, "add_node", {"name": f"c{i}r{k}", "id": f"c{i}r{k}", "kind": "state_region", "parent": f"c{i}"}
                )
                containers.append((f"c{i}r{k}", "region"))
        else:
            containers.append((f"c{i}", "plain"))
    kinds = [
        "state",
        "state",
        "state",
        "initial_pseudostate",
        "final_state",
        "choice_pseudostate",
        "fork_pseudostate",
        "join_pseudostate",
        "shallow_history_pseudostate",
        "deep_history_pseudostate",
        "terminate_pseudostate",
        "junction_pseudostate",
    ]
    ids: list[str] = []
    for i in range(draw(st.integers(1, 9))):
        parent = draw(st.sampled_from([c for c, _ in containers]))
        args = {
            "name": f"n{i}",
            "id": f"n{i}",
            "kind": draw(st.sampled_from(kinds)),
            **({"parent": parent} if parent else {}),
        }
        try:
            apply(d, "add_node", args)
            ids.append(f"n{i}")
        except Exception:  # noqa: BLE001 - a draw the rules refuse (a state next to regions)
            continue
    for _ in range(draw(st.integers(0, 14))):
        if not ids:
            break
        source, target = draw(st.sampled_from(ids)), draw(st.sampled_from(ids))
        try:
            apply(d, "add_edge", {"source": source, "target": target})
        except Exception:  # noqa: BLE001 - a transition the rules refuse
            continue
    return d


@settings(max_examples=300, deadline=None)
@given(machines())
def test_every_state_machine_exports(diagram):
    """Balanced braces, every vertex declared once, every transition written once, never an exception."""
    text = exchange.export_plantuml(diagram)
    assert text.count("{") == text.count("}")
    lines = text.splitlines()
    assert sum(1 for line in lines if " --> " in line) == len(diagram.edges)
    assert sum(1 for line in lines if line.lstrip().startswith("state ")) == sum(
        n.kind != "state_region" for n in diagram.nodes.values()
    )


JAR = os.environ.get("ARCVIZ_PLANTUML_JAR")


@pytest.mark.skipif(not JAR or not shutil.which("java"), reason="needs ARCVIZ_PLANTUML_JAR and java")
def test_plantuml_renders_the_exports(tmp_path):
    diagrams = [
        *EXPORTED,
        create_from_template("order-lifecycle", "x", "X"),
        *machines_for_the_jar(),
    ]
    for n, diagram in enumerate(diagrams):
        (tmp_path / f"d{n:03}.puml").write_text(exchange.export_plantuml(diagram), encoding="utf-8")
    result = subprocess.run(
        ["java", "-jar", str(JAR), "-tsvg", "-failfast2", str(tmp_path / "*.puml")],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(list(tmp_path.glob("*.svg"))) == len(diagrams)


def machines_for_the_jar() -> list[Diagram]:
    """A fixed sample of the property strategy's draws."""
    out: list[Diagram] = []

    @settings(max_examples=40, deadline=None, database=None, derandomize=True)
    @given(machines())
    def collect(diagram):
        out.append(diagram)

    collect()
    return out
