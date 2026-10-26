"""The ISO/IEC 25010 product quality characteristics a quality scenario is assigned to.

Spec: docs/architecture/specs/iso25010-qualities.md (ADR-0202). Pure: no package imports. A scenario names
one or at most two of the nine characteristics of the 2023 edition (`Scenario.qualities`); the ones of a driver are
computed from its scenarios (`driver_qualities`), never stored.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

MAX_QUALITIES = 2  # a scenario covers one quality characteristic, at most two

# ISO/IEC 25010:2023, in the order of the standard
QUALITY_CHARACTERISTICS = (
    "functional_suitability",
    "performance_efficiency",
    "compatibility",
    "interaction_capability",
    "reliability",
    "security",
    "maintainability",
    "flexibility",
    "safety",
)

# what each characteristic means, in English: the model reads these in the suggestion tool and the prompt
QUALITY_MEANING = {
    "functional_suitability": "the functions meet the stated needs: complete, correct, appropriate",
    "performance_efficiency": "response times, throughput and use of resources under load, capacity",
    "compatibility": "coexistence with and interoperability between systems, exchange of information",
    "interaction_capability": "usability: learnability, operability, accessibility, error protection for users",
    "reliability": "availability, fault tolerance, recoverability, maturity: keeps working, recovers after failure",
    "security": "confidentiality, integrity, non-repudiation, accountability, authenticity, resistance to attack",
    "maintainability": "modularity, reusability, analysability, modifiability, testability",
    "flexibility": "adaptability, scalability, installability, replaceability: changing context of use",
    "safety": "operational constraint, fail-safe, hazard warning: avoids harm to people, property and environment",
}


def clean_qualities(values: Iterable[Any]) -> tuple[list[str], int]:
    """The known characteristics of `values`, each once and in the given order, at most `MAX_QUALITIES`, and how many
    entries were dropped (unknown, repeated or beyond the limit). Never raises: for model output (ADR-0202)."""
    kept: list[str] = []
    dropped = 0
    for value in values:
        if value in QUALITY_CHARACTERISTICS and value not in kept and len(kept) < MAX_QUALITIES:
            kept.append(value)
        else:
            dropped += 1
    return kept, dropped


def driver_qualities(driver_id: str, scenarios: Iterable[Any]) -> list[str]:
    """The characteristics the active scenarios of a driver cover, in the order of the standard (computed, not stored)."""
    covered = {
        q
        for s in scenarios
        if s.driver == driver_id and getattr(s, "status", "active") != "retired"
        for q in s.qualities
    }
    return [q for q in QUALITY_CHARACTERISTICS if q in covered]
