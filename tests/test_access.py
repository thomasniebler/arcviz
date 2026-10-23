"""The access policy in the service layer (issues #161, #178; spec: docs/architecture/specs/personal-workspaces.md,
ADR-0084, ADR-0101): every service method checks the caller, the agents work on bound services, nothing is written
before a check fails."""

from __future__ import annotations

import inspect

import pytest

from arcviz.agent import DiagramAgent
from arcviz.llm.base import ModelReply
from arcviz.model import fingerprint
from arcviz.project_agent import ProjectAgent
from arcviz.project_model import Element
from arcviz.projects import CarriedModel, Project
from arcviz.services import (
    DiagramNotFound,
    DiagramService,
    Forbidden,
    ModelService,
    ProjectExchangeService,
    ProjectService,
    ServiceRoot,
    WorkspaceNotFound,
)
from arcviz.services.access import ProjectNotFound
from arcviz.templates import demo_diagram
from arcviz.workspaces import Identity, personal_project_id, personal_workspace_id
from tests.conftest import ScriptedProvider, call, new_loop

PEOPLE = {name: Identity(name, name.capitalize(), f"{name}@example.org") for name in ("ada", "ed", "vic", "out")}


@pytest.fixture
def root(store):
    """ada owns project "shop" (in her personal workspace) and shared it with ed (editor) and vic (viewer); out is an
    outsider. Project "shop" has the diagram "orders" with an element "svc"."""
    root = ServiceRoot(store)
    for identity in PEOPLE.values():
        root.users.seen(identity)
    ada = root.for_actor(PEOPLE["ada"])
    ada.projects.create("Shop", "shop")
    ada.projects.share("shop", "ed", "editor")
    ada.projects.share("shop", "vic", "viewer")
    ada.projects.apply("shop", "add_section", {"title": "Backend", "id": "backend"})
    ada.diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "shop")
    ada.models.add_element("shop", "Service", element_id="svc")
    return root


def as_(root: ServiceRoot, name: str):
    return root.for_actor(PEOPLE[name])


# ---- every public method, per role ------------------------------------------------------------------------

# (method, arguments, need) for every public method of DiagramService; "-" = no check (facts about the store)
DIAGRAM_CALLS = {
    "list_diagrams": ((), "list"),
    "exists": (("orders",), "exists"),
    "get": (("orders",), "viewer"),
    "read": (("orders",), "viewer"),
    "state": (("orders",), "viewer"),
    "versions": (("orders",), "viewer"),
    "version": (("orders", 1), "viewer"),
    "chat": (("orders",), "viewer"),
    "chat_generation": (("orders",), "generation"),
    "access": (("orders",), "viewer"),
    "require": (("orders", "editor"), "editor"),
    "create": (("New", "new", demo_diagram("new", "New"), "shop"), "editor-project"),
    "import_new": ((demo_diagram("imp", "Imp"), "Imp", None, "shop"), "editor-project"),
    "import_carrying": ((demo_diagram("imp", "Imp"), None, "Imp", None, "shop"), "editor-project"),
    "model_for_export": (("orders", demo_diagram("orders", "Orders")), "viewer"),
    "move": (("orders", "shop", "backend"), "editor"),
    "delete": (("orders",), "owner"),
    "update": (("orders", lambda d: None), "editor"),
    "apply_operation": (("orders", "rename_diagram", {"title": "X"}, "x"), "editor"),
    "apply_if_changed": (("orders", [("rename_diagram", {"title": "X"})], "x"), "editor"),
    "snapshot": (("orders", "Stand"), "editor"),
    "restore": (("orders", 1), "editor"),
    "undo": (("orders",), "editor"),
    "redo": (("orders",), "editor"),
    "append_chat": (("orders", [{"role": "user", "content": "x"}]), "append"),
    "compact_chat": (("orders", 1, {"role": "summary", "content": "s"}, "g"), "editor"),
    "clear_chat": (("orders",), "editor"),
    "storage_name": (None, "-"),
    "git_status": ((), "-"),
    "drain_mirror": ((), "-"),
    "start_mirror": ((), "-"),
    "ping": ((), "-"),
}


def _public(cls) -> set[str]:
    return {name for name, _ in inspect.getmembers(cls) if not name.startswith("_")} - {"guard"}


def test_every_diagram_service_method_is_classified():
    assert _public(DiagramService) == set(DIAGRAM_CALLS) | {"commit_turn", "write_through"}  # the agent's: below


@pytest.mark.parametrize(
    "method", sorted(set(DIAGRAM_CALLS) - {"storage_name", "git_status", "drain_mirror", "start_mirror", "ping"})
)
@pytest.mark.parametrize("who", ["out", "vic", "ed", "ada"])
def test_diagram_service_by_role(root, store, method, who):
    args, need = DIAGRAM_CALLS[method]
    service = as_(root, who).diagrams
    rank = {"out": 0, "vic": 1, "ed": 2, "ada": 3}[who]
    versions_before = len(store.list_versions("orders"))

    def run():
        return getattr(service, method)(*args)

    if need == "list":
        assert [d.id for d in run()] == ([] if who == "out" else ["orders"])
        return
    if need == "exists":
        assert run() is (who != "out")
        return
    if need == "generation":
        assert (run() == "") is (who == "out")
        return
    if need == "append":
        if who == "out":
            assert run() is False
        elif rank < 2:
            with pytest.raises(Forbidden):
                run()
        else:
            assert run() is True
        return
    required = {"viewer": 1, "editor": 2, "editor-project": 2, "owner": 3}[need]
    if who == "out":
        expected_error = ProjectNotFound if need == "editor-project" else DiagramNotFound
        with pytest.raises(expected_error):
            run()
    elif rank < required:
        with pytest.raises(Forbidden):
            run()
        assert len(store.list_versions("orders")) == versions_before  # nothing written
        assert store.exists("orders")
    else:
        run()


PROJECT_CALLS = {
    "get": (("shop",), "viewer"),
    "fresh": (("shop",), "viewer"),
    "require": (("shop", "editor"), "editor"),
    "view": (("shop",), "viewer"),
    "load_members": (("shop",), "viewer"),
    "chat": (("shop",), "viewer"),
    "access": (("shop",), "viewer"),
    "watch": (("shop",), "viewer"),
    "apply": (("shop", "add_section", {"title": "Neu"}), "editor"),
    "apply_all": (("shop", [("add_section", {"title": "Neu"})]), "editor"),
    "strip_requirement_refs": (("shop", {"req1"}), "editor"),
    "compact_chat": (("shop", 1, {"role": "summary", "content": "s"}, "g"), "editor"),
    "clear_chat": (("shop",), "editor"),
    "check_target": (("shop", "backend"), "editor"),
    "holds": (("shop", "orders"), "editor"),
    "place": (("orders", "shop", "backend"), "editor"),
    "uncarry": (("shop", CarriedModel(copied_elements=[("x", "x")])), "editor"),
    "carry": (
        ("shop", Project(id="src", title="Quelle", elements=[Element(id="x", name="X")]), {"x"}, set()),
        "editor",
    ),
    "share": (("shop", "out", "viewer"), "owner"),
    "revoke": (("shop", "vic"), "owner"),
    "delete": (("shop",), "owner"),
}
PROJECT_SPECIAL = {
    "list_projects",  # filtered: below
    "attach_requirements",  # wiring of the service root, no data
    "notify",  # a live hint for the open views, no data
    "visible_diagrams",  # filtered: below
    "locate",  # None when invisible: below
    "chat_generation",  # "" when invisible: below
    "append_chat",  # False when invisible: below
    "create",  # into the caller's own workspace: below
    "create_with_sections",  # likewise
    "unplace",  # after a diagram deletion, owner: test_diagram_service_by_role (delete)
    "reconcile",  # a repair, system context only (refused with a caller): test_reading_the_project_list_repairs_nothing
    "ensure_default",  # a repair, system context by design
    "ensure_own_default",  # the caller's own default project: tests/test_workspaces.py
    "adopt",  # an admin's repair (arcviz-migrate), refused with a caller: below
    "intend",  # the placement record of DiagramService.create, which checked the target first
    "settle",  # likewise
    "withdraw",  # the membership of a create given up, after DiagramService.create checked the target
    "vacate",  # before DiagramService.create creates an id it claimed: leftovers of a deleted diagram
    "memberships",  # read by DiagramService.delete after its owner check
    "holder",  # None when invisible, like locate
}


def test_every_project_service_method_is_classified():
    assert _public(ProjectService) == set(PROJECT_CALLS) | PROJECT_SPECIAL


@pytest.mark.parametrize("method", sorted(PROJECT_CALLS))
@pytest.mark.parametrize("who", ["out", "vic", "ed", "ada"])
def test_project_service_by_role(root, store, method, who):
    args, need = PROJECT_CALLS[method]
    service = as_(root, who).projects
    rank = {"out": 0, "vic": 1, "ed": 2, "ada": 3}[who]
    required = {"viewer": 1, "editor": 2, "owner": 3}[need]
    before = store.read_project("shop")[1]
    if who == "out":
        with pytest.raises(ProjectNotFound):
            getattr(service, method)(*args)
    elif rank < required:
        with pytest.raises(Forbidden):
            getattr(service, method)(*args)
        assert store.read_project("shop")[1] == before  # nothing written
    elif method == "delete":
        with pytest.raises(Exception, match="still contains"):  # allowed, but the project is not empty
            service.delete("shop")
    else:
        getattr(service, method)(*args)


def test_filtered_project_queries(root):
    assert [p.id for p in as_(root, "out").projects.list_projects()] == []
    assert as_(root, "out").projects.visible_diagrams() == set()
    assert as_(root, "out").projects.locate("orders") is None
    assert as_(root, "out").projects.chat_generation("shop") == ""
    assert as_(root, "out").projects.append_chat("shop", [{"role": "user", "content": "x"}]) is False
    with pytest.raises(Forbidden):
        as_(root, "vic").projects.append_chat("shop", [{"role": "user", "content": "x"}])
    assert as_(root, "vic").projects.locate("orders") == ("shop", None)
    assert as_(root, "vic").projects.visible_diagrams() == {"orders"}
    with pytest.raises(WorkspaceNotFound):
        as_(root, "out").projects.create("Mine", workspace=personal_workspace_id("ada"))  # never into another's
    created = as_(root, "vic").projects.create("Mine")  # anyone creates projects, in their own workspace
    assert created.workspace == personal_workspace_id("vic") and created.roles == {}


# ---- the shared model and bundles ---------------------------------------------------------------------


def test_model_writes_need_editor_and_write_nothing_before(root, store):
    vic = as_(root, "vic").models
    assert [e.id for e in vic.model("shop").elements] == ["svc"]  # reading is fine
    node = next(n.id for n in store.load("orders").nodes.values() if n.kind == "service")
    for write in (
        lambda: vic.add_element("shop", "X"),
        lambda: vic.update_element("shop", "svc", name="Y"),
        lambda: vic.remove_element("shop", "svc"),
        lambda: vic.sync("shop"),
        lambda: vic.link("orders", node, "svc"),
        lambda: vic.promote("orders", node),
        lambda: vic.link_section("shop", "backend", "svc"),
        lambda: vic.add_relation("shop", "communicates", "svc", "svc"),
        lambda: vic.update_relation("shop", "r1", description="x"),
        lambda: vic.remove_relation("shop", "r1"),
    ):
        with pytest.raises(Forbidden):
            write()
    assert vic.model("shop").relations == []
    assert [e.name for e in store.read_project("shop")[0].elements] == ["Service"]
    out = as_(root, "out").models
    with pytest.raises(ProjectNotFound):
        out.model("shop")
    with pytest.raises((ProjectNotFound, DiagramNotFound)):
        out.link("orders", node, "svc")
    with pytest.raises(ProjectNotFound):
        out.relations("shop")
    as_(root, "ed").models.link("orders", node, "svc")


def test_bundles_follow_the_policy(root):
    bundle = as_(root, "vic").bundles.export("shop")  # viewers export
    with pytest.raises(ProjectNotFound):
        as_(root, "out").bundles.export("shop")
    imported = as_(root, "vic").bundles.import_bundle(bundle, "Kopie")
    assert imported.roles == {} and imported.workspace == personal_workspace_id("vic")  # a copy of her own
    assert isinstance(as_(root, "ada").bundles, ProjectExchangeService)


def test_a_guard_sees_role_changes_from_the_next_request_on(root):
    vic = as_(root, "vic")
    assert vic.projects.view("shop").role == "viewer"
    as_(root, "ada").projects.share("shop", "vic", "editor")
    assert as_(root, "vic").projects.view("shop").role == "editor"  # a new request
    as_(root, "ada").projects.revoke("shop", "vic")
    with pytest.raises(ProjectNotFound):
        as_(root, "vic").projects.get("shop")


def test_a_guarded_diagram_service_needs_the_same_guard(store):
    root = ServiceRoot(store)
    services = root.for_actor(PEOPLE["ada"])
    with pytest.raises(ValueError, match="same guard"):
        DiagramService(store, ProjectService(store, store), services.guard)
    assert isinstance(services.models, ModelService)


# ---- the agents (ScriptedProvider) --------------------------------------------------------------------


def _diagram_agent(services, replies) -> DiagramAgent:
    base = DiagramAgent(ScriptedProvider(replies), services.diagrams, models=services.models, background=False)
    return base


def test_a_viewer_never_starts_a_diagram_turn(root, store):
    provider = ScriptedProvider([ModelReply(text="Nein", tool_calls=[], stop_reason="end_turn")])
    base = DiagramAgent(provider, as_(root, "ada").diagrams, background=False)
    vic = as_(root, "vic")
    for propose in (False, True):
        with pytest.raises(Forbidden):
            next(base.bound(vic.diagrams, vic.models).chat("orders", "Lösche alles", propose=propose))
    out = as_(root, "out")
    with pytest.raises(DiagramNotFound):
        next(base.bound(out.diagrams, out.models).chat("orders", "Hallo"))
    assert provider.requests == [] and store.load_chat("orders") == []


def test_an_editor_turn_runs_on_the_bound_services(root, store):
    replies = [
        ModelReply(text="", tool_calls=[call("rename_diagram", "c1", title="Bestellungen")], stop_reason="tool_use"),
        ModelReply(text="Umbenannt.", tool_calls=[], stop_reason="end_turn"),
    ]
    base = DiagramAgent(ScriptedProvider(replies), as_(root, "ada").diagrams, background=False)
    ed = as_(root, "ed")
    agent = base.bound(ed.diagrams, ed.models)
    assert agent.diagrams is ed.diagrams and agent.transcripts is ed.diagrams
    assert agent._compaction is base._compaction  # one background compaction for every copy
    events = list(agent.chat("orders", "Benenne um"))
    assert events[-1]["type"] == "final" and store.load("orders").title == "Bestellungen"
    assert [e["role"] for e in store.load_chat("orders")] == ["user", "assistant"]


def test_a_viewer_never_starts_a_project_turn(root, store):
    provider = ScriptedProvider([ModelReply(text="Nein", tool_calls=[], stop_reason="end_turn")])
    ada = as_(root, "ada")
    base = ProjectAgent(provider, ada.diagrams, ada.projects, ada.models, background=False)
    vic = as_(root, "vic")
    with pytest.raises(Forbidden):
        next(base.bound(vic.diagrams, vic.models, vic.projects).chat("shop", "Lege ein Diagramm an"))
    out = as_(root, "out")
    with pytest.raises(ProjectNotFound):
        next(base.bound(out.diagrams, out.models, out.projects).chat("shop", "Hallo"))
    assert provider.requests == [] and store.load_project_chat("shop") == []


def test_an_editor_project_turn_creates_diagrams_in_the_project(root, store):
    replies = [
        ModelReply(
            text="",
            tool_calls=[call("create_diagram", "c1", title="Ablauf", type="activity")],
            stop_reason="tool_use",
        ),
        ModelReply(text="Angelegt.", tool_calls=[], stop_reason="end_turn"),
    ]
    ada = as_(root, "ada")
    base = ProjectAgent(ScriptedProvider(replies), ada.diagrams, ada.projects, ada.models, background=False)
    ed = as_(root, "ed")
    events = list(base.bound(ed.diagrams, ed.models, ed.projects).chat("shop", "Lege einen Ablauf an"))
    assert events[-1]["type"] == "final" and not events[-1].get("error")
    assert {m.diagram for m in store.read_project("shop")[0].diagrams} == {"orders", "ablauf"}
    assert fingerprint(store.load("ablauf"))


# ---- findings of the review (moves, rollbacks, dangling ids) ------------------------------------------


def test_only_the_project_owner_takes_a_diagram_out_of_its_project(root, store):
    ed = as_(root, "ed")
    ed.diagrams.move("orders", "shop", "backend")  # within the project: editor is enough
    ed.projects.create("Loot", "loot")
    with pytest.raises(Forbidden):  # otherwise an editor could move it away and delete it there
        ed.diagrams.move("orders", "loot")
    assert as_(root, "ada").projects.locate("orders") == ("shop", "backend")
    assert store.load("orders").title == "Orders"  # nothing unlinked or written
    as_(root, "ada").projects.create("Archiv", "archiv")
    as_(root, "ada").diagrams.move("orders", "archiv")
    assert as_(root, "ada").projects.locate("orders") == ("archiv", None)


def test_a_failed_placement_of_a_new_diagram_leaves_nothing_behind(root, store, monkeypatch):
    from arcviz.projects import ProjectError

    def fail(*args, **kwargs):
        raise ProjectError("section vanished")

    monkeypatch.setattr(ProjectService, "place", fail)
    with pytest.raises(ProjectError):
        as_(root, "ed").diagrams.create("Plans", "plans", demo_diagram("plans", "Plans"), "shop")
    assert not store.exists("plans")


# ---- findings of the second review (the default project, orphans, taken ids, a turn losing its role) ----------


class Hooked(ScriptedProvider):
    """Runs `hooks[i]` at the start of the i-th model call, i.e. while the turn is in flight."""

    def __init__(self, replies, hooks):
        super().__init__(replies)
        self.hooks, self.calls = hooks, 0

    def _complete(self, system, messages, tools):
        hook = self.hooks.get(self.calls)
        self.calls += 1
        if hook:
            hook()
        return super()._complete(system, messages, tools)


def _add(node_id: str, call_id: str):
    return call("add_node", call_id, name=node_id, id=node_id, kind="service", parent="prod-deployment")


def _tool(*calls):
    return ModelReply(text="", tool_calls=list(calls), stop_reason="tool_use")


def _done(text: str = "Fertig."):
    return ModelReply(text=text, tool_calls=[], stop_reason="end_turn")


def _results(events):
    return [e for e in events if e["type"] == "tool_result"]


def test_reading_the_project_list_repairs_nothing(root, store):
    store.create("Waise", "waise", demo_diagram("waise", "Waise"))  # an orphan, e.g. after a crash
    for who in ("out", "vic", "ada"):
        as_(root, who).projects.list_projects()
    assert [p.id for p, _ in store.list_projects()] == ["shop"]  # no default project created, nothing adopted


def _crash_before_placing(monkeypatch):
    """The process dies between creating a diagram and placing it (nothing after the create runs)."""
    monkeypatch.setattr(DiagramService, "_place", lambda self, diagram_id, target, intent, incarnation=None: None)


def test_an_orphan_goes_into_the_project_it_was_created_for(root, store, monkeypatch):
    _crash_before_placing(monkeypatch)
    as_(root, "ed").diagrams.create("Plan", "plan", demo_diagram("plan", "Plan"), "shop")
    monkeypatch.undo()
    assert as_(root, "ed").projects.locate("plan") is None  # invisible until repaired
    report = ProjectService(store, store).reconcile()  # start-up or arcviz-migrate --projects
    assert report.adopted == ["plan"] and report.quarantined == []
    assert as_(root, "vic").projects.locate("plan") == ("shop", None)
    assert [p.id for p, _ in store.list_projects()] == ["shop"]  # not the default project
    assert store.list_documents("placement") == []


def test_an_orphan_whose_project_is_gone_is_quarantined(root, store, monkeypatch):
    ada = as_(root, "ada")
    ada.projects.create("Leer", "leer")
    _crash_before_placing(monkeypatch)
    ada.diagrams.create("Plan", "plan", demo_diagram("plan", "Plan"), "leer")
    monkeypatch.undo()
    ada.projects.delete("leer")
    report = ProjectService(store, store).reconcile()
    assert report.adopted == [] and report.quarantined == ["plan"]
    assert all(p.member("plan") is None for p, _ in store.list_projects())  # in no project: nobody sees it
    assert store.exists("plan")
    again = ProjectService(store, store).reconcile()
    assert again.quarantined == ["plan"] and not again.changed  # stays put until an admin decides


def test_a_diagram_without_a_recorded_project_is_legacy_data(store):
    store.create("Alt", "alt", demo_diagram("alt", "Alt"))  # written before projects existed
    report = ProjectService(store, store).reconcile()
    assert report.adopted == ["alt"] and store.read_project("default")[0].member("alt") is not None


def test_a_successful_create_leaves_no_placement_record(root, store):
    as_(root, "ed").diagrams.create("Plan", "plan", demo_diagram("plan", "Plan"), "shop")
    as_(root, "ed").diagrams.import_new(demo_diagram("imp", "Imp"), "Imp", None, "shop")
    assert store.list_documents("placement") == []


def test_a_taken_id_gets_a_free_variant_whether_visible_or_not(root, store):
    out = as_(root, "out")
    out.projects.create("Eigen", "eigen")
    # "orders" exists in a workspace "out" cannot see: answered exactly like an id it can see
    created = out.diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "eigen")
    assert created.id == "orders-2"
    assert as_(root, "ed").diagrams.create("Orders", "orders", demo_diagram("orders", "O"), "shop").id == "orders-3"
    assert out.diagrams.import_new(demo_diagram("x", "X"), "X", "orders", "eigen") == "orders-4"
    assert out.projects.create("Shop", "shop").id == "shop-2"
    assert store.load("orders").title == "Orders" and as_(root, "out").projects.locate("orders-2") == ("eigen", None)


def test_a_diagram_deleted_during_a_turn_is_a_tool_error(root, store):
    ada, ed = as_(root, "ada"), as_(root, "ed")
    provider = Hooked(
        [_tool(_add("one", "c1")), _tool(_add("two", "c2")), _done()], {1: lambda: ada.diagrams.delete("orders")}
    )
    base = DiagramAgent(provider, ada.diagrams, background=False)
    events = list(base.bound(ed.diagrams, ed.models).chat("orders", "Baue"))
    first, second = _results(events)
    assert not first["is_error"] and second["is_error"] and "deleted" in second["content"]
    assert events[-1]["type"] == "final" and "deleted" in events[-1]["error"]


def test_a_diagram_deleted_during_a_project_turn_is_a_tool_error(root, store):
    ada, ed = as_(root, "ada"), as_(root, "ed")
    provider = Hooked(
        [_tool(_add("one", "c1")), _tool(_add("two", "c2")), _done()], {1: lambda: ada.diagrams.delete("orders")}
    )
    base = ProjectAgent(provider, ada.diagrams, ada.projects, ada.models, background=False)
    events = list(base.bound(ed.diagrams, ed.models, ed.projects).chat("shop", "Baue", active="orders"))
    first, second = _results(events)
    assert not first["is_error"] and second["is_error"]
    assert events[-1]["type"] == "final"
    assert len(store.load_project_chat("shop")) == 2


def test_a_turn_whose_caller_is_demoted_keeps_and_versions_what_it_wrote(root, store):
    ada, ed = as_(root, "ada"), as_(root, "ed")
    versions = len(store.list_versions("orders"))
    provider = Hooked(
        [_tool(_add("one", "c1")), _tool(_add("two", "c2")), _done()],
        {1: lambda: ada.projects.share("shop", "ed", "viewer")},
    )
    base = DiagramAgent(provider, ada.diagrams, background=False)
    events = list(base.bound(ed.diagrams, ed.models).chat("orders", "Baue"))
    first, second = _results(events)
    assert not first["is_error"] and second["is_error"] and "role" in second["content"]
    final = events[-1]
    assert final["type"] == "final" and "role" in final["error"]
    assert "one" in store.load("orders").nodes and "two" not in store.load("orders").nodes
    infos = store.list_versions("orders")
    assert len(infos) == versions + 1 and infos[-1].author == "ed" and final["version"]["version"] == infos[-1].version
    assert store.load_chat("orders") == []  # a viewer writes no transcript


def test_a_project_turn_whose_caller_is_demoted_keeps_and_versions_what_it_wrote(root, store):
    ada, ed = as_(root, "ada"), as_(root, "ed")
    versions = len(store.list_versions("orders"))
    provider = Hooked(
        [_tool(_add("one", "c1")), _tool(_add("two", "c2")), _done()],
        {1: lambda: ada.projects.share("shop", "ed", "viewer")},
    )
    base = ProjectAgent(provider, ada.diagrams, ada.projects, ada.models, background=False)
    events = list(base.bound(ed.diagrams, ed.models, ed.projects).chat("shop", "Baue", active="orders"))
    first, second = _results(events)
    assert not first["is_error"] and second["is_error"] and "role" in second["content"]
    final = events[-1]
    assert final["type"] == "final" and "role" in final["error"]
    infos = store.list_versions("orders")
    assert len(infos) == versions + 1 and infos[-1].author == "ed"
    assert store.load_project_chat("shop") == []


def test_commit_turn_versions_only_what_the_same_turn_wrote(root, store):
    vic = as_(root, "vic").diagrams
    diagram, revision = vic.read("orders")
    diagram.title = "Gekapert"
    assert vic.commit_turn(diagram, revision, "x", "") is None  # a viewer cannot version arbitrary content
    assert store.load("orders").title == "Orders"


def test_no_caller_can_create_a_project_with_the_default_project_id(root, store):
    # otherwise diagrams created without a project would land in somebody's own project
    created = as_(root, "ada").projects.create("Default", "default")
    assert created.id == "default-2"
    assert as_(root, "ada").projects.create("Default").id == "default-3"


def test_orphans_are_placed_by_an_admin_only(root, store, monkeypatch):
    from arcviz.projects import ProjectError

    store.create("Waise", "waise", demo_diagram("waise", "Waise"))
    with pytest.raises(ProjectError):
        as_(root, "ada").projects.adopt("waise", "shop")
    with pytest.raises(ProjectError):
        as_(root, "ada").projects.reconcile()
    ProjectService(store, store).adopt("waise", "shop")
    assert as_(root, "vic").projects.locate("waise") == ("shop", None)
    with pytest.raises(ProjectError, match="already in a project"):
        ProjectService(store, store).adopt("waise", "shop")


# ---- findings of the third review (membership mid-turn, startup, races) -----------------------------------


def test_a_recipient_revoked_during_a_turn_changes_nothing_more(root, store):
    ada, ed = as_(root, "ada"), as_(root, "ed")
    provider = Hooked(
        [_tool(_add("one", "c1")), _tool(_add("two", "c2")), _done()],
        {1: lambda: ada.projects.revoke("shop", "ed")},
    )
    base = DiagramAgent(provider, ada.diagrams, background=False)
    events = list(base.bound(ed.diagrams, ed.models).chat("orders", "Baue"))
    first, second = _results(events)
    assert not first["is_error"] and second["is_error"]
    assert events[-1]["type"] == "final" and events[-1]["error"]
    assert "one" in store.load("orders").nodes and "two" not in store.load("orders").nodes
    assert store.load_chat("orders") == []  # no transcript for someone who cannot see the diagram any more


def test_a_failing_version_write_is_reported_not_swallowed(root, store, monkeypatch):
    from arcviz.ports import StoreError

    ada = as_(root, "ada")

    def broken(*args, **kwargs):
        raise StoreError("disk full")

    monkeypatch.setattr(store, "commit", broken)
    provider = ScriptedProvider([_tool(_add("one", "c1")), _done()])
    events = list(DiagramAgent(provider, ada.diagrams, background=False).chat("orders", "Baue"))
    final = events[-1]
    assert final["type"] == "final" and final["version"] is None
    assert "disk full" in final["error"]


def test_creating_without_a_project_goes_into_the_callers_own_default_project(store):
    root = ServiceRoot(store, admin=PEOPLE["ada"])  # ada is the admin: the reserved project `default` is hers
    for identity in PEOPLE.values():
        root.users.seen(identity)
    for who in ("out", "ed", "ada"):
        root.for_actor(PEOPLE[who]).diagrams.create(f"X {who}", f"x-{who}", demo_diagram(f"x-{who}", "X"))
        home = personal_project_id(who)
        assert root.for_actor(PEOPLE[who]).projects.locate(f"x-{who}") == (home, None)
        assert store.read_project(home)[0].workspace == personal_workspace_id(who)
    assert all(p.id != "default" for p, _ in store.list_projects())  # the reserved project is never a caller's target
    with pytest.raises(ProjectNotFound):
        root.for_actor(PEOPLE["out"]).projects.get(personal_project_id("ada"))


def test_a_failing_version_write_is_reported_in_a_project_turn(root, store, monkeypatch):
    from arcviz.ports import StoreError

    ada = as_(root, "ada")

    def broken(*args, **kwargs):
        raise StoreError("disk full")

    monkeypatch.setattr(store, "commit", broken)
    provider = ScriptedProvider([_tool(_add("one", "c1")), _done()])
    base = ProjectAgent(provider, ada.diagrams, ada.projects, ada.models, background=False)
    final = list(base.chat("shop", "Baue", active="orders"))[-1]
    assert final["type"] == "final" and "disk full" in final["error"]
    assert "one" in store.load("orders").nodes  # the change itself is saved


def test_every_failed_version_of_a_project_turn_is_reported(root, store, monkeypatch, caplog):
    from arcviz.ports import StoreError

    ada = as_(root, "ada")
    ada.diagrams.create("Zwei", "zwei", demo_diagram("zwei", "Zwei"), "shop")

    def broken(*args, **kwargs):
        raise StoreError("disk full")

    monkeypatch.setattr(store, "commit", broken)
    replies = [
        _tool(_add("one", "c1")),
        _tool(call("open_diagram", "c2", diagram="zwei")),
        _tool(_add("two", "c3")),
        _done(),
    ]
    base = ProjectAgent(ScriptedProvider(replies), ada.diagrams, ada.projects, ada.models, background=False)
    final = list(base.chat("shop", "Baue", active="orders"))[-1]
    assert "orders" in final["error"] and "zwei" in final["error"]
    entry = store.load_project_chat("shop")[-1]
    assert entry["error"] == final["error"]
    assert caplog.text.count("could not be written") >= 2


# ---- LiveService (spec: docs/architecture/specs/collaboration.md) ------------------------------------------

# (method, arguments, need): every public method of LiveService; "hub" is the hub itself (no data)
LIVE_CALLS = {
    "watch_diagram": (("orders",), "viewer"),
    "watch_project": (("shop",), "viewer-project"),
    "open_diagram": (("orders", None), "viewer"),  # + the event loop
    "open_project": (("shop", None), "viewer-project"),
    "recheck": (None, "recheck"),
    "select": (("orders", "no-such-session", None), "select"),
    "chatting": (None, "chatting"),
    "hub": (None, "-"),
}


@pytest.fixture
def live_root(store):

    from arcviz.live import LiveHub

    root = ServiceRoot(store, LiveHub())
    for identity in PEOPLE.values():
        root.users.seen(identity)
    ada = root.for_actor(PEOPLE["ada"])
    ada.projects.create("Shop", "shop")
    ada.projects.share("shop", "ed", "editor")
    ada.projects.share("shop", "vic", "viewer")
    ada.diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "shop")
    return root, new_loop()


def test_every_live_service_method_is_classified():
    from arcviz.services import LiveService

    assert _public(LiveService) == set(LIVE_CALLS)
    assert not hasattr(LiveService, "subscribe")  # streams only for watches the service built itself


@pytest.mark.parametrize("method", ["watch_diagram", "watch_project", "open_diagram", "open_project", "select"])
@pytest.mark.parametrize("who", ["out", "vic"])
def test_live_service_by_role(live_root, method, who):
    from arcviz.services import UnknownSession

    root, loop = live_root
    args, need = LIVE_CALLS[method]
    if method.startswith("open_"):
        args = (*args, loop)
    service = root.for_actor(PEOPLE[who]).live
    if who == "out":
        with pytest.raises(ProjectNotFound if need == "viewer-project" else DiagramNotFound):
            getattr(service, method)(*args)
    elif need == "select":
        with pytest.raises(UnknownSession):  # visible, but no open stream with that session
            service.select(*args)
    else:
        getattr(service, method)(*args)


def test_live_recheck_and_chatting_follow_the_callers_access(live_root):
    root, loop = live_root
    vic = root.for_actor(PEOPLE["vic"]).live
    watch = vic.watch_diagram("orders")
    assert root.for_actor(PEOPLE["out"]).live.recheck(watch) is None  # a watch is re-checked for its caller
    with root.for_actor(PEOPLE["out"]).live.chatting(diagram_id="orders", project_id="shop"):
        assert [c["diagram"] for c in root.hub._chatting.values()] == [None]


def test_a_revoked_recipients_stream_is_gone_at_the_next_recheck(live_root):
    root, loop = live_root
    vic = root.for_actor(PEOPLE["vic"]).live
    watch = vic.watch_diagram("orders")
    assert vic.recheck(watch) is not None
    root.for_actor(PEOPLE["ada"]).projects.revoke("shop", "vic")  # publishes a project event: shared reads are stale
    assert vic.recheck(watch) is None
    assert root.for_actor(PEOPLE["vic"]).live.recheck(root.for_actor(PEOPLE["ed"]).live.watch_project("shop")) is None


# ---- tenant isolation (#177, #178) -------------------------------------------------------------------------


def test_two_users_own_projects_never_meet(root, store):
    out = as_(root, "out")
    out.projects.create("Geheim", "geheim")
    out.diagrams.create("Plan", "plan", demo_diagram("plan", "Plan"), "geheim")
    for who in ("ada", "ed", "vic"):
        services = as_(root, who)
        assert "geheim" not in [p.id for p in services.projects.list_projects()]
        assert "plan" not in [d.id for d in services.diagrams.list_diagrams()]
        assert services.projects.locate("plan") is None and not services.diagrams.exists("plan")
        with pytest.raises(ProjectNotFound):
            services.projects.get("geheim")
        with pytest.raises(DiagramNotFound):
            services.diagrams.get("plan")
        with pytest.raises(ProjectNotFound):
            services.diagrams.create("Rein", "rein", demo_diagram("rein", "Rein"), "geheim")
        with pytest.raises(ProjectNotFound):
            services.projects.share("geheim", who, "editor")  # nobody shares themselves in
    assert [p.id for p in out.projects.list_projects()] == ["geheim"]  # and out sees nothing of ada's
    with pytest.raises(ProjectNotFound):
        out.projects.get("shop")
