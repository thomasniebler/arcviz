import json

from arcviz.agent import TOOLS, DiagramAgent, build_system_prompt
from arcviz.llm.base import ModelReply
from arcviz.services import DiagramService
from tests.conftest import ScriptedProvider, call


def test_vnet_scenario_streams_live_updates_and_persists(store, demo):
    provider = ScriptedProvider(
        [
            ModelReply(
                text="",
                tool_calls=[
                    call(
                        "add_node",
                        "c1",
                        name="prod-vnet",
                        kind="vnet",
                        parent="prod-deployment",
                        properties={"cidr": "10.0.0.0/16"},
                    )
                ],
                stop_reason="tool_use",
            ),
            ModelReply(
                text="",
                tool_calls=[
                    call("move_node", "c2", id="service-a", parent="prod-vnet"),
                    call("move_node", "c3", id="service-b", parent="prod-vnet"),
                    call("move_node", "c4", id="service-c", parent="prod-vnet"),
                ],
                stop_reason="tool_use",
            ),
            ModelReply(text="VNet angelegt, drei Services eingebunden.", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    agent = DiagramAgent(provider, DiagramService(store))
    events = list(agent.chat("demo", "Füge dem Deployment ein VNet hinzu und binde a, b, c ein"))

    types = [e["type"] for e in events]
    assert types.count("tool_call") == 4 and types.count("tool_result") == 4
    assert types[-2:] == ["done", "final"]

    # live updates: each successful tool result ships the intermediate diagram
    first_result = next(e for e in events if e["type"] == "tool_result")
    assert "prod-vnet" in first_result["diagram"]["nodes"]
    assert first_result["diagram"]["nodes"]["service-a"]["parent"] == "prod-deployment"
    assert "subgraph n_prod_vnet" in first_result["mermaid"]

    final = events[-1]
    assert final["reply"] == "VNet angelegt, drei Services eingebunden."
    assert final["changed"] == ["prod-vnet", "service-a", "service-b", "service-c"]
    assert final["version"]["version"] == 2 and final["version"]["source"] == "auto"
    assert "class n_prod_vnet,n_service_a,n_service_b,n_service_c changed" in final["mermaid"]

    # persisted as latest + version + transcript
    saved = store.load("demo")
    assert saved.nodes["service-c"].parent == "prod-vnet"
    assert [v.version for v in store.list_versions("demo")] == [1, 2]
    chat = store.load_chat("demo")
    assert chat[0]["role"] == "user" and chat[1]["version"] == 2 and len(chat[1]["operations"]) == 4

    # the model saw the summary and all tools, and received tool results in one message batch
    assert "[service-a] «service» service-a" in provider.requests[0]["system"]
    assert provider.requests[0]["tools"] == [t.name for t in TOOLS]
    assert [m["role"] for m in provider.requests[2]["messages"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "tool",
        "tool",
    ]


def test_tool_error_is_reported_and_nothing_is_persisted(store, demo):
    provider = ScriptedProvider(
        [
            ModelReply(
                text="",
                tool_calls=[call("move_node", "c1", id="service-a", parent="service-b")],
                stop_reason="tool_use",
            ),
            ModelReply(text="Das geht nicht, service-b ist kein Container.", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    events = list(DiagramAgent(provider, DiagramService(store)).chat("demo", "schiebe a in b"))
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["is_error"] and "cannot contain" in json.loads(result["content"])["error"]
    assert "diagram" not in result
    final = events[-1]
    assert final["version"] is None and final["changed"] == []
    assert len(store.list_versions("demo")) == 1
    tool_msg = provider.requests[1]["messages"][-1]
    assert tool_msg["is_error"] is True


def test_history_is_fed_back_and_errors_surface(store, demo):
    store.append_chat("demo", [{"role": "user", "content": "hallo"}, {"role": "assistant", "content": "hi"}])
    provider = ScriptedProvider([ModelReply(text="", tool_calls=[], stop_reason="refusal")])
    events = list(DiagramAgent(provider, DiagramService(store)).chat("demo", "mach was"))
    assert provider.requests[0]["messages"][:2] == [
        {"role": "user", "content": "hallo"},
        {"role": "assistant", "content": "hi"},
    ]
    assert any(e["type"] == "error" for e in events)
    assert events[-1]["error"]
    assert store.load_chat("demo")[-1]["error"]


def test_get_diagram_tool_returns_json(store, demo):
    provider = ScriptedProvider(
        [
            ModelReply(text="", tool_calls=[call("get_diagram", "c1")], stop_reason="tool_use"),
            ModelReply(text="7 Elemente im Deployment.", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    events = list(DiagramAgent(provider, DiagramService(store)).chat("demo", "was ist drin?"))
    payload = json.loads(next(e for e in events if e["type"] == "tool_result")["content"])
    assert set(payload["nodes"]) >= {"service-a", "prod-deployment"}
    assert len(store.list_versions("demo")) == 1  # read-only turn: no new version


def test_system_prompt_has_examples_and_instruction_passes_through_unchanged(store, demo):
    provider = ScriptedProvider(
        [
            ModelReply(
                text="",
                tool_calls=[call("get_diagram", "c1")],
                stop_reason="tool_use",
                usage={"input_tokens": 1200, "output_tokens": 30},
            ),
            ModelReply(
                text="Fertig.", tool_calls=[], stop_reason="end_turn", usage={"input_tokens": 1300, "output_tokens": 12}
            ),
        ]
    )
    agent = DiagramAgent(provider, DiagramService(store))
    message = "Bitte benenne „orders-db“ in Orders database um – ohne Rückfrage."
    events = list(agent.chat("demo", message))
    system = provider.requests[0]["system"]
    assert "Examples" in system and 'update_node(id="orders-db"' in system and "cascade=true" in system
    # the user's wording reaches the model verbatim (language, quotes, dashes), as the last message
    assert provider.requests[0]["messages"][-1] == {"role": "user", "content": message}
    # usage is summed over the rounds, shipped in done/final and persisted
    done = next(e for e in events if e["type"] == "done")
    assert done["usage"] == {"input_tokens": 2500, "output_tokens": 42}
    assert events[-1]["usage"] == {"input_tokens": 2500, "output_tokens": 42}
    assert store.load_chat("demo")[-1]["usage"] == {"input_tokens": 2500, "output_tokens": 42}


def test_usage_is_kept_when_the_turn_ends_with_an_error(store, demo):
    provider = ScriptedProvider(
        [
            ModelReply(
                text="",
                tool_calls=[call("get_diagram", "c1")],
                stop_reason="tool_use",
                usage={"input_tokens": 1000, "output_tokens": 20},
            ),
            ModelReply(
                text="Ich", tool_calls=[], stop_reason="max_tokens", usage={"input_tokens": 1100, "output_tokens": 5}
            ),
        ]
    )
    agent = DiagramAgent(provider, DiagramService(store))
    events = list(agent.chat("demo", "Erzähl mir alles über das Diagramm."))
    error = next(e for e in events if e["type"] == "error")
    assert "max_tokens" in error["message"]
    expected = {"input_tokens": 2100, "output_tokens": 25}  # both rounds, including the one that was cut off
    assert error["usage"] == expected
    assert events[-1]["error"] and events[-1]["usage"] == expected
    assert store.load_chat("demo")[-1]["usage"] == expected


def test_system_prompt_lists_kinds_and_state(demo):
    prompt = build_system_prompt(demo)
    assert "vnet" in prompt and "[prod-deployment] «deployment»" in prompt


def test_mutations_survive_a_client_disconnect(store, demo):
    provider = ScriptedProvider(
        [
            ModelReply(
                text="",
                tool_calls=[call("add_node", "c1", name="prod-vnet", kind="vnet", parent="prod-deployment")],
                stop_reason="tool_use",
            ),
            ModelReply(text="never reached", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    gen = DiagramAgent(provider, DiagramService(store)).chat("demo", "vnet bitte")
    events = []
    for event in gen:
        events.append(event)
        if event["type"] == "tool_result":
            break
    # latest.json already reflects the applied operation before the stream is closed
    assert "prod-vnet" in store.load("demo").nodes
    assert len(store.list_versions("demo")) == 1

    gen.close()  # what the server does when the browser goes away mid-stream

    assert [v.source for v in store.list_versions("demo")] == ["initial", "auto"]
    chat = store.load_chat("demo")
    assert chat[-1]["role"] == "assistant" and "interrupted" in chat[-1]["error"]
    assert chat[-1]["version"] == 2 and len(chat[-1]["operations"]) == 1
    assert len(provider.requests) == 1  # the model was not called again after the disconnect


def test_apply_batch_is_one_tool_call_one_save_and_one_version(store, demo):
    batch = [
        {"op": "add_node", "args": {"name": "prod-vnet", "kind": "vnet", "parent": "prod-deployment"}},
        {"op": "move_node", "args": {"id": "service-a", "parent": "prod-vnet"}},
        {"op": "move_node", "args": {"id": "service-b", "parent": "prod-vnet"}},
    ]
    provider = ScriptedProvider(
        [
            ModelReply(text="", tool_calls=[call("apply_batch", "c1", operations=batch)], stop_reason="tool_use"),
            ModelReply(text="Erledigt.", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    saves = []
    original = store.commit_latest
    store.commit_latest = lambda d, expected, info: (saves.append(info is not None), original(d, expected, info))[1]  # type: ignore[method-assign]
    events = list(DiagramAgent(provider, DiagramService(store)).chat("demo", "vnet mit a und b"))
    assert "apply_batch" in provider.requests[0]["tools"]
    results = [e for e in events if e["type"] == "tool_result"]
    assert len(results) == 1 and not results[0]["is_error"]
    assert results[0]["diagram"]["nodes"]["service-b"]["parent"] == "prod-vnet"
    assert saves == [False, True]  # latest once after the batch, latest + version once at finalization
    final = events[-1]
    assert final["changed"] == ["prod-vnet", "service-a", "service-b"]
    assert [op["op"] for op in final["operations"]] == ["apply_batch"]
    assert [v.source for v in store.list_versions("demo")] == ["initial", "auto"]


def test_failing_apply_batch_is_an_error_result_and_changes_nothing(store, demo):
    batch = [
        {"op": "add_node", "args": {"name": "prod-vnet", "kind": "vnet", "parent": "prod-deployment"}},
        {"op": "move_node", "args": {"id": "service-a", "parent": "nowhere"}},
    ]
    provider = ScriptedProvider(
        [
            ModelReply(text="", tool_calls=[call("apply_batch", "c1", operations=batch)], stop_reason="tool_use"),
            ModelReply(text="Ging nicht.", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    events = list(DiagramAgent(provider, DiagramService(store)).chat("demo", "vnet"))
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["is_error"] and "Batch step 2 (move_node)" in json.loads(result["content"])["error"]
    assert "prod-vnet" not in store.load("demo").nodes
    assert events[-1]["version"] is None and len(store.list_versions("demo")) == 1


def test_apply_batch_tool_schema_lists_every_other_operation():
    from arcviz.operations import INTERNAL_OPERATIONS, MODEL_OPERATIONS, RELATION_OPERATIONS, TYPE_OPERATIONS

    spec = next(t for t in TOOLS if t.name == "apply_batch")
    items = spec.parameters["properties"]["operations"]["items"]
    deployment = set(TYPE_OPERATIONS["deployment"])
    assert sorted(items["properties"]["op"]["enum"]) == sorted(deployment - {"apply_batch"} - INTERNAL_OPERATIONS)
    assert {t.name for t in TOOLS} >= deployment - set(MODEL_OPERATIONS) - set(RELATION_OPERATIONS)  # own tools


def test_compaction_input_describes_a_batch():
    from arcviz.agent import _describe_op

    op = {"op": "apply_batch", "arguments": {"operations": [{"op": "add_node"}, {"op": "add_edge"}]}}
    assert _describe_op(op) == "apply_batch (2 steps)"


def test_edge_tools_expose_direction_and_properties():
    by_name = {t.name: t.parameters["properties"] for t in TOOLS}
    for tool in ("add_edge", "update_edge"):
        assert by_name[tool]["direction"]["enum"] == ["forward", "both", "none", "default"]
        assert by_name[tool]["properties"]["additionalProperties"] == {"type": "string"}
    assert by_name["update_edge"]["remove_properties"]["type"] == "array"
