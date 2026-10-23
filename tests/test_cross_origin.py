"""Cross-site requests cannot change state (arc42 section 8, Security).

The app has no authentication or CSRF token (#82). What keeps a foreign web page from changing a
local instance is that mutating endpoints only accept `application/json` bodies: a browser sends those
only after a CORS preflight, and the app sends no CORS headers. "Simple" requests without a content
type, or with `text/plain` or form encoding, must be rejected.
"""

import json

import pytest
from fastapi.testclient import TestClient

from arcviz.server import create_app


@pytest.fixture
def client(store):
    client = TestClient(create_app(store=store))
    assert client.post("/api/diagrams", json={"title": "T", "id": "t", "template": "demo"}).status_code == 201
    return client


@pytest.mark.parametrize(
    "content_type", [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"]
)
def test_mutations_need_a_json_content_type(client, content_type):
    body = json.dumps({"op": "rename_diagram", "args": {"title": "changed"}}).encode()
    headers = {"content-type": content_type} if content_type else {}
    request = client.build_request("POST", "/api/diagrams/t/ops", content=body, headers=headers)
    assert client.send(request).status_code == 422
    assert client.get("/api/diagrams/t").json()["diagram"]["title"] == "T"


def test_no_cors_headers_for_foreign_origins(client):
    r = client.options(
        "/api/diagrams/t/ops", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in r.headers
