"""The official MCP client against the real app over HTTP (spec docs/architecture/specs/agent-access.md): protocol
compliance that the JSON-RPC tests of test_mcp_server.py cannot show."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("mcp")
import httpx2  # noqa: E402  (a dependency of the MCP SDK)
import uvicorn  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from arcviz.server import create_app  # noqa: E402
from arcviz.services import ServiceRoot  # noqa: E402
from arcviz.templates import demo_diagram  # noqa: E402
from arcviz.workspaces import Identity  # noqa: E402
from tests.conftest import ScriptedProvider  # noqa: E402

ADA = Identity("ada", "Ada", "ada@example.org")


@pytest.fixture
def server(store, monkeypatch) -> Iterator[tuple[str, str]]:
    """The app on a free local port in proxy mode, with ada's project `shop` and a write token of hers."""
    root = ServiceRoot(store)
    root.users.seen(ADA)
    ada = root.for_actor(ADA)
    ada.projects.create("Shop", "shop")
    ada.diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "shop")
    ada.models.add_element("shop", "Orders", "component", "Owns orders", element_id="orders")
    token = ada.tokens.create("test", "write").token
    for name, value in {"ARCVIZ_MCP": "1", "ARCVIZ_AUTH": "proxy", "ARCVIZ_AUTH_PROXY_PRESET": "oauth2-proxy"}.items():
        monkeypatch.setenv(name, value)
    app = create_app(store=store, provider=ScriptedProvider([]))
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_config=None, lifespan="on"
    )  # log_config=None: leave the logging setup of other tests alone
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not instance.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert instance.started
    port = instance.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}/mcp", token
    instance.should_exit = True
    thread.join(10)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_the_official_client_lists_and_calls_tools_with_a_token(server):
    url, token = server

    async def session() -> tuple[list[str], dict[str, Any] | None, bool, str]:
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
            async with Client(streamable_http_client(url, http_client=http)) as client:
                tools = [t.name for t in (await client.list_tools()).tools]
                component = await client.call_tool("get_component", {"project": "shop", "component": "orders"})
                refused = await client.call_tool("get_component", {"project": "shop", "component": "nope"})
                return tools, component.structured_content, refused.is_error, refused.content[0].text

    tools, component, refused, message = run(session())
    assert "get_component" in tools and "propose_change" in tools
    assert component is not None and component["responsibilities"] == "Owns orders"
    assert refused and "Unknown element 'nope'" in message


def test_the_official_client_without_a_credential_is_refused(server):
    url, _ = server

    async def session() -> None:
        async with httpx2.AsyncClient() as http:
            async with Client(streamable_http_client(url, http_client=http)) as client:
                await client.list_tools()

    with pytest.raises(Exception):  # noqa: B017  (the SDK's error type for a 401 is not part of our contract)
        run(session())
