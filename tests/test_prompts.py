"""System prompts composed from shared blocks and per-type expert parts.

Spec: docs/architecture/specs/assistant-prompts.md (ADR-0065), docs/architecture/specs/topic-scope.md (ADR-0066).
"""

from __future__ import annotations

import pytest

from arcviz import agent, prompts
from arcviz.agent import DiagramAgent, build_system_prompt
from arcviz.llm.base import ModelReply
from arcviz.model import DIAGRAM_TYPES, Diagram
from arcviz.services import DiagramService
from arcviz.templates import TEMPLATES
from tests.conftest import ScriptedProvider, call

STARTERS = {
    "deployment": "demo",
    "activity": "order-process",
    "sequence": "checkout",
    "state_machine": "order-lifecycle",
    "class": "webshop-domain",
    "use_case": "webshop-use-cases",
    "component": "webshop-components",
}


def empty(diagram_type: str) -> Diagram:
    return Diagram(id="x", title="X", type=diagram_type)


@pytest.mark.parametrize("diagram_type", DIAGRAM_TYPES)
def test_every_type_has_shared_blocks_then_its_expert_part_then_the_state(diagram_type):
    assert prompts.EXPERT_PARTS[diagram_type].strip()
    prompt = build_system_prompt(empty(diagram_type))
    positions = [prompt.index(block) for block in prompts.SHARED_BLOCKS]
    assert positions == sorted(positions) and positions[0] == 0
    expert = prompts.EXPERT_PARTS[diagram_type].split("{kinds}")[0]
    assert positions[-1] < prompt.index(expert) < prompt.index(prompts.STATE_HEADING)
    assert "{kinds}" not in prompt and "{summary}" not in prompt


def test_shared_blocks_cover_persona_scope_rules_hints_and_reply():
    assert prompts.SHARED_BLOCKS == (
        prompts.PERSONA,
        prompts.TOPIC_SCOPE,
        prompts.WORKING_RULES,
        prompts.MODELLING_HINTS,
        prompts.REPLY_STYLE,
    )
    assert all(block and block == block.strip() for block in prompts.SHARED_BLOCKS)
    assert "UML 2.5.1" in prompts.PERSONA and "architect" in prompts.PERSONA
    assert "at most one" in prompts.MODELLING_HINTS and "empty diagram" in prompts.MODELLING_HINTS
    assert "language" in prompts.REPLY_STYLE


@pytest.mark.parametrize("diagram_type", DIAGRAM_TYPES)
def test_prefix_is_stable_and_the_summary_comes_last(diagram_type):
    """Prompt caching: everything before the state depends on the type and the kinds only."""
    first = build_system_prompt(empty(diagram_type))
    other = TEMPLATES[STARTERS[diagram_type]]("y", "Y")
    second = build_system_prompt(other)
    head = first.index(prompts.STATE_HEADING)
    assert first[:head] == second[:head]
    assert second.endswith(other.summary(budget=agent.DEFAULT_CONTEXT_BUDGET) + "\n")


def test_placeholders_are_replaced_without_format_semantics():
    prompt = prompts.system_prompt("deployment", "vnet, subnet", "Diagram {kinds} {x}")
    assert "Available kinds: vnet, subnet." in prompt
    assert prompt.endswith("Current state:\nDiagram {kinds} {x}\n")  # the summary is never interpreted
    assert '{"cidr": "10.0.0.0/16"}' in prompt  # literal braces in examples need no escaping


def test_compose_joins_blocks_for_other_prompts():
    assert prompts.compose("a", "b\n", " c") == "a\n\nb\n\nc"
    assert prompts.prompt_prefix("activity", "x").startswith(prompts.compose(*prompts.SHARED_BLOCKS))


def test_unknown_type_has_no_prompt():
    with pytest.raises(KeyError):
        prompts.prompt_prefix("mindmap", "")


@pytest.mark.parametrize(
    ("diagram_type", "keywords"),
    [
        ("deployment", ["chapter 19", "availability zone", "subnet", "protocol", "execution environment"]),
        ("activity", ["chapter 15", "swimlane", "guard", "fork_node", "use case", "abstraction"]),
        ("sequence", ["chapter 17", "one scenario", "reply", "alt", "ref", "participants"]),
        ("state_machine", ["chapter 14", "lifecycle", "initial_pseudostate", "composite_state", "guard", "trigger"]),
        ("class", ["chapter 11", "glossary", "multiplicit", "composition", "generalization", "package"]),
        ("use_case", ["chapter 18", "system boundary", "actor", "include", "extend", "specification"]),
        ("component", ["chapter 11", "interface", "port", "responsibilit", "subsystem", "provide"]),
    ],
)
def test_expert_parts_carry_modelling_guidance_and_a_guided_start(diagram_type, keywords):
    part = prompts.EXPERT_PARTS[diagram_type]
    for keyword in keywords:
        assert keyword.lower() in part.lower(), keyword
    assert "Guided start" in part


def test_every_round_of_a_turn_sends_the_same_prompt(store, demo):
    provider = ScriptedProvider(
        [
            ModelReply(
                text="",
                tool_calls=[call("add_node", "c1", name="cache", kind="storage", parent="prod-deployment")],
                stop_reason="tool_use",
            ),
            ModelReply(text="Erledigt.", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    expected = build_system_prompt(store.load("demo"))
    list(DiagramAgent(provider, DiagramService(store), background=False).chat("demo", "Cache anlegen"))
    assert [r["system"] for r in provider.requests] == [expected, expected]


# ---- topic scope (#124) -------------------------------------------------------------------------------


@pytest.mark.parametrize("diagram_type", DIAGRAM_TYPES)
def test_every_type_prompt_carries_the_one_shared_scope(diagram_type):
    prompt = build_system_prompt(empty(diagram_type))
    assert prompt.count(prompts.TOPIC_SCOPE) == 1
    assert prompt.index(prompts.TOPIC_SCOPE) < prompt.index(prompts.EXPERT_PARTS[diagram_type].split("{kinds}")[0])


def test_scope_names_topics_refusal_and_disguised_requests():
    scope = prompts.TOPIC_SCOPE.lower()
    for topic in ("uml 2.5.1", "architecture", "cloud", "deployment", "arcviz"):
        assert topic in scope, topic
    assert "one short sentence" in scope and "no tool calls" in scope and "user's language" in scope
    assert "disguised" in scope and "unchanged" in scope
    assert "nat gateway" in scope  # borderline architecture questions stay allowed


def test_a_declined_request_changes_nothing(store, demo):
    refusal = "Dazu kann ich nicht helfen – ich unterstütze dich beim Modellieren deiner Architektur."
    provider = ScriptedProvider([ModelReply(text=refusal, tool_calls=[], stop_reason="end_turn")])
    before, versions = store.load("demo"), store.list_versions("demo")
    events = list(DiagramAgent(provider, DiagramService(store), background=False).chat("demo", "Käsekuchenrezept?"))
    final = events[-1]
    assert final["reply"] == refusal and final["operations"] == [] and final["version"] is None
    assert store.list_versions("demo") == versions and store.load("demo") == before
    assert prompts.TOPIC_SCOPE in provider.requests[0]["system"] and len(provider.requests) == 1
    assert store.load_chat("demo")[-1]["content"] == refusal


# ---- the project chat (milestone M17, spec: docs/architecture/specs/project-chat.md) -------------------------


def _project_prompt(active_type: str | None, title: str = "Shop"):
    from arcviz import projects as project_ops
    from arcviz.project_agent import build_project_prompt

    project = project_ops.new_project(title, "shop", "Ein Webshop")
    diagram = empty(active_type or "deployment")
    project_ops.apply(project, "add_diagram", {"diagram": diagram.id})
    return build_project_prompt(project, [(project.member(diagram.id), diagram)], diagram if active_type else None)


@pytest.mark.parametrize("active_type", [None, *DIAGRAM_TYPES])
def test_the_project_chat_starts_with_the_shared_blocks_then_its_part_then_the_project(active_type):
    prompt = _project_prompt(active_type)
    positions = [prompt.index(block) for block in prompts.SHARED_BLOCKS]
    assert positions == sorted(positions) and positions[0] == 0
    project_part = prompt.index(prompts.PROJECT_PART)
    assert positions[-1] < project_part < prompt.index("Project: Shop") < prompt.index("[x] «")
    if active_type is None:
        assert prompts.STATE_HEADING not in prompt
        return
    expert = prompts.EXPERT_PARTS[active_type].split("{kinds}")[0]
    assert prompt.index("Project: Shop") < prompt.index(expert) < prompt.index(prompts.STATE_HEADING)
    assert prompt.count(prompts.PERSONA) == 1  # the active diagram adds its expert part, not the shared blocks again
    assert "{kinds}" not in prompt


def test_the_project_chat_prefix_is_shared_by_every_project():
    first, second = _project_prompt("deployment", "Shop"), _project_prompt("sequence", "Anderes Projekt")
    prefix = prompts.project_prompt_prefix()
    assert first.startswith(prefix) and second.startswith(prefix)
    assert prompts.TOPIC_SCOPE in prefix  # the project chat declines off-topic requests like the diagram chat


def test_project_part_is_a_reviewable_text_block():
    assert prompts.PROJECT_PART.strip() == prompts.PROJECT_PART
    for tool in ("list_diagrams", "read_diagram", "open_diagram", "create_diagram"):
        assert tool in prompts.PROJECT_PART


@pytest.mark.parametrize("diagram_type", DIAGRAM_TYPES)
def test_diagram_part_is_the_expert_part_and_the_state(diagram_type):
    part = prompts.diagram_part(diagram_type, "a, b", "SUMMARY")
    assert part.startswith(prompts.EXPERT_PARTS[diagram_type].split("{kinds}")[0])
    assert part.endswith(f"{prompts.STATE_HEADING}\nSUMMARY\n") and prompts.PERSONA not in part


# ---- the shared project model (spec: shared-project-model.md, ADR-0076) ----------------------------------------


def test_the_model_block_is_reviewable_text_and_sits_between_prefix_and_state():
    assert "update_element" in prompts.MODEL_PART and "link_element" in prompts.MODEL_PART
    full = prompts.system_prompt("deployment", "service", "STATE", "- [a] «component» A")
    prefix = prompts.prompt_prefix("deployment", "service")
    assert full.startswith(prefix)
    assert full.index(prompts.MODEL_PART) > len(prefix) - 1
    assert full.index("- [a] «component» A") < full.index(prompts.STATE_HEADING)
    assert prompts.system_prompt("deployment", "service", "STATE") == prompts.system_prompt(
        "deployment", "service", "STATE", None
    )
    assert prompts.MODEL_HEADING not in prompts.system_prompt("deployment", "service", "STATE")
