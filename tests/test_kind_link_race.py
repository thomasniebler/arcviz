"""An element kind change and a concurrent link cannot both succeed (#155, spec: docs/architecture/specs/hardening.md,
ADR-0128). Each side writes, then reads the other side and compensates conditionally."""

from __future__ import annotations

from arcviz.llm.base import ModelReply
from arcviz.services import ModelService, ProjectService
from tests import test_server_model
from tests.conftest import ScriptedProvider, call
from tests.test_server_model import nodes

client = test_server_model.client  # the fixtures of the model API tests
shop = test_server_model.shop


def partition_of(client) -> str:
    return next(i for i, n in nodes(client, "order").items() if n["kind"] == "activity_partition")


def element(client, element_id: str) -> dict:
    return next(e for e in client.get("/api/projects/shop/model").json()["elements"] if e["id"] == element_id)


def test_a_link_landing_between_the_kind_check_and_the_kind_write_undoes_the_kind_change(shop, monkeypatch):
    shop.post("/api/projects/shop/elements", json={"name": "Order Service"})
    partition = partition_of(shop)
    real_apply_all = ProjectService.apply_all
    raced = []

    def link_first(self, project_id, steps, internal=False):
        if not raced and steps and steps[0][0] == "update_element" and steps[0][1].get("kind") == "data":
            raced.append(True)  # another request links the partition after the up-front check
            r = shop.post("/api/diagrams/order/link", json={"view": partition, "element": "order-service"})
            assert r.status_code == 200, r.text
        return real_apply_all(self, project_id, steps, internal)

    monkeypatch.setattr(ProjectService, "apply_all", link_first)
    r = shop.patch("/api/projects/shop/elements/order-service", json={"kind": "data"})
    assert raced and r.status_code == 400 and "order" in r.json()["detail"], r.text
    assert element(shop, "order-service")["kind"] == "component"  # the kind change was taken back
    assert nodes(shop, "order")[partition]["ref"] == "order-service"  # the link stays: it suits the kind


def test_a_kind_change_landing_between_the_links_read_and_its_write_undoes_the_link(shop, monkeypatch):
    shop.post("/api/projects/shop/elements", json={"name": "Order Service"})
    partition = partition_of(shop)
    real_link_args = ModelService.link_args

    def kind_change_after_reading(self, project_id, view, element_ref):
        args = real_link_args(self, project_id, view, element_ref)  # still "component"
        r = shop.patch("/api/projects/shop/elements/order-service", json={"kind": "data"})
        assert r.status_code == 200, r.text  # nothing links the element yet: the change is fine
        return args

    monkeypatch.setattr(ModelService, "link_args", kind_change_after_reading)
    r = shop.post("/api/diagrams/order/link", json={"view": partition, "element": "order-service"})
    assert r.status_code == 400 and "data" in r.json()["detail"], r.text
    assert "ref" not in nodes(shop, "order")[partition]  # the link was taken back
    assert element(shop, "order-service")["kind"] == "data"
    review = shop.get("/api/projects/shop/checks").json()
    assert not [f for f in review["findings"] if f.get("rule") == "incompatible-element-link"]


def test_the_agents_link_is_taken_back_when_the_kind_changed_meanwhile(store, monkeypatch):
    from fastapi.testclient import TestClient

    from arcviz.server import create_app

    provider = ScriptedProvider(
        [
            ModelReply(
                text="",
                tool_calls=[call("link_element", "c1", id="PARTITION", element="order-service")],
                stop_reason="tool_use",
            ),
            ModelReply(text="fertig", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    app_client = TestClient(create_app(store=store, provider=provider))
    assert app_client.post("/api/projects", json={"title": "Shop", "id": "shop"}).status_code == 201
    app_client.post(
        "/api/diagrams", json={"title": "Bestellprozess", "id": "order", "template": "order-process", "project": "shop"}
    )
    app_client.post("/api/projects/shop/elements", json={"name": "Order Service"})
    partition = partition_of(app_client)
    provider.replies[0].tool_calls[0].arguments["id"] = partition
    real_link_args = ModelService.link_args

    def kind_change_after_reading(self, project_id, view, element_ref):
        args = real_link_args(self, project_id, view, element_ref)
        system = ModelService(ProjectService(store, store), None)  # type: ignore[arg-type]
        system._projects.apply_all(
            "shop",
            [("update_element", {"element": "order-service", "name": None, "description": None, "kind": "data"})],
            internal=True,
        )
        return args

    monkeypatch.setattr(ModelService, "link_args", kind_change_after_reading)
    response = app_client.post("/api/diagrams/order/chat", json={"message": "verknüpfen"})
    assert response.status_code == 200
    results = [line for line in response.text.splitlines() if '"tool_result"' in line]
    assert results and '"is_error": true' in results[0]
    assert "ref" not in nodes(app_client, "order")[partition]


def test_a_rejected_update_leaves_nothing_behind_also_not_its_rename(shop, monkeypatch):
    """Codex review on #194: the compensation puts back the whole element (name, description, parent), not just the
    kind, and the rename is not carried into the diagrams."""
    shop.post("/api/projects/shop/elements", json={"name": "Order Service", "description": "alt"})
    partition = partition_of(shop)
    real_apply_all = ProjectService.apply_all
    raced = []

    def link_first(self, project_id, steps, internal=False):
        if not raced and steps and steps[0][0] == "update_element" and steps[0][1].get("kind") == "data":
            raced.append(True)
            assert (
                shop.post("/api/diagrams/order/link", json={"view": partition, "element": "order-service"}).status_code
                == 200
            )
        return real_apply_all(self, project_id, steps, internal)

    monkeypatch.setattr(ProjectService, "apply_all", link_first)
    r = shop.patch(
        "/api/projects/shop/elements/order-service",
        json={"kind": "data", "name": "Bestelldienst", "description": "neu"},
    )
    assert raced and r.status_code == 400, r.text
    after = element(shop, "order-service")
    assert (after["kind"], after["name"], after["description"]) == ("component", "Order Service", "alt")
    assert nodes(shop, "order")[partition]["name"] == "Order Service"  # no rename reached the diagram


def _agent_world(store, monkeypatch):
    from fastapi.testclient import TestClient

    from arcviz.server import create_app

    provider = ScriptedProvider(
        [
            ModelReply(
                text="",
                tool_calls=[call("link_element", "c1", id="PARTITION", element="order-service")],
                stop_reason="tool_use",
            ),
            ModelReply(text="fertig", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    app_client = TestClient(create_app(store=store, provider=provider))
    app_client.post("/api/projects", json={"title": "Shop", "id": "shop"})
    app_client.post(
        "/api/diagrams", json={"title": "Bestellprozess", "id": "order", "template": "order-process", "project": "shop"}
    )
    app_client.post("/api/projects/shop/elements", json={"name": "Order Service"})
    partition = partition_of(app_client)
    provider.replies[0].tool_calls[0].arguments["id"] = partition
    real_link_args = ModelService.link_args

    def kind_change_after_reading(self, project_id, view, element_ref):
        args = real_link_args(self, project_id, view, element_ref)
        ProjectService(store, store).apply_all(
            "shop",
            [("update_element", {"element": "order-service", "name": None, "description": None, "kind": "data"})],
            internal=True,
        )
        return args

    monkeypatch.setattr(ModelService, "link_args", kind_change_after_reading)
    return app_client, partition


def test_a_link_that_cannot_be_taken_back_is_reported_and_recorded(store, monkeypatch):
    """Codex review on #194: when the take-back write does not happen (busy), the tool error says so and the link,
    which stays stored, is recorded as applied (the project review reports it)."""
    from arcviz.services import DiagramService, LiveWrite

    app_client, partition = _agent_world(store, monkeypatch)
    real_write_through = DiagramService.write_through
    calls = []

    def busy_on_take_back(self, diagram_id, diagram, revision, change, failed):
        calls.append(diagram_id)
        if len(calls) == 2:  # the take-back: other requests keep changing the diagram, nothing is written
            return LiveWrite("busy", diagram, revision, None)
        return real_write_through(self, diagram_id, diagram, revision, change, failed)

    monkeypatch.setattr(DiagramService, "write_through", busy_on_take_back)
    response = app_client.post("/api/diagrams/order/chat", json={"message": "verknüpfen"})
    assert response.status_code == 200
    results = [line for line in response.text.splitlines() if '"tool_result"' in line]
    assert results and '"is_error": true' in results[0] and "could not be taken back" in results[0]
    assert nodes(app_client, "order")[partition]["ref"] == "order-service"  # stays stored
    final = [line for line in response.text.splitlines() if '"final"' in line][0]
    assert '"link_element"' in final  # recorded in the turn's operations


# ---- review of #194: fail closed, retried compensations, never undo somebody else's change ----------------------------


def _race_link_into_kind_change(shop, monkeypatch, partition, then=None):
    """Link the partition right before the kind write (as in the first test); `then(self, project_id)` runs after the
    kind write, before the verification."""
    real_apply_all = ProjectService.apply_all
    state = {"raced": False}

    def link_first(self, project_id, steps, internal=False):
        first = steps and steps[0][0] == "update_element" and steps[0][1].get("kind") == "data"
        if first and not state["raced"]:
            state["raced"] = True
            assert (
                shop.post("/api/diagrams/order/link", json={"view": partition, "element": "order-service"}).status_code
                == 200
            )
            result = real_apply_all(self, project_id, steps, internal)
            if then is not None:
                then(self, project_id)
            return result
        return real_apply_all(self, project_id, steps, internal)

    monkeypatch.setattr(ProjectService, "apply_all", link_first)
    return real_apply_all


def test_a_failing_kind_revert_is_retried_and_reported_as_the_refusal(shop, monkeypatch):
    from arcviz.ports import StoreError

    shop.post("/api/projects/shop/elements", json={"name": "Order Service"})
    partition = partition_of(shop)
    real_apply_all = _race_link_into_kind_change(shop, monkeypatch, partition)
    failures = {"left": 1}
    racing = ProjectService.apply_all

    def flaky_revert(self, project_id, steps, internal=False):
        if steps and steps[0][0] == "revert_element" and failures["left"]:
            failures["left"] -= 1
            raise StoreError("throttled (injected)")
        return racing(self, project_id, steps, internal)

    monkeypatch.setattr(ProjectService, "apply_all", flaky_revert)
    r = shop.patch("/api/projects/shop/elements/order-service", json={"kind": "data"})
    assert r.status_code == 400 and "cannot become a data element" in r.json()["detail"], r.text
    assert element(shop, "order-service")["kind"] == "component"  # retried, then put back
    assert real_apply_all is not None


def test_a_kind_revert_that_keeps_failing_says_so_in_the_refusal(shop, monkeypatch):
    from arcviz.ports import StoreError

    shop.post("/api/projects/shop/elements", json={"name": "Order Service"})
    partition = partition_of(shop)
    _race_link_into_kind_change(shop, monkeypatch, partition)
    racing = ProjectService.apply_all

    def failing_revert(self, project_id, steps, internal=False):
        if steps and steps[0][0] == "revert_element":
            raise StoreError("unavailable (injected)")
        return racing(self, project_id, steps, internal)

    monkeypatch.setattr(ProjectService, "apply_all", failing_revert)
    r = shop.patch("/api/projects/shop/elements/order-service", json={"kind": "data"})
    assert r.status_code == 400 and "could not be put back" in r.json()["detail"], r.text


def test_a_kind_revert_never_undoes_a_concurrent_rename(shop, monkeypatch):
    shop.post("/api/projects/shop/elements", json={"name": "Order Service", "description": "alt"})
    partition = partition_of(shop)

    def renamed_by_somebody_else(self, project_id):
        ProjectService(self._projects, self._diagrams).apply_all(
            "shop",
            [("update_element", {"element": "order-service", "name": None, "description": "von Bob", "kind": None})],
            internal=True,
        )

    _race_link_into_kind_change(shop, monkeypatch, partition, then=renamed_by_somebody_else)
    r = shop.patch("/api/projects/shop/elements/order-service", json={"kind": "data", "name": "Bestelldienst"})
    assert r.status_code == 400, r.text
    after = element(shop, "order-service")
    assert after["kind"] == "component" and after["description"] == "von Bob"  # Bob's change stays
    assert after["name"] == "Order Service"  # this update's own rename is undone, field by field


def test_a_rest_link_that_cannot_be_confirmed_is_taken_back(shop, monkeypatch):
    from arcviz.ports import StoreError

    shop.post("/api/projects/shop/elements", json={"name": "Order Service"})
    partition = partition_of(shop)

    def unreadable(self, project_id, element_id, diagram_type, view_kind):
        raise StoreError("throttled (injected)")

    monkeypatch.setattr(ModelService, "link_problem", unreadable)
    r = shop.post("/api/diagrams/order/link", json={"view": partition, "element": "order-service"})
    assert r.status_code == 400 and "could not be read to confirm" in r.json()["detail"], r.text
    assert "ref" not in nodes(shop, "order")[partition]


def test_a_rest_take_back_is_retried_and_a_lasting_failure_is_reported(shop, monkeypatch):
    from arcviz.ports import ConflictError
    from arcviz.services import DiagramService

    shop.post("/api/projects/shop/elements", json={"name": "Order Service"})
    partition = partition_of(shop)
    monkeypatch.setattr(
        ModelService, "link_problem", lambda self, *args: "Element 'order-service' changed; the link was taken back."
    )
    real_update = DiagramService.update
    failures = {"left": 1}

    def flaky_update(self, diagram_id, change, expected=None):
        if failures["left"]:
            failures["left"] -= 1
            raise ConflictError("kept changing (injected)")
        return real_update(self, diagram_id, change, expected)

    monkeypatch.setattr(DiagramService, "update", flaky_update)
    r = shop.post("/api/diagrams/order/link", json={"view": partition, "element": "order-service"})
    assert r.status_code == 400 and "taken back" in r.json()["detail"] and "could not" not in r.json()["detail"]
    assert "ref" not in nodes(shop, "order")[partition]  # the retry took it back

    failures["left"] = 99  # keeps failing
    r = shop.post("/api/diagrams/order/link", json={"view": partition, "element": "order-service"})
    assert r.status_code == 400 and "could not be taken back" in r.json()["detail"], r.text
    assert nodes(shop, "order")[partition]["ref"] == "order-service"  # stays, reported (the review shows it)


def test_a_kind_revert_whose_old_name_is_taken_meanwhile_still_puts_the_kind_back(shop, monkeypatch):
    """Second review of #194: a ProjectError of the compensation is a compensation failure: the kind alone goes back
    and the answer is the refusal, never an unrelated error."""
    shop.post("/api/projects/shop/elements", json={"name": "Order Service"})
    partition = partition_of(shop)

    def old_name_taken(self, project_id):
        ProjectService(self._projects, self._diagrams).apply_all(
            "shop",
            [
                (
                    "add_element",
                    {"name": "Order Service", "kind": "component", "description": "", "parent": None, "id": None},
                )
            ],
            internal=True,
        )

    _race_link_into_kind_change(shop, monkeypatch, partition, then=old_name_taken)
    r = shop.patch("/api/projects/shop/elements/order-service", json={"kind": "data", "name": "Bestelldienst"})
    assert r.status_code == 400 and "cannot become a data element" in r.json()["detail"], r.text
    after = element(shop, "order-service")
    assert after["kind"] == "component" and after["name"] == "Bestelldienst"  # the kind back, the name could not


def test_a_kind_only_update_never_writes_other_fields_back(shop, monkeypatch):
    shop.post("/api/projects/shop/elements", json={"name": "Order Service", "description": "alt"})
    partition = partition_of(shop)

    def renamed_by_somebody_else(self, project_id):
        ProjectService(self._projects, self._diagrams).apply_all(
            "shop",
            [
                (
                    "update_element",
                    {"element": "order-service", "name": "Bestellungen", "description": "neu", "kind": None},
                )
            ],
            internal=True,
        )

    _race_link_into_kind_change(shop, monkeypatch, partition, then=renamed_by_somebody_else)
    r = shop.patch("/api/projects/shop/elements/order-service", json={"kind": "data"})
    assert r.status_code == 400, r.text
    after = element(shop, "order-service")
    assert (after["kind"], after["name"], after["description"]) == ("component", "Bestellungen", "neu")
