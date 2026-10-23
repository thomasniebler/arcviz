"""One chunk of the workshop's model steps, over the services of a caller (spec: requirements-workshop.md, ADR-0192).

`refine_chunk` and `derive_chunk` are what the HTTP routes and the requirements chat both run: select a chunk, one model
call, store the result (suggestions on the requirements, or a project proposal), report what is left. They check the
caller's role before the model call, which costs, and never write anything a person has not asked for: refinement results
are suggestions, a derivation is a proposal (ADR-0190, ADR-0187).
"""

from __future__ import annotations

from typing import Any

from . import requirement_derive, requirement_model, requirement_refine
from .i18n import _
from .llm import LLMProvider
from .services import Services


def refine_chunk(
    svc: Services,
    provider: LLMProvider,
    project_id: str,
    ids: list[str] | None = None,
    limit: int = requirement_refine.MAX_CHUNK,
) -> dict[str, Any]:
    """Refine one chunk (at most 25) and store the result as suggestions; `remaining` counts the raw requirements that
    still carry no suggestion, so a caller loops until it is 0."""
    svc.projects.require(project_id, "editor")
    project = svc.projects.get(project_id)
    box = svc.requirements.box(project_id)
    chunk = requirement_refine.select_chunk(box, ids, limit)
    outcome = requirement_refine.refine(provider, project, box, chunk)
    stored: dict[str, list[Any]] = {"stored": [], "rejected": []}
    if outcome.suggestions:
        stored = svc.requirements.suggest_many(project_id, outcome.suggestions)
    return {
        "project": project_id,
        "chunk": [r.id for r in chunk],
        "refined": stored["stored"],
        "rejected": stored["rejected"],
        "remaining": requirement_refine.remaining(svc.requirements.box(project_id)),
        "llm": outcome.llm(),
    }


def derive_chunk(
    svc: Services,
    provider: LLMProvider,
    project_id: str,
    ids: list[str] | None = None,
    theme: str | None = None,
    limit: int = requirement_derive.MAX_CHUNK,
) -> dict[str, Any]:
    """Derive from one chunk (at most 30) of refined requirements and store the result as a project proposal; nothing is
    written to the project. `remaining` counts the refined requirements that nothing covers and no pending proposal serves."""
    svc.projects.require(project_id, "editor")
    project = svc.projects.get(project_id)
    box = svc.requirements.box(project_id)
    cover = requirement_model.coverage_map(box, project)
    busy = _busy(svc, project_id)
    chunk = requirement_derive.select_chunk(box, cover, ids, theme, limit, busy)
    outcome = requirement_derive.derive(provider, project, box, chunk)
    stored = None
    if outcome.items:
        named = ", ".join(r.id for r in chunk[:12]) + (" …" if len(chunk) > 12 else "")
        stored = svc.proposals.create_project(
            project_id,
            outcome.items,
            _("Abgeleitet aus {count} Anforderung(en): {named}", count=len(chunk), named=named),
            client="derive",
        )
    after = busy | frozenset(r for item in (stored.items if stored else []) for r in item.derived_from)
    return {
        "project": project_id,
        "chunk": [r.id for r in chunk],
        "proposal": stored.model_dump(mode="json") if stored is not None else None,
        "unmapped": outcome.unmapped,
        "remaining": requirement_derive.remaining(box, cover, after),
        "llm": outcome.llm(),
    }


def _busy(svc: Services, project_id: str) -> frozenset[str]:
    """The requirements that a pending project proposal already serves."""
    pending = svc.proposals.list(project_id, "pending")
    return frozenset(
        r
        for p in pending
        if p.kind == "project"
        for item in p.items
        if item.state == "pending"
        for r in item.derived_from
    )
