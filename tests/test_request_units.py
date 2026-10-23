"""Cosmos DB request units as a metric (#201, ADR-0154; spec: docs/architecture/specs/robustness.md).

Every request the Cosmos store sends records its charge (`x-ms-request-charge`) in the histogram
`arcviz.store.request_units`, with only two allow-listed attributes: the operation kind and the document kind. Never an
id, a partition key or a query. Without `start_telemetry` nothing is recorded and the SDK gets no response hook."""

from __future__ import annotations

from typing import Any

import pytest
from azure.cosmos import exceptions

from arcviz import server, telemetry
from arcviz.store.base import DOCUMENT_KINDS
from arcviz.store.cosmos import CosmosStore
from arcviz.templates import demo_diagram
from tests.conftest import ScriptedProvider
from tests.cosmos_fake import FakeContainer
from tests.test_telemetry_m24 import fresh_metrics, metric_points, otlp  # noqa: F401 (fixtures)

SECRET = "geheim-zeta"  # a diagram id: never an attribute value
CHARGES = {"read_item": 1.0, "create_item": 6.5, "upsert_item": 7.25, "replace_item": 8.0, "delete_item": 5.0}


class ChargingContainer(FakeContainer):
    """The fake container answering every request with a charge header, through the SDK's `response_hook` (or the
    error's headers), like azure-cosmos does."""

    def __init__(self) -> None:
        super().__init__()
        self.hooked: list[str] = []  # the methods that were given a response hook

    def _charged(self, name: str, charge: float, *args: Any, **kwargs: Any) -> Any:
        hook = kwargs.pop("response_hook", None)
        if hook is not None:
            self.hooked.append(name)
        try:
            result = getattr(FakeContainer, name)(self, *args, **kwargs)
        except exceptions.CosmosHttpResponseError as exc:
            exc.headers = {"x-ms-request-charge": "1.25"}
            raise
        if name == "query_items":
            result = list(result)
        if hook is not None:
            hook({"x-ms-request-charge": str(charge), "x-ms-activity-id": "a"}, result)
        return result

    def read_item(self, *args: Any, **kwargs: Any) -> Any:
        return self._charged("read_item", CHARGES["read_item"], *args, **kwargs)

    def create_item(self, *args: Any, **kwargs: Any) -> Any:
        return self._charged("create_item", CHARGES["create_item"], *args, **kwargs)

    def upsert_item(self, *args: Any, **kwargs: Any) -> Any:
        return self._charged("upsert_item", CHARGES["upsert_item"], *args, **kwargs)

    def replace_item(self, *args: Any, **kwargs: Any) -> Any:
        return self._charged("replace_item", CHARGES["replace_item"], *args, **kwargs)

    def delete_item(self, *args: Any, **kwargs: Any) -> Any:
        return self._charged("delete_item", CHARGES["delete_item"], *args, **kwargs)

    def execute_item_batch(self, *args: Any, **kwargs: Any) -> Any:
        return self._charged("execute_item_batch", 12.0, *args, **kwargs)

    def query_items(self, *args: Any, **kwargs: Any) -> Any:
        return self._charged("query_items", 2.5, *args, **kwargs)


def units(reader) -> dict[tuple[str, str], tuple[int, float]]:
    """(operation, document) -> (count, sum) of the request unit histogram."""
    out: dict[tuple[str, str], tuple[int, float]] = {}
    for point in metric_points(reader).get("arcviz.store.request_units", []):
        assert set(point.attributes) == {"db.operation.name", "arcviz.store.document"}, dict(point.attributes)
        key = (point.attributes["db.operation.name"], point.attributes["arcviz.store.document"])
        out[key] = (point.count, point.sum)
    return out


def test_every_request_records_its_charge_by_operation_and_document_kind(otlp, tmp_path):  # noqa: F811
    _, reader = otlp
    container = ChargingContainer()
    store = CosmosStore(container)
    server.create_app(store=store, provider=ScriptedProvider([]))  # starts telemetry (ARCVIZ_TELEMETRY=otlp)
    before = units(reader)
    store.create("Geheim", SECRET, demo_diagram(SECRET, "Geheim"))
    store.load(SECRET)
    store.create_document("workspace", "w1", {"name": "x"})
    store.read_document("workspace", "w1")
    with pytest.raises(Exception):  # noqa: B017 (the store's StoreError for a missing document)
        store.read_document("user", "nobody")
    store.snapshot_latest(SECRET, "zwei")
    store.append_chat(SECRET, [{"role": "user", "content": "hallo"}])
    store.load_chat(SECRET)
    store.delete(SECRET)  # every kind of request the store sends goes through the metered container
    after = units(reader)
    grown = {key: (n - before.get(key, (0, 0))[0], s - before.get(key, (0, 0))[1]) for key, (n, s) in after.items()}
    assert grown[("read", "diagram")][0] >= 1 and grown[("read", "workspace")] == (1, CHARGES["read_item"])
    assert grown[("create", "workspace")] == (1, CHARGES["create_item"])
    assert {("query", "diagram"), ("batch", "diagram"), ("delete", "diagram")} <= set(grown)
    assert grown[("read", "user")] == (1, 1.25)  # a failed request is charged too (the error's headers)
    for operation, document in after:
        assert operation in telemetry.STORE_OPERATIONS and document in telemetry.STORE_DOCUMENTS
    exported = repr(metric_points(reader))
    assert SECRET not in exported and "w1" not in exported and "SELECT" not in exported
    assert {"read_item", "create_item"} <= set(container.hooked)


def test_every_document_kind_is_a_known_attribute_value():
    assert set(DOCUMENT_KINDS) <= telemetry.STORE_DOCUMENTS
    assert telemetry.store_document(None) == "any"  # a query across partitions
    assert telemetry.store_document("orders-k3jdhs7a2b") == "diagram"
    assert telemetry.store_document("project:shop") == "project"
    assert telemetry.store_document("presence:shop") == "presence"
    assert telemetry.store_document("location:orders") == "location"
    assert telemetry.store_document("made-up:x") == "other"


def test_without_telemetry_the_sdk_gets_no_hook_and_nothing_is_measured(monkeypatch):
    monkeypatch.setattr(telemetry, "_enabled", False)

    def forbidden(*args, **kwargs):
        raise AssertionError("OpenTelemetry touched while telemetry is off")

    from opentelemetry import metrics

    monkeypatch.setattr(metrics, "get_meter", forbidden)
    container = ChargingContainer()
    store = CosmosStore(container)
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    store.load("demo")
    assert container.hooked == []


def test_a_query_page_that_fails_while_iterating_is_charged_too(otlp, tmp_path):  # noqa: F811
    """Coordinator review #8: query pages are fetched lazily, while the caller iterates; a page that fails then (a
    throttle, a bad request) is charged from its error's headers like any other failed request."""
    _, reader = otlp

    class FailingPages(ChargingContainer):
        def query_items(self, *args: Any, **kwargs: Any) -> Any:
            hook = kwargs.pop("response_hook", None)
            if hook is not None:
                self.hooked.append("query_items")

            def pages():
                hook({"x-ms-request-charge": "2.5"}, [])  # the first page
                yield {"n": 1}
                error = exceptions.CosmosHttpResponseError(status_code=400, message="second page refused")
                error.headers = {"x-ms-request-charge": "0.75"}
                raise error

            return pages()

    store = CosmosStore(FailingPages())
    server.create_app(store=store, provider=ScriptedProvider([]))
    before = units(reader).get(("query", "diagram"), (0, 0.0))
    with pytest.raises(Exception):  # noqa: B017 (the store's StoreError)
        store._query("SELECT * FROM c", "orders-k3jdhs7a2b")
    count, total = units(reader)[("query", "diagram")]
    assert (count - before[0], total - before[1]) == (2, 3.25)
