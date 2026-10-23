"""Golden outputs of the existing diagram types (spec: docs/architecture/specs/uml-graph-types.md, ADR-0183).

Digests of everything a graph profile refactor could change for deployment, activity and sequence diagrams: render
payload, fingerprint, JSON and PlantUML export, tool schemas, system prompt, summary. Written once before the
refactor (`ARCVIZ_UPDATE_GOLDEN=1 pytest tests/test_golden_types.py` rewrites the file); the types added after it have
no entry here, their own tests pin them.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from arcviz import agent, exchange, render
from arcviz.model import Diagram, kinds_for
from arcviz.store import fingerprint
from arcviz.templates import TEMPLATE_TYPES, TEMPLATES

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "existing_types.json"
EXISTING = ("deployment", "activity", "sequence")


def digest(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def template_digests(template: str) -> dict[str, str]:
    diagram = TEMPLATES[template]("golden", "Golden")
    diagram.created_at = diagram.updated_at = "2026-01-01T00:00:00+00:00"  # the document carries both
    document = exchange.export_document(diagram)
    del document["exported_at"]
    out = {
        "payload": digest(render.payload(diagram)),
        "fingerprint": fingerprint(diagram),
        "document": digest(document),
        "summary": digest(diagram.summary(budget=12_000)),
    }
    out["plantuml"] = digest(exchange.export_plantuml(diagram))
    return out


def type_digests(diagram_type: str) -> dict[str, str]:
    tools = [
        {"name": t.name, "description": t.description, "parameters": t.parameters}
        for t in agent.build_tools(kinds_for(diagram_type), diagram_type)
    ]
    template = next(name for name, t in TEMPLATE_TYPES.items() if t == diagram_type)
    prompt = agent.build_system_prompt(TEMPLATES[template]("golden", "Golden"))
    return {"tools": digest(tools), "prompt": digest(prompt)}


def current() -> dict[str, dict[str, dict[str, str]]]:
    templates = {name: template_digests(name) for name, t in sorted(TEMPLATE_TYPES.items()) if t in (None, *EXISTING)}
    types = {t: type_digests(t) for t in EXISTING}
    empty = {"empty": template_digests("empty")}
    return {"templates": {**empty, **templates}, "types": types}


def test_golden_file_is_current():
    if os.environ.get("ARCVIZ_UPDATE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(current(), indent=2, sort_keys=True) + "\n")
    assert GOLDEN.exists(), "run ARCVIZ_UPDATE_GOLDEN=1 pytest tests/test_golden_types.py on the unmodified code"
    expected = json.loads(GOLDEN.read_text())
    assert current() == expected


@pytest.mark.parametrize("diagram_type", EXISTING)
def test_golden_covers_every_existing_type(diagram_type):
    expected = json.loads(GOLDEN.read_text())
    assert diagram_type in expected["types"]
    assert any(TEMPLATE_TYPES[name] == diagram_type for name in expected["templates"])


def test_golden_diagrams_are_valid():
    for name, diagram_type in TEMPLATE_TYPES.items():
        if diagram_type in (None, *EXISTING):
            diagram = TEMPLATES[name]("golden", "Golden")
            assert isinstance(diagram, Diagram) and not diagram.integrity_errors()
