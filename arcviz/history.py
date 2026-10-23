"""The version history as metadata: undo/redo targets computed from one listing, the page a response carries, and
the opt-in retention policy (#114, spec: docs/architecture/specs/hardening.md, ADR-0129, ADR-0130).

Pure functions over `VersionInfo` lists (ascending by version, gaps allowed); no store access.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .envconfig import Env
from .ports import DEFAULT_VERSIONS_PAGE, MAX_VERSIONS_PAGE, History, VersionInfo

DEFAULT_SLACK = 20  # versions beyond `keep` before a prune runs (ARCVIZ_VERSIONS_PRUNE_SLACK)
MIN_KEEP = 10  # the smallest ARCVIZ_VERSIONS_KEEP other than 0
REPLAYED = ("undo", "redo")  # sources the redo replay acts on


@dataclass(frozen=True)
class HistoryConfig:
    """`page`: versions per response; `keep`: versions retained per diagram (0 = all, the default); `slack`: how far
    the history may grow beyond `keep` before a prune; `prune_manual`: manual snapshots may be pruned too."""

    page: int = DEFAULT_VERSIONS_PAGE
    keep: int = 0
    slack: int = DEFAULT_SLACK
    prune_manual: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> HistoryConfig:
        env = Env(environ)
        keep = env.integer("ARCVIZ_VERSIONS_KEEP", 0, minimum=0)
        if 0 < keep < MIN_KEEP:
            env.problem(f"ARCVIZ_VERSIONS_KEEP must be 0 (keep all) or at least {MIN_KEEP}, got {keep}.")
        config = cls(
            page=env.integer("ARCVIZ_VERSIONS_PAGE", DEFAULT_VERSIONS_PAGE, minimum=1, maximum=MAX_VERSIONS_PAGE),
            keep=keep,
            slack=env.integer("ARCVIZ_VERSIONS_PRUNE_SLACK", DEFAULT_SLACK, minimum=0),
            prune_manual=env.flag("ARCVIZ_VERSIONS_PRUNE_MANUAL", False),
        )
        env.check()
        return config


def _replay(versions: list[VersionInfo]) -> tuple[list[int], int]:
    """The redo stack after replaying the history, and the index where it was last cleared (versions from there on
    decide it). An undo pushes the version current before it, a redo pops, manual snapshots and versions that did
    not change the content are transparent, anything else clears the stack."""
    stack: list[int] = []
    current: int | None = None
    current_fp: str | None = None
    cleared = 0
    for index, v in enumerate(versions):
        if v.source == "undo":
            if current is not None:
                stack.append(current)
            current = v.restored_from
        elif v.source == "redo":
            if stack:
                stack.pop()
            current = v.restored_from
        elif v.source == "manual" or (v.fingerprint and v.fingerprint == current_fp):
            continue
        else:
            stack = []
            current = v.restored_from or v.version
            cleared = index
        current_fp = v.fingerprint
    return stack, cleared


def redo_target_of(versions: list[VersionInfo]) -> int | None:
    """See `DiagramRepository.redo_target`."""
    stack, _ = _replay(versions)
    return stack[-1] if stack else None


def _undo_cursor(versions: list[VersionInfo]) -> VersionInfo | None:
    if len(versions) < 2:
        return None
    by_number = {v.version: v for v in versions}
    latest = versions[-1]
    cursor = latest
    for v in reversed(versions):
        if v.fingerprint != latest.fingerprint:
            break
        cursor = v
        if v.restored_from:
            cursor = by_number.get(v.restored_from, v)
            break
    return cursor


def undo_target_of(versions: list[VersionInfo]) -> int | None:
    """See `DiagramRepository.undo_target`."""
    cursor = _undo_cursor(versions)
    if cursor is None:
        return None
    by_number = {v.version: v for v in versions}
    for k in range(cursor.version - 1, 0, -1):
        candidate = by_number.get(k)
        if candidate is not None and candidate.fingerprint != cursor.fingerprint:
            return k
    return None


def prune_due(latest: int, config: HistoryConfig | None) -> bool:
    """Whether a write that created version `latest` should look for versions to prune (ADR-0130): from the number
    alone, no listing; once the history can have outgrown `keep + slack`, every `slack` versions (every version with
    slack 0), so a commit costs no extra read in between."""
    if config is None or config.keep <= 0:
        return False
    beyond = latest - config.keep - config.slack
    return beyond > 0 and beyond % max(config.slack, 1) == 0


def history_of(versions: list[VersionInfo]) -> History:
    return History(versions, undo_target_of(versions), redo_target_of(versions))


def prunable(versions: list[VersionInfo], config: HistoryConfig) -> list[int]:
    """The version numbers a prune removes (ADR-0130): nothing unless `keep` is set and the history has grown beyond
    `keep + slack`; then everything older than the newest `keep + slack` (a grace: a version is pruned only once it
    was already beyond the window at an earlier prune, so an undo, restore or comparison picked from a list a moment
    ago does not lose its target), except the first and the latest version, every
    version from where the redo stack was last cleared on, the undo target and its cursor, every version the redo
    stack or a kept version's `restored_from` refers to, and manual snapshots unless `prune_manual`."""
    if config.keep <= 0 or len(versions) <= config.keep + config.slack:
        return []
    kept = {v.version for v in versions[-(config.keep + config.slack) :]}
    kept |= {versions[0].version, versions[-1].version}
    stack, cleared = _replay(versions)
    # the replay from where the stack was last cleared: that version and every undo/redo after it (manual snapshots
    # and no-op versions are transparent to it)
    kept |= {v.version for v in versions[cleared:] if v.version == versions[cleared].version or v.source in REPLAYED}
    kept |= set(stack)
    undo = undo_target_of(versions)
    cursor = _undo_cursor(versions)
    kept |= {n for n in (undo, cursor.version if cursor else None) if n is not None}
    kept |= {v.restored_from for v in versions if v.version in kept and v.restored_from}
    if not config.prune_manual:
        kept |= {v.version for v in versions if v.source == "manual"}
    return [v.version for v in versions if v.version not in kept]
