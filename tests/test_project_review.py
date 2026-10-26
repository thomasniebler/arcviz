"""Project review: project rules, aggregated diagram findings, one LLM review of the whole project.

Spec: docs/architecture/specs/project-review.md (ADR-0062..0064). The model is scripted; no network.
"""

from __future__ import annotations

import json

import pytest

from arcviz import operations, projects
from arcviz import project_review as pr
from arcviz.llm.base import ModelReply
from arcviz.model import Diagram
from arcviz.projects import Membership
from arcviz.rules import Finding
from arcviz.templates import demo_diagram
from tests.conftest import ScriptedProvider, call


def snapshot(sections=(), members=(), description="Ein Shop"):
    """A project with `sections` [(id, title, parent)] and `members` [(Diagram, section)]."""
    project = projects.new_project("Shop", "shop", description)
    for section_id, title, parent in sections:
        projects.apply(project, "add_section", {"title": title, "id": section_id, "parent": parent})
    pairs = []
    for diagram, section in members:
        projects.apply(project, "add_diagram", {"diagram": diagram.id, "section": section})
        pairs.append((project.member(diagram.id), diagram))
    return pr.ProjectSnapshot(project, pairs)


def diagram(diagram_id, title=None, type="deployment", nodes=True):
    if type == "deployment" and nodes:
        return demo_diagram(diagram_id, title or diagram_id)
    d = Diagram(id=diagram_id, title=title or diagram_id, type=type)
    if nodes and type == "activity":
        from arcviz import operations

        operations.apply(d, "add_node", {"name": "Start", "kind": "initial_node"})
    if nodes and type == "sequence":
        from arcviz import operations

        operations.apply(d, "add_lifeline", {"name": "Kunde"})
    return d


def by_rule(findings, rule):
    return [f for f in findings if f.rule == rule and not f.id.startswith(f"{f.diagram}:")]


# ---- project rules ------------------------------------------------------------------------------------


def test_project_finding_extends_the_finding_schema():
    finding = pr.ProjectFinding(id="x", source="rule", category="risk", severity="low", message="m", diagram="d")
    assert isinstance(finding, Finding)
    assert finding.model_dump()["diagrams"] == [] and finding.section is None


def test_empty_sections_are_reported_once_at_the_outermost_level():
    snap = snapshot(
        sections=[("backend", "Backend", None), ("db", "Datenbanken", "backend"), ("frontend", "Frontend", None)],
        members=[(diagram("web"), "frontend")],
    )
    found = by_rule(pr.run_project_checks(snap), "empty-section")
    assert [(f.section, f.category, f.severity) for f in found] == [("backend", "suggestion", "low")]
    assert "Backend" in found[0].message


def test_a_section_with_two_diagrams_needs_an_overview():
    snap = snapshot(
        sections=[("backend", "Backend", None), ("frontend", "Frontend", None), ("ops", "Betrieb", None)],
        members=[
            (diagram("a", "Bestellservice"), "backend"),
            (diagram("b", "Zahlungsservice", type="activity"), "backend"),
            (diagram("c", "Frontend-Übersicht"), "frontend"),
            (diagram("d", "Web", type="sequence"), "frontend"),
            (diagram("e", "Einzeln"), "ops"),
        ],
    )
    found = by_rule(pr.run_project_checks(snap), "section-without-overview")
    assert [f.section for f in found] == ["backend"]  # frontend has one, ops has a single diagram


@pytest.mark.parametrize(
    "title",
    ["Übersicht", "System overview", "Kontextsicht", "C4 Context", "Systemlandschaft", "Gesamtbild", "Big Picture"],
)
def test_overview_titles_are_recognised(title):
    assert pr.is_overview(Diagram(id="x", title=title))


def test_sections_showing_only_structure_or_only_behaviour():
    snap = snapshot(
        sections=[("s1", "Nur Struktur", None), ("s2", "Nur Abläufe", None), ("s3", "Beides", None)],
        members=[
            (diagram("a"), "s1"),
            (diagram("b", type="activity"), "s2"),
            (diagram("c", type="sequence"), "s2"),
            (diagram("d"), "s3"),
            (diagram("e", type="sequence"), "s3"),
        ],
    )
    found = {f.section: f.message for f in by_rule(pr.run_project_checks(snap), "missing-diagram-types")}
    assert set(found) == {"s1", "s2"}
    assert "Verhaltensdiagramm" in found["s1"] and "Verteilungsdiagramm" in found["s2"]


def test_duplicate_and_near_duplicate_titles():
    snap = snapshot(
        members=[
            (diagram("a", "Bestellung"), None),
            (diagram("b", "bestellung!"), None),
            (diagram("c", "Checkout Ablauf"), None),
            (diagram("d", "Checkout-Ablauf 2"), None),
            (diagram("e", "API"), None),
            (diagram("f", "APIs"), None),  # too short to compare loosely
        ]
    )
    found = by_rule(pr.run_project_checks(snap), "duplicate-title")
    assert [(f.diagrams, f.severity) for f in found] == [(["a", "b"], "medium"), (["c", "d"], "low")]
    assert all(f.category == "risk" and f.diagram == f.diagrams[0] for f in found)


def test_empty_diagrams_are_reported():
    snap = snapshot(
        members=[(diagram("leer", nodes=False), None), (diagram("seq", type="sequence", nodes=False), None)]
    )
    found = by_rule(pr.run_project_checks(snap), "empty-diagram")
    assert [f.diagram for f in found] == ["leer", "seq"]


def test_diagram_findings_are_aggregated_with_their_diagram():
    d = demo_diagram("demo", "Demo")
    snap = snapshot(members=[(d, None)])
    findings = pr.run_project_checks(snap)
    from arcviz import rules

    own = rules.run_checks(d)
    aggregated = [f for f in findings if f.id.startswith("demo:")]
    assert own and len(aggregated) == len(own)
    assert {f.id for f in aggregated} == {f"demo:{f.id}" for f in own}
    assert all(f.diagram == "demo" and f.diagrams == ["demo"] for f in aggregated)
    first = aggregated[0]
    original = next(f for f in own if f"demo:{f.id}" == first.id)
    assert first.elements == original.elements and first.message == original.message


def test_findings_are_sorted_by_severity_then_category():
    snap = snapshot(
        sections=[("leer", "Leer", None)],
        members=[(diagram("a", "Bestellung"), None), (diagram("b", "Bestellung"), None)],
    )
    findings = pr.run_project_checks(snap)
    from arcviz.rules import CATEGORIES, SEVERITY_ORDER

    keys = [(SEVERITY_ORDER.index(f.severity), CATEGORIES.index(f.category)) for f in findings]
    assert keys == sorted(keys)


def test_the_project_rules_are_listed():
    assert [r.id for r in pr.PROJECT_RULES] == [
        "empty-section",
        "section-without-overview",
        "missing-diagram-types",
        "duplicate-title",
        "empty-diagram",
        "dangling-element-ref",
        "element-name-drift",
        "incompatible-element-link",
        "unused-element",
        "element-not-in-structure",
        "message-without-connection",
        "unlinked-namesake",
        "similar-element-names",
        "relation-not-shown",
        "connection-without-relation",
        "complete-view-missing-relation",
        "relation-property-conflict",
        "deployed-on-several-nodes",
        "containment-conflict",
        "invalid-relation-link",
        "requirement-unrefined",
        "requirement-uncovered",
        "requirement-no-stakeholder",
        "quality-requirement-no-measure",
        "requirement-conflict-open",
        "requirement-duplicate-open",
        "scenario-no-requirement",
        "scenario-no-quality",
        "decision-no-basis",
        "stakeholder-no-requirement",
    ]


# ---- LLM review -----------------------------------------------------------------------------------------


def shop():
    return snapshot(
        sections=[("backend", "Backend", None)],
        members=[
            (demo_diagram("demo", "Demo deployment"), "backend"),
            (diagram("order", "Bestellung", type="activity"), None),
        ],
    )


def review_reply(**arguments):
    return ModelReply("", [call("submit_project_review", "r1", **arguments)], "tool_use", usage={"input_tokens": 9})


def test_review_input_holds_the_structure_every_diagram_and_the_rule_findings():
    snap = shop()
    text = pr.review_input(snap, pr.run_project_checks(snap))
    assert "Project: Shop" in text and "Ein Shop" in text
    assert "- Section: Backend [backend]" in text and "[demo] «deployment» Demo deployment" in text
    assert "[service-a]" in text  # the diagram summaries
    assert "Rule findings (already reported, do not repeat):" in text and "(diagram demo)" in text


def test_review_input_shares_the_budget_and_flags_collapsed_diagrams(monkeypatch):
    monkeypatch.setattr(pr, "PROJECT_REVIEW_BUDGET", 2_000)
    big = demo_diagram("big", "Big")
    from arcviz import operations

    for i in range(80):
        operations.apply(big, "add_node", {"name": f"service-{i:03d}", "kind": "service", "parent": "prod-deployment"})
    snap = snapshot(members=[(big, None), (demo_diagram("demo", "Demo"), None)])
    assert pr.is_partial(snap)
    assert pr.PARTIAL_NOTE in pr.review_input(snap, [])
    assert not pr.is_partial(shop())


def test_review_project_validates_the_model_findings():
    provider = ScriptedProvider(
        [
            review_reply(
                summary="Solide Struktur, Abläufe fehlen.",
                findings=[
                    {
                        "category": "risk",
                        "severity": "high",
                        "message": "service-a spricht ohne TLS mit der Datenbank.",
                        "diagram": "demo",
                        "elements": ["service-a", "ghost"],
                        "edges": ["e1", "e99"],
                        "suggestion": "Setze an e1 protocol=tls.",
                    },
                    {
                        "category": "suggestion",
                        "severity": "low",
                        "message": "Ablauf ergänzen.",
                        "diagram": "Bestellung",
                    },
                    {
                        "category": "suggestion",
                        "severity": "medium",
                        "message": "Ein Abschnitt für den Betrieb fehlt.",
                        "section": "backend",
                        "elements": ["service-a"],  # no diagram: element ids are meaningless and dropped
                    },
                    {
                        "category": "risk",
                        "severity": "low",
                        "message": "Unbekannt.",
                        "diagram": "nope",
                        "section": "nope",
                    },
                    {"category": "praise", "severity": "high", "message": "ungültig"},
                ],
            )
        ]
    )
    result = pr.review_project(provider, shop())
    assert result["project"] == "shop" and result["summary"] == "Solide Struktur, Abläufe fehlen."
    assert (
        result["llm"]["ok"] is True and result["llm"]["dropped"] == 1 and result["llm"]["usage"] == {"input_tokens": 9}
    )
    llm = {f["message"]: f for f in result["findings"] if f["source"] == "llm"}
    tls = llm["service-a spricht ohne TLS mit der Datenbank."]
    assert tls["diagram"] == "demo" and tls["diagrams"] == ["demo"]
    assert tls["elements"] == ["service-a"] and tls["edges"] == ["e1"] and tls["suggestion"]
    assert llm["Ablauf ergänzen."]["diagram"] == "order"  # a title resolves to the id
    project_level = llm["Ein Abschnitt für den Betrieb fehlt."]
    assert (
        project_level["diagram"] is None and project_level["section"] == "backend" and project_level["elements"] == []
    )
    unknown = llm["Unbekannt."]
    assert unknown["diagram"] is None and unknown["section"] is None
    assert result["diagrams"]["demo"] == {"title": "Demo deployment", "type": "deployment", "section": "backend"}
    assert any(f["source"] == "rule" for f in result["findings"])
    request = provider.requests[0]
    assert request["tools"] == ["submit_project_review"] and "whole project" in request["system"]


def test_a_failed_model_call_keeps_the_rule_findings():
    class Broken(ScriptedProvider):
        def _complete(self, system, messages, tools):
            raise RuntimeError("boom")

    result = pr.review_project(Broken([]), shop())
    assert result["llm"]["ok"] is False and "boom" in result["llm"]["error"]
    assert result["summary"] is None and result["findings"] and all(f["source"] == "rule" for f in result["findings"])


def test_unusable_arguments_are_a_failed_review_not_an_exception():
    result = pr.review_project(ScriptedProvider([review_reply(summary="x", findings="nope")]), shop())
    assert result["llm"]["ok"] is False


def test_at_most_twenty_model_findings_are_kept():
    raw = [{"category": "suggestion", "severity": "low", "message": f"m{i}"} for i in range(25)]
    summary, findings, dropped = pr.parse_project_review({"summary": "s", "findings": raw}, shop())
    assert len(findings) == 20 and dropped == 5 and summary == "s"
    assert [f.id for f in findings][:2] == ["llm-1", "llm-2"]


def test_checks_payload_lists_rules_findings_and_diagrams():
    payload = pr.checks(shop())
    assert payload["project"] == "shop"
    assert [r["id"] for r in payload["rules"]] == [r.id for r in pr.PROJECT_RULES]
    assert set(payload["diagrams"]) == {"demo", "order"}
    json.dumps(payload)  # JSON-able


def test_snapshot_from_members_keeps_the_membership():
    snap = shop()
    assert isinstance(snap.members[0][0], Membership) and snap.members[0][0].section == "backend"


# ---- consistency of the shared model (spec: shared-project-model.md, ADR-0075) ---------------------------------


def op(d, operation, **args):
    return operations.apply(d, operation, args)


def link(d, view, element, name, kind="component"):
    op(d, "link_element", id=view, element=element, name=name, element_kind=kind)


def model_snapshot():
    """Deployment: web -> api (connection), db unconnected; sequence: Kunde, Web, API, DB; activity: partitions."""
    dep = Diagram(id="dep", title="Plattform")
    op(dep, "add_node", name="Web", kind="service", id="web")
    op(dep, "add_node", name="API", kind="service", id="api")
    op(dep, "add_node", name="DB", kind="database", id="db")
    op(dep, "add_edge", source="web", target="api")
    seq = Diagram(id="seq", title="Checkout", type="sequence")
    for name in ("Kunde", "Web", "API", "DB"):
        op(seq, "add_lifeline", name=name)
    op(seq, "add_message", sender="web", receiver="api", name="order()")
    op(seq, "add_message", sender="api", receiver="db", name="insert()")
    op(seq, "add_message", sender="api", receiver="db", name="select()")
    act = Diagram(id="act", title="Bestellprozess", type="activity")
    op(act, "add_node", name="Kunde", kind="activity_partition", id="kunde")
    op(act, "add_node", name="Billing", kind="activity_partition", id="billing")
    snap = snapshot(members=[(dep, None), (seq, None), (act, None)])
    for name, kind in (("Web", "component"), ("API", "component"), ("DB", "data"), ("Billing", "component")):
        projects.apply(snap.project, "add_element", {"name": name, "kind": kind})
    projects.apply(snap.project, "add_element", {"name": "Kunde", "kind": "actor"})
    for view, element, name in (("web", "web", "Web"), ("api", "api", "API")):
        link(dep, view, element, name)
    link(dep, "db", "db", "DB", "data")
    for view, element, name in (("web", "web", "Web"), ("api", "api", "API")):
        link(seq, view, element, name)
    link(seq, "db", "db", "DB", "data")
    link(act, "billing", "billing", "Billing")
    return snap, dep, seq, act


def rule(findings, name):
    return [f for f in findings if f.rule == name]


def test_projects_without_a_model_get_no_model_findings():
    findings = pr.run_project_checks(snapshot(members=[(diagram("a"), None), (diagram("b", type="sequence"), None)]))
    model_rules = {
        "dangling-element-ref",
        "element-name-drift",
        "incompatible-element-link",
        "unused-element",
        "element-not-in-structure",
        "message-without-connection",
        "unlinked-namesake",
    }
    assert not [f for f in findings if f.rule in model_rules]
    assert model_rules <= {r.id for r in pr.PROJECT_RULES}


def test_message_without_a_deployment_connection():
    snap, dep, seq, _ = model_snapshot()
    found = rule(pr.run_project_checks(snap), "message-without-connection")
    assert len(found) == 1  # api -> db twice, one finding; web -> api is connected
    f = found[0]
    assert f.diagram == "seq" and f.category == "risk" and f.severity == "medium"
    assert len(f.elements) == 2 and "„API“" in f.message and "„DB“" in f.message
    op(dep, "add_edge", source="api", target="db")
    assert not rule(pr.run_project_checks(snap), "message-without-connection")


def test_containment_counts_as_a_connection():
    snap, dep, _, _ = model_snapshot()
    projects.apply(snap.project, "add_element", {"name": "Backend", "kind": "node"})
    op(dep, "add_node", name="Backend", kind="node", id="backend")
    link(dep, "backend", "backend", "Backend", "node")
    op(dep, "move_node", id="api", parent="backend")
    op(dep, "move_node", id="db", parent="backend")
    op(dep, "add_edge", source="api", target="backend")  # nothing between api and db, but both in the backend node
    # api and db are inside the backend node: each stands for backend as well, so backend-backend pairs exist
    assert rule(pr.run_project_checks(snap), "message-without-connection")  # api <-> db still not connected
    projects.apply(snap.project, "update_element", {"element": "backend", "kind": "subsystem"})
    op(dep, "update_node", id="backend", kind="node")
    # a message from the backend itself to the db inside it is a connection by containment
    seq = snap.members[1][1]
    op(seq, "add_lifeline", name="Backend")
    op(seq, "link_element", id="backend", element="backend", name="Backend", element_kind="node")
    op(seq, "add_message", sender="backend", receiver="db", name="vacuum()")
    found = rule(pr.run_project_checks(snap), "message-without-connection")
    assert all("Backend" not in f.message for f in found)


def test_element_in_behaviour_but_not_in_structure():
    snap, _, _, act = model_snapshot()
    found = rule(pr.run_project_checks(snap), "element-not-in-structure")
    assert [f.model_element for f in found] == ["billing"]
    assert found[0].diagrams == ["act"] and found[0].elements == ["billing"]
    link(act, "kunde", "kunde", "Kunde", "actor")  # actors are not deployed
    assert [f.model_element for f in rule(pr.run_project_checks(snap), "element-not-in-structure")] == ["billing"]


def test_unused_element_and_unlinked_namesake():
    snap, _, _, _ = model_snapshot()
    findings = pr.run_project_checks(snap)
    assert [f.model_element for f in rule(findings, "unused-element")] == ["kunde"]  # namesakes, but no link
    namesakes = rule(findings, "unlinked-namesake")
    assert [f.model_element for f in namesakes] == ["kunde"]
    assert namesakes[0].diagrams == ["seq", "act"] and namesakes[0].elements == ["kunde"]
    projects.apply(snap.project, "add_element", {"name": "Lager"})
    assert "lager" in [f.model_element for f in rule(pr.run_project_checks(snap), "unused-element")]


def test_drift_dangling_and_incompatible_links():
    snap, dep, seq, act = model_snapshot()
    op(seq, "sync_element", element="web", name="Web Frontend")  # as if a rename reached only this diagram
    op(act, "update_node", id="billing", properties={"x": "y"})
    op(dep, "link_element", id="web", element="web", name="Web", element_kind="component")
    projects.apply(snap.project, "update_element", {"element": "db", "kind": "actor"})  # bypassing the service
    projects.apply(snap.project, "remove_element", {"element": "billing"})
    findings = pr.run_project_checks(snap)
    drift = rule(findings, "element-name-drift")
    assert [(f.diagram, f.model_element, f.elements) for f in drift] == [("seq", "web", ["web"])]
    dangling = rule(findings, "dangling-element-ref")
    assert [(f.diagram, f.elements) for f in dangling] == [("act", ["billing"])]
    wrong = rule(findings, "incompatible-element-link")
    assert [(f.diagram, f.model_element) for f in wrong] == [("dep", "db")]  # a database cannot be an actor
    assert all(f.category == "risk" for f in drift + dangling + wrong)


def test_findings_name_the_model_and_the_digest_lists_it():
    snap, _, _, _ = model_snapshot()
    result = pr.checks(snap)
    assert result["model"]["db"] == {"name": "DB", "kind": "data"}
    text = pr.review_input(snap, [])
    assert "Model elements" in text and "[api] «component» API (in: dep, seq)" in text
    assert "[kunde] «actor» Kunde (not shown in any diagram)" in text
    assert "(= element api)" in text
