"""ProjectAgent: one chat over the diagrams of a project (spec: docs/architecture/specs/project-chat.md).

Every model reply is scripted (ScriptedProvider); no network.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from arcviz import operations as ops
from arcviz.agent import TOOLS
from arcviz.llm.base import ModelReply
from arcviz.model import Diagram
from arcviz.project_agent import PROJECT_TOOLS, ProjectAgent, diagram_brief, project_structure
from arcviz.services import DiagramService, ProjectNotFound, ProjectService
from arcviz.store import Change
from tests.conftest import ScriptedProvider, call
from tests.test_concurrency import HookedProvider, done, tool_round

PROJECT_TOOL_NAMES = [t.name for t in PROJECT_TOOLS]


@pytest.fixture
def services(store, demo):
    projects = ProjectService(store, store)
    projects.create("Shop", "shop", description="Ein Webshop")
    projects.apply("shop", "add_section", {"title": "Backend"})
    projects.place("demo", "shop", "backend")
    diagrams = DiagramService(store, projects)
    diagrams.create("Bestellung", "order", Diagram(id="order", title="Bestellung", type="activity"), "shop")
    return diagrams, projects


def make_agent(provider, services, **options: Any) -> ProjectAgent:
    diagrams, projects = services
    return ProjectAgent(provider, diagrams, projects, background=False, **options)


def run(agent: ProjectAgent, message: str = "Bitte ändern", **kwargs: Any) -> list[dict[str, Any]]:
    return list(agent.chat("shop", message, **kwargs))


def results(events):
    return [e for e in events if e["type"] == "tool_result"]


def payload(event) -> dict[str, Any]:
    return json.loads(event["content"])


def add_service(node_id: str, call_id: str) -> Any:
    return call("add_node", call_id, name=node_id, id=node_id, kind="service", parent="prod-deployment")


# ---- prompt and tools -----------------------------------------------------------------------


def test_the_system_prompt_carries_the_project_structure_and_the_active_diagram(services):
    provider = ScriptedProvider([done("Hallo")])
    events = run(make_agent(provider, services), active="demo")
    system = provider.requests[0]["system"]
    assert "Shop" in system and "Ein Webshop" in system
    assert "Backend [backend]" in system
    assert "[demo] «deployment» Demo" in system and "[order] «activity» Bestellung" in system
    assert "Active diagram: [demo]" in system
    assert "[service-a] «service» service-a" in system  # the active diagram's full summary
    assert provider.requests[0]["tools"] == PROJECT_TOOL_NAMES + [t.name for t in TOOLS]
    final = events[-1]
    assert final["type"] == "final" and final["active"] == "demo" and final["diagrams"] == []


def test_without_an_active_diagram_only_the_project_tools_are_offered(services):
    provider = ScriptedProvider([tool_round(add_service("x", "c1")), done("Erst öffnen.")])
    events = run(make_agent(provider, services))
    assert "No diagram is open" in provider.requests[0]["system"]
    assert provider.requests[0]["tools"] == PROJECT_TOOL_NAMES
    (result,) = results(events)
    assert result["is_error"] and "open_diagram or create_diagram" in payload(result)["error"]
    assert events[-1]["active"] is None


def test_an_active_diagram_outside_the_project_is_ignored(services, store):
    store.create("Other", "other", Diagram(id="other", title="Other"))
    provider = ScriptedProvider([done()])
    events = run(make_agent(provider, services), active="other")
    assert events[-1]["active"] is None
    assert provider.requests[0]["tools"] == PROJECT_TOOL_NAMES


def test_open_diagram_switches_the_tools_from_the_next_round(services):
    provider = ScriptedProvider(
        [
            tool_round(call("open_diagram", "c1", diagram="Bestellung")),  # a title works as reference
            tool_round(call("add_node", "c2", name="Start", kind="initial_node")),
            done("Startknoten angelegt."),
        ]
    )
    events = run(make_agent(provider, services), active="demo")
    opened, added = results(events)
    assert not opened["is_error"] and payload(opened)["active"] == "order"
    assert "activity diagram" in payload(opened)["instructions"]
    assert opened["diagram_id"] == "order" and opened["diagram"]["id"] == "order" and opened["renderer"]
    assert not added["is_error"] and added["diagram_id"] == "order"
    tools_after = provider.requests[1]["tools"]
    assert "add_node" in tools_after and "add_lifeline" not in tools_after
    final = events[-1]
    assert final["active"] == "order" and final["diagram"]["id"] == "order"
    assert [d["id"] for d in final["diagrams"]] == ["order"]


def test_open_diagram_refuses_a_diagram_outside_the_project(services, store):
    store.create("Other", "other", Diagram(id="other", title="Other"))
    provider = ScriptedProvider([tool_round(call("open_diagram", "c1", diagram="other")), done()])
    (result,) = results(run(make_agent(provider, services)))
    assert result["is_error"] and "demo" in payload(result)["error"] and "order" in payload(result)["error"]


def test_list_and_read_diagrams_do_not_change_the_active_diagram(services):
    provider = ScriptedProvider(
        [tool_round(call("list_diagrams", "c1"), call("read_diagram", "c2", diagram="demo")), done()]
    )
    events = run(make_agent(provider, services), active="order")
    listed, read = results(events)
    structure = payload(listed)
    assert structure["active"] == "order"
    assert [(d["id"], d["type"], d["section"]) for d in structure["diagrams"]] == [
        ("demo", "deployment", "backend"),
        ("order", "activity", None),
    ]
    assert structure["sections"] == [{"id": "backend", "title": "Backend", "parent": None}]
    assert "[service-a]" in payload(read)["summary"] and payload(read)["type"] == "deployment"
    assert "diagram_id" not in listed and "diagram_id" not in read  # no switch, no render
    assert events[-1]["active"] == "order"


# ---- creating and editing several diagrams ---------------------------------------------------------


def test_create_diagram_places_it_in_the_section_and_makes_it_active(services, store):
    diagrams, projects = services
    provider = ScriptedProvider(
        [
            tool_round(call("create_diagram", "c1", title="Checkout", type="sequence", section="Backend")),
            tool_round(call("add_lifeline", "c2", name="Shop")),
            done("Sequenzdiagramm angelegt."),
        ]
    )
    events = run(make_agent(provider, services), active="demo")
    created, lifeline = results(events)
    assert not created["is_error"] and payload(created)["diagram"] == "checkout"
    assert created["diagram_id"] == "checkout" and created["renderer"] == "svg"
    assert "add_lifeline" in provider.requests[1]["tools"]
    assert not lifeline["is_error"]
    assert projects.locate("checkout") == ("shop", "backend")
    assert [v.source for v in store.list_versions("checkout")] == ["initial", "auto"]
    final = events[-1]
    assert final["active"] == "checkout" and final["project_changed"] is True
    (entry,) = final["diagrams"]
    assert entry["id"] == "checkout" and entry["created"] is True and entry["version"] == 2


def test_create_diagram_with_an_unknown_section_or_type_is_a_tool_error(services, store):
    provider = ScriptedProvider(
        [
            tool_round(
                call("create_diagram", "c1", title="X", type="deployment", section="nope"),
                call("create_diagram", "c2", title="Y", type="mindmap"),
            ),
            done(),
        ]
    )
    first, second = results(run(make_agent(provider, services)))
    assert first["is_error"] and "backend" in payload(first)["error"]
    assert second["is_error"] and "deployment" in payload(second)["error"]
    assert {d.id for d in store.list_diagrams()} == {"demo", "order"}


def test_a_turn_over_two_diagrams_versions_each_and_records_them_in_the_project_transcript(services, store):
    provider = ScriptedProvider(
        [
            tool_round(add_service("payment", "c1")),
            tool_round(call("open_diagram", "c2", diagram="order")),
            tool_round(call("add_node", "c3", name="Bezahlen", kind="opaque_action")),
            done("Payment ergänzt und Bezahlschritt angelegt."),
        ]
    )
    events = run(make_agent(provider, services), "Payment überall ergänzen", active="demo")
    final = events[-1]
    assert [d["id"] for d in final["diagrams"]] == ["demo", "order"]
    demo_entry, order_entry = final["diagrams"]
    assert demo_entry["version"] == 2 and demo_entry["changed"] == ["payment"] and demo_entry["created"] is False
    assert order_entry["version"] == 2 and order_entry["title"] == "Bestellung"
    assert [(op["diagram"], op["op"]) for op in final["operations"]] == [("demo", "add_node"), ("order", "add_node")]
    assert final["active"] == "order" and final["changed"] == ["bezahlen"]
    assert "payment" in store.load("demo").nodes and "bezahlen" in store.load("order").nodes
    assert store.list_versions("demo")[-1].label == "Payment überall ergänzen"
    assert store.list_versions("order")[-1].source == "auto"
    # the transcript is the project's; the diagram transcripts stay untouched
    assert store.load_chat("demo") == [] and store.load_chat("order") == []
    user, assistant = store.load_project_chat("shop")
    assert user["content"] == "Payment überall ergänzen"
    assert assistant["content"].startswith("Payment ergänzt")
    assert [d["id"] for d in assistant["diagrams"]] == ["demo", "order"] and assistant["active"] == "order"
    assert [op["diagram"] for op in assistant["operations"]] == ["demo", "order"]


def test_live_tool_results_render_the_active_diagram_with_its_changes(services):
    provider = ScriptedProvider([tool_round(add_service("payment", "c1")), done()])
    (result,) = results(run(make_agent(provider, services), active="demo", focus="prod-deployment"))
    assert result["diagram_id"] == "demo" and "payment" in result["diagram"]["nodes"]
    assert "changed" in result["mermaid"] and result["focus"] == "prod-deployment"


def test_a_failing_operation_writes_nothing(services, store):
    provider = ScriptedProvider([tool_round(call("remove_node", "c1", id="nope")), done()])
    events = run(make_agent(provider, services), active="demo")
    assert results(events)[0]["is_error"]
    assert events[-1]["diagrams"] == [] and len(store.list_versions("demo")) == 1


def test_get_diagram_reads_the_active_diagram(services):
    provider = ScriptedProvider([tool_round(call("get_diagram", "c1", ids=["service-a"])), done()])
    (result,) = results(run(make_agent(provider, services), active="demo"))
    assert "service-a" in payload(result)["nodes"]


# ---- persistence semantics --------------------------------------------------------------------------


def test_mutations_survive_a_client_disconnect(services, store):
    provider = ScriptedProvider([tool_round(add_service("payment", "c1")), done("never reached")])
    gen = make_agent(provider, services).chat("shop", "Payment", active="demo")
    for event in gen:
        if event["type"] == "tool_result":
            break
    assert "payment" in store.load("demo").nodes  # written through before the stream ends
    gen.close()
    assert [v.source for v in store.list_versions("demo")] == ["initial", "auto"]
    assert "interrupted" in store.load_project_chat("shop")[-1]["error"]


def test_a_concurrent_edit_of_the_active_diagram_is_kept(services, store):
    def direct_edit() -> None:
        def change(diagram):
            ops.add_node(diagram, "direct", kind="service", id="direct", parent="prod-deployment")
            return Change(diagram, None, label="direct")

        store.update("demo", change)

    provider = HookedProvider(
        [tool_round(add_service("one", "c1")), tool_round(add_service("two", "c2")), done()],
        hooks={1: direct_edit},
    )
    events = run(make_agent(provider, services), active="demo")
    assert {"one", "direct", "two"} <= set(store.load("demo").nodes)
    assert set(events[-1]["diagram"]["nodes"]) == set(store.load("demo").nodes)


def test_a_diagram_deleted_during_the_turn_is_not_resurrected(services, store):
    diagrams, _ = services
    provider = HookedProvider(
        [tool_round(add_service("one", "c1")), tool_round(add_service("two", "c2")), done("?")],
        hooks={1: lambda: diagrams.delete("demo")},
    )
    events = run(make_agent(provider, services), active="demo")
    first, second = results(events)
    assert not first["is_error"] and second["is_error"] and "deleted" in payload(second)["error"]
    assert not store.exists("demo")
    final = events[-1]
    assert final["active"] is None and final["diagrams"][0]["version"] is None
    assert len(store.load_project_chat("shop")) == 2  # the project transcript is still written


def test_an_unknown_project_raises_before_anything_runs(services):
    provider = ScriptedProvider([done()])
    with pytest.raises(ProjectNotFound):
        next(make_agent(provider, services).chat("nope", "Hallo"))
    assert provider.requests == []


# ---- propose mode (ADR-0060) -----------------------------------------------------------------------


def test_propose_mode_previews_one_diagram_and_writes_nothing_but_the_transcript(services, store):
    provider = ScriptedProvider(
        [
            tool_round(add_service("payment", "c1")),
            tool_round(call("create_diagram", "c2", title="Neu", type="deployment")),
            tool_round(
                call("open_diagram", "c3", diagram="order"), call("add_node", "c4", name="X", kind="opaque_action")
            ),
            done("Vorschlag für das Deployment."),
        ]
    )
    events = run(make_agent(provider, services), active="demo", propose=True)
    added, created, opened, refused = results(events)
    assert not added["is_error"]
    assert created["is_error"] and "preview" in payload(created)["error"]
    assert not opened["is_error"]
    assert refused["is_error"] and "demo" in payload(refused)["error"]
    final = events[-1]
    proposal = final["proposal"]
    assert proposal["diagram_id"] == "demo"
    assert [op["op"] for op in proposal["operations"]] == ["add_node"]
    assert proposal["diff"]["nodes"]["added"][0]["id"] == "payment"
    assert "payment" not in store.load("demo").nodes and len(store.list_versions("demo")) == 1
    assert {d.id for d in store.list_diagrams()} == {"demo", "order"}
    entry = store.load_project_chat("shop")[-1]
    assert entry["proposed"] is True and entry["proposal_diagram"] == "demo"


# ---- compaction -----------------------------------------------------------------------------------


def test_the_project_transcript_is_compacted_like_a_diagram_transcript(services, store):
    provider = ScriptedProvider([done("eins"), done("zwei"), ModelReply("Zusammenfassung", [], "end_turn")])
    agent = make_agent(provider, services, compact_after=3, keep=2)
    run(agent, "erste Frage")
    run(agent, "zweite Frage")
    chat = store.load_project_chat("shop")
    assert chat[0]["role"] == "summary" and chat[0]["content"] == "Zusammenfassung"
    assert [e["content"] for e in chat[1:]] == ["zweite Frage", "zwei"]
    assert "diagrams of a project" in provider.requests[-1]["system"]


def test_history_replays_the_project_transcript(services):
    provider = ScriptedProvider([done("eins"), done("zwei")])
    agent = make_agent(provider, services)
    run(agent, "erste Frage")
    run(agent, "zweite Frage")
    assert [m["content"] for m in provider.requests[1]["messages"]] == ["erste Frage", "eins", "zweite Frage"]


# ---- prompt helpers ------------------------------------------------------------------------------


def test_diagram_brief_is_short_and_names_the_top_level(store, demo):
    diagram = store.load("demo")
    brief = diagram_brief(diagram, 400)
    assert brief.startswith(f"{len(diagram.nodes)} elements, {len(diagram.edges)} edges")
    assert "prod-deployment" in brief
    assert len(diagram_brief(diagram, 60)) <= 60


def test_project_structure_respects_its_budget(services, store):
    _, projects = services
    for i in range(40):
        store.create(f"Diagramm {i}", f"d{i}", Diagram(id=f"d{i}", title=f"Diagramm {i}"))
        projects.place(f"d{i}", "shop")
    project, members = projects.load_members("shop")
    text = project_structure(project, members, budget=2_000)
    assert "[d39] «deployment» Diagramm 39" in text
    assert "read_diagram" in text  # too large for briefs: says how to get details
    assert len(project_structure(project, members[:2], budget=8_000)) < 8_000


def test_the_fixed_instructions_come_before_anything_project_specific(services):
    """Prompt caching: the stable part of the project prompt (shared blocks, project part) is a prefix."""
    from arcviz import prompts

    provider = ScriptedProvider([done()])
    run(make_agent(provider, services), active="demo")
    system = provider.requests[0]["system"]
    assert system.startswith(prompts.project_prompt_prefix())
    assert "Shop" not in prompts.project_prompt_prefix()


# ---- membership and incarnation during a turn (Codex review on #140) --------------------------------------


def _move_to_other_project(services, diagram_id):
    _, projects = services
    try:
        projects.get("other")
    except ProjectNotFound:
        projects.create("Other", "other")
    projects.place(diagram_id, "other")


def test_a_diagram_moved_to_another_project_cannot_be_opened(services, store):
    provider = HookedProvider(
        [tool_round(call("open_diagram", "c1", diagram="order")), done()],
        hooks={0: lambda: _move_to_other_project(services, "order")},  # after the turn read the project
    )
    events = run(make_agent(provider, services))
    (result,) = results(events)
    assert result["is_error"] and "no longer in this project" in payload(result)["error"]
    assert events[-1]["active"] is None


def test_a_diagram_moved_away_during_the_turn_is_not_edited_any_more(services, store):
    provider = HookedProvider(
        [tool_round(add_service("one", "c1")), tool_round(add_service("two", "c2")), done()],
        hooks={1: lambda: _move_to_other_project(services, "demo")},
    )
    events = run(make_agent(provider, services), active="demo")
    first, second = results(events)
    assert not first["is_error"] and second["is_error"] and "no longer in this project" in payload(second)["error"]
    nodes = store.load("demo").nodes
    assert "one" in nodes and "two" not in nodes


def test_the_transcript_of_a_deleted_and_recreated_project_is_not_continued(services, store):
    diagrams, projects = services
    projects.create("Leer", "leer")

    def recreate() -> None:
        projects.delete("leer")
        projects.create("Leer neu", "leer")

    provider = HookedProvider([tool_round(call("list_diagrams", "c1")), done("alt")], hooks={1: recreate})
    list(make_agent(provider, services).chat("leer", "Hallo"))
    assert store.load_project_chat("leer") == []  # the new project's transcript stays empty


def test_create_diagram_is_refused_after_the_project_was_recreated(services, store):
    _, projects = services
    projects.create("Leer", "leer")

    def recreate() -> None:
        projects.delete("leer")
        projects.create("Leer neu", "leer")

    provider = HookedProvider(
        [tool_round(call("create_diagram", "c1", title="Neu", type="deployment")), done()], hooks={0: recreate}
    )
    events = list(make_agent(provider, services).chat("leer", "Lege ein Diagramm an"))
    (result,) = results(events)
    assert result["is_error"] and "deleted or its chat cleared" in payload(result)["error"]
    assert projects.get("leer").diagrams == [] and not store.exists("neu")


def test_list_diagrams_after_the_project_was_deleted_is_a_tool_error(services):
    _, projects = services
    projects.create("Leer", "leer")
    provider = HookedProvider(
        [tool_round(call("list_diagrams", "c1")), done("Weg.")], hooks={0: lambda: projects.delete("leer")}
    )
    events = list(make_agent(provider, services).chat("leer", "Was gibt es?"))
    (result,) = results(events)
    assert result["is_error"] and "deleted" in payload(result)["error"]
    assert events[-1]["type"] == "final" and events[-1]["reply"] == "Weg."
