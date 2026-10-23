"""Propose mode: the diff of two diagrams, and confirming or discarding a proposal.

A proposal is a dry run of a chat turn: its operations were applied to an in-memory copy only. The
client holds the proposal (operations plus the content fingerprints of the base and the result) and
sends it back to confirm; it is then replayed through `operations.apply` on the stored diagram.
Spec: docs/architecture/specs/propose-confirm.md, ADR-0015.

The code lives where the service layer can use it (an agent's stored proposal, ADR-0172, is replayed the same way):
the diff and the outcome notes in `proposals.py`, the replay in `services/proposals.py`. This module is the name the
agents and routes know.
"""

from __future__ import annotations

from .proposals import APPLIED_NOTE, DISCARDED_NOTE, ProposalConflict, diagram_diff
from .services.proposals import _note, apply_proposal, discard_proposal

__all__ = [
    "APPLIED_NOTE",
    "DISCARDED_NOTE",
    "ProposalConflict",
    "_note",
    "apply_proposal",
    "diagram_diff",
    "discard_proposal",
]
