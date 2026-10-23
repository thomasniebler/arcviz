"""Kind roles, traits and the redundancy property (spec docs/architecture/specs/deployment-traits.md, ADR-0169)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from arcviz import operations as ops
from arcviz import rules
from arcviz.kinds import KindConfigError, kind_pack_path, load_kind_catalog
from arcviz.model import Diagram, active_kinds, kind_trait, role_of, use_kinds
from arcviz.server import create_app


def write(tmp_path, content) -> str:
    path = tmp_path / "kinds.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return str(path)


def kinds_file(**spec) -> dict:
    return {"node_kinds": {"cosmos": spec}}


def diagram(*nodes: tuple[str, str, dict[str, str]]) -> Diagram:
    d = Diagram(id="t", title="T")
    for name, kind, properties in nodes:
        ops.add_node(d, name, kind=kind, id=name, properties=properties)
    return d


def rule_ids(d: Diagram, rule: str) -> list[list[str]]:
    return [f.elements for f in rules.run_checks(d) if f.rule == rule]


@pytest.fixture
def catalog(tmp_path):
    use_kinds(
        load_kind_catalog(
            write(
                tmp_path,
                {
                    "node_kinds": {
                        "cosmos": {"role": "database", "traits": {"redundancy": "built_in"}},
                        "pgsql": {"role": "database"},
                        "blobs": {"role": "storage", "traits": {"redundancy": "built_in"}},
                        "fn": {"role": "function"},
                        "plain": {},
                    }
                },
            )
        )
    )


# ---- kinds files -----------------------------------------------------------------------------


def test_role_and_traits_are_read(tmp_path):
    cat = load_kind_catalog(write(tmp_path, kinds_file(role="database", traits={"redundancy": "built_in"})))
    assert cat.node_roles == {"cosmos": "database"}
    assert cat.node_traits == {"cosmos": {"redundancy": "built_in"}}


def test_kinds_without_role_have_none(tmp_path):
    cat = load_kind_catalog(write(tmp_path, kinds_file(style="fill:#fff")))
    assert cat.node_roles == {} and cat.node_traits == {}


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"role": "network"}, "unknown role 'network'"),
        ({"role": 3}, "unknown role"),
        ({"container": True, "role": "database"}, "roles are for leaf kinds"),
        ({"traits": {"redundancy": "built_in"}}, "traits need a role"),
        ({"role": "database", "traits": []}, "'traits' must be an object"),
        ({"role": "database", "traits": {"speed": "fast"}}, "unknown trait 'speed'"),
        ({"role": "database", "traits": {"redundancy": "magic"}}, "unknown value 'magic'"),
        ({"role": "database", "traits": {"redundancy": "none"}}, "unknown value 'none'"),
    ],
)
def test_invalid_role_or_traits_name_the_kind(tmp_path, spec, message):
    with pytest.raises(KindConfigError, match=message) as exc:
        load_kind_catalog(write(tmp_path, kinds_file(**spec)))
    assert "'cosmos'" in str(exc.value) and "kinds.json" in str(exc.value)


def test_role_of_and_kind_trait(catalog):
    d = diagram(("a", "database", {}), ("b", "cosmos", {}), ("c", "plain", {}), ("d", "service", {}))
    assert [role_of(d.nodes[i]) for i in "abcd"] == ["database", "database", None, "service"]
    assert kind_trait(d.nodes["b"], "redundancy") == "built_in"
    assert kind_trait(d.nodes["a"], "redundancy") is None


def test_roles_are_part_of_the_config(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "ARCVIZ_KINDS_FILE", write(tmp_path, kinds_file(role="database", traits={"redundancy": "built_in"}))
    )
    cfg = TestClient(create_app()).get("/api/config").json()
    assert cfg["node_roles"] == {"cosmos": "database"}
    assert cfg["node_traits"] == {"cosmos": {"redundancy": "built_in"}}


# ---- rules -----------------------------------------------------------------------------------


def test_generic_database_is_still_reported():
    assert rule_ids(diagram(("db", "database", {})), "database-without-replication") == [["db"]]


@pytest.mark.parametrize("value", ["built_in", "Built_In", " built_in "])
def test_redundancy_property_marks_a_database_as_replicated_by_the_platform(value):
    d = diagram(("db", "database", {"redundancy": value}))
    assert rule_ids(d, "database-without-replication") == []
    [finding] = [f for f in rules.run_checks(d) if f.rule == "replicated-database"]
    assert finding.category == "strength" and "Plattform" in finding.message and finding.edges == []


def test_redundancy_none_is_reported():
    assert rule_ids(diagram(("db", "database", {"redundancy": "none"})), "database-without-replication") == [["db"]]


def test_unknown_redundancy_value_falls_back_to_the_kind():
    assert rule_ids(diagram(("db", "database", {"redundancy": "yes"})), "database-without-replication") == [["db"]]


def test_a_pack_kind_with_a_trait_is_not_reported(catalog):
    d = diagram(("c", "cosmos", {}))
    assert rule_ids(d, "database-without-replication") == []
    assert rule_ids(d, "replicated-database") == [["c"]]


def test_the_node_overrides_the_kind_trait(catalog):
    d = diagram(("c", "cosmos", {"redundancy": "none"}))
    assert rule_ids(d, "database-without-replication") == [["c"]]
    assert rule_ids(d, "replicated-database") == []


def test_a_pack_database_without_trait_is_reported_until_the_node_says_otherwise(catalog):
    assert rule_ids(diagram(("p", "pgsql", {})), "database-without-replication") == [["p"]]
    assert rule_ids(diagram(("p", "pgsql", {"redundancy": "built_in"})), "database-without-replication") == []


def test_a_replica_edge_wins_over_redundancy_none():
    d = diagram(("a", "database", {"redundancy": "none"}), ("b", "database", {"redundancy": "none"}))
    ops.add_edge(d, "a", "b", kind="replication")
    assert rule_ids(d, "database-without-replication") == []


def test_roles_bring_pack_kinds_into_the_other_rules(catalog):
    d = diagram(("users", "external", {}), ("c", "cosmos", {}), ("f", "fn", {}))
    ops.add_edge(d, "users", "c")
    ops.add_edge(d, "users", "f")
    assert rule_ids(d, "external-to-datastore") == [["users", "c"]]
    assert rule_ids(d, "public-entry-without-gateway") == [["users", "f"]]


def test_kinds_without_role_stay_outside_the_rules(catalog):
    d = diagram(("users", "external", {}), ("x", "plain", {}))
    ops.add_edge(d, "users", "x")
    assert rule_ids(d, "public-entry-without-gateway") == []


# ---- shipped kind packs ----------------------------------------------------------------------


BUILT_IN = {"azure_cosmos_db", "aws_dynamodb", "gcp_spanner"}
OPTIONAL = {"azure_sql", "azure_postgresql", "aws_rds", "aws_aurora", "gcp_cloud_sql", "gcp_bigtable"}


@pytest.mark.parametrize("pack", ["azure", "aws", "gcp"])
def test_pack_databases_have_a_role_and_only_built_in_ones_a_trait(pack):
    cat = load_kind_catalog(kind_pack_path(pack))
    names = {k for k in cat.leaf_kinds if k in BUILT_IN | OPTIONAL}
    assert names, pack
    for name in names:
        assert cat.node_roles[name] == "database"
        assert (cat.node_traits.get(name, {}).get("redundancy") == "built_in") is (name in BUILT_IN)


def test_every_role_in_the_packs_is_valid():
    for pack in ("azure", "aws", "gcp"):
        cat = load_kind_catalog(kind_pack_path(pack))
        assert set(cat.node_roles) <= cat.leaf_kinds
    assert active_kinds() is not None
