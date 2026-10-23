"""Property tests of the requirement operations (spec: requirements.md): whatever sequence of operations is applied, the box
stays consistent, a failing operation changes nothing, and the box survives a JSON round trip."""

from __future__ import annotations

import json

from hypothesis import given
from hypothesis import strategies as st

from arcviz import requirement_model as rqm
from arcviz.requirement_model import RequirementBox, RequirementError

WORDS = ["Shop", "Daten", "Export", "schnell", "verschlüsselt", "Backup", "Nutzer", "Antwortzeit", "Bericht", "Zugriff"]
text = st.lists(st.sampled_from(WORDS), min_size=1, max_size=6).map(" ".join)
ref = st.one_of(st.sampled_from(["req1", "req2", "req3", "req4", "nope"]), text)


@st.composite
def operation(draw) -> tuple[str, dict]:
    kind = draw(st.sampled_from(["add", "update", "status", "suggest", "accept", "reject", "issue", "remove", "dup"]))
    target = draw(ref)
    if kind == "add":
        return "add_requirement", {"raw": draw(text), "priority": draw(st.sampled_from(["", "must", "bad"]))}
    if kind == "update":
        return "update_requirement", {
            "requirement": target,
            "statement": draw(st.one_of(st.just(""), text)),
            "theme": draw(text),
        }
    if kind == "status":
        return "set_status", {
            "requirement": target,
            "status": draw(st.sampled_from(["raw", "refined", "rejected", "odd"])),
        }
    if kind == "dup":
        return "set_status", {"requirement": target, "status": "duplicate", "duplicate_of": draw(ref)}
    if kind == "suggest":
        return "set_suggestion", {
            "requirement": target,
            "suggestion": {"statement": draw(text), "duplicate_of": draw(st.one_of(st.none(), ref))},
        }
    if kind == "accept":
        return "accept_suggestion", {
            "requirement": target,
            "expected": draw(st.sampled_from(["", "x"])),
            "fields": draw(st.one_of(st.none(), st.just(["statement"]))),
        }
    if kind == "reject":
        return "reject_suggestion", {"requirement": target}
    if kind == "issue":
        return "update_requirement", {
            "requirement": target,
            "issues": [
                {
                    "kind": draw(st.sampled_from(["conflict", "ambiguous", "bad"])),
                    "other": draw(st.one_of(st.none(), ref)),
                }
            ],
        }
    return "remove_requirement", {"requirement": target}


@given(st.lists(operation(), max_size=40))
def test_operations_keep_the_box_consistent(ops):
    box = RequirementBox()
    for name, args in ops:
        before = box.model_dump()
        try:
            rqm.apply(box, name, args)
        except RequirementError:
            assert box.model_dump() == before  # a refused operation changes nothing
        assert rqm.integrity_errors(box) == []
    again = RequirementBox.model_validate(json.loads(box.model_dump_json()))
    assert again == box
    ids = [r.id for r in box.requirements]
    assert len(ids) == len(set(ids)) and all(r.statement for r in box.requirements if r.status == "refined")


@given(st.lists(operation(), max_size=30))
def test_a_suggestion_never_changes_what_is_in_force(ops):
    box = RequirementBox()
    for name, args in ops:
        if name not in ("add_requirement", "set_suggestion", "reject_suggestion"):
            continue
        before = {r.id: r.model_dump(exclude={"suggestion", "updated_at"}) for r in box.requirements}
        try:
            rqm.apply(box, name, args)
        except RequirementError:
            continue
        for r in box.requirements:
            if r.id in before:
                assert r.model_dump(exclude={"suggestion", "updated_at"}) == before[r.id]


@given(st.lists(text, min_size=1, max_size=25, unique=True))
def test_duplicate_candidates_are_symmetric_and_bounded(texts):
    box = RequirementBox()
    for raw in texts:
        rqm.apply(box, "add_requirement", {"raw": raw})
    pairs = rqm.duplicate_candidates(box)
    assert len(pairs) <= rqm.MAX_CANDIDATES
    assert all(a < b and 0 < score <= 1 for a, b, score in pairs)
    only = {box.requirements[0].id}
    assert {frozenset(p[:2]) for p in rqm.duplicate_candidates(box, ids=only)} <= {frozenset(p[:2]) for p in pairs}
