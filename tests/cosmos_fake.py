"""In-process stand-in for the subset of ``azure.cosmos.ContainerProxy`` that ``CosmosStore`` uses.

Items live in a dict keyed by (partition key, id). ``query_items`` understands exactly the query
shapes ``arcviz.store.cosmos`` issues (see ``_parse``), so a changed query in the backend fails here
loudly instead of silently returning nothing. Like the service, every write gives the item a new
``_etag``; ``replace_item(etag=..., match_condition=IfNotModified)`` and batch operations with
``{"if_match_etag": ...}`` fail with 412 when it changed.
"""

from __future__ import annotations

import copy
import re
import threading
import uuid
from collections.abc import Iterable
from typing import Any

from azure.core import MatchConditions
from azure.core.exceptions import ServiceResponseError
from azure.cosmos import exceptions

_QUERY = re.compile(
    r"^SELECT (?P<proj>\*|VALUE c\.(?P<field>\w+)|VALUE MAX\(c\.(?P<maxf>\w+)\)|(?P<count>VALUE COUNT\(1\))"
    r"|(?P<fields>c\.\w+(?:, c\.\w+)+)) FROM c"
    r"(?: WHERE (?P<where>.+?))?(?: ORDER BY c\.(?P<order>\w+))?$"
)
_COND = re.compile(r"^c\.(?P<field>\w+) (?P<op>=|<=) @(?P<param>\w+)$")


def _http_error(
    cls: type[exceptions.CosmosHttpResponseError], status: int, msg: str
) -> exceptions.CosmosHttpResponseError:
    err = cls(message=msg)
    err.status_code = status
    return err


def _stored(body: dict[str, Any]) -> dict[str, Any]:
    """`body` as the service stores it: system properties replaced, a fresh `_etag`."""
    item = {k: copy.deepcopy(v) for k, v in body.items() if not k.startswith("_")}
    item["_etag"] = f'"{uuid.uuid4().hex}"'
    return item


def _precondition_failed(item_id: str) -> exceptions.CosmosHttpResponseError:
    return _http_error(exceptions.CosmosAccessConditionFailedError, 412, f"{item_id}: precondition failed")


class FakeContainer:
    def __init__(self, partition_key_path: str = "/diagram_id"):
        self.pk_field = partition_key_path.lstrip("/")
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.queries: list[str] = []
        self.cross_partition: list[str] = []  # the queries that ran across partitions (cost on every partition)
        self.batches: list[list[tuple[Any, ...]]] = []
        self.fail_batches_after: int | None = None  # raise on the Nth batch (test hook)
        self.transient_failures = 0  # test hook: the next N point writes raise a 503 before succeeding
        self.lose_next_create_response = False  # test hook: the write lands, but the client sees a connection error
        self.lose_next_batch_response = False
        self.lose_next_replace_response = False
        self.transient_batch_failures = 0  # test hook: the next N batches raise a 503 before applying anything
        self.before_write: Any = None  # test hook: called (without the lock) before every write

    # ---- point operations -------------------------------------------------

    def read_item(self, item: str, partition_key: str, **_: Any) -> dict[str, Any]:
        with self.lock:
            try:
                return copy.deepcopy(self.items[(partition_key, item)])
            except KeyError:
                raise _http_error(exceptions.CosmosResourceNotFoundError, 404, f"{item} not found") from None

    def _maybe_transient(self) -> None:
        with self.lock:
            if self.transient_failures > 0:
                self.transient_failures -= 1
                raise _http_error(exceptions.CosmosHttpResponseError, 503, "service unavailable (injected)")

    def _hook(self) -> None:
        if self.before_write is not None:
            self.before_write()

    def create_item(self, body: dict[str, Any], **_: Any) -> dict[str, Any]:
        self._hook()
        self._maybe_transient()
        key = (body[self.pk_field], body["id"])
        with self.lock:
            if key in self.items:
                raise _http_error(exceptions.CosmosResourceExistsError, 409, f"{body['id']} exists")
            self.items[key] = stored = _stored(body)
            if self.lose_next_create_response:
                self.lose_next_create_response = False
                raise ServiceResponseError("connection reset after the write (injected)")
        return copy.deepcopy(stored)

    def upsert_item(self, body: dict[str, Any], **_: Any) -> dict[str, Any]:
        self._hook()
        with self.lock:
            self.items[(body[self.pk_field], body["id"])] = stored = _stored(body)
        return copy.deepcopy(stored)

    def replace_item(
        self,
        item: str,
        body: dict[str, Any],
        etag: str | None = None,
        match_condition: MatchConditions | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        self._hook()
        self._maybe_transient()
        with self.lock:
            key = (body[self.pk_field], item)
            if key not in self.items:
                raise _http_error(exceptions.CosmosResourceNotFoundError, 404, f"{item} not found")
            if match_condition == MatchConditions.IfNotModified and self.items[key]["_etag"] != etag:
                raise _precondition_failed(item)
            self.items[key] = stored = _stored(body)
            if self.lose_next_replace_response:
                self.lose_next_replace_response = False
                raise ServiceResponseError("connection reset after the replace (injected)")
        return copy.deepcopy(stored)

    def delete_item(
        self,
        item: str,
        partition_key: str,
        etag: str | None = None,
        match_condition: MatchConditions | None = None,
        **_: Any,
    ) -> None:
        self._hook()
        with self.lock:
            if (partition_key, item) not in self.items:
                raise _http_error(exceptions.CosmosResourceNotFoundError, 404, f"{item} not found")
            if match_condition == MatchConditions.IfNotModified and self.items[(partition_key, item)]["_etag"] != etag:
                raise _precondition_failed(item)
            del self.items[(partition_key, item)]

    def execute_item_batch(
        self, batch_operations: Iterable[tuple[Any, ...]], partition_key: str, **_: Any
    ) -> list[Any]:
        self._hook()
        # Like the SDK, which pops the options out of the caller's dicts while formatting the request,
        # before anything is sent: a caller that replays the same list would lose its conditions.
        ops = [
            (op, args, {k: rest[0].pop(k) for k in list(rest[0])}) if rest else (op, args)
            for op, args, *rest in batch_operations
        ]
        if len(ops) > 100:
            raise _http_error(exceptions.CosmosHttpResponseError, 400, "batch too large")
        self.batches.append(ops)
        if self.fail_batches_after is not None and len(self.batches) > self.fail_batches_after:
            raise _http_error(exceptions.CosmosHttpResponseError, 503, "injected failure")
        with self.lock:
            transient = self.transient_batch_failures > 0
            self.transient_batch_failures -= 1 if transient else 0
        if transient:
            raise _http_error(exceptions.CosmosHttpResponseError, 503, "service unavailable (injected)")
        results = []
        with self.lock:
            snapshot = dict(self.items)
            index = 0  # of the operation being applied; reported when the batch fails
            try:
                for op, args, *rest in ops:
                    options = dict(rest[0]) if rest else {}
                    if_match = options.pop("if_match_etag", None)
                    if options:  # pragma: no cover - an option the fake does not model
                        raise NotImplementedError(options)
                    item_id = args[0]["id"] if op in ("create", "upsert") else args[0]
                    key = (partition_key, item_id)
                    if op in ("delete", "replace", "read") and key not in self.items:
                        raise _http_error(exceptions.CosmosResourceNotFoundError, 404, f"{item_id} not found")
                    if if_match is not None and (key not in self.items or self.items[key]["_etag"] != if_match):
                        raise _precondition_failed(item_id)
                    if op == "read":
                        pass
                    elif op == "delete":
                        del self.items[key]
                    elif op == "create":
                        if key in self.items:
                            raise _http_error(exceptions.CosmosResourceExistsError, 409, f"{item_id} exists")
                        self.items[key] = _stored(args[0])
                    elif op == "upsert":
                        self.items[key] = _stored(args[0])
                    elif op == "replace":
                        self.items[key] = _stored(args[1])
                    else:  # pragma: no cover
                        raise NotImplementedError(op)
                    stored = self.items.get(key)
                    results.append({"statusCode": 200, **({"eTag": stored["_etag"]} if stored else {})})
                    index += 1
            except exceptions.CosmosHttpResponseError as exc:
                self.items = snapshot  # transactional: all or nothing
                # Like the SDK: a failed batch surfaces as CosmosBatchOperationError (a plain HttpResponseError,
                # *not* a CosmosHttpResponseError) carrying the failing operation's status code and index.
                raise exceptions.CosmosBatchOperationError(
                    error_index=index, headers={}, status_code=exc.status_code, message=str(exc)
                ) from None
            if self.lose_next_batch_response:
                self.lose_next_batch_response = False
                raise ServiceResponseError("connection reset after the batch (injected)")
        return results

    # ---- queries ----------------------------------------------------------

    def query_items(
        self,
        query: str,
        parameters: list[dict[str, Any]] | None = None,
        partition_key: str | None = None,
        enable_cross_partition_query: bool | None = None,
        **_: Any,
    ) -> Iterable[Any]:
        self.queries.append(query)
        if partition_key is None and not enable_cross_partition_query:
            raise _http_error(exceptions.CosmosHttpResponseError, 400, "cross partition query without flag")
        if partition_key is None:
            self.cross_partition.append(query)
        m = _QUERY.match(query.strip())
        if not m:
            raise NotImplementedError(f"fake container does not understand: {query}")
        params = {p["name"]: p["value"] for p in parameters or []}
        conds = []
        for raw in (m.group("where") or "").split(" AND "):
            if not raw:
                continue
            c = _COND.match(raw.strip())
            if not c:
                raise NotImplementedError(f"fake container does not understand condition: {raw}")
            conds.append((c.group("field"), c.group("op"), params["@" + c.group("param")]))
        with self.lock:
            rows = [
                copy.deepcopy(v) for (pk, _), v in self.items.items() if (partition_key is None or pk == partition_key)
            ]
        for field, op, value in conds:
            rows = [r for r in rows if (r.get(field) == value if op == "=" else r.get(field) <= value)]
        if m.group("order"):
            rows.sort(key=lambda r: r[m.group("order")])
        if m.group("maxf"):
            values = [r[m.group("maxf")] for r in rows if m.group("maxf") in r]
            return [max(values)] if values else []
        if m.group("count"):
            return [len(rows)]
        if m.group("fields"):  # like the service, a projection leaves out the fields an item does not have
            names = [f.strip()[2:] for f in m.group("fields").split(",")]
            return [{n: r[n] for n in names if n in r} for r in rows]
        if m.group("field"):  # like the service, `SELECT VALUE c.x` leaves out items without `x`
            return [r[m.group("field")] for r in rows if m.group("field") in r]
        return rows
