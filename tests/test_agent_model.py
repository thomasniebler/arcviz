"""Shared elements in the diagram chat and the project chat (spec: shared-project-model.md, ADR-0076).

Every model reply is scripted (ScriptedProvider); no network.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from arcviz.agent import DiagramAgent
from arcviz.agent_model import MODEL_TOOL_NAMES
from arcviz.project_agent import ProjectAgent
from arcviz.services import DiagramService, ModelService, ProjectService
from arcviz.templates import create_from_template
from tests.conftest import ScriptedProvider, call
from tests.test_concurrency import done, tool_round


@pytest.fixture
def services(store, demo):
    projects = ProjectService(store, store)
    projects.create("Shop", "shop")
    projects.place("demo", "shop")
    diagrams = DiagramService(store, projects)
    diagrams.create("Checkout", "checkout", create_from_template("checkout", "checkout", "Checkout"), "shop")
    return diagrams, projects, ModelService(projects, diagrams)


def diagram_agent(provider, services) -> DiagramAgent:
    diagrams, _, models = services
    return DiagramAgent(provider, diagrams, background=False, models=models)


def project_agent(provider, services) -> ProjectAgent:
    diagrams, projects, models = services
    return ProjectAgent(provider, diagrams, projects, models, background=False)


def results(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [json.loads(e["content"]) for e in events if e["type"] == "tool_result"]


def versions(services, diagram_id: str) -> list[str]:
    return [v.label for v in services[0].versions(diagram_id)]


# ---- prompt and tools --------------------------------------------------------------------------------------


def test_the_element_tools_come_with_a_project_and_the_elements_with_the_model(services):
    _, _, models = services
    provider = ScriptedProvider([done("Hallo"), done("Hallo")])
    list(diagram_agent(provider, services).chat("demo", "Hallo"))
    assert set(provider.requests[0]["tools"]) >= MODEL_TOOL_NAMES
    # no elements yet: the model block says so (its reuse rule applies from the first node on, ADR-0164)
    assert "Project elements:\n(none yet" in provider.requests[0]["system"]
    models.add_element("shop", "Order Service", "component", "Nimmt Bestellungen an")
    list(diagram_agent(provider, services).chat("demo", "Hallo"))
    system = provider.requests[1]["system"]
    assert "Shared project model:" in system
    assert "- [order-service] «component» Order Service — Nimmt Bestellungen an" in system
    assert system.index("Project elements:") < system.index("Current state:")


def test_without_a_model_service_there_are_no_element_tools(services):
    diagrams, _, _ = services
    provider = ScriptedProvider([done("Hallo")])
    list(DiagramAgent(provider, diagrams, background=False).chat("demo", "Hallo"))
    assert not MODEL_TOOL_NAMES & set(provider.requests[0]["tools"])


# ---- the diagram chat ----------------------------------------------------------------------------------------


def test_create_and_link_in_one_turn(services):
    diagrams, _, models = services
    provider = ScriptedProvider(
        [
            tool_round(call("create_element", "c1", name="Order Service", kind="component")),
            tool_round(call("link_element", "c2", id="service-a", element="order-service")),
            done(),
        ]
    )
    events = list(diagram_agent(provider, services).chat("demo", "Order Service anlegen"))
    assert all(r["ok"] for r in results(events)), results(events)
    node = diagrams.get("demo").nodes["service-a"]
    assert node.ref == "order-service" and node.name == "Order Service"
    assert events[-1]["changed"] == ["service-a"]
    assert [op["op"] for op in events[-1]["operations"]] == ["create_element", "link_element"]
    assert versions(services, "demo")[-1] == "Order Service anlegen"
    assert [u.diagram for u in models.model("shop").elements[0].usages] == ["demo"]


def test_link_errors_reach_the_model(services):
    provider = ScriptedProvider(
        [
            tool_round(call("link_element", "c1", id="service-a", element="ghost")),
            tool_round(call("sync_element", "c2", element="x", name="y")),
            done(),
        ]
    )
    events = list(diagram_agent(provider, services).chat("demo", "Verknüpfen"))
    first, second = results(events)
    assert not first["ok"] and "Unknown element 'ghost'" in first["error"]
    assert not second["ok"]
    assert services[0].get("demo").nodes["service-a"].ref is None


def test_a_rename_mid_turn_reaches_the_other_diagrams_and_keeps_the_turn_consistent(services):
    diagrams, _, models = services
    models.add_element("shop", "Order Service")
    models.link("demo", "service-a", "order-service")
    lifeline = diagrams.get("checkout").interaction.lifelines[1].id
    models.link("checkout", lifeline, "order-service")
    before = len(diagrams.versions("demo"))
    provider = ScriptedProvider(
        [
            tool_round(call("update_node", "c1", id="service-b", properties={"runtime": "go"})),
            tool_round(call("update_element", "c2", element="order-service", name="Bestellservice")),
            tool_round(call("add_node", "c3", name="cache", id="cache", kind="database", parent="prod-deployment")),
            done(),
        ]
    )
    events = list(diagram_agent(provider, services).chat("demo", "Umbenennen"))
    assert all(r["ok"] for r in results(events)), results(events)
    assert sorted(results(events)[1]["renamed_in"]) == ["checkout", "demo"]
    demo = diagrams.get("demo")
    assert demo.nodes["service-a"].name == "Bestellservice"
    assert demo.nodes["service-b"].properties == {"runtime": "go"} and "cache" in demo.nodes
    assert diagrams.get("checkout").interaction.lifelines[1].name == "Bestellservice"
    # the rename's own version holds the turn's earlier call; the turn's version holds the rest
    labels = versions(services, "demo")[before:]
    assert labels == ["Element „Bestellservice“ umbenannt", "Umbenennen"]
    assert (
        "service-a" in events[-1]["changed"] and events[-1]["diagram"]["nodes"]["service-a"]["name"] == "Bestellservice"
    )


def test_a_rename_alone_writes_no_second_version(services):
    diagrams, _, models = services
    models.add_element("shop", "Order Service")
    models.link("demo", "service-a", "order-service")
    before = len(diagrams.versions("demo"))
    provider = ScriptedProvider([tool_round(call("update_element", "c1", element="Order Service", name="X")), done()])
    list(diagram_agent(provider, services).chat("demo", "Umbenennen"))
    assert versions(services, "demo")[before:] == ["Element „X“ umbenannt"]


def test_preview_mode_refuses_model_writes_but_previews_an_unlink(services):
    diagrams, _, models = services
    models.add_element("shop", "Order Service")
    models.link("demo", "service-a", "order-service")
    provider = ScriptedProvider(
        [
            tool_round(
                call("create_element", "c1", name="X", kind="component"),
                call("update_element", "c2", element="order-service", name="Y"),
                call("link_element", "c3", id="service-b", element="order-service"),
                call("list_elements", "c4"),
            ),
            tool_round(call("unlink_element", "c5", id="service-a")),
            done(),
        ]
    )
    events = list(diagram_agent(provider, services).chat("demo", "Vorschau", propose=True))
    outcome = results(events)
    assert [r["ok"] for r in outcome] == [False, False, False, True, True]
    assert all("preview" in r["error"] for r in outcome[:3])
    assert [e.name for e in models.model("shop").elements] == ["Order Service"]
    assert diagrams.get("demo").nodes["service-a"].ref == "order-service"  # nothing applied yet
    proposal = events[-1]["proposal"]
    assert proposal["operations"] == [{"op": "unlink_element", "arguments": {"id": "service-a"}}]
    assert proposal["diff"]["nodes"]["changed"][0]["changes"] == {"ref": ["order-service", None]}


# ---- the project chat ----------------------------------------------------------------------------------------


def test_the_project_chat_lists_creates_and_links_across_diagrams(services):
    diagrams, _, models = services
    models.add_element("shop", "Kunde", "actor")
    actor = next(lf.id for lf in diagrams.get("checkout").interaction.lifelines if lf.actor)
    provider = ScriptedProvider(
        [
            tool_round(
                call("list_elements", "c1"), call("create_element", "c2", name="Order Service", kind="component")
            ),
            tool_round(call("open_diagram", "c3", diagram="checkout")),
            tool_round(call("link_element", "c4", id=actor, element="kunde")),
            tool_round(call("open_diagram", "c5", diagram="demo")),
            tool_round(call("link_element", "c6", id="service-a", element="order-service")),
            tool_round(call("update_element", "c7", element="order-service", name="Bestellservice")),
            tool_round(call("add_node", "c8", name="cache", id="cache", kind="database", parent="prod-deployment")),
            done(),
        ]
    )
    events = list(project_agent(provider, services).chat("shop", "Modell aufbauen"))
    assert all(r["ok"] for r in results(events)), results(events)
    assert "Project elements:" in provider.requests[0]["system"]
    assert {"list_elements", "create_element", "update_element"} <= set(provider.requests[0]["tools"])
    assert "link_element" not in provider.requests[0]["tools"]  # no active diagram yet
    assert "link_element" in provider.requests[2]["tools"]
    assert results(events)[0]["elements"][0]["id"] == "kunde"
    assert next(lf for lf in diagrams.get("checkout").interaction.lifelines if lf.id == actor).ref == "kunde"
    demo = diagrams.get("demo")
    assert demo.nodes["service-a"].name == "Bestellservice" and "cache" in demo.nodes
    final = events[-1]
    assert {d["id"] for d in final["diagrams"]} == {"checkout", "demo"}
    assert final["diagrams"][-1]["version"] is not None  # demo: cache added after the rename
    assert versions(services, "demo")[-2:] == ["Element „Bestellservice“ umbenannt", "Modell aufbauen"]


def test_a_model_change_that_writes_nothing_here_keeps_the_turns_version(services):
    """Review of #154: a description change (or a rename of an element this diagram does not show) must not reset
    the turn's start state, or the turn's earlier edits would never be versioned."""
    diagrams, _, models = services
    models.add_element("shop", "Order Service")
    models.add_element("shop", "Payment")
    models.link("checkout", diagrams.get("checkout").interaction.lifelines[1].id, "payment")
    before = len(diagrams.versions("demo"))
    provider = ScriptedProvider(
        [
            tool_round(call("add_node", "c1", name="cache", id="cache", kind="database", parent="prod-deployment")),
            tool_round(call("update_element", "c2", element="order-service", description="Bestellungen")),
            tool_round(call("update_element", "c3", element="payment", name="Zahlung")),  # not shown in demo
            done(),
        ]
    )
    events = list(diagram_agent(provider, services).chat("demo", "Cache anlegen"))
    assert all(r["ok"] for r in results(events)), results(events)
    assert versions(services, "demo")[before:] == ["Cache anlegen"]
    assert "cache" in diagrams.version("demo", diagrams.versions("demo")[-1].version)[1].nodes


def test_the_project_chat_keeps_versions_of_diagrams_a_rename_did_not_reach(services):
    diagrams, _, models = services
    models.add_element("shop", "Order Service")
    before = len(diagrams.versions("demo"))
    provider = ScriptedProvider(
        [
            tool_round(call("open_diagram", "c1", diagram="demo")),
            tool_round(call("add_node", "c2", name="cache", id="cache", kind="database", parent="prod-deployment")),
            tool_round(call("update_element", "c3", element="order-service", name="Bestellservice")),
            done(),
        ]
    )
    events = list(project_agent(provider, services).chat("shop", "Cache anlegen"))
    assert all(r["ok"] for r in results(events)), results(events)
    assert versions(services, "demo")[before:] == ["Cache anlegen"]
    model_ops = [op for op in events[-1]["operations"] if op["op"] == "update_element"]
    assert model_ops[0]["result"]["element"]["name"] == "Bestellservice"
