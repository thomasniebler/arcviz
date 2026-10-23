"""Stored proposals of agents: the domain types and bounds (spec: docs/architecture/specs/agent-access.md, ADR-0172).

An agent's proposal outlives the call that made it: it is kept in the keyed document `proposal` of its project until a
person applies or discards it. This module holds the stored shape, the bounds that keep the project's document far below
Cosmos DB's 2 MB item limit, and the retention sweep; reading, writing and the access checks are
`services/proposals.py`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

from . import sequence
from .envconfig import Env
from .model import Diagram

DOCUMENT = "proposal"  # kind of the keyed document, one per project (key = the project id)
STATUSES = ("pending", "applying", "applied", "discarded", "stale")
CLAIM_TTL_SECONDS = (
    300  # an `applying` claim older than this belongs to a process that is gone: another may take it over
)
LIVE = ("pending", "applying")  # not decided yet: never swept, and counted against MAX_PENDING
DEFAULT_RETENTION_DAYS = 30
MAX_OPERATIONS = 50  # per proposal
MAX_RATIONALE = 2000
MAX_BYTES = 16 * 1024  # of one proposal's operations and diff, serialised
MAX_PENDING = 30  # per project
KEEP_DECIDED = 20  # the newest decided proposals a project keeps: 50 proposals at the bounds stay under ~1 MB

# Project proposals: items that are project operations (spec: architecture-objects-over-mcp.md, ADR-0187).
KINDS = ("diagram", "project")
MAX_ITEMS = 60  # per project proposal
MAX_PROJECT_PROPOSAL_BYTES = 64 * 1024  # of one project proposal's items, serialised
MAX_PENDING_PROJECT = 5  # pending project proposals per project
ITEM_STATES = ("pending", "applied", "discarded", "stale", "failed")
PROPOSABLE_OPERATIONS = (
    "add_driver",
    "update_driver",
    "add_scenario",
    "update_scenario",
    "add_decision",
    "update_decision",
    "link_requirements",
)
# What a proposal (made by an agent or by the derivation) may not set: only a person decides a decision's fate.
AGENT_REFUSED_FIELDS = {
    "add_decision": ("status", "superseded_by"),
    "update_decision": ("status", "superseded_by"),
}
KEY_RE = re.compile(r"^[a-z0-9_]{1,24}$")
MAX_TITLE = 160


class ProposalItem(BaseModel):
    """One project operation of a project proposal; a person applies it alone or with others."""

    key: str  # local label, KEY_RE, unique within the proposal; later items refer to it as "@<key>"
    op: str  # one of PROPOSABLE_OPERATIONS
    arguments: dict[str, Any] = Field(default_factory=dict)  # emptied once the proposal is decided
    title: str = ""  # for lists, ≤ MAX_TITLE
    derived_from: list[str] = Field(default_factory=list)  # requirement ids it came from (traceability)
    base: str | None = None  # update_*: fingerprint of the target when the item was made
    state: str = "pending"  # ITEM_STATES
    created: str | None = None  # the id an applied add_* produced
    error: str | None = None  # why a failed or stale item was not applied


def references(value: Any) -> set[str]:
    """The keys a value refers to as `"@<key>"`, anywhere in nested lists and objects."""
    if isinstance(value, str):
        return {value[1:]} if value.startswith("@") and len(value) > 1 else set()
    if isinstance(value, list):
        return set().union(*(references(v) for v in value)) if value else set()
    if isinstance(value, dict):
        return set().union(*(references(v) for v in value.values())) if value else set()
    return set()


def resolve(value: Any, ids: Mapping[str, str]) -> Any:
    """`value` with every `"@<key>"` replaced by the id `ids` holds for it (KeyError for an unknown key)."""
    if isinstance(value, str) and value.startswith("@") and len(value) > 1:
        return ids[value[1:]]
    if isinstance(value, list):
        return [resolve(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, ids) for k, v in value.items()}
    return value


class StoredProposal(BaseModel):
    id: str  # p<n>, from the box's counter: never reused
    project: str
    diagram: str
    author: str  # identity id of the caller that proposed
    author_name: str = ""
    client: str = "mcp"
    created_at: str
    rationale: str = ""
    operations: list[dict[str, Any]]  # [{"op", "arguments"}] as in a chat proposal
    base: str  # fingerprint of the diagram the proposal was made on
    result: str  # fingerprint the replay must reproduce
    diff: dict[str, Any] = Field(default_factory=dict)  # proposal.diagram_diff of the preview
    status: str = (
        "pending"  # `applying`: claimed by an apply in progress (decided_by/decided_at name who and since when)
    )
    decided_by: str | None = None
    decided_at: str | None = None
    version: int | None = None  # the version the application wrote
    kind: str = "diagram"  # KINDS: a project proposal has `items` and no diagram, base, result or diff
    items: list[ProposalItem] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _omit_project_fields(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Diagram proposals dump as before M31 (no `kind`, no `items`)."""
        data = handler(self)
        if data.get("kind") == "diagram":
            data.pop("kind", None)
        if not data.get("items"):
            data.pop("items", None)
        return data

    def pending_items(self) -> list[ProposalItem]:
        return [i for i in self.items if i.state == "pending"]


class ProposalBox(BaseModel):
    """The document of one project."""

    incarnation: str = (
        ""  # of the project the proposals belong to (`projects.incarnation_of`); another one: ignore the box
    )
    seq: int = 0  # highest generated number
    proposals: list[StoredProposal] = Field(default_factory=list)


@dataclass(frozen=True)
class ProposalConfig:
    retention_days: int = DEFAULT_RETENTION_DAYS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ProposalConfig:
        env = Env(environ)
        config = cls(retention_days=env.integer("ARCVIZ_PROPOSAL_RETENTION_DAYS", DEFAULT_RETENTION_DAYS, minimum=1))
        env.check()
        return config


def _when(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def expired(box: ProposalBox, retention_days: int, now: datetime | None = None) -> list[StoredProposal]:
    """The decided proposals to drop: older than the retention, and all but the newest `KEEP_DECIDED`. Pending ones
    are never dropped (the cap on them bounds the document)."""
    now = now or datetime.now(UTC)
    decided = [p for p in box.proposals if p.status not in LIVE]
    # decided_at has one-second resolution: the counter breaks ties, so the newest are the ones decided last
    decided.sort(key=lambda p: (_when(p.decided_at) or datetime.min.replace(tzinfo=UTC), int(p.id[1:])), reverse=True)
    cutoff = now - timedelta(days=retention_days)
    drop = decided[KEEP_DECIDED:]
    for item in decided[:KEEP_DECIDED]:
        decided_at = _when(item.decided_at)
        if decided_at is not None and decided_at.astimezone(UTC) < cutoff:
            drop.append(item)
    return drop


def sweep(box: ProposalBox, retention_days: int, now: datetime | None = None) -> bool:
    """Drop what `expired` names; True when something was dropped."""
    gone = {p.id for p in expired(box, retention_days, now)}
    if not gone:
        return False
    box.proposals = [p for p in box.proposals if p.id not in gone]
    return True


# ---- the preview of a proposal and its outcome (moved from proposal.py: the service layer needs them) -------------

APPLIED_NOTE = "Proposal applied as version v{version}."
DISCARDED_NOTE = "Proposal discarded; the diagram was not changed."


class ProposalConflict(Exception):
    """The proposal cannot be applied as previewed; nothing was written."""


def diagram_diff(before: Diagram, after: Diagram) -> dict[str, Any]:
    """Structured difference between two diagrams: added, removed and changed nodes and edges, title."""
    nodes: dict[str, list[dict[str, Any]]] = {"added": [], "removed": [], "changed": []}
    for node_id in sorted(set(before.nodes) | set(after.nodes)):
        old, new = before.nodes.get(node_id), after.nodes.get(node_id)
        if old is None and new is not None:
            nodes["added"].append({"id": new.id, "name": new.name, "kind": new.kind, "parent": new.parent})
        elif new is None and old is not None:
            nodes["removed"].append({"id": old.id, "name": old.name, "kind": old.kind, "parent": old.parent})
        elif old is not None and new is not None:
            changes: dict[str, list[Any]] = {
                field: [getattr(old, field), getattr(new, field)]
                for field in ("name", "kind", "parent", "ref")
                if getattr(old, field) != getattr(new, field)
            }
            for key in sorted(set(old.properties) | set(new.properties)):
                if old.properties.get(key) != new.properties.get(key):
                    changes[f"properties.{key}"] = [old.properties.get(key), new.properties.get(key)]
            if changes:
                nodes["changed"].append({"id": new.id, "name": new.name, "changes": changes})

    edges: dict[str, list[dict[str, Any]]] = {"added": [], "removed": [], "changed": []}
    old_edges = {e.id: e for e in before.edges}
    new_edges = {e.id: e for e in after.edges}
    for edge_id in sorted(set(old_edges) | set(new_edges)):
        old_e, new_e = old_edges.get(edge_id), new_edges.get(edge_id)
        # Edge ids are reused (first free eN), so the same id between other endpoints is a new edge.
        if old_e is not None and new_e is not None and (old_e.source, old_e.target) != (new_e.source, new_e.target):
            edges["removed"].append(old_e.model_dump())
            edges["added"].append(new_e.model_dump())
        elif old_e is None and new_e is not None:
            edges["added"].append(new_e.model_dump())
        elif new_e is None and old_e is not None:
            edges["removed"].append(old_e.model_dump())
        elif old_e is not None and new_e is not None:
            changes = {
                field: [getattr(old_e, field), getattr(new_e, field)]
                for field in ("label", "kind", "direction")
                if getattr(old_e, field) != getattr(new_e, field)
            }
            for key in sorted(set(old_e.properties) | set(new_e.properties)):
                if old_e.properties.get(key) != new_e.properties.get(key):
                    changes[f"properties.{key}"] = [old_e.properties.get(key), new_e.properties.get(key)]
            if changes:
                edges["changed"].append(
                    {"id": new_e.id, "source": new_e.source, "target": new_e.target, "changes": changes}
                )

    title = [before.title, after.title] if before.title != after.title else None
    direction = [before.direction, after.direction] if before.direction != after.direction else None
    out: dict[str, Any] = {"title": title, "direction": direction, "nodes": nodes, "edges": edges}
    if before.interaction is not None and after.interaction is not None:
        out["elements"] = _interaction_diff(before.interaction, after.interaction)
    return out


def _signatures(interaction: sequence.Interaction, keep: set[str]) -> dict[str, tuple[str, Any]]:
    """id -> (kind, content and position) of every interaction element, without nested contents. The
    position counts only the elements in `keep` (present in both versions), so a removal or insertion
    does not make its neighbours look moved."""
    out: dict[str, tuple[str, Any]] = {}
    lifelines = [lf for lf in interaction.lifelines if lf.id in keep]
    for lifeline in interaction.lifelines:
        position = lifelines.index(lifeline) if lifeline in lifelines else None
        out[lifeline.id] = ("lifeline", (lifeline.model_dump(), position))
    for fragment, container, owner in sequence.walk(interaction.fragments):
        siblings = [f.id for f in container if f.id in keep]
        position = siblings.index(fragment.id) if fragment.id in siblings else None
        content = fragment.model_dump(exclude={"operands"})
        out[fragment.id] = (fragment.element, (content, owner.id if owner else None, position))
        if isinstance(fragment, sequence.CombinedFragment):
            kept = [op.id for op in fragment.operands if op.id in keep]
            for op in fragment.operands:
                out[op.id] = (
                    "interaction_operand",
                    (op.guard, fragment.id, kept.index(op.id) if op.id in kept else None),
                )
    for execution in interaction.executions:
        out[execution.id] = ("execution_specification", execution.model_dump())
    return out


def _interaction_diff(before: sequence.Interaction, after: sequence.Interaction) -> dict[str, list[dict[str, Any]]]:
    """Added, removed and changed (content or position) elements of a sequence diagram's interaction."""
    common = set(sequence.element_ids(before)) & set(sequence.element_ids(after))
    old, new = _signatures(before, common), _signatures(after, common)
    out: dict[str, list[dict[str, Any]]] = {"added": [], "removed": [], "changed": []}
    for element_id in [*new, *(i for i in old if i not in new)]:
        if element_id not in old:
            out["added"].append(
                {"id": element_id, "element": new[element_id][0], "description": sequence.describe(after, element_id)}
            )
        elif element_id not in new:
            out["removed"].append(
                {"id": element_id, "element": old[element_id][0], "description": sequence.describe(before, element_id)}
            )
        elif old[element_id] != new[element_id]:
            out["changed"].append(
                {"id": element_id, "element": new[element_id][0], "description": sequence.describe(after, element_id)}
            )
    return out
