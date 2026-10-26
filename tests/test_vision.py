"""The project vision (spec: docs/architecture/specs/project-vision.md, ADR-0203)."""

from __future__ import annotations

import pytest

from arcviz import exchange, projects
from arcviz.project_model import MAX_VISION_GOALS, MAX_VISION_ITEM, MAX_VISION_STATEMENT, vision_lines
from arcviz.project_review import ProjectSnapshot, run_project_checks
from arcviz.projects import ProjectError, new_project
from arcviz.prompts import architecture_part
from arcviz.proposals import PROPOSABLE_OPERATIONS


def make():
    return new_project("Shop", "shop")


def test_unset_vision_is_omitted_from_dumps():
    assert "vision" not in make().model_dump(mode="json")


def test_set_vision_round_trip_and_clear():
    project = make()
    result = projects.apply(
        project, "set_vision", {"statement": " Schnell ", "goals": ["A", " ", "B"], "non_goals": ["X"]}
    )
    assert result["result"]["vision"] == {"statement": "Schnell", "goals": ["A", "B"], "non_goals": ["X"]}
    assert project.model_dump(mode="json")["vision"]["goals"] == ["A", "B"]
    projects.apply(project, "set_vision", {})
    assert project.vision is None
    assert "vision" not in project.model_dump(mode="json")


@pytest.mark.parametrize(
    "args",
    [
        {"statement": "x" * (MAX_VISION_STATEMENT + 1)},
        {"goals": ["g"] * (MAX_VISION_GOALS + 1)},
        {"non_goals": ["x" * (MAX_VISION_ITEM + 1)]},
        {"statement": "a\x00b"},
    ],
)
def test_set_vision_bounds(args):
    project = make()
    with pytest.raises(ProjectError):
        projects.apply(project, "set_vision", args)
    assert project.vision is None


def test_vision_is_proposable_but_not_an_agent_tool():
    assert "set_vision" in PROPOSABLE_OPERATIONS
    assert "set_vision" in projects.VISION_OPERATIONS


def test_prompt_block_only_with_a_vision():
    assert architecture_part(None) is None
    project = make()
    projects.apply(project, "set_vision", {"statement": "Schnell", "goals": ["Hoch verfügbar"]})
    lines = vision_lines(project.vision)
    assert lines is not None and "[g1] goal: Hoch verfügbar" in lines
    block = architecture_part(None, vision=lines)
    assert block is not None and "Product vision" in block and "Schnell" in block


def test_rule_vision_missing_fires_only_for_a_project_with_content():
    project = make()
    assert not [f for f in run_project_checks(ProjectSnapshot(project, [])) if f.rule == "vision-missing"]
    projects.apply(project, "add_driver", {"title": "Kosten", "kind": "constraint"})
    assert [f for f in run_project_checks(ProjectSnapshot(project, [])) if f.rule == "vision-missing"]
    projects.apply(project, "set_vision", {"statement": "Billig"})
    assert not [f for f in run_project_checks(ProjectSnapshot(project, [])) if f.rule == "vision-missing"]


def test_bundle_carries_the_vision_as_schema_9_only_when_present():
    project = make()
    assert exchange.export_project(project, [])["schema_version"] < 9
    projects.apply(project, "set_vision", {"statement": "Schnell", "goals": ["A"]})
    bundle = exchange.export_project(project, [])
    assert bundle["schema_version"] == 9
    parsed = exchange.parse_project(bundle)
    assert parsed.vision is not None and parsed.vision.goals == ["A"]
