"""Persistence: the DiagramRepository contract, its backends and the factory.

Spec: docs/architecture/specs/persistence.md.
"""

from .base import (
    MUTATION_ATTEMPTS,
    Change,
    ConflictError,
    DiagramInfo,
    DiagramRepository,
    RevisionMismatch,
    StoreError,
    Updated,
    VersionInfo,
    fingerprint,
)
from .factory import create_store, store_from_spec
from .file import FileStore

DiagramStore = FileStore  # backwards-compatible alias, removed after one release

__all__ = [
    "MUTATION_ATTEMPTS",
    "Change",
    "ConflictError",
    "Updated",
    "DiagramInfo",
    "DiagramRepository",
    "DiagramStore",
    "FileStore",
    "RevisionMismatch",
    "StoreError",
    "VersionInfo",
    "create_store",
    "fingerprint",
    "store_from_spec",
]
