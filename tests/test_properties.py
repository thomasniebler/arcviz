"""Property-based tests of operations -> integrity -> Mermaid (spec: docs/architecture/specs/property-tests.md)."""

from __future__ import annotations

import contextlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from hypothesis import given
from hypothesis import strategies as st

from arcviz import diff, exchange, mermaid, operations, project_model, render, rules, sequence, sequence_svg
from arcviz.model import SUMMARY_SLACK, Diagram, Edge, Node, single_line
from arcviz.projects import Project
from arcviz.templates import TEMPLATES
from tests.strategies import names, operation, project_operation, texts

START = st.sampled_from(sorted(TEMPLATES))
STEPS = st.integers(min_value=1, max_value=25)


def start_diagram(template: str) -> Diagram:
    return TEMPLATES[template]("prop", "Property test")


def run(data: st.DataObject, diagram: Diagram, steps: int, check=None) -> list[str]:
    """Apply `steps` random operations; returns the ids touched by the successful ones."""
    touched: list[str] = []
    for _ in range(steps):
        op, args = operation(data, diagram)
        before = diagram.model_dump()
        try:
            outcome = operations.apply(diagram, op, args)
        except operations.OperationError:
            assert diagram.model_dump() == before, f"rejected {op} changed the diagram"
        else:
            touched = outcome["touched"]
            assert all(diagram.has_element(t) for t in touched), f"{op} reported a touched id that does not exist"
        if check:
            check(diagram)
    return touched


def assert_one_line(dumped: object) -> None:
    """Every name, title and label in a dumped diagram or project is one line without control characters (#103)."""
    if isinstance(dumped, dict):
        for key, value in dumped.items():
            if key in operations.ONE_LINE_ARGUMENTS and isinstance(value, str):
                assert value == single_line(value), f"{key} {value!r} is not one line"
            assert_one_line(value)
    elif isinstance(dumped, list):
        for item in dumped:
            assert_one_line(item)


def assert_integrity(diagram: Diagram) -> None:
    assert diagram.integrity_errors() == []
    assert all(key == node.id for key, node in diagram.nodes.items())
    edge_ids = [e.id for e in diagram.edges]
    assert len(edge_ids) == len(set(edge_ids)), "edge ids are unique"
    for node in diagram.nodes.values():
        if node.parent is not None:
            assert diagram.nodes[node.parent].is_container, f"{node.id} sits in a non-container"
    # every reachable state can be exported and imported again (the import rules are the strictest)
    assert exchange._rule_errors(diagram) == []
    # links to project elements (shared-project-model.md): only on view elements that can represent one, and an
    # unset ref never appears in the stored document (old documents keep their fingerprints)
    for view in project_model.view_elements(diagram):
        if view.ref is not None:
            assert project_model.linkable_kinds(diagram.type, view.kind), view
    dumped = diagram.model_dump(mode="json")
    for node in dumped["nodes"].values():
        assert node.get("ref", "set") is not None
    for lifeline in (dumped.get("interaction") or {}).get("lifelines", []):
        assert lifeline.get("ref", "set") is not None
    # links to relations (model-relations.md): only on connections that can show one, whose ends both stand for
    # elements (a link needs them, and a change of an end clears it); an unset ref is never stored
    for connection in project_model.view_connections(diagram):
        if connection.ref is not None:
            assert project_model.relation_kinds_for(diagram.type, connection.kind), connection
            assert connection.source_ref is not None and connection.target_ref is not None, connection
    for edge in dumped["edges"]:
        assert edge.get("ref", "set") is not None
    assert '"ref": null' not in json.dumps(dumped)
    assert_one_line(dumped)
    # completeness and hidden markers (ADR-0137): kinds the type can show, sorted, no duplicates, omitted while empty
    assert set(diagram.complete) <= set(project_model.shown_relation_kinds(diagram.type))
    assert diagram.complete == sorted(set(diagram.complete))
    assert diagram.hidden_relations == sorted(set(diagram.hidden_relations))
    assert dumped.get("complete", ["x"]) != [] and dumped.get("hidden_relations", ["x"]) != []


# ---------------------------------------------------------------------------
# Mermaid source structure
# ---------------------------------------------------------------------------

_DEF = re.compile(r"^\s*(subgraph )?(n_\w+)(?=[^\w\s])")
_EDGE = re.compile(r'^  (n_\w+) ([-.=<>o]+)(?:\|"([^"]*)"\|)? (n_\w+)$')


@dataclass
class Parsed:
    definitions: dict[str, list[tuple[str | None, bool]]] = field(default_factory=dict)  # mid -> [(parent, subgraph)]
    edges: list[tuple[str, str]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


def parse(src: str) -> Parsed:
    src = src.removeprefix(mermaid.SWIMLANE_FRONT_MATTER)  # activity diagrams with partitions (ADR-0038)
    out = Parsed(lines=src.splitlines())
    stack: list[str] = []
    for line in out.lines[1:]:
        if line.strip() == "end":
            assert stack, "unbalanced end"
            stack.pop()
            continue
        if edge := _EDGE.match(line):
            out.edges.append((edge.group(1), edge.group(4)))
            if edge.group(3) is not None:
                out.labels.append(edge.group(3))
            continue
        if definition := _DEF.match(line):
            subgraph, mid = bool(definition.group(1)), definition.group(2)
            out.definitions.setdefault(mid, []).append((stack[-1] if stack else None, subgraph))
            if '"' in line:  # activity control nodes (`@{ shape: f-circ }`) have no label
                out.labels.append(line[line.index('"') + 1 : line.rindex('"')])
            if subgraph:
                stack.append(mid)
            continue
        assert re.match(r"^\s*(direction [A-Z]{2}|classDef |class |linkStyle )", line), f"unexpected line {line!r}"
    assert not stack, "unclosed subgraph"
    return out


def assert_labels_escaped(parsed: Parsed) -> None:
    for label in parsed.labels:
        raw = label.replace("<br/>", "")
        assert not set(raw) & set('<>"&'), f"unescaped text in label {label!r}"


def assert_full_render(diagram: Diagram, src: str) -> Parsed:
    ids = mermaid.id_map(diagram)
    assert len(set(ids.values())) == len(ids), "id_map is injective"
    assert all(re.fullmatch(r"n_[A-Za-z0-9_]+", m) for m in ids.values())
    assert src.removeprefix(mermaid.SWIMLANE_FRONT_MATTER).splitlines()[0] == f"flowchart {diagram.direction}"
    partitions = any(n.kind == "activity_partition" for n in diagram.nodes.values())
    assert src.startswith(mermaid.SWIMLANE_FRONT_MATTER) == partitions
    parsed = parse(src)
    assert set(parsed.definitions) == set(ids.values())
    for node in diagram.nodes.values():
        [(parent, subgraph)] = parsed.definitions[ids[node.id]]
        assert parent == (ids[node.parent] if node.parent else None), f"{node.id} drawn outside its parent"
        assert subgraph == bool(node.is_container or diagram.children(node.id))
    assert parsed.edges == [(ids[e.source], ids[e.target]) for e in diagram.edges]
    assert_labels_escaped(parsed)
    return parsed


def assert_focus_render(diagram: Diagram, focus: str) -> None:
    ids = mermaid.id_map(diagram)
    parsed = parse(mermaid.render(diagram, focus=focus))
    visible = {focus, *(d.id for d in diagram.descendants(focus))}
    touching = [e for e in diagram.edges if e.source in visible or e.target in visible]
    stubs = {end for e in touching for end in (e.source, e.target) if end not in visible}
    assert set(parsed.definitions) == {ids[n] for n in visible | stubs}
    for stub in stubs:
        assert parsed.definitions[ids[stub]] == [(None, False)], "stubs are plain nodes at the top level"
    assert parsed.definitions[ids[focus]][0][0] is None
    for node_id in visible - {focus}:
        [(parent, _)] = parsed.definitions[ids[node_id]]
        assert parent == ids[diagram.nodes[node_id].parent]  # type: ignore[index]
    assert parsed.edges == [(ids[e.source], ids[e.target]) for e in touching]
    stub_classes = [line for line in parsed.lines if line.endswith(" stub") and line.startswith("  class ")]
    assert stub_classes == ([f"  class {','.join(sorted(ids[s] for s in stubs))} stub"] if stubs else [])
    assert_labels_escaped(parsed)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@given(START, STEPS, st.data())
def test_operations_keep_integrity_and_rejected_ones_change_nothing(template, steps, data):
    diagram = start_diagram(template)
    run(data, diagram, steps, check=assert_integrity)


def assert_svg(diagram: Diagram, highlight: list[str]) -> None:
    """The own SVG renderer: well-formed XML, every element exactly once with its id, text shown literally."""
    source = sequence_svg.render(diagram, highlight=highlight)
    root = ET.fromstring(source)  # raises on anything not escaped
    ids = [g.get("data-id") for g in root.iter("{http://www.w3.org/2000/svg}g") if g.get("data-id") is not None]
    assert sorted(ids) == sorted(sequence.element_ids(diagram.interaction))
    text = "".join(root.itertext())
    for lifeline in diagram.interaction.lifelines:
        assert sequence_svg.head_label(lifeline) in text
    for message in sequence.messages(diagram.interaction):
        assert message.name in text
    changed = {
        g.get("data-id")
        for g in root.iter("{http://www.w3.org/2000/svg}g")
        if "changed" in (g.get("class") or "").split()
    }
    assert changed == set(highlight)
    x, y, w, h = (float(v) for v in root.get("viewBox").split())
    assert w > 0 and h > 0


@given(START, STEPS, st.data())
def test_mermaid_structure_matches_the_model(template, steps, data):
    diagram = start_diagram(template)
    touched = run(data, diagram, steps)
    if render.renderer_for(diagram).name == "svg":
        assert_svg(diagram, sorted(set(touched)))
        return
    parsed = assert_full_render(diagram, mermaid.render(diagram, highlight=touched))
    changed = sorted(mermaid.id_map(diagram)[t] for t in set(touched))
    highlight = [line for line in parsed.lines if line.startswith("  class ") and line.endswith(" changed")]
    assert highlight == ([f"  class {','.join(changed)} changed"] if changed else [])


@given(START, STEPS, st.data())
def test_focused_views_show_the_subtree_and_stubs(template, steps, data):
    diagram = start_diagram(template)
    run(data, diagram, steps)
    for node_id in sorted(diagram.nodes):
        assert_focus_render(diagram, node_id)


def _unescape(text: str) -> str:
    """What Mermaid shows for an escaped label (it decodes `#name;` / `#<number>;` entity codes)."""
    named = {"quot": '"', "gt": ">", "lt": "<", "amp": "&"}
    return re.sub(
        r"#(\w+);",
        lambda m: named.get(m.group(1)) or (chr(int(m.group(1))) if m.group(1).isdigit() else m.group(0)),
        text,
    )


@given(START, STEPS, st.data())
def test_rule_findings_reference_existing_elements(template, steps, data):
    """Every rule of every type runs on any reachable state and names only what exists (rule-checks.md)."""
    diagram = start_diagram(template)
    run(data, diagram, steps)
    findings = rules.run_checks(diagram)
    assert len({f.id for f in findings}) == len(findings)
    edge_ids = {e.id for e in diagram.edges}
    for finding in findings:
        assert finding.elements or finding.edges, finding
        assert all(diagram.has_element(e) for e in finding.elements), finding
        assert set(finding.edges) <= edge_ids, finding
        assert (finding.severity == "info") == (finding.category == "strength"), finding


@given(texts)
def test_escape_text_removes_markup(text):
    assert not set(mermaid.escape_text(text)) & set('<>"&')


@given(texts)
def test_escape_text_shows_text_literally(text):
    # literally as the operations store it: one line (#103); injective on one-line text (#102)
    assert _unescape(mermaid.escape_text(text)) == single_line(text)


def _mermaid_prepass(line: str) -> str:
    """Mermaid 11's `encodeEntities` runs these over the source before decoding entity codes (vendored build): on a
    line with `style…:` or `classDef…:` it drops the last `;` of a following `#…;` (#102 review)."""
    for pattern in (r"style.*:\S*#.*;", r"classDef.*:\S*#.*;"):
        line = re.sub(pattern, lambda m: m.group(0)[:-1], line)
    return line


@given(texts)
def test_escaped_labels_survive_mermaids_style_prepass(text):
    for line in (
        f'  n_lifestyle["«service»<br/>{mermaid.escape_text(text)}"]',
        f'  n_a -->|"style {mermaid.escape_text(text)}"| n_b',
    ):
        label = line.split('"')[1]
        assert _unescape(_mermaid_prepass(line).split('"')[1]) == _unescape(label)


def test_a_colon_after_style_does_not_lose_an_entity_code():
    line = f'  n_x["«service»<br/>{mermaid.escape_text("Lifestyle:#1")}"]'
    assert _unescape(_mermaid_prepass(line).split('"')[1]) == "«service»<br/>Lifestyle:#1"


def test_escape_text_shows_literal_entity_codes_literally():
    assert _unescape(mermaid.escape_text("#lt;b#gt; #35;")) == "#lt;b#gt; #35;"


@given(START, STEPS, st.data())
def test_exchange_round_trip(template, steps, data):
    diagram = start_diagram(template)
    run(data, diagram, steps)
    assert exchange.parse_document(exchange.export_document(diagram)) == diagram
    assert diagram.type in exchange.PLANTUML_TYPES  # every type has a PlantUML export (#107)
    plantuml = [line.strip() for line in exchange.export_plantuml(diagram).strip().splitlines()]
    assert plantuml[0] == "@startuml" and plantuml[-1] == "@enduml"
    if diagram.type == "activity":  # every action and object node drawn once, every connector used in pairs
        drawn = [n for n in diagram.nodes.values() if n.kind.endswith(("action", "buffer_node", "store_node"))]
        assert len([line for line in plantuml if line.startswith(":")]) == len(drawn)
        connectors = [line for line in plantuml if re.fullmatch(r"\(\d+\)", line)]
        assert all(connectors.count(c) >= 2 for c in connectors)
    if diagram.interaction is not None:  # every message is one arrow line
        arrows = [line for line in plantuml if re.match(r"(L_\w+ |\[o)-", line) and "[hidden]" not in line]
        assert len(arrows) == len(sequence.messages(diagram.interaction))


def _check_sequence_view(old, new, changes, view, marks) -> None:
    """The merged view of two interactions: every element of both exactly once (a removed element whose id the
    newer state reuses for another kind under a new id), right marks, references intact, and it renders."""
    after = set(sequence.element_ids(new.interaction))
    shown = sequence.element_ids(view.interaction)
    assert len(shown) == len(set(shown)) == len(after) + len(changes.removed_elements)
    assert after <= set(shown)
    assert {i for i, m in marks.items() if m == "added"} == set(changes.added_elements)
    assert len([m for m in marks.values() if m == "removed"]) == len(changes.removed_elements)
    assert set(marks) <= set(shown)
    lifelines = {lf.id for lf in view.interaction.lifelines}
    for message in sequence.messages(view.interaction):
        assert {message.sender, message.receiver} - {None} <= lifelines, message
    svg = ET.fromstring(render.payload(view, marks=marks)["svg"])
    drawn = {g.get("data-id"): g.get("class", "").split() for g in svg.iter() if g.get("data-id")}
    assert all(marks[i] in classes for i, classes in drawn.items() if i in marks)


@given(STEPS, STEPS, st.data())
def test_any_two_sequence_states_compare_and_render(old_steps, new_steps, data):
    """Two unrelated states, ids reused across kinds included (#108 review): merged() always renders."""
    old = start_diagram("checkout")
    run(data, old, old_steps)
    new = start_diagram("checkout") if data.draw(st.booleans()) else Diagram(id="prop", title="P", type="sequence")
    run(data, new, new_steps)
    for element_id in data.draw(st.lists(st.sampled_from(sequence.element_ids(old.interaction)), max_size=3)):
        if new.interaction is not None and sequence.find(new.interaction, element_id) is None:
            operations.apply(new, "add_lifeline", {"name": "clash", "id": element_id})  # another kind, same id
    changes = diff.compare(old, new)
    view, marks, edge_marks = diff.merged(old, new, changes)
    _check_sequence_view(old, new, changes, view, marks)


@given(START, STEPS, st.data())
def test_comparison_view_contains_both_states(template, steps, data):
    old = start_diagram(template)
    assert old.type in diff.SUPPORTED_TYPES  # every type since #108
    run(data, old, data.draw(st.integers(0, 5)))
    assert diff.compare(old, old).to_dict()["summary"] == {"added": 0, "removed": 0, "changed": 0}
    new = old.model_copy(deep=True)
    run(data, new, steps)
    changes = diff.compare(old, new)
    view, marks, edge_marks = diff.merged(old, new, changes)
    if old.interaction is not None and new.interaction is not None:
        assert view.interaction is not None
        _check_sequence_view(old, new, changes, view, marks)
        assert edge_marks == {}
        return
    assert set(view.nodes) == set(old.nodes) | set(new.nodes)
    assert {n for n, m in marks.items() if m == "added"} == set(new.nodes) - set(old.nodes)
    assert {n for n, m in marks.items() if m == "removed"} == set(old.nodes) - set(new.nodes)
    assert all(marks.get(n) != "changed" or n in new.nodes for n in marks)
    assert view.integrity_errors() == []
    edge_ids = [e.id for e in view.edges]
    assert len(edge_ids) == len(set(edge_ids)) and set(edge_marks) <= set(edge_ids)
    parsed = assert_full_render(view, mermaid.render(view, marks=marks, edge_marks=edge_marks))
    link_styles = [line for line in parsed.lines if line.startswith("  linkStyle ")]
    assert len(link_styles) == len(edge_marks)


# ---------------------------------------------------------------------------
# Projects (spec: docs/architecture/specs/projects.md)
# ---------------------------------------------------------------------------


def _tree_members(tree: dict) -> list[str]:
    return [*tree["diagrams"], *(d for s in tree["sections"] for d in _tree_members(s))]


def _tree_sections(tree: dict) -> list[str]:
    return [s["id"] for s in tree["sections"]] + [i for s in tree["sections"] for i in _tree_sections(s)]


@given(STEPS, st.data())
def test_project_operations_keep_integrity_and_rejected_ones_change_nothing(steps, data):
    from arcviz import projects

    project = projects.new_project("Shop", "shop")
    for _ in range(steps):
        op, args = project_operation(data, project)
        before = project.model_copy(deep=True)
        try:
            projects.apply(project, op, args)
        except projects.ProjectError:
            assert project == before, f"{op} {args!r} failed but changed the project"
        assert project.integrity_errors() == [], f"after {op} {args!r}"
        tree = project.tree()
        assert sorted(_tree_members(tree)) == sorted(m.diagram for m in project.diagrams)  # each exactly once
        assert sorted(_tree_sections(tree)) == sorted(s.id for s in project.sections)
        assert Project.model_validate(project.model_dump(mode="json")) == project  # what the store writes
        assert_one_line(project.model_dump(mode="json"))
        for section in project.sections:  # a section standing for an element carries its name (ADR-0074)
            if section.element is not None:
                assert section.title == project.find_element(section.element).name


@given(STEPS, st.data())
def test_project_bundle_round_trip(steps, data):
    """Any project built by random operations survives export and import of its bundle unchanged."""
    from arcviz import projects

    project = projects.new_project("Shop", "shop")
    for _ in range(steps):
        op, args = project_operation(data, project)
        with contextlib.suppress(projects.ProjectError):
            projects.apply(project, op, args)
    diagrams = [Diagram(id=m.diagram, title=m.diagram.title()) for m in project.diagrams]
    parsed = exchange.parse_project(exchange.export_project(project, diagrams))
    assert parsed.title == project.title and parsed.description == project.description
    assert parsed.sections == project.sections
    assert parsed.members == [(m.diagram, m.section) for m in project.diagrams]
    assert parsed.elements == project.elements  # the shared model travels with it (ADR-0077)
    assert sorted(parsed.diagrams) == sorted(d.id for d in diagrams)


# ---- the context budget holds for every diagram shape (#115) -------------------------------------------------------

BUDGETS = st.integers(min_value=0, max_value=6000)


def assert_within_budget(diagram: Diagram, budget: int) -> None:
    text = diagram.summary(budget)
    assert len(text) <= budget + SUMMARY_SLACK, (len(text), budget)
    full = diagram.summary()
    if len(full) <= budget:
        assert text == full  # nothing is cut while everything fits
    if "not shown" in text:  # whatever is cut, the model is told how to get it
        assert "call get_diagram" in text


@given(START, STEPS, st.data(), BUDGETS)
def test_the_summary_respects_the_budget(template, steps, data, budget):
    diagram = start_diagram(template)
    run(data, diagram, steps)
    assert_within_budget(diagram, budget)


@st.composite
def large_diagrams(draw: st.DrawFn) -> Diagram:
    """Flat and nested deployment diagrams far beyond any budget: many leaves, few containers, long names, notes,
    properties and edges (the generator of the operation steps never gets this large)."""
    title = draw(st.text(min_size=1, max_size=3000))
    diagram = Diagram(id="big", title=title)
    containers = [f"c{i}" for i in range(draw(st.integers(0, 5)))]
    for i, cid in enumerate(containers):
        parent = containers[i - 1] if i and draw(st.booleans()) else None
        diagram.nodes[cid] = Node(id=cid, name=draw(names), kind="group", parent=parent)
    for i in range(draw(st.integers(0, 600))):
        node_id = f"n{i}"
        diagram.nodes[node_id] = Node(
            id=node_id,
            name=draw(st.sampled_from(["x", "Dienst " * 30, "<b>&quot;"])),
            kind="service",
            parent=draw(st.sampled_from([None, *containers])),
            properties=draw(st.sampled_from([{}, {"port": "443", "zone": "eu-west " * 5}])),
            description=draw(st.sampled_from([None, "Notiz " * 300])),
        )
    ids = sorted(diagram.nodes)
    if ids:
        for i in range(draw(st.integers(0, 600))):
            source, target = draw(st.sampled_from(ids)), draw(st.sampled_from(ids))
            label = draw(st.sampled_from([None, "ruft " * 20]))
            diagram.edges.append(Edge(id=f"e{i + 1}", source=source, target=target, label=label))
    return diagram


@given(large_diagrams(), BUDGETS)
def test_the_summary_respects_the_budget_for_large_diagrams(diagram, budget):
    assert_within_budget(diagram, budget)
