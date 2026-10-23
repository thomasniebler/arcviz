"""What agents can ask and do over MCP, as plain calls (spec docs/architecture/specs/agent-access.md, ADR-0170..0172); the
protocol and the credentials are tests/test_mcp_server.py."""

from __future__ import annotations

from typing import Any

import pytest

from arcviz.agent_tools import AgentTools, ToolFailure
from arcviz.services import ServiceRoot
from arcviz.templates import demo_diagram
from arcviz.workspaces import Identity

PEOPLE = {name: Identity(name, name.capitalize(), f"{name}@example.org") for name in ("ada", "vic", "out")}


@pytest.fixture
def root(store):
    root = ServiceRoot(store)
    for identity in PEOPLE.values():
        root.users.seen(identity)
    ada = root.for_actor(PEOPLE["ada"])
    ada.projects.create("Shop", "shop")
    ada.projects.share("shop", "vic", "viewer")
    ada.diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "shop")
    models = ada.models
    models.add_element("shop", "API", "component", "Takes orders from clients and validates them", element_id="api")
    models.add_element("shop", "Orders", "component", "Owns the order lifecycle", element_id="orders")
    models.add_element("shop", "Orders DB", "data", "Stores orders", element_id="orders-db")
    models.add_element("shop", "Payments", "system", "External payment provider", element_id="payments")
    models.add_relation("shop", "communicates", "api", "orders", {"protocol": "https", "port": "443"}, "REST calls")
    models.add_relation("shop", "data_flow", "orders", "orders-db", {"protocol": "postgres"}, "reads and writes")
    ada.projects.apply("shop", "add_driver", {"title": "Availability", "importance": "high", "risk": "high"})
    ada.projects.apply(
        "shop", "add_decision", {"title": "Second region", "status": "accepted", "decision": "Run in two regions"}
    )
    ada.projects.apply("shop", "add_decision", {"title": "Draft only"})
    ada.projects.apply("shop", "add_stakeholder", {"name": "Operations", "role": "Ops"})
    ada.models.code_ref_operation(
        "shop", "add_code_ref", {"element": "orders", "path": "src/orders", "symbol": "OrderService"}
    )
    ada.models.code_ref_operation("shop", "add_code_ref", {"element": "api", "path": "src/api"})
    return root


def tools(root: ServiceRoot, who: str = "ada", scope: str = "write") -> AgentTools:
    return AgentTools(root.for_actor(PEOPLE[who]), scope)


# ---- reading ---------------------------------------------------------------------------------------------------


def test_list_projects_shows_role_and_size(root):
    [project] = tools(root, "vic").list_projects()["projects"]
    assert (project["id"], project["title"], project["role"], project["diagrams"]) == ("shop", "Shop", "viewer", 1)
    assert tools(root, "out").list_projects()["projects"] == []


def test_list_components_filters_and_bounds(root):
    everything = tools(root).list_components("shop")
    assert everything["total"] == 4 and everything["truncated"] is False
    by_id = {c["id"]: c for c in everything["components"]}
    assert by_id["orders"] == {
        "id": "orders",
        "name": "Orders",
        "kind": "component",
        "description": "Owns the order lifecycle",
        "parent": None,
        "code_links": 1,
    }
    assert [c["id"] for c in tools(root).list_components("shop", kind="data")["components"]] == ["orders-db"]
    assert [c["id"] for c in tools(root).list_components("shop", query="LIFECYCLE")["components"]] == ["orders"]
    limited = tools(root).list_components("shop", limit=2)
    assert len(limited["components"]) == 2 and limited["truncated"] is True and limited["total"] == 4
    with pytest.raises(ToolFailure, match="kind"):
        tools(root).list_components("shop", kind="bogus")


def test_get_component_answers_what_a_coding_agent_asks(root):
    c = tools(root).get_component("shop", "orders")
    assert (c["id"], c["name"], c["kind"]) == ("orders", "Orders", "component")
    assert c["responsibilities"] == "Owns the order lifecycle"
    assert c["code_links"] == [{"path": "src/orders", "repo": "", "symbol": "OrderService", "note": ""}]
    relations = {(r["direction"], r["other"]["id"], r["kind"]) for r in c["relations"]}
    assert relations == {("both", "api", "communicates"), ("out", "orders-db", "data_flow")}
    flow = next(r for r in c["relations"] if r["kind"] == "data_flow")
    assert flow["other"] == {"id": "orders-db", "name": "Orders DB", "kind": "data"}
    assert flow["properties"] == {"protocol": "postgres"} and flow["description"] == "reads and writes"
    assert c["parent"] is None and c["parts"] == [] and c["views"] == [] and c["findings"] == []
    assert tools(root).get_component("shop", "ORDERS DB")["id"] == "orders-db"  # by name too


def test_get_component_names_the_views_and_findings(root):
    ada = root.for_actor(PEOPLE["ada"])
    node = next(n for n in ada.diagrams.get("orders").nodes.values() if n.kind == "database")
    ada.models.link("orders", node.id, "orders-db")
    c = tools(root).get_component("shop", "orders-db")
    assert c["views"] == [{"diagram": "orders", "title": "Orders", "view": node.id, "name": "Orders DB"}]
    assert any(f["rule"] == "database-without-replication" for f in c["findings"])


def test_get_component_parts_and_parent(root):
    ada = root.for_actor(PEOPLE["ada"])
    ada.models.add_element("shop", "Core", "subsystem", element_id="core")
    ada.models.move_element("shop", "orders", "core")
    assert [p["id"] for p in tools(root).get_component("shop", "core")["parts"]] == ["orders"]
    assert tools(root).get_component("shop", "orders")["parent"] == {"id": "core", "name": "Core", "kind": "subsystem"}


def test_unknown_things_name_what_exists(root):
    with pytest.raises(ToolFailure, match="Unknown element 'nope'.*orders"):
        tools(root).get_component("shop", "nope")
    with pytest.raises(ToolFailure, match="not found"):
        tools(root).get_component("nope", "orders")
    with pytest.raises(ToolFailure, match="not found"):
        tools(root, "out").list_components("shop")  # another user's project does not exist for them


def test_find_component_by_path(root):
    found = tools(root).find_component_by_path("shop", "src/orders/db/models.py")
    assert [(o["component"]["id"], o["code_link"]["path"]) for o in found["owners"]] == [("orders", "src/orders")]
    assert tools(root).find_component_by_path("shop", "docs/readme.md")["owners"] == []
    assert tools(root).find_component_by_path("shop", "../etc")["owners"] == []


def test_the_architecture_context_has_drivers_accepted_decisions_and_stakeholders(root):
    context = tools(root).get_architecture_context("shop")
    assert [d["title"] for d in context["drivers"]] == ["Availability"]
    assert [d["title"] for d in context["decisions"]] == ["Second region"]  # accepted ones only: they bind
    assert context["decisions"][0]["id"] == "ADR-1" and context["decisions"][0]["decision"] == "Run in two regions"
    assert [s["name"] for s in context["stakeholders"]] == ["Operations"]


def test_list_findings_filters_by_severity(root):
    findings = tools(root).list_findings("shop")
    assert findings["total"] == len(findings["findings"]) > 0
    assert {"id", "rule", "severity", "message", "diagram", "elements"} <= set(findings["findings"][0])
    high = tools(root).list_findings("shop", severity="high")["findings"]
    assert high and all(f["severity"] == "high" for f in high)
    with pytest.raises(ToolFailure, match="severity"):
        tools(root).list_findings("shop", severity="catastrophic")


def test_diagrams_and_operations(root):
    [diagram] = tools(root).list_diagrams("shop")["diagrams"]
    assert (diagram["id"], diagram["title"], diagram["type"]) == ("orders", "Orders", "deployment")
    shown = tools(root).get_diagram("shop", "orders")
    assert shown["id"] == "orders" and "service-a" in shown["summary"]
    operations = {o["name"]: o for o in tools(root).list_operations("deployment")["operations"]}
    assert "add_node" in operations and "get_diagram" not in operations
    assert operations["add_node"]["arguments"]["properties"]["name"]
    with pytest.raises(ToolFailure, match="diagram type"):
        tools(root).list_operations("bogus")
    with pytest.raises(ToolFailure, match="not in project"):
        tools(root).get_diagram("shop", "nope")


# ---- writing ---------------------------------------------------------------------------------------------------


def test_code_links_are_added_and_removed_with_write_scope(root):
    out = tools(root).add_code_ref("shop", "payments", "src/payments", symbol="Gateway", note="adapter")
    assert out["code_links"][0]["symbol"] == "Gateway"
    assert tools(root).remove_code_ref("shop", "payments", "src/payments", symbol="Gateway")["code_links"] == []
    with pytest.raises(ToolFailure, match="path"):
        tools(root).add_code_ref("shop", "payments", "../x")


def test_a_read_credential_writes_nothing(root):
    read = tools(root, scope="read")
    for call in (
        lambda: read.add_code_ref("shop", "payments", "src"),
        lambda: read.remove_code_ref("shop", "orders", "src/orders", symbol="OrderService"),
        lambda: read.propose_change("shop", "orders", [{"op": "rename_diagram", "arguments": {"title": "X"}}]),
    ):
        with pytest.raises(ToolFailure, match="read-only"):
            call()
    assert tools(root).get_component("shop", "payments")["code_links"] == []
    assert tools(root).list_proposals("shop")["proposals"] == []


def test_a_viewer_cannot_write_even_with_a_write_credential(root):
    with pytest.raises(ToolFailure, match="viewer"):
        tools(root, "vic").add_code_ref("shop", "payments", "src")
    with pytest.raises(ToolFailure, match="viewer"):
        tools(root, "vic").propose_change("shop", "orders", [{"op": "rename_diagram", "arguments": {"title": "X"}}])


def test_a_proposal_is_stored_and_listed_and_changes_no_diagram(root):
    ops: list[dict[str, Any]] = [{"op": "add_node", "arguments": {"name": "Cache", "kind": "service", "id": "cache"}}]
    out = tools(root).propose_change("shop", "orders", ops, "Hot reads")
    proposal = out["proposal"]
    assert (proposal["id"], proposal["status"], proposal["diagram"], proposal["rationale"]) == (
        "p1",
        "pending",
        "orders",
        "Hot reads",
    )
    assert proposal["diff"]["nodes"]["added"][0]["id"] == "cache" and proposal["author"] == "ada"
    assert "cache" not in root.for_actor(PEOPLE["ada"]).diagrams.get("orders").nodes
    listed = tools(root, "vic", "read").list_proposals("shop")["proposals"]
    assert [(p["id"], p["status"]) for p in listed] == [("p1", "pending")]
    assert "diff" not in listed[0]  # the list is short; get_proposal has the diff
    assert tools(root, "vic", "read").get_proposal("shop", "p1")["proposal"]["diff"]["nodes"]["added"]
    assert tools(root).list_proposals("shop", status="applied")["proposals"] == []


def test_a_proposal_that_cannot_be_applied_says_why(root):
    with pytest.raises(ToolFailure, match="Operation 1 \\(add_node\\).*bogus"):
        tools(root).propose_change("shop", "orders", [{"op": "add_node", "arguments": {"name": "X", "kind": "bogus"}}])
    with pytest.raises(ToolFailure, match="link_element"):
        tools(root).propose_change("shop", "orders", [{"op": "link_element", "arguments": {}}])
    with pytest.raises(ToolFailure, match="not in project"):
        tools(root).propose_change("shop", "nope", [{"op": "rename_diagram", "arguments": {"title": "X"}}])


def test_the_agent_sees_when_its_proposal_was_applied(root):
    tools(root).propose_change("shop", "orders", [{"op": "rename_diagram", "arguments": {"title": "Bestellungen"}}])
    root.for_actor(PEOPLE["ada"]).proposals.apply("shop", "p1")
    got = tools(root, scope="read").get_proposal("shop", "p1")["proposal"]
    assert (got["status"], got["decided_by"], got["version"]) == ("applied", "ada", 2)


def test_there_is_no_tool_that_applies_a_proposal(root):
    names = {n for n in dir(AgentTools) if not n.startswith("_")}
    assert not {n for n in names if "apply" in n or "discard" in n or "decide" in n}
