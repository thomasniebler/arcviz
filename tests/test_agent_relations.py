"""Model relations in the diagram chat and the project chat (spec: model-relations.md, *Agent*; ADR-0140).

Every model reply is scripted (ScriptedProvider); no network.
"""

from __future__ import annotations

from arcviz import agent_model
from arcviz.agent_model import RELATION_TOOL_NAMES
from arcviz.project_model import model_summary
from tests.conftest import ScriptedProvider, call
from tests.test_agent_model import diagram_agent, project_agent, results, services, versions  # noqa: F401
from tests.test_concurrency import done, tool_round


def linked(services):  # noqa: F811
    """demo: api-gateway -> service-a (e1) between two linked nodes; elements gateway and orders."""
    _, _, models = services
    models.add_element("shop", "Gateway", "component", element_id="gateway")
    models.add_element("shop", "Orders", "component", element_id="orders")
    models.link("demo", "api-gateway", "gateway")
    models.link("demo", "service-a", "orders")
    return models


def test_relation_tools_and_relations_in_the_prompt(services):  # noqa: F811
    models = linked(services)
    models.add_relation("shop", "communicates", "gateway", "orders", {"protocol": "HTTPS"}, "Bestellungen")
    provider = ScriptedProvider([done("Hallo")])
    list(diagram_agent(provider, services).chat("demo", "Hallo"))
    request = provider.requests[0]
    assert set(request["tools"]) >= RELATION_TOOL_NAMES
    assert "Relations:" in request["system"]
    assert "- [r1] communicates: gateway -> orders {protocol=HTTPS} — Bestellungen" in request["system"]
    assert "link_relation" in request["system"]  # the guidance of prompts/shared/model.txt


def test_link_a_matching_relation_after_drawing_an_edge(services):  # noqa: F811
    diagrams, _, _ = services
    models = linked(services)
    models.add_relation("shop", "communicates", "gateway", "orders")
    provider = ScriptedProvider(
        [
            tool_round(call("add_edge", "c1", source="service-a", target="api-gateway", kind="dependency")),
            tool_round(call("link_relation", "c2", id="e7", relation="r1")),
            done(),
        ]
    )
    events = list(diagram_agent(provider, services).chat("demo", "Service A ruft das Gateway auf"))
    assert all(r["ok"] for r in results(events)), results(events)
    edge = next(e for e in diagrams.get("demo").edges if e.id == "e7")
    assert edge.ref == "r1"
    assert [op["op"] for op in events[-1]["operations"]] == ["add_edge", "link_relation"]
    assert versions(services, "demo")[-1] == "Service A ruft das Gateway auf"  # one version for the turn


def test_create_update_and_unlink_relations(services):  # noqa: F811
    diagrams, _, models = services
    linked(services)
    provider = ScriptedProvider(
        [
            tool_round(
                call(
                    "create_relation",
                    "c1",
                    kind="communicates",
                    source="Gateway",
                    target="orders",
                    properties={"protocol": "HTTP"},
                )
            ),
            tool_round(call("link_relation", "c2", id="e1", relation="r1")),
            tool_round(
                call("update_relation", "c3", relation="r1", properties={"protocol": "HTTPS"}, description="TLS")
            ),
            tool_round(call("unlink_relation", "c4", id="e1")),
            tool_round(call("list_elements", "c5")),
            done(),
        ]
    )
    events = list(diagram_agent(provider, services).chat("demo", "Beziehung anlegen"))
    outcome = results(events)
    assert all(r["ok"] for r in outcome), outcome
    assert outcome[0]["relation"]["id"] == "r1"
    assert models.relations("shop")[0].properties == {"protocol": "HTTPS"}
    assert diagrams.get("demo").edges[0].ref is None
    assert outcome[4]["relations"][0]["id"] == "r1"


def test_a_relation_change_that_unlinks_this_diagram_is_read_again(services):  # noqa: F811
    diagrams, _, models = services
    linked(services)
    models.add_element("shop", "DB", "data", element_id="db")
    models.add_relation("shop", "communicates", "gateway", "orders")
    models.link_relation("demo", "e1", "r1")
    provider = ScriptedProvider(
        [
            tool_round(call("update_relation", "c1", relation="r1", target="db")),
            tool_round(call("add_node", "c2", name="cache", id="cache", kind="database", parent="prod-deployment")),
            done(),
        ]
    )
    events = list(diagram_agent(provider, services).chat("demo", "Ziel ändern"))
    assert all(r["ok"] for r in results(events)), results(events)
    demo = diagrams.get("demo")
    assert demo.edges[0].ref is None and "cache" in demo.nodes
    assert events[-1]["version"] is not None


def test_preview_mode_refuses_relation_writes(services):  # noqa: F811
    _, _, models = services
    linked(services)
    models.add_relation("shop", "communicates", "gateway", "orders")
    provider = ScriptedProvider(
        [
            tool_round(call("create_relation", "c1", kind="communicates", source="orders", target="gateway")),
            tool_round(call("update_relation", "c2", relation="r1", description="x")),
            tool_round(call("link_relation", "c3", id="e1", relation="r1")),
            done(),
        ]
    )
    events = list(diagram_agent(provider, services).chat("demo", "Vorschau", propose=True))
    assert all(not r["ok"] and "preview" in r["error"] for r in results(events))
    assert models.relations("shop")[0].description == ""


def test_activity_diagrams_get_no_relation_link_tools(services):  # noqa: F811
    from arcviz.templates import create_from_template

    diagrams, _, _ = services
    diagrams.create("Ablauf", "ablauf", create_from_template("order-process", "ablauf", "Ablauf"), "shop")
    provider = ScriptedProvider([done("Hallo")])
    list(diagram_agent(provider, services).chat("ablauf", "Hallo"))
    tools = set(provider.requests[0]["tools"])
    assert "create_relation" in tools and not {"link_relation", "unlink_relation"} & tools


def test_the_project_chat_creates_and_links_relations(services):  # noqa: F811
    diagrams, _, _ = services
    linked(services)
    provider = ScriptedProvider(
        [
            tool_round(call("create_relation", "c1", kind="communicates", source="gateway", target="orders")),
            tool_round(call("open_diagram", "c2", diagram="demo")),
            tool_round(call("link_relation", "c3", id="e1", relation="r1")),
            done(),
        ]
    )
    events = list(project_agent(provider, services).chat("shop", "Beziehungen pflegen"))
    assert all(r["ok"] for r in results(events)), results(events)
    assert {"create_relation", "update_relation"} <= set(provider.requests[0]["tools"])
    assert "link_relation" not in provider.requests[0]["tools"]  # no active diagram yet
    assert diagrams.get("demo").edges[0].ref == "r1"


def test_model_summary_budgets_relations_after_elements():
    from arcviz.project_model import Element, Relation

    elements = [Element(id=f"e{i}", name=f"E{i}") for i in range(3)]
    relations = [
        Relation(id=f"r{i}", kind="communicates", source="e0", target="e1", description="x" * 50) for i in range(40)
    ]
    text = model_summary(elements, relations=relations, budget=1_000)
    assert len(text) <= 1_000 and "- [e2] «component» E2" in text
    assert "more relations" in text and "list_elements" in text
    assert model_summary(elements) == model_summary(elements, relations=[])
    assert {"create_relation", "update_relation", "link_relation", "unlink_relation"} == agent_model.RELATION_TOOL_NAMES
