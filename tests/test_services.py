"""DiagramService: the conflict handling moved out of the entry points (spec: application-services.md)."""

from __future__ import annotations

import pytest

from arcviz import operations
from arcviz.model import Diagram
from arcviz.ports import StoreError
from arcviz.services import DiagramService
from arcviz.templates import demo_diagram


@pytest.fixture
def service(store, demo):
    return DiagramService(store)


def test_import_new_takes_the_first_free_suffix(service):
    imported = demo_diagram("demo", "Demo")
    assert service.import_new(imported, "Copy") == "demo-2"
    assert service.import_new(imported, "Copy") == "demo-3"
    assert service.get("demo-3").title == "Copy"


def test_import_new_with_a_taken_id_moves_on(service):
    assert service.import_new(demo_diagram("x", "X"), "X", "chosen") == "chosen"
    assert service.import_new(demo_diagram("x", "X"), "X", "chosen") == "chosen-2"  # ADR-0089
    with pytest.raises(StoreError, match="Invalid"):
        service.import_new(demo_diagram("x", "X"), "X", "Bad Id")


def test_import_new_moves_on_when_a_concurrent_create_takes_the_id(service, store, monkeypatch):
    original = store.create

    def racing_create(title, diagram_id=None, diagram=None, author=None):
        if diagram_id == "demo-2" and not store.exists("demo-2"):
            original("Other", "demo-2", Diagram(id="demo-2", title="Other"))  # someone else wins the id
        return original(title, diagram_id, diagram, author)

    monkeypatch.setattr(store, "create", racing_create)
    assert service.import_new(demo_diagram("demo", "Demo"), "Mine") == "demo-3"
    assert service.get("demo-2").title == "Other"


def test_import_new_without_a_usable_id_uses_diagram(service):
    assert service.import_new(Diagram(id="--", title="T"), "T") == "diagram"


def test_append_chat_reports_a_deleted_diagram(service):
    assert service.append_chat("demo", [{"role": "user", "content": "hi"}]) is True
    service.delete("demo")
    assert service.append_chat("demo", [{"role": "user", "content": "late"}]) is False
    assert not service.exists("demo")  # nothing recreated


def _add(name, kind="service"):
    def change(diagram):
        try:
            return operations.apply(diagram, "add_node", {"name": name, "kind": kind})
        except operations.OperationError as exc:
            return exc

    return change


def _is_error(result):
    return isinstance(result, Exception)


def test_write_through_writes_and_returns_the_new_revision(service):
    diagram, revision = service.read("demo")
    write = service.write_through("demo", diagram, revision, _add("new"), _is_error)
    assert write.outcome == "written" and write.revision != revision
    assert "new" in service.get("demo").nodes


def test_write_through_reapplies_on_the_newer_state_after_a_conflict(service):
    diagram, revision = service.read("demo")
    service.apply_operation("demo", "add_node", {"name": "concurrent", "kind": "service"}, "other request")
    write = service.write_through("demo", diagram, revision, _add("mine"), _is_error)
    assert write.outcome == "written"
    assert {"concurrent", "mine"} <= set(service.get("demo").nodes)


def test_write_through_rejected_writes_nothing(service):
    diagram, revision = service.read("demo")
    write = service.write_through("demo", diagram, revision, _add("x", kind="no-such-kind"), _is_error)
    assert write.outcome == "rejected" and write.revision == revision
    assert isinstance(write.result, operations.OperationError)
    assert service.read("demo")[1] == revision


def test_write_through_reports_a_deleted_diagram(service):
    diagram, revision = service.read("demo")
    service.delete("demo")
    assert service.write_through("demo", diagram, revision, _add("x"), _is_error).outcome == "deleted"


def test_write_through_gives_up_when_the_diagram_keeps_changing(service):
    diagram, revision = service.read("demo")
    calls = []

    def change(target):
        calls.append(1)
        service.apply_operation("demo", "rename_diagram", {"title": f"T{len(calls)}"}, "interfering")
        return {}

    write = service.write_through("demo", diagram, revision, change, _is_error)
    assert write.outcome == "busy" and len(calls) == 5


def test_commit_turn_returns_none_when_latest_moved_on(service):
    diagram, revision = service.read("demo")
    operations.apply(diagram, "add_node", {"name": "turn", "kind": "service"})
    service.snapshot("demo", "concurrent snapshot")  # a new revision
    assert service.commit_turn(diagram, revision, label="turn", details="") is None
    fresh, fresh_revision = service.read("demo")
    version = service.commit_turn(fresh, fresh_revision, label="turn", details="- add_node")
    assert version is not None and version.source == "auto"


def test_state_bundles_versions_targets_and_chat(service):
    service.apply_operation("demo", "add_node", {"name": "n", "kind": "service"}, "add n")
    state = service.state("demo")
    assert [v.version for v in state.versions] == [1, 2]
    assert state.undo_target == 1 and state.redo_target is None and state.chat == []
