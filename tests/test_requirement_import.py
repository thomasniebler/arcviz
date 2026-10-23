"""The requirement import parser (spec: requirements-workshop.md, *Import*): lines, Markdown, CSV, no model."""

from __future__ import annotations

import pytest

from arcviz import requirement_import as ri


def texts(result: ri.ParseResult) -> list[str]:
    return [i["raw"] for i in result.items]


def test_lines_strip_bullets_numbers_and_ids():
    result = ri.parse(
        "- Das System speichert Daten\n"
        "* Es gibt eine Suche\n"
        "1. Erster Punkt\n"
        "2) Zweiter Punkt\n"
        "REQ-12: Mit Kennung\n"
        "• Punkt mit Bullet\n"
        "\n"
        "Einfach eine Zeile\n"
    )
    assert result.format == "lines"
    assert texts(result) == [
        "Das System speichert Daten",
        "Es gibt eine Suche",
        "Erster Punkt",
        "Zweiter Punkt",
        "Mit Kennung",
        "Punkt mit Bullet",
        "Einfach eine Zeile",
    ]
    assert result.items[4]["external_id"] == "REQ-12" and "external_id" not in result.items[0]
    assert [i["row"] for i in result.items] == [1, 2, 3, 4, 5, 6, 8]
    assert result.problems == []


def test_a_bulleted_id_keeps_the_id():
    (item,) = ri.parse("- R-7: mit Aufzählung und Kennung\n").items
    assert (item["raw"], item["external_id"]) == ("mit Aufzählung und Kennung", "R-7")


def test_indented_lines_continue_the_previous_requirement():
    result = ri.parse("- Das System soll\n    schnell antworten\n  und zuverlässig sein\n- Nächste\n")
    assert texts(result) == ["Das System soll schnell antworten und zuverlässig sein", "Nächste"]


def test_a_blank_line_ends_a_requirement():
    assert texts(ri.parse("Eins\n  weiter\n\n  Zwei steht allein\n")) == ["Eins weiter", "Zwei steht allein"]


def test_empty_after_a_bullet_is_a_problem_not_an_item():
    result = ri.parse("- \n- ok\n", fmt="lines")
    assert texts(result) == ["ok"]


def test_markdown_headings_become_the_theme_and_prose_is_ignored():
    result = ri.parse(
        "# Anforderungen\n\nEinleitungstext, der keine Anforderung ist.\n\n"
        "## Verfügbarkeit\n- Rund um die Uhr erreichbar\n- Ausfall unter 5 Minuten\n\n"
        "## Daten\n1. Daten werden verschlüsselt\n   gespeichert\n"
    )
    assert result.format == "markdown"
    assert texts(result) == [
        "Rund um die Uhr erreichbar",
        "Ausfall unter 5 Minuten",
        "Daten werden verschlüsselt gespeichert",
    ]
    assert [i["theme"] for i in result.items] == ["Verfügbarkeit", "Verfügbarkeit", "Daten"]


def test_markdown_without_lists_takes_the_lines_below_headings():
    result = ri.parse("# Thema A\nEine Zeile\nNoch eine\n", fmt="markdown")
    assert [(i["raw"], i["theme"]) for i in result.items] == [("Eine Zeile", "Thema A"), ("Noch eine", "Thema A")]


def test_csv_with_german_headers_and_semicolons():
    result = ri.parse(
        "ID;Anforderung;Quelle;Thema;Priorität;Stakeholder\n"
        'R-1;"Antwortzeit unter 200 ms; gemessen am Gateway";Workshop;Performance;Muss;Betrieb, Marketing\n'
        "R-2;Daten sind verschlüsselt;;;kann;\n"
        "R-3;;;;;\n"
    )
    assert result.format == "csv"
    first, second = result.items
    assert first == {
        "raw": "Antwortzeit unter 200 ms; gemessen am Gateway",
        "row": 2,
        "external_id": "R-1",
        "source": "Workshop",
        "theme": "Performance",
        "priority": "must",
        "stakeholder_names": ["Betrieb", "Marketing"],
    }
    assert second["priority"] == "could" and "source" not in second
    assert result.problems == [{"row": 4, "reason": "empty text"}]


def test_csv_with_commas_tabs_and_a_bom():
    assert texts(ri.parse("﻿text,priority\nEins,high\nZwei,low\n")) == ["Eins", "Zwei"]
    assert texts(ri.parse("requirement\tkey\nEins\tA\nZwei\tB\n")) == ["Eins", "Zwei"]


def test_csv_needs_a_text_column_and_reports_unknown_priorities():
    result = ri.parse("id,priority\nR-1,must\n", fmt="csv")
    assert result.items == [] and "no text column" in result.problems[0]["reason"]
    result = ri.parse("text,prio\nEins,dringend\n", fmt="csv")
    assert texts(result) == ["Eins"] and "unknown priority 'dringend'" in result.problems[0]["reason"]
    assert "priority" not in result.items[0]


def test_auto_detection():
    assert ri.detect("text,source\na,b\n") == "csv"
    assert ri.detect("a,b,c\nd,e,f\n") == "lines"  # no text column in the header
    assert ri.detect("# Titel\n- a\n") == "markdown"
    assert ri.detect("- a\n- b\n") == "lines"  # bullets alone are a list of lines
    assert ri.detect("Eine Zeile mit Komma, und Rest\n") == "lines"
    assert ri.detect("") == "lines"
    assert ri.parse("").items == []


def test_unknown_format_is_refused():
    with pytest.raises(ValueError, match="Available: auto, lines, markdown, csv"):
        ri.parse("x", fmt="xml")


def test_bounds_are_applied_and_reported():
    long_line = "x" * 1600
    result = ri.parse(f"{long_line}\nkurz\n")
    assert len(result.items[0]["raw"]) == 1500 and result.items[0]["raw"].endswith("…")
    assert result.problems == [{"row": 1, "reason": "longer than 1500 characters: shortened"}]
    many = ri.parse("\n".join(f"Anforderung {n}" for n in range(520)))
    assert len(many.items) == 500 and "more than 500" in many.problems[-1]["reason"]
    small = ri.parse("a1\na2\na3\n", max_items=2)
    assert texts(small) == ["a1", "a2"]


def test_a_default_source_fills_only_what_is_missing():
    result = ri.parse("text,source\nEins,Meeting\nZwei,\n", source="Datei.csv")
    assert [i["source"] for i in result.items] == ["Meeting", "Datei.csv"]


def test_hostile_text_stays_text():
    (item,) = ri.parse("<script>alert(1)</script> & \x00\x07 ende\n").items
    assert item["raw"] == "<script>alert(1)</script> & ende"


def test_prepare_maps_stakeholders_and_keeps_the_rows():
    parsed = ri.parse('text,stakeholder\nEins,Betrieb\nZwei,"Betrieb; Unbekannt"\n', fmt="csv")
    prepared = ri.prepare(parsed.items, {"betrieb": "s1"})
    assert prepared.items == [{"raw": "Eins", "stakeholders": ["s1"]}, {"raw": "Zwei", "stakeholders": ["s1"]}]
    assert prepared.rows == [2, 3]
    assert prepared.problems == [{"row": 3, "reason": "unknown stakeholder 'Unbekannt' (left out)"}]
