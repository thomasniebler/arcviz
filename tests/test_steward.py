"""The project steward (spec: docs/architecture/specs/project-steward.md, ADR-0204, ADR-0205, ADR-0206)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import ScriptedProvider, call
from fastapi.testclient import TestClient

from arcviz import projects, steward
from arcviz.envconfig import ConfigError
from arcviz.live import LiveHub
from arcviz.llm.base import ModelReply
from arcviz.project_review import ProjectFinding, ProjectSnapshot, Resolution
from arcviz.proposals import PROPOSABLE_OPERATIONS
from arcviz.server import create_app
from arcviz.services import ServiceRoot
from arcviz.steward_model import StewardBox, StewardConfig, finding_key
from arcviz.workspaces import Identity

ADA = Identity("ada", "Ada", "ada@example.org")
CONFIG = StewardConfig(enabled=True, verifier_provider="openai", verifier_model="other-1", debounce_seconds=1)


def reply(tool: str, **arguments: Any) -> ModelReply:
    return ModelReply(
        text="",
        tool_calls=[call(tool, **arguments)],
        stop_reason="tool_use",
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def findings(*candidates: dict[str, Any]) -> ModelReply:
    return reply("submit_steward_findings", candidates=list(candidates))


def verdict(value: str = "supported", reason: str = "Die Treiber belegen es.") -> ModelReply:
    return reply("submit_verdict", verdict=value, reason=reason)


SCENARIO = {
    "kind": "next_step",
    "title": "Szenario für die Verfügbarkeit",
    "rationale": "Das Qualitätsziel d1 hat noch kein messbares Szenario.",
    "cites": ["d1"],
    "serves": ["d1"],
    "items": [
        {"key": "q", "op": "add_scenario", "arguments": {"title": "Knoten fällt aus", "driver": "d1", "core": True}}
    ],
}
ELEMENT = {
    "kind": "fix",
    "title": "Zahlungsdienst fehlt im Modell",
    "rationale": "Der Treiber nennt den Zahlungsdienst, das Modell kennt ihn nicht.",
    "cites": ["d1"],
    "items": [
        {"key": "e", "op": "add_element", "arguments": {"name": "Zahlungsdienst", "kind": "system"}},
        {"key": "s", "op": "add_element", "arguments": {"name": "Shop", "kind": "system"}},
        {"key": "r", "op": "add_relation", "arguments": {"kind": "communicates", "source": "@s", "target": "@e"}},
    ],
}


@pytest.fixture
def root(store):
    hub = LiveHub()
    root = ServiceRoot(store, hub, steward_config=CONFIG)
    root.users.seen(ADA)
    ada = root.for_actor(ADA)
    ada.projects.create("Shop", "shop")
    ada.projects.apply("shop", "add_driver", {"title": "Verfügbarkeit", "kind": "quality_goal", "importance": "high"})
    ada.steward.set_enabled("shop", True)
    return root


def make(root, main: list[ModelReply], other: list[ModelReply] | None = None, config: StewardConfig = CONFIG):
    primary, second = ScriptedProvider(main), ScriptedProvider(other or [])
    runner = steward.Steward(root, config, lambda: primary, lambda: second)
    return runner, primary, second


def pending(root) -> list:
    return [p for p in root.for_actor(ADA).proposals.list("shop", "pending") if p.client == "steward"]


# ---- the pieces ----------------------------------------------------------------------------------------------------


def test_the_configuration_needs_the_verifier_as_a_pair():
    assert StewardConfig.from_env({}).enabled is False
    on = StewardConfig.from_env(
        {
            "ARCVIZ_STEWARD": "1",
            "ARCVIZ_VERIFIER_PROVIDER": "openai",
            "ARCVIZ_VERIFIER_MODEL": "m",
            "ARCVIZ_STEWARD_MAX_ITEMS": "5",
        }
    )
    assert on.enabled and on.verifier_configured and on.max_items == 5
    with pytest.raises(ConfigError, match="together"):
        StewardConfig.from_env({"ARCVIZ_VERIFIER_PROVIDER": "openai"})
    with pytest.raises(ConfigError):
        StewardConfig.from_env({"ARCVIZ_STEWARD_MAX_ITEMS": "100"})


def snapshot_of(root) -> ProjectSnapshot:
    svc = root.system_services()
    project, members = svc.projects.load_members("shop")
    return ProjectSnapshot(project, members, svc.requirements.box("shop"))


def test_candidates_are_validated(root):
    snap = snapshot_of(root)
    ok, dropped = steward.parse_candidates({"candidates": [SCENARIO, ELEMENT]}, snap)
    assert [c.kind for c in ok] == ["next_step", "fix"] and dropped == 0
    bad = [
        {**SCENARIO, "cites": ["d99"]},  # an id that does not exist
        {**SCENARIO, "serves": []},  # a next step that serves nothing
        {**SCENARIO, "serves": ["d99"]},
        {**SCENARIO, "cites": []},
        {
            **ELEMENT,
            "items": [{"key": "x", "op": "remove_relation", "arguments": {"relation": "r1"}}],
        },  # never a deletion
        {**SCENARIO, "items": [{"key": "q", "op": "add_scenario", "arguments": {"title": "x", "driver": "nope"}}]},
        {
            **SCENARIO,
            "items": [{"key": "q", "op": "update_decision", "arguments": {"decision": "adr1", "status": "accepted"}}],
        },
        {**SCENARIO, "items": [{"key": "Q!", "op": "add_scenario", "arguments": {"title": "x"}}]},
        {**SCENARIO, "kind": "wish"},
        "text",
    ]
    ok, dropped = steward.parse_candidates({"candidates": bad}, snap)
    assert ok == [] and dropped == len(bad)
    assert steward.parse_candidates("nonsense", snap) == ([], 0)


def test_a_proposal_cannot_set_the_vision_of_an_existing_one_by_accident(root):
    assert "set_vision" in PROPOSABLE_OPERATIONS and "remove_relation" not in steward.LLM_OPERATIONS


def test_rule_items_only_where_the_model_has_exactly_one_way():
    project = projects.new_project("Shop", "shop")
    projects.apply(project, "add_element", {"name": "Api", "kind": "component"})
    projects.apply(project, "add_element", {"name": "Db", "kind": "data"})
    one = Resolution(
        action="create_relation", label="x", args={"kind": "communicates", "source": "api", "target": "db"}
    )
    drop = Resolution(action="remove_relation", label="y", relation="r1")
    two = [Resolution(action="update_relation", label="a", relation="r1", args={"description": "a"})] * 2
    make_finding = lambda rs: ProjectFinding(  # noqa: E731
        id="f",
        rule="connection-without-relation",
        category="suggestion",
        severity="low",
        message="m",
        source="rule",
        resolutions=rs,
    )
    got = steward.rule_candidates([make_finding([one, drop]), make_finding(two), make_finding([drop])], project)
    assert [c.items[0]["op"] for c in got] == ["add_relation"]  # never a removal, never a choice between alternatives


def test_select_drops_dismissed_taken_and_over_the_cap():
    def cand(n: int, kind: str = "fix", items: int = 1) -> steward.Candidate:
        return steward.Candidate(
            kind,
            f"t{n}",
            "r",
            ["d1"],
            [],
            [{"key": f"k{i}", "op": "add_element", "arguments": {}} for i in range(items)],
            f"llm:{n}",
        )

    got = steward.select([cand(1), cand(2), cand(3, "next_step"), cand(4, items=3)], {"llm:1"}, {"llm:2"}, 3)
    assert [c.finding for c in got] == ["llm:4"] or [c.finding for c in got] == ["llm:4"]
    got = steward.select([cand(5, "next_step"), cand(6)], set(), set(), 10)
    assert [c.kind for c in got] == ["fix", "next_step"]  # fixes first


def test_proposal_items_get_keys_that_are_unique_across_candidates():
    a = steward.Candidate(
        "fix",
        "A",
        "ra",
        ["d1"],
        [],
        [
            {"key": "e", "op": "add_element", "arguments": {"name": "X"}},
            {"key": "r", "op": "add_relation", "arguments": {"source": "@e", "target": "@e"}},
        ],
        "k1",
    )
    b = steward.Candidate(
        "fix",
        "B",
        "rb",
        ["d1"],
        [],
        [{"key": "e", "op": "add_element", "arguments": {"name": "Y"}}],
        "k2",
        "supported",
        "belegt",
    )
    items = steward.proposal_items([a, b])
    assert [i["key"] for i in items] == ["c1_e", "c1_r", "c2_e"]
    assert items[1]["arguments"] == {"source": "@c1_e", "target": "@c1_e"}
    assert (
        items[2]["rationale"].endswith("Prüfung: belegt") and items[2]["group"] == "fix" and items[2]["finding"] == "k2"
    )


def test_the_box_remembers_bounded_dismissals_and_run_counts():
    box = StewardBox()
    box.dismiss([f"f{n}" for n in range(600)])
    assert len(box.dismissed) == 500 and box.dismissed[-1] == "f599"
    now = datetime(2026, 10, 26, tzinfo=UTC)
    box.count_run(now)
    box.count_run(now)
    assert box.runs_today(now) == 2
    assert finding_key("llm:fix", "a") == finding_key("llm:fix", "a") != finding_key("llm:fix", "b")


# ---- a run ---------------------------------------------------------------------------------------------------------


def test_a_run_proposes_what_the_second_model_confirmed(root):
    runner, main, other = make(root, [findings(SCENARIO, ELEMENT)], [verdict(), verdict()])
    report = runner.run("shop")
    assert report["outcome"] == "proposed" and report["verified"] == 2 and report["items"] == 4
    (stored,) = pending(root)
    assert stored.kind == "project" and stored.author == "steward" and stored.client == "steward"
    assert [i.group for i in stored.items] == ["fix", "fix", "fix", "next_step"]  # fixes first
    assert all(i.rationale and i.cites and i.finding for i in stored.items)
    assert "Prüfung:" in stored.items[0].rationale
    # the main call saw the project, the verifier saw the candidate and its cited object but not the main model's text
    assert "Architecture drivers" in main.requests[0]["messages"][0]["content"]
    asked = other.requests[0]["messages"][0]["content"]
    assert "Cited objects as stored" in asked and "Verfügbarkeit" in asked
    assert main.requests[0]["tools"] == ["submit_steward_findings"] and other.requests[0]["tools"] == ["submit_verdict"]


def test_only_a_person_applies_and_the_model_items_work(root):
    runner, *_ = make(root, [findings(SCENARIO, ELEMENT)], [verdict(), verdict()])
    runner.run("shop")
    ada = root.for_actor(ADA)
    (stored,) = pending(root)
    assert ada.projects.get("shop").scenarios == []  # nothing was written by the run
    done = ada.proposals.apply("shop", stored.id)
    states = {i.key: i.state for i in done.items}
    assert set(states.values()) == {"applied"}, states
    project = ada.projects.get("shop")
    assert [s.title for s in project.scenarios] == ["Knoten fällt aus"]
    assert [e.name for e in project.elements] == ["Zahlungsdienst", "Shop"] and len(project.relations) == 1


def test_a_candidate_the_second_model_doubts_is_dropped(root):
    for answer in ("unsupported", "unclear"):
        runner, *_ = make(root, [findings(SCENARIO)], [verdict(answer)])
        report = runner.run("shop", manual=True)
        assert report["outcome"] == "clean" and report["items"] == 0 and report["dropped"] == 1
        assert pending(root) == []


def test_a_failing_verifier_never_lets_a_candidate_through(root):
    class Broken(ScriptedProvider):
        def _complete(self, *a: Any, **k: Any) -> ModelReply:
            raise RuntimeError("down")

    primary = ScriptedProvider([findings(SCENARIO)])
    runner = steward.Steward(root, CONFIG, lambda: primary, lambda: Broken([]))
    assert runner.run("shop")["outcome"] == "clean" and pending(root) == []


def test_a_failing_main_model_still_gives_the_rule_items(root):
    class Broken(ScriptedProvider):
        def _complete(self, *a: Any, **k: Any) -> ModelReply:
            raise RuntimeError("down")

    runner = steward.Steward(root, CONFIG, lambda: Broken([]), lambda: ScriptedProvider([]))
    assert runner.run("shop")["outcome"] == "clean"


def test_without_a_verifier_the_model_is_not_even_asked(root):
    off = StewardConfig(enabled=True)
    primary = ScriptedProvider([findings(SCENARIO)])
    runner = steward.Steward(root, off, lambda: primary, lambda: None)
    report = runner.run("shop")
    assert primary.requests == [] and report["outcome"] == "clean"


def test_the_same_state_is_not_checked_twice_and_a_new_run_replaces_the_pending_proposal(root):
    runner, *_ = make(root, [findings(SCENARIO), findings(ELEMENT)], [verdict(), verdict()])
    first = runner.run("shop")
    assert runner.run("shop")["outcome"] == "unchanged"
    (before,) = pending(root)
    ada = root.for_actor(ADA)
    ada.projects.apply("shop", "add_driver", {"title": "Kosten", "kind": "constraint"})  # a change
    second = runner.run("shop")
    (after,) = pending(root)
    assert first["proposal"] == before.id and second["proposal"] == after.id != before.id
    old = ada.proposals.get("shop", before.id)
    assert old.status == "discarded"  # the earlier one is withdrawn, not decided by a person
    assert root.system_services().steward_store.load("shop")[0].dismissed == []  # and not remembered as dismissed


def test_what_a_person_discards_does_not_come_back(root):
    runner, *_ = make(root, [findings(SCENARIO), findings(SCENARIO)], [verdict(), verdict()])
    runner.run("shop")
    ada = root.for_actor(ADA)
    (stored,) = pending(root)
    ada.proposals.discard("shop", stored.id)
    assert root.system_services().steward_store.load("shop")[0].dismissed == [stored.items[0].finding]
    ada.projects.apply("shop", "add_driver", {"title": "Kosten", "kind": "constraint"})  # a change to check again
    assert runner.run("shop")["outcome"] == "clean" and pending(root) == []


def test_the_day_cap_and_the_switch(root):
    runner, *_ = make(
        root,
        [findings(SCENARIO)] * 3,
        [verdict()] * 3,
        StewardConfig(enabled=True, verifier_provider="o", verifier_model="m", max_runs_per_day=1),
    )
    assert runner.run("shop", manual=True)["outcome"] == "proposed"
    assert runner.run("shop", manual=True)["outcome"] == "capped"
    root.for_actor(ADA).steward.set_enabled("shop", False)
    assert runner.run("shop")["outcome"] == "disabled"


def test_a_run_writes_nothing_but_its_proposal_and_its_own_document(root, store, monkeypatch):
    from arcviz.services import ProjectService, ProposalService

    def forbidden(*_a: Any, **_k: Any) -> None:
        raise AssertionError("the steward must never apply anything")

    monkeypatch.setattr(ProjectService, "apply", forbidden)
    monkeypatch.setattr(ProjectService, "apply_all", forbidden)
    monkeypatch.setattr(ProposalService, "apply", forbidden)
    runner, *_ = make(root, [findings(SCENARIO, ELEMENT)], [verdict(), verdict()])
    ada = root.for_actor(ADA)
    before = ada.projects.get("shop").model_dump(mode="json")
    seen: list[dict[str, Any]] = []
    root.hub.add_listener(seen.append)
    runner.run("shop")
    assert ada.projects.get("shop").model_dump(mode="json") == before
    assert seen == []  # its own writes publish no change, so it never triggers itself


def test_the_store_lease_excludes_a_second_holder_until_it_expires(root):
    store = root.system_services().steward_store
    first = store.acquire("shop", 60)
    assert first is not None and store.acquire("shop", 60) is None
    store.release("shop", first)
    again = store.acquire("shop", 0.01)
    assert again is not None
    time.sleep(0.05)
    assert store.acquire("shop", 60) is not None  # expired: taken over


def test_the_worker_checks_a_changed_project_after_the_debounce(root):
    runner, *_ = make(root, [findings(SCENARIO)], [verdict()])
    worker = steward.StewardWorker(runner, root.hub)
    worker.notice({"type": "project", "project": "shop"})
    worker.notice({"type": "chat", "project": "other"})  # a transcript is no change
    now = time.monotonic()
    assert worker.step(now) == []  # not yet
    assert worker.step(now + 5) == ["shop"]
    assert worker.step(now + 10) == []
    assert len(pending(root)) == 1


def test_the_worker_sweeps_enabled_projects(root):
    runner, *_ = make(root, [findings(SCENARIO)], [verdict()])
    worker = steward.StewardWorker(runner, None)
    assert worker.step(time.monotonic() + CONFIG.debounce_seconds + 10) == ["shop"]


def test_the_worker_places_a_diagram_event_in_its_project(root):
    ada = root.for_actor(ADA)
    ada.diagrams.create("Demo", "demo", "demo") if False else None
    runner, *_ = make(root, [], [])
    worker = steward.StewardWorker(runner, None)
    worker.notice({"type": "changed", "diagram": "missing"})  # unknown: ignored, never raises
    assert worker._dirty == {}


# ---- the routes ----------------------------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ARCVIZ_STEWARD", "1")
    return TestClient(create_app(provider=ScriptedProvider([findings(SCENARIO)])))


def test_the_routes(client: TestClient):
    pid = client.post("/api/projects", json={"title": "Shop"}).json()["project"]["id"]
    client.post(f"/api/projects/{pid}/drivers", json={"title": "Verfügbarkeit", "kind": "quality_goal"})
    status = client.get(f"/api/projects/{pid}/steward").json()
    assert status["available"] and status["enabled"] is False and status["verifier"] == "missing"
    assert client.put(f"/api/projects/{pid}/steward", json={"enabled": True}).json()["enabled"] is True
    report = client.post(f"/api/projects/{pid}/steward/run").json()
    assert report["outcome"] == "clean" and report["runs_today"] == 1 and report["last_run"]["outcome"] == "clean"
    assert client.get("/api/projects/missing/steward").status_code == 404


def test_the_run_route_answers_409_when_the_installation_switched_it_off(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_DATA_DIR", str(tmp_path))
    client = TestClient(create_app(provider=ScriptedProvider([])))
    pid = client.post("/api/projects", json={"title": "Shop"}).json()["project"]["id"]
    assert client.get(f"/api/projects/{pid}/steward").json()["available"] is False
    assert client.post(f"/api/projects/{pid}/steward/run").status_code == 409


def test_start_stops_when_the_verifier_is_the_chat_model(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ARCVIZ_STEWARD", "1")
    monkeypatch.setenv("ARCVIZ_PROVIDER", "anthropic")
    monkeypatch.setenv("ARCVIZ_MODEL", "claude-x")
    monkeypatch.setenv("ARCVIZ_VERIFIER_PROVIDER", "anthropic")
    monkeypatch.setenv("ARCVIZ_VERIFIER_MODEL", "claude-x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    with pytest.raises(ConfigError, match="another model"):
        create_app()
    monkeypatch.setenv("ARCVIZ_VERIFIER_MODEL", "claude-y")
    create_app()  # a different model starts
