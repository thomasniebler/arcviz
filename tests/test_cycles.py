"""Malformed containment cycles terminate every traversal (issue #68).

Operations never create a cycle (`move_node` refuses), so a cycle can only come from corrupt stored data:
`load_latest` does not validate. Every traversal must still terminate, `integrity_errors()` must name the
cycle, and the store must refuse to persist it. Each test runs under a wall-clock guard, so a regression
fails the test instead of hanging the suite.
"""

from __future__ import annotations

import signal
from collections.abc import Iterator

import pytest

from arcviz import exchange, mermaid, operations, render, rules
from arcviz.model import SUMMARY_SLACK, Diagram, Node
from arcviz.store import StoreError

GUARD_SECONDS = 5


@pytest.fixture(autouse=True)
def terminates() -> Iterator[None]:
    """Fail (instead of hanging) when the test body does not finish within GUARD_SECONDS."""
    if not hasattr(signal, "setitimer"):  # pragma: no cover - Windows: no guard, the tests still run
        yield
        return

    def expire(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"did not terminate within {GUARD_SECONDS}s (containment cycle?)")

    previous = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, GUARD_SECONDS)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def node(node_id: str, parent: str | None, kind: str = "vnet") -> Node:
    return Node(id=node_id, name=node_id.upper(), kind=kind, parent=parent)


def cyclic(length: int) -> Diagram:
    """A valid top-level part (`root` with `leaf`) plus a cycle c0 -> c1 -> ... -> c0 and a node `inside`
    hanging off the cycle. `length` 1 is a self-cycle."""
    nodes = [node("root", None), node("leaf", "root", "service")]
    nodes += [node(f"c{i}", f"c{(i + 1) % length}") for i in range(length)]
    nodes.append(node("inside", "c0", "service"))
    return Diagram(id="cyc", title="Cyclic", nodes={n.id: n for n in nodes})


CYCLES = pytest.mark.parametrize("length", [1, 2, 5], ids=["self", "two", "five"])


@CYCLES
def test_integrity_errors_name_every_node_on_the_cycle(length):
    errors = cyclic(length).integrity_errors()
    assert sorted(errors) == sorted(f"node 'c{i}' is part of a containment cycle" for i in range(length))


@CYCLES
def test_traversals_terminate(length):
    d = cyclic(length)
    cycle = {f"c{i}" for i in range(length)}
    assert set(d.ancestors("inside")) == cycle
    assert {n.id for n in d.descendants("c0")} == (cycle - {"c0"}) | {"inside"}
    assert d.path("inside").endswith("INSIDE")
    assert set(d.subset(focus="c0")["nodes"]) == cycle | {"inside"}
    assert set(d.subset(ids=["inside"])["nodes"]) == cycle | {"inside"}
    assert "[root]" in d.summary() and "[root]" in d.summary(budget=500)  # collapsing terminates
    assert len(d.summary(budget=50)) <= 50 + SUMMARY_SLACK  # and so does cutting (#115)
    assert [c["id"] for c in mermaid.breadcrumb(d, "inside")][-1] == "inside"


@CYCLES
def test_rendering_terminates(length):
    d = cyclic(length)
    whole = mermaid.render(d)
    assert mermaid.id_map(d)["root"] in whole
    focused = mermaid.render(d, focus="c0")
    ids = mermaid.id_map(d)
    for node_id in ["c0", "inside"]:
        assert focused.count(f"subgraph {ids[node_id]}[") + focused.count(f"  {ids[node_id]}[") >= 1
    assert render.payload(d, focus="c0")["renderer"] == "mermaid"
    rules.run_checks(d)
    exchange.export_document(d)


@CYCLES
def test_operations_on_a_cyclic_diagram_terminate(length):
    d = cyclic(length)
    with pytest.raises(operations.OperationError, match="cycle"):
        operations.apply(d, "move_node", {"id": "c0", "parent": f"c{length - 1}"})  # its own child
    operations.apply(d, "move_node", {"id": "leaf", "parent": "c0"})
    operations.apply(d, "remove_node", {"id": "c0", "cascade": True})
    assert not ({f"c{i}" for i in range(length)} | {"inside", "leaf"}) & set(d.nodes)


@CYCLES
def test_import_rejects_a_cycle(length):
    with pytest.raises(exchange.ExchangeError, match="containment cycle"):
        exchange.parse_document(exchange.export_document(cyclic(length)))


@CYCLES
def test_store_refuses_to_persist_a_cycle(store, length):
    store.create("Cyclic", "cyc")
    before = store.load("cyc")
    with pytest.raises(StoreError, match="containment cycle"):
        store.save_latest(cyclic(length))
    with pytest.raises(StoreError, match="containment cycle"):
        store.create("Other", "other", cyclic(length))
    assert store.load("cyc") == before and not store.exists("other")
