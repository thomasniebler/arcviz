"""Store contract against a real Cosmos DB endpoint (emulator or account). Skipped unless configured.

ARCVIZ_TEST_COSMOS_URL=https://localhost:8081 ARCVIZ_TEST_COSMOS_KEY=... ARCVIZ_TEST_COSMOS_INSECURE=1 pytest tests/test_store_cosmos_live.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from arcviz.store.cosmos import CosmosStore
from tests.store_contract import *  # noqa: F401,F403

URL = os.environ.get("ARCVIZ_TEST_COSMOS_URL")
pytestmark = pytest.mark.skipif(not URL, reason="ARCVIZ_TEST_COSMOS_URL not set")


@pytest.fixture
def repo():
    from azure.cosmos import CosmosClient, PartitionKey

    client = CosmosClient(
        URL,
        credential=os.environ["ARCVIZ_TEST_COSMOS_KEY"],
        connection_verify=os.environ.get("ARCVIZ_TEST_COSMOS_INSECURE") != "1",
    )
    db = client.create_database_if_not_exists("arcviz-test")
    name = f"t-{uuid.uuid4().hex[:8]}"
    container = db.create_container_if_not_exists(id=name, partition_key=PartitionKey(path="/diagram_id"))
    try:
        yield CosmosStore(container)
    finally:
        db.delete_container(name)
