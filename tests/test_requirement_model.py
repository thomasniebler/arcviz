"""The requirement model: operations, integrity, suggestions, coverage, duplicates (spec: requirements.md)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from arcviz import requirement_model as rm
from arcviz.requirement_model import Known, Meta, RequirementBox, RequirementError


def add(box: RequirementBox, raw: str = "Das System soll schnell sein", **args: object) -> str:
    out = rm.apply(box, "add_requirement", {"raw": raw, **args})
    return out["result"]["id"]


def suggest(box: RequirementBox, rid: str, statement: str = "Das System antwortet in 200 ms.", **fields: object) -> str:
    rm.apply(box, "set_suggestion", {"requirement": rid, "suggestion": {"statement": statement, **fields}})
    suggestion = rm.find_requirement(box, rid).suggestion  # type: ignore[union-attr]
    assert suggestion is not None
    return suggestion.rev


def test_add_assigns_ids_and_never_reuses_them() -> None:
    box = RequirementBox()
    first, second = add(box, "a one"), add(box, "a two")
    assert (first, second) == ("req1", "req2")
    rm.apply(box, "remove_requirement", {"requirement": second})
    assert add(box, "a three") == "req3"


def test_raw_is_one_line_and_never_changed_by_update() -> None:
    box = RequirementBox()
    rid = add(box, "Das  System\n soll\tschnell sein")
    assert rm.find_requirement(box, rid).raw == "Das System soll schnell sein"  # type: ignore[union-attr]
    with pytest.raises(RequirementError, match="unknown argument 'raw'"):
        rm.apply(box, "update_requirement", {"requirement": rid, "raw": "other"})


def test_bounds_and_closed_sets() -> None:
    box = RequirementBox()
    with pytest.raises(RequirementError, match="needs a text"):
        add(box, "   ")
    with pytest.raises(RequirementError, match="at most"):
        add(box, "x" * (rm.MAX_RAW + 1))
    with pytest.raises(RequirementError, match="Unknown priority"):
        add(box, "x", priority="urgent")
    rid = add(box, "ok")
    with pytest.raises(RequirementError, match="Unknown requirement kind"):
        rm.apply(box, "update_requirement", {"requirement": rid, "kind": "wish"})
    assert rm.integrity_errors(box) == []


def test_requirement_limit() -> None:
    box = RequirementBox(
        requirements=[
            rm.Requirement(id=f"req{n}", raw=f"r {n}", created_at="t", updated_at="t")
            for n in range(1, rm.MAX_REQUIREMENTS + 1)
        ],
        seq=rm.MAX_REQUIREMENTS,
    )
    with pytest.raises(RequirementError, match="at most 600"):
        add(box, "one more")


def test_external_id_is_unique_per_source() -> None:
    box = RequirementBox()
    add(box, "a", source="jira", external_id="R-1")
    add(box, "b", source="csv", external_id="R-1")  # another source: fine
    with pytest.raises(RequirementError, match="exists already"):
        add(box, "c", source="jira", external_id="R-1")


def test_loose_references() -> None:
    box = RequirementBox()
    rid = add(box, "Die Antwortzeit beträgt höchstens 200 ms", external_id="R-7")
    assert rm.find_requirement(box, "R-7").id == rid  # type: ignore[union-attr]
    assert rm.find_requirement(box, "die antwortzeit").id == rid  # type: ignore[union-attr]
    add(box, "Die Antwortzeit ist egal")
    assert rm.find_requirement(box, "die antwortzeit") is None  # ambiguous prefix
    with pytest.raises(RequirementError, match="Unknown requirement 'nope'. Requirements: req1, req2"):
        rm.apply(box, "update_requirement", {"requirement": "nope", "theme": "x"})


def test_refined_needs_a_statement_and_status_rules() -> None:
    box = RequirementBox()
    rid = add(box)
    with pytest.raises(RequirementError, match="needs a statement"):
        rm.apply(box, "set_status", {"requirement": rid, "status": "refined"})
    rm.apply(box, "update_requirement", {"requirement": rid, "statement": "Das System antwortet schnell."})
    assert rm.find_requirement(box, rid).status == "raw"  # type: ignore[union-attr]
    rm.apply(box, "set_status", {"requirement": rid, "status": "refined"})
    with pytest.raises(RequirementError, match="needs a statement"):
        rm.apply(box, "update_requirement", {"requirement": rid, "statement": ""})
    rm.apply(box, "set_status", {"requirement": rid, "status": "rejected", "reason": "außerhalb des Scopes"})
    assert rm.find_requirement(box, rid).reason == "außerhalb des Scopes"  # type: ignore[union-attr]


def test_duplicate_status() -> None:
    box = RequirementBox()
    a, b = add(box, "alpha"), add(box, "beta")
    with pytest.raises(RequirementError, match="names the requirement"):
        rm.apply(box, "set_status", {"requirement": b, "status": "duplicate"})
    with pytest.raises(RequirementError, match="itself"):
        rm.apply(box, "set_status", {"requirement": b, "status": "duplicate", "duplicate_of": b})
    rm.apply(box, "set_status", {"requirement": b, "status": "duplicate", "duplicate_of": a})
    assert rm.find_requirement(box, b).duplicate_of == a  # type: ignore[union-attr]
    rm.apply(box, "set_status", {"requirement": b, "status": "raw"})
    assert rm.find_requirement(box, b).duplicate_of is None  # type: ignore[union-attr]
    rm.apply(box, "set_status", {"requirement": b, "status": "duplicate", "duplicate_of": a})
    rm.apply(box, "remove_requirement", {"requirement": a})  # the duplicate becomes a plain requirement again
    assert (rm.find_requirement(box, b).status, rm.find_requirement(box, b).duplicate_of) == ("raw", None)  # type: ignore[union-attr]


def test_set_suggestion_never_touches_the_fields_in_force() -> None:
    box = RequirementBox()
    rid = add(box)
    suggest(box, rid, kind="quality", priority="must", measure="p95 < 200 ms", theme="Performance")
    r = rm.find_requirement(box, rid)
    assert r is not None and r.statement == "" and r.kind == "unclear" and r.status == "raw"
    assert r.suggestion is not None and r.suggestion.kind == "quality" and r.suggestion.rev


def test_suggestion_records_who_made_it() -> None:
    box = RequirementBox()
    rid = add(box)
    rm.apply(
        box,
        "set_suggestion",
        {"requirement": rid, "suggestion": {"statement": "S."}},
        meta=Meta(author="u1", author_name="Ada", client="mcp"),
    )
    s = rm.find_requirement(box, rid).suggestion  # type: ignore[union-attr]
    assert (s.by, s.by_name, s.client) == ("u1", "Ada", "mcp")  # type: ignore[union-attr]


def test_accept_moves_fields_and_refines() -> None:
    box = RequirementBox()
    rid = add(box)
    rev = suggest(box, rid, kind="quality", priority="must", measure="p95 < 200 ms", theme="Performance")
    rm.apply(box, "accept_suggestion", {"requirement": rid, "expected": rev})
    r = rm.find_requirement(box, rid)
    assert r is not None
    assert (r.status, r.statement, r.kind, r.priority, r.measure, r.theme) == (
        "refined",
        "Das System antwortet in 200 ms.",
        "quality",
        "must",
        "p95 < 200 ms",
        "Performance",
    )
    assert r.suggestion is None


def test_accept_with_a_stale_revision_is_refused() -> None:
    box = RequirementBox()
    rid = add(box)
    seen = suggest(box, rid, "Erste Fassung.")
    suggest(box, rid, "Zweite Fassung.")  # replaced meanwhile
    with pytest.raises(RequirementError, match="changed since it was shown"):
        rm.apply(box, "accept_suggestion", {"requirement": rid, "expected": seen})
    assert rm.find_requirement(box, rid).statement == ""  # type: ignore[union-attr]


def test_accept_per_field_keeps_the_rest_suggested() -> None:
    box = RequirementBox()
    rid = add(box)
    rev = suggest(box, rid, kind="quality", priority="must", theme="Performance")
    rm.apply(box, "accept_suggestion", {"requirement": rid, "expected": rev, "fields": ["theme"]})
    r = rm.find_requirement(box, rid)
    assert r is not None and r.theme == "Performance" and r.status == "raw" and r.statement == ""
    assert r.suggestion is not None and r.suggestion.accepted == ["theme"] and r.suggestion.rev == rev
    rm.apply(box, "accept_suggestion", {"requirement": rid, "expected": rev, "fields": ["statement"]})
    assert r.status == "refined" and r.suggestion is not None
    rm.apply(box, "accept_suggestion", {"requirement": rid, "expected": rev})  # the remaining ones
    assert r.suggestion is None and r.kind == "quality" and r.priority == "must"


def test_accept_duplicate_suggestion_sets_the_status() -> None:
    box = RequirementBox()
    a, b = add(box, "alpha"), add(box, "alpha again")
    rev = suggest(box, b, "Alpha.", duplicate_of=a)
    rm.apply(box, "accept_suggestion", {"requirement": b, "expected": rev, "fields": ["duplicate_of"]})
    r = rm.find_requirement(box, b)
    assert r is not None and (r.status, r.duplicate_of) == ("duplicate", a)


def test_reject_suggestion_and_issues() -> None:
    box = RequirementBox()
    rid = add(box)
    suggest(box, rid, issues=[{"kind": "not_measurable", "text": "schnell ist nicht messbar"}])
    rm.apply(box, "reject_suggestion", {"requirement": rid})
    assert rm.find_requirement(box, rid).suggestion is None  # type: ignore[union-attr]
    rm.apply(
        box, "update_requirement", {"requirement": rid, "issues": [{"kind": "ambiguous", "text": "was heißt schnell"}]}
    )
    rm.apply(box, "resolve_issue", {"requirement": rid, "index": 0, "resolved": True})
    assert rm.find_requirement(box, rid).issues[0].resolved  # type: ignore[union-attr]
    with pytest.raises(RequirementError, match="index 3 is invalid"):
        rm.apply(box, "resolve_issue", {"requirement": rid, "index": 3})
    with pytest.raises(RequirementError, match="at most 4 issues"):
        rm.apply(box, "update_requirement", {"requirement": rid, "issues": [{"kind": "ambiguous"}] * 5})
    with pytest.raises(RequirementError, match="Unknown issue kind"):
        rm.apply(box, "update_requirement", {"requirement": rid, "issues": [{"kind": "vague"}]})


def test_suggestion_with_unknown_references_is_refused() -> None:
    box = RequirementBox()
    rid = add(box)
    with pytest.raises(RequirementError, match="duplicate_of"):
        suggest(box, rid, duplicate_of="req99")
    with pytest.raises(RequirementError, match="Unknown suggestion field"):
        rm.apply(box, "set_suggestion", {"requirement": rid, "suggestion": {"statement": "S.", "status": "refined"}})
    with pytest.raises(RequirementError, match="needs a statement"):
        suggest(box, rid, "  ")


def test_removing_a_requirement_clears_references_in_the_box() -> None:
    box = RequirementBox()
    a, b = add(box, "alpha"), add(box, "beta")
    rm.apply(box, "update_requirement", {"requirement": b, "issues": [{"kind": "conflict", "other": a}]})
    rm.apply(box, "remove_requirement", {"requirement": a})
    assert rm.find_requirement(box, b).issues[0].other is None  # type: ignore[union-attr]
    assert rm.integrity_errors(box) == []


def test_apply_rolls_back_on_error() -> None:
    box = RequirementBox()
    rid = add(box)
    before = box.model_dump()
    with pytest.raises(RequirementError):
        rm.apply(box, "update_requirement", {"requirement": rid, "theme": "ok", "kind": "wish"})
    assert box.model_dump() == before


def test_unknown_operation_and_arguments_name_the_alternatives() -> None:
    box = RequirementBox()
    with pytest.raises(RequirementError, match="Available: add_requirement"):
        rm.apply(box, "explode", {})
    with pytest.raises(RequirementError, match="missing required argument 'raw'"):
        rm.apply(box, "add_requirement", {})


def test_references_are_checked_against_the_project_on_write() -> None:
    box = RequirementBox()
    known = Known(stakeholders=frozenset({"s1"}), elements=frozenset({"api"}))
    add_ok = rm.apply(box, "add_requirement", {"raw": "x", "stakeholders": ["s1"], "elements": ["api"]}, known=known)
    assert add_ok["result"]["stakeholders"] == ["s1"]
    with pytest.raises(RequirementError, match="Unknown stakeholder 's9'. Known: s1"):
        rm.apply(box, "add_requirement", {"raw": "y", "stakeholders": ["s9"]}, known=known)
    with pytest.raises(RequirementError, match="Unknown element 'db'"):
        rm.apply(box, "add_requirement", {"raw": "y", "elements": ["db"]}, known=known)
    rid = add_ok["result"]["id"]
    with pytest.raises(RequirementError, match="Unknown stakeholder 's9'"):
        rm.apply(
            box,
            "set_suggestion",
            {"requirement": rid, "suggestion": {"statement": "S.", "stakeholders": ["s9"]}},
            known=known,
        )


def test_dangling_references_are_counted_and_dropped() -> None:
    box = RequirementBox()
    rm.apply(box, "add_requirement", {"raw": "x", "stakeholders": ["s1", "s2"], "elements": ["api", "db"]})
    known = Known(stakeholders=frozenset({"s1"}), elements=frozenset({"api"}))
    assert rm.dangling(box, known) == {"stakeholders": 1, "elements": 1}
    assert rm.drop_dangling(box, known) is True
    assert rm.dangling(box, known) == {"stakeholders": 0, "elements": 0}
    assert rm.drop_dangling(box, known) is False
    assert rm.drop_ids(box, stakeholder="s1") is True and box.requirements[0].stakeholders == []


def test_integrity_catches_a_damaged_box() -> None:
    now = {"created_at": "t", "updated_at": "t"}
    box = RequirementBox(
        requirements=[
            rm.Requirement(id="req1", raw="a", status="duplicate", duplicate_of="req2", **now),
            rm.Requirement(id="req2", raw="b", status="duplicate", duplicate_of="req1", **now),
            rm.Requirement(id="req2", raw="c", status="refined", **now),
        ]
    )
    errors = " | ".join(rm.integrity_errors(box))
    assert "duplicate id req2" in errors and "refined without a statement" in errors and "cycle" in errors


def test_agent_policy() -> None:
    rm.check_agent("add_requirement", {"raw": "x"})
    rm.check_agent("update_requirement", {"requirement": "req1", "theme": "t", "stakeholders": []})
    rm.check_agent("set_suggestion", {"requirement": "req1", "suggestion": {"statement": "S."}})
    for op in ("set_status", "accept_suggestion", "reject_suggestion", "remove_requirement", "resolve_issue"):
        with pytest.raises(rm.Rejected, match="people decide"):
            rm.check_agent(op, {})
    with pytest.raises(rm.Rejected, match="cannot change statement"):
        rm.check_agent("update_requirement", {"requirement": "req1", "statement": "x"})


def project_with(**links: dict[str, list[str]]) -> SimpleNamespace:
    def items(prefix: str, key: str) -> list[SimpleNamespace]:
        return [SimpleNamespace(id=i, requirements=r) for i, r in links.get(key, {}).items()]

    return SimpleNamespace(
        drivers=items("d", "drivers"), scenarios=items("q", "scenarios"), decisions=items("adr", "decisions")
    )


def test_coverage_levels() -> None:
    box = RequirementBox()
    ids = [add(box, f"requirement number {n} text") for n in range(1, 7)]
    for rid in ids[:5]:
        rm.apply(box, "update_requirement", {"requirement": rid, "statement": f"Aussage {rid}."})
        rm.apply(box, "set_status", {"requirement": rid, "status": "refined"})
    rm.apply(box, "set_status", {"requirement": ids[4], "status": "duplicate", "duplicate_of": ids[2]})
    project = project_with(drivers={"d1": [ids[1]]}, scenarios={"q1": [ids[2]]}, decisions={"adr1": [ids[3], ids[2]]})
    cover = rm.coverage_map(box, project)
    assert cover[ids[0]] == "uncovered"
    assert cover[ids[1]] == "driver"
    assert cover[ids[2]] == "decision"  # the highest level present
    assert cover[ids[3]] == "decision"
    assert cover[ids[4]] == "decision"  # a duplicate counts through the original
    assert cover[ids[5]] is None  # raw: no coverage
    trace = rm.traces(project)[ids[2]]
    assert (trace.scenarios, trace.decisions) == (["q1"], ["adr1"])


def test_similarity_is_language_neutral() -> None:
    assert (
        rm.similarity("Das System speichert alle Daten verschlüsselt", "Alle Daten speichert das System verschlüsselt")
        == 1.0
    )
    assert rm.similarity("Der Nutzer meldet sich an", "Die Datenbank wird nachts gesichert") == 0.0
    assert rm.similarity("", "irgendetwas") == 0.0
    assert 0 < rm.similarity("Antwortzeit unter 200 ms messen", "Antwortzeit unter 500 ms messen") < 1


def test_duplicate_candidates() -> None:
    box = RequirementBox()
    a = add(box, "Das System speichert alle Kundendaten verschlüsselt in der Datenbank")
    b = add(box, "Das System speichert alle Kundendaten dauerhaft verschlüsselt in der Datenbank")
    c = add(box, "Der Export liefert eine CSV Datei pro Monat")
    pairs = rm.duplicate_candidates(box)
    assert [(x, y) for x, y, _ in pairs] == [(a, b)]
    assert rm.duplicate_candidates(box, ids={c}) == []
    assert rm.duplicate_candidates(box, ids={a})[0][:2] == (a, b)
    rm.apply(box, "set_status", {"requirement": b, "status": "duplicate", "duplicate_of": a})
    assert rm.duplicate_candidates(box) == []  # a marked duplicate is not a candidate any more


def test_duplicate_candidates_scale() -> None:
    box = RequirementBox()
    for n in range(500):
        add(box, f"Anforderung {n} betrifft Modul{n % 50} und Funktion{n % 7} im Bereich{n % 11} des Systems")
    pairs = rm.duplicate_candidates(box, limit=10)
    assert len(pairs) <= 10
