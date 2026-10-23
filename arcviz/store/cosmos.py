"""Azure Cosmos DB (NoSQL API) repository.

One container, partition key ``/diagram_id``; every item of a diagram lives in one logical partition.
Item ids: ``latest``, ``version:NNNN``, ``chat:NNNNNN`` and ``chat-generation``, distinguished by a
``type`` field. A project is one item ``project`` (type ``project``) in its own partition
``project:<project_id>``, which no diagram id can be (``check_id`` forbids ``:``); the project's transcript
(``chat:NNNNNN``, ``chat-generation``) lives in the same partition. Keyed documents (workspaces, users; ADR-0082)
are one item each, id and type the kind, in partition ``<kind>:<key>``. ``latest`` carries the state's ``revision``; a conditional write checks it and replaces
the item with ``If-Match`` on its ETag inside a transactional batch that also creates the version. Version
and seq numbers are allocated by creating the deterministic id and retrying on 409, which is safe
across replicas.

The class is written against the subset of ``azure.cosmos.ContainerProxy`` listed in ``Container``
so tests can run it against an in-process fake (``tests/cosmos_fake.py``).
"""

from __future__ import annotations

import logging
import math
import random
import time
from collections.abc import Callable, Iterable, Iterator
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from .. import telemetry
from ..model import Diagram
from ..projects import Project

if TYPE_CHECKING:
    from ..gitmirror import GitMirror
from .base import (
    LEGACY_REVISION,
    ConflictError,
    DiagramInfo,
    DiagramRepository,
    StoreError,
    VersionInfo,
    check_document,
    check_id,
    finalize_info,
    hide_staged,
    missing_document,
    new_token,
    strip_seq,
)

LATEST = "latest"
PROJECT = "project"  # id and type of a project item; its partition key value is PROJECT_PREFIX + project id
PROJECT_PREFIX = "project:"
PRESENCE = "presence"  # type of a presence item; id: the replica, partition: PRESENCE_PREFIX + project (ADR-0104)
PRESENCE_PREFIX = "presence:"
GENERATION = "chat-generation"  # id and type of the item holding the transcript's generation
BATCH_LIMIT = 100  # Cosmos transactional batch limit
TRANSIENT_STATUS = {408, 429, 449, 500, 503}  # retried with backoff on top of the SDK's own retries
TRANSIENT_ATTEMPTS = 6
T = TypeVar("T")
logger = logging.getLogger(__name__)

# Every query this backend issues; the test fake understands exactly these shapes.
Q_BY_TYPE = "SELECT * FROM c WHERE c.type = @type ORDER BY c.{field}"
Q_MAX = "SELECT VALUE MAX(c.{field}) FROM c WHERE c.type = @type"
Q_ALL_OF_TYPE = "SELECT * FROM c WHERE c.type = @type"
Q_IDS = "SELECT VALUE c.id FROM c"
Q_IDS_OF_TYPE = "SELECT VALUE c.id FROM c WHERE c.type = @type"
Q_CHAT_GENERATIONS = "SELECT c.id, c.generation FROM c WHERE c.type = @type"  # the generation each item was written in
Q_VERSION_META = "SELECT c.version, c.meta FROM c WHERE c.type = @type ORDER BY c.version"  # no snapshots (#114)
Q_FIELD_OF = "SELECT VALUE c.{field} FROM c WHERE c.id = @id"  # one field of one item (single partition)
Q_INFO = "SELECT c.title, c.updated_at FROM c WHERE c.id = @id"  # what a DiagramInfo needs of `latest`
Q_COUNT = "SELECT VALUE COUNT(1) FROM c WHERE c.type = @type"
Q_IDS_UPTO = "SELECT VALUE c.id FROM c WHERE c.type = @type AND c.seq <= @seq"


class Container(Protocol):
    def read_item(self, item: str, partition_key: str, **kwargs: Any) -> Any: ...
    def create_item(self, body: dict[str, Any], **kwargs: Any) -> Any: ...
    def upsert_item(self, body: dict[str, Any], **kwargs: Any) -> Any: ...
    def replace_item(self, item: str, body: dict[str, Any], **kwargs: Any) -> Any: ...
    def delete_item(self, item: str, partition_key: str, **kwargs: Any) -> None: ...
    def query_items(self, query: str, **kwargs: Any) -> Iterable[Any]: ...
    def execute_item_batch(self, batch_operations: Any, partition_key: str, **kwargs: Any) -> Any: ...


# The container methods whose requests are charged and measured (#201), by the operation name a metric shows.
_METERED = {
    "read_item": "read",
    "create_item": "create",
    "upsert_item": "upsert",
    "replace_item": "replace",
    "delete_item": "delete",
    "query_items": "query",
    "execute_item_batch": "batch",
}
REQUEST_CHARGE = "x-ms-request-charge"


class _MeteredContainer:
    """The requests of the store, each one's charge recorded in `arcviz.store.request_units` (#201, ADR-0154): the
    SDK's `response_hook` gets the response headers (a failed request's are on the error). Only the operation and the
    kind of document (from the partition key) become attributes. While telemetry is off a method is the container's
    own: no hook, nothing recorded."""

    def __init__(self, container: Container):
        self._container = container

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._container, name)
        operation = _METERED.get(name)
        if operation is None or not telemetry.enabled():
            return method
        return partial(_metered, method, operation)


def _metered(method: Callable[..., Any], operation: str, *args: Any, **kwargs: Any) -> Any:
    partition = kwargs.get("partition_key")
    if partition is None and operation in ("create", "upsert", "replace"):
        body = kwargs.get("body", args[-1] if args else None)
        partition = body.get("diagram_id") if isinstance(body, dict) else None
    document = telemetry.store_document(partition if isinstance(partition, str) else None)
    theirs = kwargs.pop("response_hook", None)

    def hook(headers: Any, *rest: Any) -> None:
        _record_charge(operation, document, headers)
        if theirs is not None:
            theirs(headers, *rest)

    try:
        result = method(*args, response_hook=hook, **kwargs)
    except Exception as exc:  # a failed request is charged too (its headers are on the error)
        _record_charge(operation, document, getattr(exc, "headers", None))
        raise
    return _metered_pages(result, operation, document) if operation == "query" else result


def _metered_pages(pages: Iterable[Any], operation: str, document: str) -> Iterator[Any]:
    """A query's results: its pages are fetched while the caller iterates, so a page that fails then is charged here
    (the hook sees only the pages that arrive)."""
    try:
        yield from pages
    except Exception as exc:
        _record_charge(operation, document, getattr(exc, "headers", None))
        raise


def _record_charge(operation: str, document: str, headers: Any) -> None:
    try:
        charge = float(headers.get(REQUEST_CHARGE)) if headers is not None else math.nan
    except (TypeError, ValueError, AttributeError):
        return
    if math.isfinite(charge):
        telemetry.record_request_units(operation, document, charge)


def _exceptions() -> Any:
    from azure.cosmos import exceptions  # imported lazily: azure-cosmos is an optional extra

    return exceptions


def _is_transient(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if status in TRANSIENT_STATUS:
        return True
    try:
        from azure.core.exceptions import ServiceRequestError, ServiceResponseError
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, ServiceRequestError | ServiceResponseError)


def _fresh(ops: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """`ops` with copies of every option dict. The SDK pops `if_match_etag` out of the dict it is given, so
    replaying the same list would silently drop the condition (a lost update); every call gets fresh ones."""
    return [(op, args, dict(rest[0])) if rest else (op, args) for op, args, *rest in ops]


def _has_revision(stored: dict[str, Any], revision: str) -> bool:
    return stored.get("revision") == revision


def _same_item(stored: Any, item: dict[str, Any]) -> bool:
    """True when `stored` is `item` as Cosmos returns it (system properties `_rid`, `_ts`, ... ignored)."""
    return {k: v for k, v in dict(stored).items() if not k.startswith("_")} == item


def _has_generation(generation: str, stored: dict[str, Any]) -> bool:
    return stored.get("generation") == generation


def _body(item: Any) -> dict[str, Any]:
    """An item as returned by the SDK without its system properties (`_etag`, `_rid`, ...), for a write."""
    return {k: v for k, v in dict(item).items() if not k.startswith("_")}


def _batch_errors() -> tuple[type[BaseException], ...]:
    """Exceptions a transactional batch raises: a failed operation surfaces as CosmosBatchOperationError,
    which derives from azure.core's HttpResponseError, *not* from CosmosHttpResponseError."""
    exceptions = _exceptions()
    return (exceptions.CosmosHttpResponseError, exceptions.CosmosBatchOperationError)


def _with_retry(call: Callable[[], T], what: str) -> T:
    """Run one SDK call; retry transient failures (throttling, timeouts, connection resets) with backoff.

    The SDK retries 429s itself; under heavy concurrency the emulator and real accounts still surface
    408/449/503 and connection errors, which would otherwise become StoreErrors on the first hit.
    """
    for attempt in range(1, TRANSIENT_ATTEMPTS + 1):
        try:
            return call()
        except Exception as exc:
            if not _is_transient(exc) or attempt == TRANSIENT_ATTEMPTS:
                raise
            _backoff(attempt, what, exc)
    raise AssertionError("unreachable")  # pragma: no cover


def _backoff(attempt: int, what: str, exc: BaseException) -> None:
    delay = min(2.0, 0.05 * 2**attempt) + random.uniform(0, 0.05)
    logger.debug("Cosmos DB %s: transient error (%s), retry %d in %.2fs", what, type(exc).__name__, attempt, delay)
    time.sleep(delay)


def connect(url: str, key: str | None, database: str, container: str) -> Container:
    """Build a ContainerProxy, creating database and container if missing. Raises StoreError without the extra."""
    try:
        from azure.cosmos import CosmosClient, PartitionKey, exceptions
    except ImportError as exc:
        raise StoreError(
            "The cosmos backend needs the optional dependency: pip install 'arcviz-agent[cosmos]'"
        ) from exc
    credential: Any
    if key:
        credential = key
    else:
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
    client = CosmosClient(url, credential=credential, retry_total=9)
    db: Any = client.get_database_client(database)
    try:
        db = client.create_database_if_not_exists(database)
        proxy: Container = db.create_container_if_not_exists(
            id=container,
            partition_key=PartitionKey(path="/diagram_id"),
            default_ttl=-1,  # TTL on, nothing expires unless an item says so (presence items do, ADR-0104)
        )
    except exceptions.CosmosHttpResponseError as exc:
        if exc.status_code != 403:
            raise StoreError(f"Cosmos DB connection failed: {exc}") from exc
        # Data-plane RBAC (managed identity) cannot create databases or containers; they must exist.
        proxy = client.get_database_client(database).get_container_client(container)
        try:
            proxy.read()
        except exceptions.CosmosHttpResponseError as read_exc:
            raise StoreError(
                f"Cosmos DB container '{database}/{container}' is not reachable with the given identity: {read_exc}. "
                "With managed identity, create the database and container up front (partition key /diagram_id) "
                "and grant the 'Cosmos DB Built-in Data Contributor' role."
            ) from read_exc
    ensure_ttl(db, proxy)
    return proxy


# container settings a replace sends back unchanged (a replace resets every setting it does not carry)
_KEPT_SETTINGS = {
    "indexingPolicy": "indexing_policy",
    "conflictResolutionPolicy": "conflict_resolution_policy",
    "analyticalStorageTtl": "analytical_storage_ttl",
    "computedProperties": "computed_properties",
    "fullTextPolicy": "full_text_policy",
    "vectorEmbeddingPolicy": "vector_embedding_policy",
}
# settings `replace_container` cannot carry: a container with one of them set is never replaced
_UNCARRIED_SETTINGS = ("uniqueKeyPolicy", "clientEncryptionPolicy", "globalSecondaryIndexDefinition")


def ensure_ttl(database: Any, proxy: Any) -> str:
    """Turn the container's Time to Live on (`default_ttl = -1`: nothing expires unless an item says so) so Cosmos DB
    removes the presence items of replicas that stopped (ADR-0104). The replace passes every other setting the
    container has back unchanged (partition key with kind and version, indexing policy, …); a container with a setting
    a replace cannot carry (unique keys, …) is left alone. "on" when it already was, "enabled" when it is now, "off" when
    it may not or cannot be changed (managed identity with data-plane roles only): logged, readers clean up instead.
    Never raises."""
    try:
        properties = proxy.read()
    except Exception:
        logger.warning("Could not read the Cosmos DB container settings; Time to Live is not checked", exc_info=True)
        return "off"
    if properties.get("defaultTtl") is not None:
        return "on"
    uncarried = [name for name in _UNCARRIED_SETTINGS if _is_set(properties.get(name))]
    try:
        if uncarried:
            raise ValueError(f"the container has settings a replace would reset ({', '.join(uncarried)})")
        kept = {arg: properties[name] for name, arg in _KEPT_SETTINGS.items() if properties.get(name) is not None}
        partition_key = properties.get("partitionKey") or {"paths": ["/diagram_id"], "kind": "Hash"}
        database.replace_container(properties.get("id", proxy.id), partition_key=partition_key, default_ttl=-1, **kept)
    except Exception as exc:
        logger.warning(
            "Time to Live is off on the Cosmos DB container and could not be turned on (%s). Enable it once (Settings → "
            "Time to Live → On (no default), `az cosmosdb sql container update … --ttl -1`, or `defaultTtl: -1` in "
            "Bicep/ARM); until then stale presence items are removed by the readers.",
            type(exc).__name__,
        )
        return "off"
    logger.info("Turned Time to Live on for the Cosmos DB container (presence items expire)")
    return "enabled"


def _is_set(value: Any) -> bool:
    """A container setting with content (`{"uniqueKeys": []}` is the empty unique key policy)."""
    if isinstance(value, dict):
        return any(_is_set(v) for v in value.values())
    return bool(value)


class CosmosStore(DiagramRepository):
    name = "cosmos"

    def __init__(self, container: Container, mirror: GitMirror | None = None):
        super().__init__(mirror)
        self.container = container
        self._requests = _MeteredContainer(container)  # every request goes through it (request units, #201)

    # ---- helpers ----------------------------------------------------------

    def _query(self, query: str, diagram_id: str | None = None, **params: Any) -> list[Any]:
        parameters = [{"name": f"@{k}", "value": v} for k, v in params.items()]

        def run() -> list[Any]:
            if diagram_id is None:
                return list(self._requests.query_items(query, parameters=parameters, enable_cross_partition_query=True))
            return list(self._requests.query_items(query, parameters=parameters, partition_key=diagram_id))

        try:
            return _with_retry(run, "query")
        except _exceptions().CosmosHttpResponseError as exc:
            raise StoreError(f"Cosmos DB query failed: {exc}") from exc

    def _next_number(self, diagram_id: str, type_: str, field: str) -> int:
        values = self._query(Q_MAX.format(field=field), diagram_id, type=type_)
        current = values[0] if values and values[0] is not None else 0
        return int(current) + 1

    def _create_exact(self, item: dict[str, Any], partition_key: str) -> bool:
        """Create `item` under its deterministic id. False when another writer holds the id.

        A transient failure may hide a write that did reach the server; a replay then gets a 409. So a
        409 is reconciled by reading the item: our own content means success, anything else a conflict.
        """
        exceptions = _exceptions()
        ambiguous = False  # a transient error happened: the write may have reached the server
        for attempt in range(1, TRANSIENT_ATTEMPTS + 1):
            try:
                self._requests.create_item(item)
                return True
            except exceptions.CosmosResourceExistsError:
                if not ambiguous:
                    return False  # a clean 409: someone else holds the id
                try:
                    stored = _with_retry(
                        partial(self._requests.read_item, item["id"], partition_key=partition_key), "read"
                    )
                except exceptions.CosmosResourceNotFoundError:  # pragma: no cover - deleted in between
                    return False
                except exceptions.CosmosHttpResponseError as exc:
                    raise StoreError(f"Cosmos DB read failed: {exc}") from exc
                return _same_item(stored, item)
            except Exception as exc:
                if not _is_transient(exc) or attempt == TRANSIENT_ATTEMPTS:
                    if isinstance(exc, exceptions.CosmosHttpResponseError):
                        raise StoreError(f"Cosmos DB write failed: {exc}") from exc
                    raise
                ambiguous = True
                _backoff(attempt, "create", exc)
        raise AssertionError("unreachable")  # pragma: no cover

    def _create_with_retry(self, diagram_id: str, type_: str, field: str, make_item: Any) -> dict[str, Any]:
        """Create an item whose id is derived from an allocated number; on a real conflict take the next number."""
        number = self._next_number(diagram_id, type_, field)
        for _ in range(1000):
            item = make_item(number)
            if self._create_exact(item, diagram_id):
                return item
            number += 1
        raise StoreError(f"Could not allocate a {type_} number for '{diagram_id}'.")  # pragma: no cover

    def _execute(self, ops: list[tuple[Any, ...]], diagram_id: str) -> Any:
        """One transactional batch; every call sends fresh option dicts (see `_fresh`)."""
        return self._requests.execute_item_batch(_fresh(ops), partition_key=diagram_id)

    def _batch(self, diagram_id: str, ops: list[tuple[Any, ...]], what: str) -> None:
        """Run one transactional batch. A replay after a lost response finds its deletes gone (404);
        then the batch is retried without the ids that no longer exist (upserts are idempotent)."""
        for _ in range(2):
            try:
                _with_retry(partial(self._execute, ops, diagram_id), what)
                return
            except _batch_errors() as exc:
                if getattr(exc, "status_code", None) != 404:
                    raise StoreError(f"Cosmos DB {what} failed: {exc}") from exc
                wanted = [args[0] for op, args, *_ in ops if op == "delete"]
                existing = set(self._query(Q_IDS, diagram_id))
                ops = [(op, args, *rest) for op, args, *rest in ops if op != "delete" or args[0] in existing]
                if not any(op == "delete" for op, *_ in ops) and not any(op == "upsert" for op, *_ in ops):
                    return
                if all(i not in existing for i in wanted) and not any(op == "upsert" for op, *_ in ops):
                    return
        raise StoreError(f"Cosmos DB {what} failed: items vanished while retrying.")  # pragma: no cover

    def _delete_ids(self, diagram_id: str, ids: list[str]) -> None:
        for start in range(0, len(ids), BATCH_LIMIT):
            ops: list[tuple[Any, ...]] = [("delete", (item_id,)) for item_id in ids[start : start + BATCH_LIMIT]]
            self._batch(diagram_id, ops, "batch delete")

    # ---- diagrams ---------------------------------------------------------

    def list_diagrams(self) -> list[DiagramInfo]:
        items = self._query(Q_ALL_OF_TYPE, None, type=LATEST)
        out = [
            DiagramInfo(
                id=i["diagram_id"],
                title=i["title"],
                updated_at=i["updated_at"],
                version_count=len(self.list_versions(i["diagram_id"])),
            )
            for i in items
        ]
        return sorted(out, key=lambda d: d.id)

    def diagram_infos(self, diagram_ids: list[str]) -> list[DiagramInfo]:
        """Per diagram two single-partition queries (title and time of `latest`, the number of versions); never a
        cross-partition query and never the diagram itself (#181)."""
        out = []
        for diagram_id in sorted(set(diagram_ids)):
            found = self._query(Q_INFO, check_id(diagram_id), id=LATEST)
            if not found:
                continue
            count = self._query(Q_COUNT, diagram_id, type="version")
            title, updated_at = found[0].get("title", diagram_id), found[0].get("updated_at", "")
            out.append(DiagramInfo(id=diagram_id, title=title, updated_at=updated_at, version_count=count[0]))
        return out

    def _read_latest(self, diagram_id: str) -> dict[str, Any] | None:
        exceptions = _exceptions()
        try:
            return dict(_with_retry(lambda: self._requests.read_item(LATEST, partition_key=diagram_id), "read"))
        except exceptions.CosmosResourceNotFoundError:
            return None
        except exceptions.CosmosHttpResponseError as exc:
            raise StoreError(f"Cosmos DB read failed: {exc}") from exc

    def exists(self, diagram_id: str) -> bool:
        return self._read_latest(diagram_id) is not None

    def read_latest(self, diagram_id: str) -> tuple[Diagram, str]:
        item = self._read_latest(diagram_id)
        if item is None:
            raise StoreError(f"Diagram '{diagram_id}' not found.")
        return Diagram.model_validate(item["diagram"]), str(item.get("revision") or LEGACY_REVISION)

    def read_revision(self, diagram_id: str) -> str:
        """A single-partition projection query: the revision without transferring the diagram (#174, ADR-0122). An item
        stored without a revision projects to nothing; then the item's existence is checked."""
        found = self._query(Q_FIELD_OF.format(field="revision"), check_id(diagram_id), id=LATEST)
        if found and found[0]:
            return str(found[0])
        if self._query(Q_FIELD_OF.format(field="id"), diagram_id, id=LATEST):
            return LEGACY_REVISION
        raise StoreError(f"Diagram '{diagram_id}' not found.")

    @staticmethod
    def _latest_item(diagram: Diagram, revision: str) -> dict[str, Any]:
        return {
            "id": LATEST,
            "type": LATEST,
            "diagram_id": diagram.id,
            "title": diagram.title,
            "updated_at": diagram.updated_at,
            "revision": revision,
            "diagram": diagram.model_dump(mode="json"),
        }

    @staticmethod
    def _version_item(diagram_id: str, diagram: Diagram | dict[str, Any], meta: VersionInfo) -> dict[str, Any]:
        return {
            "id": f"version:{meta.version:04d}",
            "type": "version",
            "diagram_id": diagram_id,
            "version": meta.version,
            "meta": meta.model_dump(),
            "snapshot": diagram.model_dump(mode="json") if isinstance(diagram, Diagram) else diagram,
        }

    def _landed(self, diagram_id: str, item_id: str, marker: Callable[[dict[str, Any]], bool]) -> bool:
        """After a transient error: whether our write reached the server (the stored item carries our marker)."""
        stored = self._read(item_id, diagram_id)
        return stored is not None and marker(stored)

    def _run_batch(
        self, diagram_id: str, ops: list[tuple[Any, ...]], landed: Callable[[], bool], what: str
    ) -> int | None:
        """Run a transactional batch once, replaying it after transient errors unless it already landed.

        Returns None on success, else the status (404, 409, 412) of the operation that failed; other
        errors become StoreError. `landed` tells whether a write that hit a transient error went through.
        """
        ambiguous = False
        for attempt in range(1, TRANSIENT_ATTEMPTS + 1):
            try:
                self._execute(ops, diagram_id)
                return None
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if _is_transient(exc) and attempt < TRANSIENT_ATTEMPTS:
                    ambiguous = True
                    _backoff(attempt, what, exc)
                    if landed():
                        return None
                    continue
                if isinstance(exc, _batch_errors()) and status in (404, 409, 412):
                    return None if ambiguous and landed() else int(status)
                if isinstance(exc, _batch_errors()):
                    raise StoreError(f"Cosmos DB {what} failed: {exc}") from exc
                raise
        raise AssertionError("unreachable")  # pragma: no cover

    def create_latest(self, diagram: Diagram, info: VersionInfo) -> tuple[str, VersionInfo]:
        revision = new_token()
        meta = finalize_info(info, 1)
        ops: list[tuple[Any, ...]] = [
            ("create", (self._latest_item(diagram, revision),)),
            ("create", (self._version_item(diagram.id, diagram, meta),)),
        ]
        ours = partial(self._landed, diagram.id, LATEST, partial(_has_revision, revision=revision))
        if self._run_batch(diagram.id, ops, ours, "create") is not None:
            raise StoreError(f"Diagram '{diagram.id}' already exists.")
        return revision, meta

    def commit_latest(
        self, diagram: Diagram, expected: str | None, info: VersionInfo | None
    ) -> tuple[str, VersionInfo | None]:
        for _ in range(1000):
            current = self._read_latest(diagram.id)
            if current is None:
                raise ConflictError(f"Diagram '{diagram.id}' does not exist (any more).")
            if expected is not None and str(current.get("revision") or LEGACY_REVISION) != expected:
                raise ConflictError(f"Diagram '{diagram.id}' was changed by another request.")
            revision = new_token()
            ops: list[tuple[Any, ...]] = [
                ("replace", (LATEST, self._latest_item(diagram, revision)), {"if_match_etag": current["_etag"]})
            ]
            meta = None
            if info is not None:
                meta = finalize_info(info, self._next_number(diagram.id, "version", "version"))
                ops.append(("create", (self._version_item(diagram.id, diagram, meta),)))
            ours = partial(self._landed, diagram.id, LATEST, partial(_has_revision, revision=revision))
            status = self._run_batch(diagram.id, ops, ours, "commit")
            if status is None:
                return revision, meta
            # 409: the version number was taken; 412/404: latest changed or vanished. Both: read again,
            # which raises the conflict if the expected revision is gone.
        raise StoreError(f"Could not commit '{diagram.id}'.")  # pragma: no cover

    def write_latest(self, diagram: Diagram) -> str:
        revision = new_token()
        try:
            _with_retry(lambda: self._requests.upsert_item(self._latest_item(diagram, revision)), "upsert")
        except _exceptions().CosmosHttpResponseError as exc:
            raise StoreError(f"Cosmos DB write failed: {exc}") from exc
        return revision

    def purge(self, diagram_id: str, expected: str | None = None) -> None:
        # `latest` first and alone: from then on commits, appends (they read latest in their batch) and
        # generation writes fail, so the second query sees every item that can still appear.
        if expected is not None:
            self._delete_latest_at(diagram_id, expected)
            self._delete_ids(diagram_id, self._query(Q_IDS, diagram_id))
            return
        try:
            _with_retry(partial(self._requests.delete_item, LATEST, partition_key=diagram_id), "delete")
        except _exceptions().CosmosResourceNotFoundError:
            raise StoreError(f"Diagram '{diagram_id}' not found.") from None
        except _exceptions().CosmosHttpResponseError as exc:
            raise StoreError(f"Cosmos DB delete failed: {exc}") from exc
        self._delete_ids(diagram_id, self._query(Q_IDS, diagram_id))

    def _delete_latest_at(self, diagram_id: str, expected: str) -> None:
        """Delete `latest` if it is at revision `expected`: If-Match on the ETag of the revision read (#175)."""
        current = self._read_latest(diagram_id)
        if current is None:
            raise StoreError(f"Diagram '{diagram_id}' not found.")
        if str(current.get("revision") or LEGACY_REVISION) != expected:
            raise ConflictError(f"Diagram '{diagram_id}' was changed by another request.")

        def gone() -> bool:
            return self._read_latest(diagram_id) is None

        ops: list[tuple[Any, ...]] = [("delete", (LATEST,), {"if_match_etag": current["_etag"]})]
        if self._run_batch(diagram_id, ops, gone, "delete") is not None:  # 412/404: changed or gone meanwhile
            if gone():  # deleted by another request: "not found", like the file and SQLite stores
                raise StoreError(f"Diagram '{diagram_id}' not found.")
            raise ConflictError(f"Diagram '{diagram_id}' was changed by another request.")

    # ---- versions ---------------------------------------------------------

    def append_version(self, diagram_id: str, diagram: Diagram, info: VersionInfo) -> VersionInfo:
        snapshot = diagram.model_dump(mode="json")

        def make(number: int) -> dict[str, Any]:
            return self._version_item(diagram_id, snapshot, finalize_info(info, number))

        item = self._create_with_retry(diagram_id, "version", "version", make)
        return VersionInfo.model_validate(item["meta"])

    def list_versions(self, diagram_id: str) -> list[VersionInfo]:
        """A single-partition projection: the metadata, never the snapshots (#114, ADR-0129)."""
        items = self._query(Q_VERSION_META, diagram_id, type="version")
        return [VersionInfo.model_validate(i["meta"]) for i in items]

    def put_version(self, diagram_id: str, diagram: Diagram, info: VersionInfo) -> None:
        item = self._version_item(diagram_id, diagram, info)
        try:
            _with_retry(partial(self._requests.upsert_item, item), "upsert")
        except _exceptions().CosmosHttpResponseError as exc:
            raise StoreError(f"Cosmos DB write failed: {exc}") from exc

    def delete_versions(self, diagram_id: str, versions: list[int]) -> None:
        """Batches of deletes in the diagram's partition; a version that is gone already is skipped."""
        existing = set(self._query(Q_IDS_OF_TYPE, diagram_id, type="version"))
        ids = [item_id for n in versions if (item_id := f"version:{n:04d}") in existing]
        try:
            self._delete_ids(diagram_id, ids)
        except StoreError:  # a concurrent prune removed some of them: retry with what is left
            existing = set(self._query(Q_IDS_OF_TYPE, diagram_id, type="version"))
            self._delete_ids(diagram_id, [i for i in ids if i in existing])

    def load_version(self, diagram_id: str, version: int) -> tuple[VersionInfo, Diagram]:
        exceptions = _exceptions()
        try:
            item = _with_retry(
                lambda: self._requests.read_item(f"version:{version:04d}", partition_key=diagram_id), "read"
            )
        except exceptions.CosmosResourceNotFoundError:
            raise StoreError(f"Version {version} of diagram '{diagram_id}' not found.") from None
        except exceptions.CosmosHttpResponseError as exc:
            raise StoreError(f"Cosmos DB read failed: {exc}") from exc
        return VersionInfo.model_validate(item["meta"]), Diagram.model_validate(item["snapshot"])

    # ---- chat transcripts ---------------------------------------------------
    # Diagram transcripts live in the diagram's partition (anchor item `latest`), project transcripts in the
    # project's partition (anchor item `project`); both share this implementation (ADR-0057).

    def load_chat(self, diagram_id: str) -> list[dict[str, Any]]:
        return self._t_load(diagram_id)

    def append_chat(self, diagram_id: str, entries: list[dict[str, Any]], generation: str | None = None) -> None:
        self._t_append(diagram_id, LATEST, f"Diagram '{diagram_id}' not found.", entries, generation)

    def chat_generation(self, diagram_id: str) -> str:
        return self._t_generation(diagram_id, LATEST)

    def compact_chat(
        self, diagram_id: str, upto_seq: int, summary: dict[str, Any], generation: str | None = None
    ) -> bool:
        return self._t_compact(diagram_id, LATEST, upto_seq, summary, generation)

    def clear_chat(self, diagram_id: str, generation: str | None = None) -> None:
        self._t_clear(diagram_id, LATEST, f"Diagram '{diagram_id}' not found.", generation)

    def load_project_chat(self, project_id: str) -> list[dict[str, Any]]:
        return self._t_load(PROJECT_PREFIX + check_id(project_id))

    def append_project_chat(
        self, project_id: str, entries: list[dict[str, Any]], generation: str | None = None
    ) -> None:
        missing = f"Project '{project_id}' not found."
        self._t_append(PROJECT_PREFIX + check_id(project_id), PROJECT, missing, entries, generation)

    def project_chat_generation(self, project_id: str) -> str:
        return self._t_generation(PROJECT_PREFIX + check_id(project_id), PROJECT)

    def compact_project_chat(
        self, project_id: str, upto_seq: int, summary: dict[str, Any], generation: str | None = None
    ) -> bool:
        return self._t_compact(PROJECT_PREFIX + check_id(project_id), PROJECT, upto_seq, summary, generation)

    def clear_project_chat(self, project_id: str, generation: str | None = None) -> None:
        missing = f"Project '{project_id}' not found."
        self._t_clear(PROJECT_PREFIX + check_id(project_id), PROJECT, missing, generation)

    def _t_load(self, partition: str) -> list[dict[str, Any]]:
        """The current generation's entries: an item of another generation (left by a clear whose cleanup did not
        finish) is hidden, and so is an untagged one once a clear has made the generation `strict` (ADR-0127)."""
        items = self._query(Q_BY_TYPE.format(field="seq"), partition, type="chat")
        current = self._read(GENERATION, partition)
        if current is not None:
            value, strict = current.get("generation"), bool(current.get("strict"))
            items = [i for i in items if i.get("generation") == value or ("generation" not in i and not strict)]
        return hide_staged([{**i["entry"], "seq": i["seq"]} for i in items])

    def _generation_guard(self, partition: str, generation: str | None) -> list[tuple[Any, ...]]:
        """The batch operation that binds a write to the transcript generation (ADR-0127): a replace of the
        generation item with itself, conditional on its ETag; [] without a generation. ConflictError when the
        transcript has another generation (cleared, imported, or its owner deleted and created again)."""
        if generation is None:
            return []
        current = self._read(GENERATION, partition)
        if current is None or current.get("generation") != generation:
            raise ConflictError(f"The transcript of '{partition}' changed (generation).")
        return [("replace", (GENERATION, _body(current)), {"if_match_etag": current["_etag"]})]

    def _t_append(
        self, partition: str, anchor: str, missing: str, entries: list[dict[str, Any]], generation: str | None = None
    ) -> None:
        # Each entry is one batch: "read <anchor>" (`latest` or `project`; 404 once the owner is deleted, so a late
        # append never recreates a transcript), the guard of `_generation_guard` (a replace of the generation item
        # conditional on its ETag) and "create chat:NNNNNN" tagged with that generation (ADR-0127). Every append is
        # guarded, also one without a `generation`: it binds itself to the generation it reads, so no item is ever
        # written with a generation a clear has already replaced. A 412/404 on the generation item (another write
        # touched it) reads it again: the same value retries; another one is a ConflictError with a `generation`,
        # else the append goes into the new transcript.
        number = self._next_number(partition, "chat", "seq")
        for entry in strip_seq(entries):
            for _ in range(1000):
                tag = generation if generation is not None else self._t_generation(partition, anchor)
                if not tag:
                    raise StoreError(missing)
                try:
                    guard = self._generation_guard(partition, tag)
                except ConflictError:
                    if generation is not None:
                        raise
                    continue  # cleared between the two reads: append to the new generation
                item_id = f"chat:{number:06d}"
                item: dict[str, Any] = {
                    "id": item_id,
                    "type": "chat",
                    "diagram_id": partition,
                    "seq": number,
                    "entry": entry,
                    "generation": tag,
                }
                ops: list[tuple[Any, ...]] = [("read", (anchor,)), *guard, ("create", (item,))]
                ours = partial(self._landed, partition, item_id, partial(_same_item, item=item))
                status = self._run_batch(partition, ops, ours, "append")
                if status is None:
                    number += 1
                    break
                if status == 404 and self._read(anchor, partition) is None:
                    raise StoreError(missing)
                if status == 409:
                    number += 1  # the number was taken by a concurrent append: the next one
                # 412 or 404 on the generation item: read it again
            else:  # pragma: no cover
                raise StoreError(f"Could not allocate a chat number for '{partition}'.")

    def _read(self, item_id: str, partition: str) -> dict[str, Any] | None:
        exceptions = _exceptions()
        try:
            return dict(_with_retry(partial(self._requests.read_item, item_id, partition_key=partition), "read"))
        except exceptions.CosmosResourceNotFoundError:
            return None
        except exceptions.CosmosHttpResponseError as exc:
            raise StoreError(f"Cosmos DB read failed: {exc}") from exc

    def _generation_item(self, partition: str, strict: bool = False) -> dict[str, Any]:
        """A fresh generation item. `strict` (written by a clear or an import, after which every item is tagged):
        untagged items are hidden from then on; a lazily created first generation of an old transcript is not."""
        item = {"id": GENERATION, "type": GENERATION, "diagram_id": partition, "generation": new_token()}
        return {**item, "strict": True} if strict else item

    def _new_generation(self, partition: str) -> str:
        item = self._generation_item(partition, strict=True)
        try:
            _with_retry(partial(self._requests.upsert_item, item), "upsert")
        except _exceptions().CosmosHttpResponseError as exc:
            raise StoreError(f"Cosmos DB write failed: {exc}") from exc
        return str(item["generation"])

    def _t_generation(self, partition: str, anchor: str) -> str:
        for _ in range(3):
            item = self._read(GENERATION, partition)
            if item is not None:
                return str(item["generation"])
            if self._read(anchor, partition) is None:
                return ""
            # A transcript from before generations existed: the first reader creates one.
            self._create_exact(self._generation_item(partition), partition)
        raise StoreError(f"Could not read the chat generation of '{partition}'.")  # pragma: no cover

    def _t_compact(
        self, partition: str, owner: str, upto_seq: int, summary: dict[str, Any], generation: str | None
    ) -> bool:
        anchor = f"chat:{upto_seq:06d}"
        if generation is None:  # always guarded (ADR-0127): bound to the generation read now
            generation = self._t_generation(partition, owner)
            if not generation:
                return False
        # With a generation the fold carries a replace of the generation item conditional on its ETag: a
        # clear, import or delete in between changes or removes it, and the whole batch fails (412/404).
        guard: list[tuple[Any, ...]] = []
        current = self._read(GENERATION, partition)
        if generation is not None:
            if current is None or current.get("generation") != generation:
                return False
            guard = [("replace", (GENERATION, _body(current)), {"if_match_etag": current["_etag"]})]
        ids = [i for i in self._query(Q_IDS_UPTO, partition, type="chat", seq=upto_seq) if i != anchor]
        item = {
            "id": anchor,
            "type": "chat",
            "diagram_id": partition,
            "seq": upto_seq,
            "entry": strip_seq([summary])[0],
            "generation": generation if generation is not None else (current or {}).get("generation"),
        }
        # The summary *replaces* the item at upto_seq: if that item is gone (chat cleared or compacted
        # past it meanwhile) the replace fails with 404 and the whole fold is skipped.
        staged = len(guard) + len(ids) + 1 > BATCH_LIMIT
        # The common case is one atomic batch. Staged (more than a batch holds): the summary first (with
        # the generation guard), then the deletes; load_chat hides items below the newest summary, so a
        # failure in between leaves hidden leftovers that the next compact_chat/clear_chat removes.
        ops: list[tuple[Any, ...]] = [*guard, ("replace", (anchor, item))]
        if not staged:
            ops += [("delete", (item_id,)) for item_id in ids]
        for _ in range(2):
            try:
                _with_retry(partial(self._execute, ops, partition), "batch compact")
                break
            except _batch_errors() as exc:
                status = getattr(exc, "status_code", None)
                if status == 412:  # the generation changed: cleared, imported, or deleted and created again
                    return False
                if status != 404:
                    raise StoreError(f"Cosmos DB batch compact failed: {exc}") from exc
                # 404: the anchor or the generation item is gone (the fold is skipped), or a replay after
                # a lost response finds its deletes already done; then retry without the vanished ids.
                existing = set(self._query(Q_IDS, partition))
                if anchor not in existing or (guard and GENERATION not in existing):
                    return False
                ops = [(op, *rest) for op, *rest in ops if op != "delete" or rest[0][0] in existing]
        else:
            raise StoreError("Cosmos DB batch compact failed: items vanished while retrying.")  # pragma: no cover
        if staged:
            if generation is None:
                self._delete_ids(partition, ids)
            else:
                self._delete_guarded(partition, ids, generation)
        return True

    def _delete_guarded(self, partition: str, ids: list[str], generation: str) -> None:
        """The deletes of a staged fold, each batch guarded by the generation like the first one: after a
        clear or import, fresh entries may reuse these ids, so the cleanup stops instead (the leftovers of
        the old transcript are gone with the clear anyway)."""
        size = BATCH_LIMIT - 1
        for start in range(0, len(ids), size):
            chunk = ids[start : start + size]
            for _ in range(2):
                current = self._read(GENERATION, partition)
                if current is None or current.get("generation") != generation:
                    return
                guard = ("replace", (GENERATION, _body(current)), {"if_match_etag": current["_etag"]})
                ops: list[tuple[Any, ...]] = [guard, *(("delete", (item_id,)) for item_id in chunk)]
                try:
                    _with_retry(partial(self._execute, ops, partition), "batch delete")
                    break
                except _batch_errors() as exc:
                    status = getattr(exc, "status_code", None)
                    if status == 412:
                        return
                    if status != 404:
                        raise StoreError(f"Cosmos DB batch delete failed: {exc}") from exc
                    existing = set(self._query(Q_IDS, partition))  # a replay found some deletes done
                    chunk = [i for i in chunk if i in existing]
                    if not chunk:
                        break

    def _t_clear(self, partition: str, anchor: str, missing: str, generation: str | None = None) -> None:
        """A new generation first (a compaction or append that started before fails from now on), in one batch with a
        read of the owner, so a deleted owner gets none; with `generation` only while the transcript has it. Then the
        items written in the *old* generation (and untagged ones from before tags existed) are deleted, and only those
        (ADR-0127): entries appended in the new generation stay, and so does the transcript of an owner deleted and
        created again meanwhile (its items carry its own generation). An append in the new generation touches the
        generation item too; that is not a conflict, the clear just reads it again."""
        for _ in range(TRANSIENT_ATTEMPTS):
            if self._read(anchor, partition) is None:
                raise StoreError(missing)
            current = self._read(GENERATION, partition)
            if generation is not None and (current is None or current.get("generation") != generation):
                raise ConflictError(f"The transcript of '{partition}' changed (generation).")
            fresh = self._generation_item(partition, strict=True)
            if current is None:
                ops: list[tuple[Any, ...]] = [("read", (anchor,)), ("create", (fresh,))]
            else:
                ops = [("read", (anchor,)), ("replace", (GENERATION, fresh), {"if_match_etag": current["_etag"]})]
            landed = partial(self._landed, partition, GENERATION, partial(_has_generation, fresh["generation"]))
            status = self._run_batch(partition, ops, landed, "clear")
            if status is None:
                break
            if status == 404 and self._read(anchor, partition) is None:
                raise StoreError(missing)
            # 409/412/404 on the generation item: another write touched it; read it again
        else:  # pragma: no cover
            raise StoreError(f"Could not clear the transcript of '{partition}'.")
        self._sweep(partition, str(fresh["generation"]))

    def _sweep(self, partition: str, generation: str) -> None:
        """Delete every chat item not written in `generation` (ADR-0127), in batches guarded by the generation item:
        an append in this generation only bumps its ETag (read it again, go on); another generation (a newer clear,
        or the owner deleted and created again) stops the sweep, which then is that one's business. Until the sweep
        is done, `_t_load` hides the items of other generations."""
        items = self._query(Q_CHAT_GENERATIONS, partition, type="chat")
        ids = [i["id"] for i in items if i.get("generation") != generation]
        size = BATCH_LIMIT - 1
        for start in range(0, len(ids), size):
            chunk = ids[start : start + size]
            for _ in range(TRANSIENT_ATTEMPTS):
                current = self._read(GENERATION, partition)
                if current is None or current.get("generation") != generation:
                    return
                guard = ("replace", (GENERATION, _body(current)), {"if_match_etag": current["_etag"]})
                try:
                    _with_retry(partial(self._execute, [guard, *(("delete", (i,)) for i in chunk)], partition), "sweep")
                    break
                except _batch_errors() as exc:
                    status = getattr(exc, "status_code", None)
                    if status not in (404, 412):
                        raise StoreError(f"Cosmos DB batch delete failed: {exc}") from exc
                    if status == 404:  # some items are gone already (a replay): retry without them
                        existing = set(self._query(Q_IDS, partition))
                        chunk = [i for i in chunk if i in existing]
                        if not chunk:
                            break

    def _delete_chat_items(self, partition: str) -> None:
        ids = [i for i in self._query(Q_IDS, partition) if i.startswith("chat:")]
        self._delete_ids(partition, ids)

    def import_chat(self, partition: str, entries: list[dict[str, Any]]) -> None:
        if not self.exists(partition):
            raise StoreError(f"Diagram '{partition}' not found.")
        tag = self._new_generation(partition)
        self._delete_chat_items(partition)
        for e in entries:
            item = {
                "id": f"chat:{e['seq']:06d}",
                "type": "chat",
                "diagram_id": partition,
                "seq": e["seq"],
                "entry": strip_seq([e])[0],
                "generation": tag,
            }
            try:
                _with_retry(partial(self._requests.upsert_item, item), "upsert")
            except _exceptions().CosmosHttpResponseError as exc:
                raise StoreError(f"Cosmos DB write failed: {exc}") from exc

    # ---- projects ---------------------------------------------------------

    @staticmethod
    def _project_item(project: Project, revision: str) -> dict[str, Any]:
        return {
            "id": PROJECT,
            "type": PROJECT,
            "diagram_id": PROJECT_PREFIX + project.id,
            "revision": revision,
            "project": project.model_dump(mode="json"),
        }

    def list_projects(self) -> list[tuple[Project, str]]:
        items = self._query(Q_ALL_OF_TYPE, None, type=PROJECT)
        found = [(Project.model_validate(i["project"]), str(i["revision"])) for i in items]
        return sorted(found, key=lambda pair: pair[0].id)

    def read_project(self, project_id: str) -> tuple[Project, str]:
        item = self._read(PROJECT, PROJECT_PREFIX + project_id)
        if item is None:
            raise StoreError(f"Project '{project_id}' not found.")
        return Project.model_validate(item["project"]), str(item["revision"])

    def create_project(self, project: Project) -> str:
        check_id(project.id)
        partition = PROJECT_PREFIX + project.id
        if self._read(PROJECT, partition) is not None:
            raise StoreError(f"Project '{project.id}' already exists.")
        # The transcript of a deleted project whose cleanup failed is never inherited. The project is checked again
        # after the listing: if a concurrent create of the same id has won meanwhile, the listing may hold its fresh
        # entries, so nothing is deleted (and this create fails below). Each delete is conditional on the listed
        # ETag, so entries written after the listing survive as well.
        leftovers = self._transcript_items(partition)
        if leftovers and self._read(PROJECT, partition) is not None:
            raise StoreError(f"Project '{project.id}' already exists.")
        for item in leftovers:
            self._delete_if_unchanged(partition, item["id"], item["_etag"])
        revision = new_token()
        if not self._create_exact(self._project_item(project, revision), PROJECT_PREFIX + project.id):
            raise StoreError(f"Project '{project.id}' already exists.")
        return revision

    def _project_batch(self, project_id: str, expected: str, op: tuple[Any, ...], landed: Callable[[], bool]) -> None:
        """One conditional replace or delete of a project item: If-Match on the ETag of the revision read."""
        partition = PROJECT_PREFIX + project_id
        current = self._read(PROJECT, partition)
        if current is None:
            raise ConflictError(f"Project '{project_id}' does not exist (any more).")
        if str(current.get("revision")) != expected:
            raise ConflictError(f"Project '{project_id}' was changed by another request.")
        ops: list[tuple[Any, ...]] = [(*op, {"if_match_etag": current["_etag"]})]
        if self._run_batch(partition, ops, landed, f"project {op[0]}") is not None:  # 412/404: changed or gone
            raise ConflictError(f"Project '{project_id}' was changed by another request.")

    def commit_project(self, project: Project, expected: str) -> str:
        revision = new_token()
        partition = PROJECT_PREFIX + project.id
        ours = partial(self._landed, partition, PROJECT, partial(_has_revision, revision=revision))
        self._project_batch(project.id, expected, ("replace", (PROJECT, self._project_item(project, revision))), ours)
        return revision

    def delete_project(self, project_id: str, expected: str) -> None:
        partition = PROJECT_PREFIX + project_id

        def gone() -> bool:
            return self._read(PROJECT, partition) is None

        self._project_batch(project_id, expected, ("delete", (PROJECT,)), gone)
        # The transcript after the project item (appends read it in their batch, so none can follow); a failure
        # in between leaves items that the next create_project of this id removes. A project created again under
        # this id meanwhile is never touched: its item is not listed, its create already removed the leftovers,
        # and every delete is conditional on the ETag listed here, so its new items that reuse an id survive.
        leftovers = self._transcript_items(partition)
        if leftovers and self._read(PROJECT, partition) is None:
            for item in leftovers:
                self._delete_if_unchanged(partition, item["id"], item["_etag"])

    def _transcript_items(self, partition: str) -> list[dict[str, Any]]:
        """The chat and chat-generation items of a project partition, with their ETags."""
        return [item for kind in ("chat", GENERATION) for item in self._query(Q_ALL_OF_TYPE, partition, type=kind)]

    def _delete_if_unchanged(self, partition: str, item_id: str, etag: str) -> None:
        """Delete the item if it still has `etag`; an item written again meanwhile (412) or gone (404) is left."""

        def landed() -> bool:
            stored = self._read(item_id, partition)
            return stored is None or stored.get("_etag") != etag

        self._run_batch(partition, [("delete", (item_id,), {"if_match_etag": etag})], landed, "cleanup")

    # ---- keyed documents (workspaces, users) ---------------------------------

    @staticmethod
    def _document_item(kind: str, key: str, body: dict[str, Any], revision: str) -> dict[str, Any]:
        return {"id": kind, "type": kind, "diagram_id": f"{kind}:{key}", "revision": revision, "document": body}

    def list_documents(self, kind: str) -> list[tuple[str, dict[str, Any], str]]:
        check_document(kind, "probe")
        prefix = f"{kind}:"
        items = sorted(self._query(Q_ALL_OF_TYPE, None, type=kind), key=lambda i: str(i["diagram_id"]))
        return [(str(i["diagram_id"])[len(prefix) :], dict(i["document"]), str(i["revision"])) for i in items]

    def read_document(self, kind: str, key: str) -> tuple[dict[str, Any], str]:
        item = self._read(kind, f"{kind}:{check_document(kind, key)}")
        if item is None:
            raise missing_document(kind, key)
        return dict(item["document"]), str(item["revision"])

    def create_document(self, kind: str, key: str, body: dict[str, Any]) -> str:
        partition = f"{kind}:{check_document(kind, key)}"
        revision = new_token()
        if not self._create_exact(self._document_item(kind, key, body, revision), partition):
            raise StoreError(f"{kind.capitalize()} '{key}' already exists.")
        return revision

    def _document_batch(
        self, kind: str, key: str, expected: str, op: tuple[Any, ...], landed: Callable[[], bool]
    ) -> None:
        """One conditional replace or delete of a keyed document: If-Match on the ETag of the revision read."""
        partition = f"{kind}:{check_document(kind, key)}"
        current = self._read(kind, partition)
        name = kind.capitalize()
        if current is None:
            raise ConflictError(f"{name} '{key}' does not exist (any more).")
        if str(current.get("revision")) != expected:
            raise ConflictError(f"{name} '{key}' was changed by another request.")
        ops: list[tuple[Any, ...]] = [(*op, {"if_match_etag": current["_etag"]})]
        if self._run_batch(partition, ops, landed, f"{kind} {op[0]}") is not None:  # 412/404: changed or gone
            raise ConflictError(f"{name} '{key}' was changed by another request.")

    def commit_document(self, kind: str, key: str, body: dict[str, Any], expected: str) -> str:
        revision = new_token()
        partition = f"{kind}:{check_document(kind, key)}"
        ours = partial(self._landed, partition, kind, partial(_has_revision, revision=revision))
        item = self._document_item(kind, key, body, revision)
        self._document_batch(kind, key, expected, ("replace", (kind, item)), ours)
        return revision

    def delete_document(self, kind: str, key: str, expected: str) -> None:
        partition = f"{kind}:{check_document(kind, key)}"

        def gone() -> bool:
            return self._read(kind, partition) is None

        self._document_batch(kind, key, expected, ("delete", (kind,)), gone)

    # ---- presence shared by the replicas (ADR-0104) --------------------------

    def presence_channel(self) -> CosmosStore:
        return self

    def write_presence(self, project: str, replica: str, body: dict[str, Any], ttl: float) -> None:
        """Upsert this replica's item of the project; Cosmos removes it `ttl` seconds after the last write (when the
        container has TTL enabled; readers ignore expired items anyway)."""
        partition = PRESENCE_PREFIX + check_id(project)
        item = {
            "id": check_id(replica),
            "type": PRESENCE,
            "diagram_id": partition,
            "ttl": max(1, math.ceil(ttl)),
            "presence": body,
        }
        try:
            _with_retry(lambda: self._requests.upsert_item(item), "presence write")
        except _exceptions().CosmosHttpResponseError as exc:
            raise StoreError(f"Cosmos DB presence write failed: {exc}") from exc

    def read_presence(self, project: str) -> list[dict[str, Any]]:
        """One query in the project's presence partition. Items whose TTL ran out long ago (a container without TTL)
        are deleted on the way, best effort."""
        partition = PRESENCE_PREFIX + check_id(project)
        now = time.time()
        bodies = []
        for item in self._query(Q_ALL_OF_TYPE, partition, type=PRESENCE):
            body = item.get("presence")
            if not isinstance(body, dict):
                continue
            expires = body.get("expires")
            expires = float(expires) if isinstance(expires, int | float) else float("-inf")
            if expires >= now:
                bodies.append(dict(body))
            elif expires + float(item.get("ttl") or 0) < now:
                self._collect_presence(partition, item)
        return sorted(bodies, key=lambda b: str(b.get("replica", "")))

    def _collect_presence(self, partition: str, item: dict[str, Any]) -> None:
        """Delete an item that expired long ago, unless it was written again meanwhile (If-Match on its ETag)."""
        try:
            self._requests.delete_item(
                item["id"], partition_key=partition, etag=item.get("_etag"), match_condition=_if_not_modified()
            )
        except Exception:
            logger.debug("Leftover presence item %s/%s not collected", partition, item.get("id"), exc_info=True)

    def delete_presence(self, project: str, replica: str) -> None:
        partition = PRESENCE_PREFIX + check_id(project)
        exceptions = _exceptions()
        try:
            _with_retry(
                lambda: self._requests.delete_item(check_id(replica), partition_key=partition), "presence delete"
            )
        except exceptions.CosmosResourceNotFoundError:
            return
        except exceptions.CosmosHttpResponseError as exc:
            if exc.status_code == 404:
                return
            raise StoreError(f"Cosmos DB presence delete failed: {exc}") from exc


def _if_not_modified() -> Any:
    from azure.core import MatchConditions

    return MatchConditions.IfNotModified
