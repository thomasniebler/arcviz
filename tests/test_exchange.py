"""Diagram import/export (spec: docs/architecture/specs/diagram-exchange.md)."""

import json

import pytest
from fastapi.testclient import TestClient

from arcviz import exchange
from arcviz import operations as ops
from arcviz.model import Diagram
from arcviz.server import create_app
from arcviz.templates import demo_diagram
from tests.conftest import ScriptedProvider


def _demo() -> Diagram:
    d = demo_diagram("demo", "Demo")
    ops.update_node(d, "orders-db", properties={"engine": "postgres 16"})
    ops.set_direction(d, "LR")
    return d


def _errors(document) -> list[str]:
    with pytest.raises(exchange.ExchangeError) as info:
        exchange.parse_document(document)
    return info.value.errors


# ---- JSON document ----------------------------------------------------------------


def test_round_trip_is_lossless():
    d = _demo()
    doc = exchange.export_document(d)
    assert doc["format"] == "arcviz-diagram" and doc["schema_version"] == exchange.DEPLOYMENT_SCHEMA_VERSION
    assert "exported_at" in doc
    assert exchange.parse_document(doc) == d


def test_bare_diagram_is_accepted():
    d = _demo()
    assert exchange.parse_document(d.model_dump(mode="json")) == d


@pytest.mark.parametrize(
    ("document", "needle"),
    [
        ([], "must be a JSON object"),
        ({"format": "drawio", "diagram": {}}, "Unknown format 'drawio'"),
        ({"format": "arcviz-diagram", "schema_version": 99, "diagram": {}}, "newer Arcviz"),
        ({"format": "arcviz-diagram", "schema_version": "1", "diagram": {}}, "schema_version"),
        ({"format": "arcviz-diagram", "schema_version": 1}, "diagram"),
        ({"id": "x"}, "title"),
        ({"id": "x", "title": "x", "nodes": []}, "nodes"),
    ],
)
def test_structural_errors(document, needle):
    errors = _errors(document)
    assert any(needle in e for e in errors), errors


def test_model_rule_violations_are_all_reported():
    doc = _demo().model_dump(mode="json")
    doc["nodes"]["service-a"]["kind"] = "spaceship"
    doc["nodes"]["service-b"]["parent"] = "orders-db"  # a leaf
    doc["nodes"]["service-c"]["parent"] = "nowhere"
    doc["nodes"]["alias"] = {**doc["nodes"]["idp"], "id": "idp-2"}
    doc["edges"][0]["kind"] = "teleport"
    doc["edges"][1]["id"] = doc["edges"][2]["id"]
    doc["edges"][3]["target"] = "ghost"
    doc["direction"] = "UP"
    errors = _errors(doc)
    joined = "\n".join(errors)
    for needle in (
        "node 'service-a' has unknown kind 'spaceship'",
        "node 'service-b' has parent 'orders-db' which is not a container",
        "unknown parent 'nowhere'",
        "key 'alias' does not match node id 'idp-2'",
        "unknown kind 'teleport'",
        "duplicate edge id",
        "unknown target 'ghost'",
        "unknown direction 'UP'",
    ):
        assert needle in joined, (needle, errors)


def test_containment_cycle_and_empty_names_are_rejected():
    doc = _demo().model_dump(mode="json")
    doc["nodes"]["prod-deployment"]["parent"] = "prod-deployment"
    doc["nodes"]["idp"]["name"] = "  "
    joined = "\n".join(_errors(doc))
    assert "containment cycle" in joined and "node 'idp' has an empty name" in joined


# ---- PlantUML -------------------------------------------------------------------------


def test_plantuml_structure():
    d = _demo()
    ops.add_node(d, 'Say "hi"\nnow', kind="service", id="quote", parent="prod-deployment")
    ops.add_edge(d, "quote", "orders-db", label="sql\n'x'", kind="dependency")
    src = exchange.export_plantuml(d)
    lines = [line.strip() for line in src.splitlines()]
    assert lines[0] == "@startuml" and lines[-1] == "@enduml"
    assert "title Demo" in lines
    assert "left to right direction" in lines
    assert 'node "prod-deployment" <<deployment>> as n_prod_deployment {' in lines
    assert 'database "orders-db" <<database>> as n_orders_db' in lines
    assert 'component "Say &#34;hi&#34; now" <<service>> as n_quote' in lines
    assert "n_quote ..> n_orders_db : sql 'x'" in lines
    # nesting: the database sits inside the deployment block
    start = lines.index('node "prod-deployment" <<deployment>> as n_prod_deployment {')
    assert start < lines.index('database "orders-db" <<database>> as n_orders_db') < lines.index("}", start)
    assert 'cloud "Identity Provider" <<external>> as n_idp' in lines
    assert "n_api_gateway ..> n_idp : oidc" in lines


@pytest.mark.parametrize(
    ("direction", "line"),
    [("TB", "top to bottom direction"), ("BT", "top to bottom direction"), ("RL", "left to right direction")],
)
def test_plantuml_direction(direction, line):
    d = demo_diagram()
    ops.set_direction(d, direction)
    assert line in exchange.export_plantuml(d).splitlines()


@pytest.mark.parametrize(
    ("title", "line"),
    [("Demo", "title Demo"), ("a --> b; 5%", "title a &#45;&#45;&#62; b&#59; 5&#37;"), ("😀", "title &#128512;")],
)
def test_plantuml_title_is_shown_literally(title, line):
    # PlantUML 1.2026.8 refuses the titles ";", "-->", "%%" and a lone emoji written as they are
    d = Diagram(id="x", title=title)
    assert exchange.export_plantuml(d).splitlines()[1] == line


def test_plantuml_arrows():
    d = Diagram(id="x", title="x")
    ops.add_node(d, "a", kind="service")
    ops.add_node(d, "b", kind="service")
    for kind in ("connection", "deploys", "association", "replication"):
        ops.add_edge(d, "a", "b", kind=kind)
    lines = exchange.export_plantuml(d).splitlines()
    for arrow in ("n_a --> n_b", "n_a -[bold]-> n_b", "n_a -- n_b", "n_a <--> n_b"):
        assert arrow in lines


# ---- HTTP -----------------------------------------------------------------------------


@pytest.fixture
def client(store):
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
    return client


def test_export_endpoints(client):
    client.post("/api/diagrams/demo/ops", json={"op": "set_direction", "args": {"direction": "LR"}})
    r = client.get("/api/diagrams/demo/export?format=json")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert 'filename="demo.arcviz.json"' in r.headers["content-disposition"]
    assert r.json()["diagram"]["direction"] == "LR"
    r = client.get("/api/diagrams/demo/export?format=json&version=1")
    assert r.json()["diagram"]["direction"] == "TB"
    assert 'filename="demo-v1.arcviz.json"' in r.headers["content-disposition"]
    r = client.get("/api/diagrams/demo/export?format=plantuml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert r.text.startswith("@startuml") and 'filename="demo.puml"' in r.headers["content-disposition"]
    assert client.get("/api/diagrams/demo/export").json()["format"] == "arcviz-diagram"  # json is the default
    assert client.get("/api/diagrams/demo/export?format=drawio").status_code == 400
    assert client.get("/api/diagrams/demo/export?version=99").status_code == 404
    assert client.get("/api/diagrams/nope/export").status_code == 404


def test_import_creates_new_diagrams_with_unique_ids(client, store):
    doc = client.get("/api/diagrams/demo/export").json()
    r = client.post("/api/diagrams/import", json={"document": doc})
    assert r.status_code == 201
    body = r.json()
    assert body["diagram"]["id"] == "demo-2" and body["diagram"]["title"] == "Demo"
    assert body["diagram"]["nodes"] == doc["diagram"]["nodes"] and "subgraph n_prod_deployment" in body["mermaid"]
    assert [v["source"] for v in body["versions"]] == ["initial"]
    r = client.post("/api/diagrams/import", json={"document": doc["diagram"], "title": "Copy"})
    assert r.status_code == 201 and r.json()["diagram"]["id"] == "demo-3" and r.json()["diagram"]["title"] == "Copy"
    r = client.post("/api/diagrams/import", json={"document": doc, "id": "imported"})
    assert r.status_code == 201 and r.json()["diagram"]["id"] == "imported"
    r = client.post("/api/diagrams/import", json={"document": doc, "id": "imported"})  # taken: a free variant
    assert r.status_code == 201 and r.json()["diagram"]["id"] == "imported-2"
    assert client.post("/api/diagrams/import", json={"document": doc, "id": "Bad Id"}).status_code == 400
    assert [d.id for d in store.list_diagrams()] == ["demo", "demo-2", "demo-3", "imported", "imported-2"]


def test_import_derives_a_valid_id(client):
    doc = Diagram(id="Fancy Name!", title="Fancy").model_dump(mode="json")
    assert client.post("/api/diagrams/import", json={"document": doc}).json()["diagram"]["id"] == "fancy-name"
    doc = Diagram(id="", title="Untitled").model_dump(mode="json")
    assert client.post("/api/diagrams/import", json={"document": doc}).json()["diagram"]["id"] == "diagram"


def test_invalid_import_persists_nothing(client, store):
    doc = client.get("/api/diagrams/demo/export").json()
    doc["diagram"]["nodes"]["service-a"]["parent"] = "ghost"
    r = client.post("/api/diagrams/import", json={"document": doc})
    assert r.status_code == 400 and "unknown parent 'ghost'" in r.json()["detail"]
    assert [d.id for d in store.list_diagrams()] == ["demo"]


@pytest.mark.parametrize(
    ("text", "escaped"),
    [
        ("prod-deployment", "prod-deployment"),  # single markup characters stay readable
        ("<b>x</b> & co", "&#60;b>x&#60;/b> &#38; co"),
        ('say "hi" ~ C:\\new', "say &#34;hi&#34; &#126; C:\\\\new"),
        (
            "**b** //i// __u__ --s-- [[l]] ==h==",
            "~*~*b~*~* ~/~/i~/~/ ~_~_u~_~_ ~-~-s~-~- ~[~[l]] &#61;&#61;h&#61;&#61;",
        ),
        ("http://x a=b", "http:~/~/x a=b"),
        ("two\n  lines", "two lines"),
        ("%date() 50%", "&#37;date() 50&#37;"),  # the preprocessor would call %date()
    ],
)
def test_plantuml_text_is_shown_literally(text, escaped):
    # checked against PlantUML 1.2026.8 rendering names, titles and edge labels
    assert exchange._plain(text) == escaped


def test_automatic_import_ids_survive_a_creation_race(client, store, monkeypatch):
    doc = client.get("/api/diagrams/demo/export").json()
    original = store.create

    def racing_create(title, diagram_id=None, diagram=None, author=None):
        if diagram_id == "demo-2" and not store.exists("demo-2"):
            original("Concurrent", "demo-2", demo_diagram("demo-2", "Concurrent"))  # another request wins the id
        return original(title, diagram_id, diagram, author)

    monkeypatch.setattr(store, "create", racing_create)
    r = client.post("/api/diagrams/import", json={"document": doc})
    assert r.status_code == 201 and r.json()["diagram"]["id"] == "demo-3"
    assert store.load("demo-2").title == "Concurrent"


def test_import_rejects_an_unknown_edge_direction():
    document = exchange.export_document(_demo())
    document["diagram"]["edges"][0]["direction"] = "sideways"
    with pytest.raises(exchange.ExchangeError, match="unknown direction 'sideways'"):
        exchange.parse_document(document)


# ---- project bundles (spec: docs/architecture/specs/project-exchange.md) --------------------------------


def _bundle_project():
    from arcviz import projects

    project = projects.new_project("Shop", "shop", "Alles")
    projects.apply(project, "add_section", {"title": "Backend"})
    projects.apply(project, "add_section", {"title": "Services", "parent": "backend"})
    projects.apply(project, "add_diagram", {"diagram": "demo", "section": "services"})
    projects.apply(project, "add_diagram", {"diagram": "flow"})
    projects.apply(project, "add_diagram", {"diagram": "gone"})  # deleted meanwhile: not exported
    flow = Diagram(id="flow", title="Ablauf", type="activity")
    return project, [demo_diagram("demo", "Demo"), flow]


def test_project_bundle_round_trip():
    project, diagrams = _bundle_project()
    bundle = exchange.export_project(project, diagrams)
    assert bundle["format"] == "arcviz-project" and bundle["schema_version"] == 1
    assert bundle["project"]["diagrams"] == [
        {"diagram": "demo", "section": "services"},
        {"diagram": "flow", "section": None},
    ]
    assert [d["schema_version"] for d in bundle["diagrams"]] == [1, 2]  # each document keeps its own version
    parsed = exchange.parse_project(json.loads(json.dumps(bundle)))
    assert parsed.title == "Shop" and parsed.description == "Alles"
    assert [(s.id, s.parent) for s in parsed.sections] == [("backend", None), ("services", "backend")]
    assert parsed.members == [("demo", "services"), ("flow", None)]
    assert parsed.diagrams["demo"].model_dump() == diagrams[0].model_dump()
    assert parsed.diagrams["flow"].type == "activity"


def test_project_bundle_collects_every_problem():
    project, diagrams = _bundle_project()
    bundle = exchange.export_project(project, diagrams)
    bundle["project"]["sections"].append({"id": "loose", "title": "Lose", "parent": "missing"})
    bundle["project"]["diagrams"].append({"diagram": "phantom", "section": None})
    bundle["diagrams"][0]["diagram"]["nodes"]["service-a"]["kind"] = "nope"
    bundle["diagrams"].append(exchange.export_document(Diagram(id="extra", title="Extra")))
    with pytest.raises(exchange.ExchangeError) as info:
        exchange.parse_project(bundle)
    text = " | ".join(info.value.errors)
    for fragment in ("unknown parent 'missing'", "'phantom'", "diagram 1 (demo)", "unknown kind 'nope'", "'extra'"):
        assert fragment in text, fragment


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda b: b.update(format="arcviz-diagram"), "Unknown format"),
        (lambda b: b.update(schema_version=exchange.PROJECT_SCHEMA_VERSION + 1), "newer Arcviz"),
        (lambda b: b.pop("project"), "no 'project'"),
        (lambda b: b.update(diagrams={}), "'diagrams' must be a list"),
        (lambda b: b["project"].update(title="  "), "title"),
        (lambda b: b["diagrams"].append(b["diagrams"][0]), "duplicate diagram id 'demo'"),
    ],
)
def test_project_bundle_envelope_errors(change, message):
    project, diagrams = _bundle_project()
    bundle = exchange.export_project(project, diagrams)
    change(bundle)
    with pytest.raises(exchange.ExchangeError, match=message):
        exchange.parse_project(bundle)


def test_project_bundle_must_be_an_object():
    with pytest.raises(exchange.ExchangeError, match="JSON object"):
        exchange.parse_project([1, 2])
