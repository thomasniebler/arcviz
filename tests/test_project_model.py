"""The shared project model: view elements linked to project elements (spec: shared-project-model.md).

Diagram side: `Node.ref` / `Lifeline.ref`, the operations `link_element`, `unlink_element`, `sync_element`,
`clear_element` and which view elements may represent which element kinds (ADR-0070, 0071, 0073).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz import agent, diff, exchange, operations, project_model, projects, proposal
from arcviz.llm import ToolCall
from arcviz.model import Diagram, KindCatalog, active_kinds, fingerprint, use_kinds
from arcviz.projects import Project, Section
from arcviz.server import create_app
from arcviz.templates import TEMPLATES, create_from_template


def deployment() -> Diagram:
    return create_from_template("aks-database", "platform", "Plattform")


def activity() -> Diagram:
    return create_from_template("order-process", "order", "Bestellprozess")


def sequence() -> Diagram:
    return create_from_template("checkout", "checkout", "Checkout")


def link(d: Diagram, view: str, element: str = "orders", name: str = "Order Service", kind: str = "component"):
    return operations.apply(d, "link_element", {"id": view, "element": element, "name": name, "element_kind": kind})


# ---- the reference field ----------------------------------------------------------------------------------


def test_unset_refs_are_omitted_so_old_documents_keep_their_fingerprints():
    for template in TEMPLATES:
        d = create_from_template(template, "x", "X")
        dumped = d.model_dump(mode="json")
        assert all("ref" not in n for n in dumped["nodes"].values())
        assert all("ref" not in lf for lf in (dumped.get("interaction") or {}).get("lifelines", []))
        assert '"ref"' not in d.model_dump_json()
        assert fingerprint(Diagram.model_validate(dumped)) == fingerprint(d)


def test_a_set_ref_is_stored_and_changes_the_fingerprint():
    d = deployment()
    before = fingerprint(d)
    node = next(iter(d.nodes))
    link(d, node, kind=project_model.linkable_kinds("deployment", d.nodes[node].kind)[0])
    assert d.model_dump()["nodes"][node]["ref"] == "orders"
    assert fingerprint(d) != before
    assert Diagram.model_validate(d.model_dump(mode="json")).nodes[node].ref == "orders"


# ---- which view elements may represent which element kinds (ADR-0073) -----------------------------------------


def test_linkable_kinds_follow_the_table():
    assert project_model.linkable_kinds("sequence", "lifeline") == project_model.linkable_kinds("sequence", "lifeline")
    assert set(project_model.linkable_kinds("sequence", "lifeline")) == set(project_model.ELEMENT_KINDS)
    assert "data" not in project_model.linkable_kinds("activity", "activity_partition")
    assert project_model.linkable_kinds("activity", "data_store_node") == ("data",)
    assert project_model.linkable_kinds("activity", "opaque_action") == ()
    assert project_model.linkable_kinds("activity", "decision_node") == ()
    assert project_model.linkable_kinds("deployment", "vnet") == ("node", "system", "subsystem")
    assert project_model.linkable_kinds("deployment", "database")[0] == "data"
    assert project_model.linkable_kinds("deployment", "service")[0] == "component"
    assert "actor" in project_model.linkable_kinds("deployment", "external")
    assert project_model.linkable_kinds("deployment", "no-such-kind") == ()
    for diagram_type in ("deployment", "activity", "sequence"):
        for kinds in project_model.linkable_table(diagram_type).values():
            assert set(kinds) <= set(project_model.ELEMENT_KINDS)


def test_configured_kinds_are_linkable_with_permissive_defaults():
    builtin = active_kinds()
    use_kinds(
        KindCatalog(builtin.container_kinds | {"landing-zone"}, builtin.leaf_kinds | {"bucket"}, builtin.edge_kinds)
    )
    assert project_model.linkable_kinds("deployment", "landing-zone") == ("node", "system", "subsystem")
    assert set(project_model.linkable_kinds("deployment", "bucket")) == set(project_model.ELEMENT_KINDS)


def test_default_kind_of_promote():
    d = sequence()
    actor = next(lf for lf in d.interaction.lifelines if lf.actor)
    other = next(lf for lf in d.interaction.lifelines if not lf.actor)
    assert project_model.default_kind(d, project_model.find_view(d, actor.id)) == "actor"
    assert project_model.default_kind(d, project_model.find_view(d, other.id)) == "component"
    a = activity()
    action = next(n for n in a.nodes.values() if n.kind == "opaque_action")
    assert project_model.default_kind(a, project_model.find_view(a, action.id)) is None


# ---- link_element ---------------------------------------------------------------------------------------------


def test_link_a_deployment_node_takes_the_element_name():
    d = deployment()
    node = next(n for n in d.nodes.values() if n.kind == "service")
    old_name = node.name
    result = link(d, old_name.upper(), element="orders", name="Order Service")  # resolved like find_node
    assert result["touched"] == [node.id]
    assert result["result"]["renamed_from"] == old_name
    assert d.nodes[node.id].ref == "orders" and d.nodes[node.id].name == "Order Service"
    assert "(= element orders)" in d.summary()


def test_link_an_activity_partition_and_a_lifeline():
    a = activity()
    partition = next(n for n in a.nodes.values() if n.kind == "activity_partition")
    link(a, partition.id, kind="actor", name="Kunde")
    assert a.nodes[partition.id].ref == "orders"
    s = sequence()
    lifeline = s.interaction.lifelines[1]
    link(s, lifeline.name, element="shop", name="Shop")
    assert s.interaction.lifelines[1].ref == "shop"
    assert "(= element shop)" in s.summary()
    assert project_model.references(s, "shop")[0].id == lifeline.id


def test_link_refuses_behaviour_and_incompatible_kinds_without_changing_anything():
    a = activity()
    action = next(n for n in a.nodes.values() if n.kind == "opaque_action")
    before = a.model_dump()
    with pytest.raises(operations.OperationError, match="cannot represent a project element"):
        link(a, action.id)
    store = next(iter(a.nodes))
    with pytest.raises(operations.OperationError):
        link(a, store, kind="robot")
    d = deployment()
    database = next(n for n in d.nodes.values() if n.kind == "database")
    with pytest.raises(operations.OperationError, match="may represent data, component"):
        link(d, database.id, kind="actor")
    with pytest.raises(operations.OperationError, match="Unknown node"):
        link(d, "ghost")
    with pytest.raises(operations.OperationError, match="Unknown lifeline"):
        link(sequence(), "ghost")
    assert a.model_dump() == before


def test_unlink_keeps_the_name():
    d = deployment()
    node = next(n for n in d.nodes.values() if n.kind == "service")
    link(d, node.id)
    operations.apply(d, "unlink_element", {"id": node.id})
    assert d.nodes[node.id].ref is None and d.nodes[node.id].name == "Order Service"
    with pytest.raises(operations.OperationError, match="does not represent"):
        operations.apply(d, "unlink_element", {"id": node.id})


def test_a_linked_view_element_cannot_be_renamed_in_the_view():
    d = deployment()
    node = next(n for n in d.nodes.values() if n.kind == "service")
    link(d, node.id)
    with pytest.raises(operations.OperationError, match="update_element"):
        operations.apply(d, "update_node", {"id": node.id, "name": "Other"})
    operations.apply(d, "update_node", {"id": node.id, "name": "Order Service", "properties": {"runtime": "go"}})
    operations.apply(d, "update_node", {"id": node.id, "kind": "function"})  # still linkable
    s = sequence()
    link(s, s.interaction.lifelines[0].id, element="shop", name="Kunde")
    with pytest.raises(operations.OperationError, match="update_element"):
        operations.apply(s, "update_lifeline", {"id": s.interaction.lifelines[0].id, "name": "Kundin"})
    operations.apply(s, "update_lifeline", {"id": s.interaction.lifelines[0].id, "type": "Person"})


def test_a_linked_partition_cannot_become_an_action():
    a = activity()
    operations.apply(a, "add_node", {"name": "Versand", "kind": "activity_partition", "id": "versand"})
    link(a, "versand", kind="actor", name="Versand")
    with pytest.raises(operations.OperationError, match="unlink"):
        operations.apply(a, "update_node", {"id": "versand", "kind": "opaque_action"})


# ---- sync_element / clear_element --------------------------------------------------------------------------


def test_sync_renames_every_view_of_the_element_and_is_a_no_op_when_consistent():
    d = deployment()
    services = [n.id for n in d.nodes.values() if n.kind in ("service", "component")][:2]
    for node in services:
        link(d, node)
    result = operations.apply(d, "sync_element", {"element": "orders", "name": "Bestellungen"})
    assert sorted(result["touched"]) == sorted(services)
    assert {d.nodes[n].name for n in services} == {"Bestellungen"}
    stamp = d.updated_at
    again = operations.apply(d, "sync_element", {"element": "orders", "name": "Bestellungen"})
    assert again["touched"] == [] and d.updated_at == stamp


def test_clear_unlinks_every_view_of_the_element():
    s = sequence()
    for lifeline in s.interaction.lifelines[:2]:
        link(s, lifeline.id, element="shop", name=lifeline.name)
    result = operations.apply(s, "clear_element", {"element": "shop"})
    assert len(result["touched"]) == 2
    assert all(lf.ref is None for lf in s.interaction.lifelines)


# ---- internal operations are the model service's (ADR-0071) ---------------------------------------------------


def test_batches_refuse_internal_operations_but_accept_unlink():
    d = deployment()
    node = next(n for n in d.nodes.values() if n.kind == "service")
    steps = [{"op": "link_element", "args": {"id": node.id, "element": "e", "name": "E", "element_kind": "component"}}]
    with pytest.raises(operations.OperationError, match="cannot be part of a batch"):
        operations.apply(d, "apply_batch", {"operations": steps})
    link(d, node.id)
    operations.apply(d, "apply_batch", {"operations": [{"op": "unlink_element", "args": {"id": node.id}}]})
    assert d.nodes[node.id].ref is None


def test_the_model_cannot_call_internal_operations_as_tools():
    d = deployment()
    node = next(iter(d.nodes))
    call = ToolCall("c1", "link_element", {"id": node, "element": "e", "name": "E", "element_kind": "node"})
    result = agent.apply_tool_call(d, call)
    assert result.is_error and d.nodes[node].ref is None


def test_proposals_cannot_carry_internal_operations(tmp_path):
    from arcviz.services import DiagramService
    from arcviz.store import FileStore

    service = DiagramService(FileStore(tmp_path))
    service.create("Plattform", "platform", deployment())
    base = fingerprint(service.get("platform"))
    node = next(iter(service.get("platform").nodes))
    ops = [{"op": "sync_element", "arguments": {"element": "e", "name": "X"}}]
    with pytest.raises(proposal.ProposalConflict, match="cannot contain"):
        proposal.apply_proposal(service, "platform", ops, base, base)
    assert service.get("platform").nodes[node].ref is None


def test_rest_ops_refuse_internal_operations(tmp_path):
    from arcviz.store import FileStore

    client = TestClient(create_app(store=FileStore(tmp_path)))
    client.post("/api/diagrams", json={"title": "Plattform", "id": "platform", "template": "aks-database"})
    node = next(iter(client.get("/api/diagrams/platform").json()["diagram"]["nodes"]))
    args = {"id": node, "element": "e", "name": "E", "element_kind": "node"}
    r = client.post("/api/diagrams/platform/ops", json={"op": "link_element", "args": args})
    assert r.status_code == 400 and "/link" in r.json()["detail"]


# ---- exchange, comparison ----------------------------------------------------------------------------------


def test_exchange_carries_refs_and_checks_them():
    d = deployment()
    node = next(n for n in d.nodes.values() if n.kind == "service")
    link(d, node.id)
    parsed = exchange.parse_document(exchange.export_document(d))
    assert parsed.nodes[node.id].ref == "orders"
    broken = exchange.export_document(d)
    broken["diagram"]["nodes"][node.id]["ref"] = "Not A Slug"
    with pytest.raises(exchange.ExchangeError, match="invalid element reference"):
        exchange.parse_document(broken)
    a = activity()
    action = next(n for n in a.nodes.values() if n.kind == "opaque_action")
    document = exchange.export_document(a)
    document["diagram"]["nodes"][action.id]["ref"] = "orders"
    with pytest.raises(exchange.ExchangeError, match="cannot represent"):
        exchange.parse_document(document)
    s = sequence()
    link(s, s.interaction.lifelines[0].id, element="shop", name="Kunde")
    assert exchange.parse_document(exchange.export_document(s)).interaction.lifelines[0].ref == "shop"


def test_a_changed_link_is_a_changed_node():
    old = deployment()
    new = old.model_copy(deep=True)
    node = next(n for n in new.nodes.values() if n.kind == "service")
    link(new, node.id, name=node.name)
    assert node.id in diff.compare(old, new).changed_nodes
    changes = proposal.diagram_diff(old, new)["nodes"]["changed"]
    assert changes == [{"id": node.id, "name": node.name, "changes": {"ref": [None, "orders"]}}]


# ---- project side: elements and section links (ADR-0070, 0073, 0074) ----------------------------------------


def shop() -> Project:
    project = projects.new_project("Shop", "shop")
    projects.apply(project, "add_section", {"title": "Bestellung", "id": "bestellung"})
    return project


def test_add_element_derives_unique_ids_and_refuses_duplicate_names():
    project = shop()
    first = projects.apply(project, "add_element", {"name": "Order Service"})["result"]
    assert first == {
        "id": "order-service",
        "name": "Order Service",
        "kind": "component",
        "description": "",
        "parent": None,
    }
    projects.apply(project, "add_element", {"name": "Order-Service!", "kind": "system"})
    assert [e.id for e in project.elements] == ["order-service", "order-service-2"]
    with pytest.raises(projects.ProjectError, match="already called"):
        projects.apply(project, "add_element", {"name": "order service"})
    with pytest.raises(projects.ProjectError, match="Unknown element kind"):
        projects.apply(project, "add_element", {"name": "X", "kind": "robot"})
    with pytest.raises(projects.ProjectError, match="Invalid element id"):
        projects.apply(project, "add_element", {"name": "X", "id": "Not Slug"})
    with pytest.raises(projects.ProjectError, match="already used"):
        projects.apply(project, "add_element", {"name": "X", "id": "order-service"})
    with pytest.raises(projects.ProjectError, match="must not be empty"):
        projects.apply(project, "add_element", {"name": "  "})


def test_the_model_has_a_size_limit(monkeypatch):
    monkeypatch.setattr(projects, "MAX_ELEMENTS", 2)
    project = shop()
    projects.apply(project, "add_element", {"name": "A"})
    projects.apply(project, "add_element", {"name": "B"})
    with pytest.raises(projects.ProjectError, match="at most 2"):
        projects.apply(project, "add_element", {"name": "C"})


def test_elements_nest_without_cycles():
    project = shop()
    projects.apply(project, "add_element", {"name": "Shop", "kind": "system"})
    projects.apply(project, "add_element", {"name": "Checkout", "kind": "subsystem", "parent": "shop"})
    projects.apply(project, "add_element", {"name": "Cart", "parent": "Checkout"})
    assert project.find_element("cart").parent == "checkout"
    with pytest.raises(projects.ProjectError, match="inside itself"):
        projects.apply(project, "move_element", {"element": "shop", "parent": "cart"})
    projects.apply(project, "move_element", {"element": "cart", "parent": None})
    assert project.find_element("cart").parent is None
    removed = projects.apply(project, "remove_element", {"element": "shop"})["result"]
    assert removed["moved_elements"] == ["checkout"] and project.find_element("checkout").parent is None


def test_sections_stand_for_elements_and_follow_their_names():
    project = shop()
    projects.apply(project, "add_element", {"name": "Checkout", "kind": "subsystem"})
    result = projects.apply(project, "link_section", {"section": "bestellung", "element": "checkout"})["result"]
    assert result == {"section": "bestellung", "element": "checkout", "title": "Checkout"}
    with pytest.raises(projects.ProjectError, match="rename the element"):
        projects.apply(project, "rename_section", {"section": "bestellung", "title": "Kasse"})
    renamed = projects.apply(project, "update_element", {"element": "checkout", "name": "Kasse"})["result"]
    assert renamed["renamed_from"] == "Checkout" and renamed["sections"] == ["bestellung"]
    assert project.find_section("bestellung").title == "Kasse"
    removed = projects.apply(project, "remove_element", {"element": "kasse"})["result"]
    assert removed["unlinked_sections"] == ["bestellung"] and project.find_section("bestellung").element is None
    projects.apply(project, "rename_section", {"section": "bestellung", "title": "Bestellung"})


def test_unset_section_links_are_omitted_from_dumps():
    assert "element" not in Section(id="a", title="A").model_dump()
    assert Section(id="a", title="A", element="x").model_dump()["element"] == "x"


def test_integrity_covers_the_model():
    project = shop()
    project.elements = [projects.Element(id="a", name="A", parent="ghost"), projects.Element(id="a", name="B")]
    project.sections[0].element = "nope"
    errors = project.integrity_errors()
    assert any("duplicate element id" in e for e in errors)
    assert any("unknown parent 'ghost'" in e for e in errors)
    assert any("unknown element 'nope'" in e for e in errors)


def test_element_operations_roll_back_on_errors():
    project = shop()
    projects.apply(project, "add_element", {"name": "A"})
    before = project.model_copy(deep=True)
    with pytest.raises(projects.ProjectError):
        projects.apply(project, "update_element", {"element": "a", "name": "B", "kind": "robot"})
    assert project == before


def test_model_summary_fits_its_budget():
    elements = [
        project_model.Element(id=f"e{i}", name=f"E{i}", description="x" * 40, parent="e0" if i else None)
        for i in range(300)
    ]
    full = project_model.model_summary(elements[:3], {"e1": ["demo"]})
    assert full.splitlines() == [
        "- [e0] «component» E0 — " + "x" * 40,
        "- [e1] «component» E1 (part of e0) — " + "x" * 40 + " (in: demo)",
        "- [e2] «component» E2 (part of e0) — " + "x" * 40,
    ]
    short = project_model.model_summary(elements, budget=2_000)
    assert len(short) <= 2_000 and "x" not in short and "call list_elements" in short.splitlines()[-1]


def test_a_linked_node_keeps_a_kind_that_can_stand_for_its_element():
    """Review of #154: a database linked to a data element must not become a service (services cannot be data)."""
    d = deployment()
    database = next(n for n in d.nodes.values() if n.kind == "database")
    link(d, database.id, element="db", name="DB", kind="data")
    with pytest.raises(operations.OperationError, match="unlink"):
        operations.apply(d, "update_node", {"id": database.id, "kind": "service"})
    operations.apply(d, "update_node", {"id": database.id, "kind": "storage"})  # same element kinds
    assert d.nodes[database.id].kind == "storage" and d.nodes[database.id].ref == "db"


def test_a_new_element_name_equal_to_another_elements_id_still_finds_the_name_clash():
    project = shop()
    projects.apply(project, "add_element", {"name": "Alpha", "id": "beta"})
    projects.apply(project, "add_element", {"name": "Beta", "id": "gamma"})
    projects.apply(project, "add_element", {"name": "Gamma", "id": "delta"})
    # "Beta" is the id of Alpha and the name of gamma: the name clash must be found
    with pytest.raises(projects.ProjectError, match="already called"):
        projects.apply(project, "update_element", {"element": "delta", "name": "beta"})
