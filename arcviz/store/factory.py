"""Build the configured repository from environment variables (the only place store config is read)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..history import HistoryConfig
from .base import DiagramRepository, StoreError
from .modelbacked import ModelBackedStore

if TYPE_CHECKING:
    from ..gitmirror import GitMirror

BACKENDS = ("file", "sqlite", "cosmos")
SQLITE_FILENAME = "arcviz.sqlite3"


def create_store(env: dict[str, str] | None = None, backend: Any = None) -> DiagramRepository:
    """The configured backend, with the optional Git mirror (ARCVIZ_GIT_DIR) attached, as a `ModelBackedStore`: diagrams
    are views of their project's model (spec: model-first.md, ADR-0213). With `backend` (tests, tools) that backend is
    wrapped instead, keeping its mirror and retention."""
    if backend is not None:
        if isinstance(backend, ModelBackedStore):
            return backend
        wrapped = ModelBackedStore(backend)  # type: ignore[abstract]  # its primitives are delegated (modelbacked.py)
        wrapped.mirror, wrapped.retention = backend.mirror, backend.retention
        backend.mirror = None
        return wrapped
    env = dict(os.environ) if env is None else env
    kind = env.get("ARCVIZ_STORE", "file").strip().lower()
    data_dir = Path(env.get("ARCVIZ_DATA_DIR", "data"))
    if kind == "file":
        store = _file(data_dir)
    elif kind == "sqlite":
        store = _sqlite(data_dir)
    elif kind == "cosmos":
        url = env.get("ARCVIZ_STORE_URL")
        if not url:
            raise StoreError("ARCVIZ_STORE=cosmos needs ARCVIZ_STORE_URL (the account endpoint).")
        store = _cosmos(url, env)
    else:
        raise StoreError(f"Unknown ARCVIZ_STORE '{kind}'. Valid values: {', '.join(BACKENDS)}.")
    wrapped = ModelBackedStore(store)  # type: ignore[abstract]
    wrapped.mirror = _mirror(env)
    wrapped.retention = HistoryConfig.from_env(env)  # ConfigError for invalid values (ADR-0130)
    return wrapped


def _mirror(env: dict[str, str]) -> GitMirror | None:
    from ..gitmirror import GitMirror, GitMirrorConfig

    config = GitMirrorConfig.from_env(env)
    return GitMirror(config) if config else None


def parse_store_spec(spec: str) -> tuple[str, str]:
    """`file:<dir>` | `sqlite:<dir-or-file>` | `cosmos:<endpoint>` -> (backend, location)."""
    backend, sep, location = spec.partition(":")
    backend = backend.strip().lower()
    if not sep or backend not in BACKENDS or not location:
        raise StoreError(f"Invalid store spec '{spec}'. Use file:<dir>, sqlite:<dir-or-file> or cosmos:<endpoint>.")
    return backend, location


def store_from_spec(spec: str, env: dict[str, str] | None = None) -> DiagramRepository:
    env = dict(os.environ) if env is None else env
    backend, location = parse_store_spec(spec)
    if backend == "file":
        return _file(Path(location))
    if backend == "sqlite":
        return _sqlite(Path(location))
    return _cosmos(location, env)


def _file(root: Path) -> DiagramRepository:
    from .file import FileStore

    return FileStore(root)


def _sqlite(location: Path) -> DiagramRepository:
    from .sqlite import SqliteStore

    path = location if location.suffix == ".sqlite3" else location / SQLITE_FILENAME
    return SqliteStore(path)


def _cosmos(url: str, env: dict[str, str]) -> DiagramRepository:
    from .cosmos import CosmosStore, connect

    container = connect(
        url,
        key=env.get("ARCVIZ_STORE_KEY") or None,
        database=env.get("ARCVIZ_STORE_DATABASE", "arcviz"),
        container=env.get("ARCVIZ_STORE_CONTAINER", "diagrams"),
    )
    return CosmosStore(container)
