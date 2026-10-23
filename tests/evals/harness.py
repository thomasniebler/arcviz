"""Eval harness: run a case through the real agent and score the result loosely.

Spec: docs/architecture/specs/agent-evals.md. The file name keeps it out of pytest collection.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from arcviz import operations
from arcviz.agent import DiagramAgent
from arcviz.llm import LLMProvider
from arcviz.model import Diagram
from arcviz.project_agent import ProjectAgent
from arcviz.requirements_agent import RequirementsAgent
from arcviz.services import DiagramService, ModelService, ProjectService, ServiceRoot
from arcviz.store import FileStore, fingerprint
from arcviz.templates import create_from_template
from arcviz.workspaces import Identity

CASES_FILE = Path(__file__).with_name("cases.json")
CASE_KEYS = {
    "id",
    "template",
    "type",
    "title",
    "setup",
    "request",
    "expect",
    "scope",
    "model",
    "requirements",
}  # scope "project": the project chat (M17); model: shared elements and relations (M18, M25)
EXPECT_KEYS = {
    "title",
    "nodes",
    "absent",
    "edges",
    "absent_edges",
    "unchanged",
    "reply_contains",
    "reply_contains_any",
    "reply_max_chars",
    "max_operations",
    "created_diagrams",
    "relations",
    "relation_count",
    "requirements_added",
    "raw_contains",
    "nothing_in_force",
    "suggested",
}  # the last four: scope "requirements" (M31)
DIAGRAM_ID = "eval"
PROJECT_ID = "eval-project"
# Cases with "scope": "project" run through the project chat (spec: project-chat.md): the eval diagram sits in a
# project next to OTHER_DIAGRAM and is not open, so the model has to find and open it (or create a new diagram).
OTHER_DIAGRAM = ("checkout", "Checkout", "sequence")


@dataclass
class CaseResult:
    id: str
    score: float
    passed: bool
    checks: list[dict[str, Any]]
    operations: int
    reply: str
    error: str | None
    usage: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_cases(path: Path = CASES_FILE) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def start_diagram(case: dict[str, Any]) -> Diagram:
    """The case's starting diagram: its template (of the case's `type`, if given) plus the setup operations."""
    diagram = create_from_template(case["template"], DIAGRAM_ID, case.get("title", "Eval"), case.get("type"))
    for step in case.get("setup", []):
        operations.apply(diagram, step["op"], step.get("args") or {})
    return diagram


def setup_model(models: ModelService, model: dict[str, Any]) -> None:
    """The case's shared model: elements (`id`, `name`, `kind`, `views` of the eval diagram to link) and relations."""
    for element in model.get("elements", []):
        models.add_element(PROJECT_ID, element["name"], element.get("kind", "component"), element_id=element["id"])
        for view in element.get("views", []):
            models.link(DIAGRAM_ID, view, element["id"])
    for relation in model.get("relations", []):
        models.add_relation(
            PROJECT_ID, relation["kind"], relation["source"], relation["target"], relation.get("properties")
        )


def _norm(value: Any) -> str:
    return str(value).strip().lower()


def _resolve(diagram: Diagram, ref: str) -> str | None:
    node = diagram.find_node(ref)
    return node.id if node else None


def check_case(
    case: dict[str, Any],
    before: Diagram,
    after: Diagram,
    reply: str,
    applied: int,
    error: str | None,
    created: list[Diagram] | None = None,
    relations: list[Any] | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    """Score `after` against the case's expectations: share of passed checks, plus the checks."""
    expect = case.get("expect", {})
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool) -> None:
        checks.append({"name": name, "ok": bool(ok)})

    check("turn finished without error", error is None)
    if "title" in expect:
        check(f"title = {expect['title']}", _norm(after.title) == _norm(expect["title"]))
    for spec in expect.get("nodes", []):
        ref = spec["ref"]
        node = after.find_node(ref)
        check(f"node {ref} exists", node is not None)
        if "name" in spec:
            check(f"{ref} name = {spec['name']}", node is not None and _norm(node.name) == _norm(spec["name"]))
        if "kind" in spec:
            check(f"{ref} kind = {spec['kind']}", node is not None and node.kind == spec["kind"])
        if "parent" in spec:
            wanted = None if spec["parent"] is None else _resolve(after, spec["parent"])
            ok = node is not None and (spec["parent"] is None or wanted is not None) and node.parent == wanted
            check(f"{ref} parent = {spec['parent']}", ok)
        for key, value in (spec.get("properties") or {}).items():
            actual = node.properties.get(key) if node is not None else None
            check(f"{ref} {key} = {value}", actual is not None and _norm(actual) == _norm(value))
    for ref in expect.get("absent", []):
        check(f"node {ref} absent", after.find_node(ref) is None)
    for spec in expect.get("edges", []):
        src, dst = _resolve(after, spec["source"]), _resolve(after, spec["target"])
        name = f"edge {spec['source']} → {spec['target']}"
        edge = next((e for e in after.edges if src and dst and e.source == src and e.target == dst), None)
        check(f"{name} exists", edge is not None)
        if "label" in spec:
            check(
                f"{name} label ~ {spec['label']}", edge is not None and _norm(spec["label"]) in _norm(edge.label or "")
            )
        if "kind" in spec:
            check(f"{name} kind = {spec['kind']}", edge is not None and edge.kind == spec["kind"])
        if "relation" in spec:  # the edge shows this project relation (model-relations.md)
            check(f"{name} shows relation {spec['relation']}", edge is not None and edge.ref == spec["relation"])
    for spec in expect.get("absent_edges", []):
        src, dst = _resolve(after, spec["source"]), _resolve(after, spec["target"])
        present = any({e.source, e.target} == {src, dst} for e in after.edges) if src and dst else False
        check(f"no edge {spec['source']} ↔ {spec['target']}", not present)
    if expect.get("unchanged"):
        check("diagram unchanged", fingerprint(after) == fingerprint(before))
    for text in expect.get("reply_contains", []):
        check(f"reply mentions {text}", _norm(text) in _norm(reply))
    if "reply_contains_any" in expect:
        options = expect["reply_contains_any"]
        check(f"reply mentions one of {', '.join(options)}", any(_norm(t) in _norm(reply) for t in options))
    for spec in expect.get("created_diagrams", []):  # project scope: diagrams the turn created
        match = next((d for d in created or [] if _norm(d.title) == _norm(spec["title"])), None)
        check(f"diagram {spec['title']} created", match is not None)
        if "type" in spec:
            check(f"{spec['title']} type = {spec['type']}", match is not None and match.type == spec["type"])
    for spec in expect.get("relations", []):  # relations of the project model after the turn
        name = f"relation {spec['kind']}: {spec['source']} - {spec['target']}"
        check(
            f"{name} exists",
            any(
                r.kind == spec["kind"] and {r.source, r.target} == {spec["source"], spec["target"]}
                for r in relations or []
            ),
        )
    if "relation_count" in expect:
        check(f"{expect['relation_count']} relation(s) in the model", len(relations or []) == expect["relation_count"])
    if "reply_max_chars" in expect:
        check(f"reply at most {expect['reply_max_chars']} characters", len(reply.strip()) <= expect["reply_max_chars"])
    if "max_operations" in expect:
        check(f"at most {expect['max_operations']} operations", applied <= expect["max_operations"])

    passed = sum(1 for c in checks if c["ok"])
    return passed / len(checks), checks


def check_requirements_case(
    case: dict[str, Any], before: Any, after: Any, reply: str, applied: int, error: str | None
) -> tuple[float, list[dict[str, Any]]]:
    """Score the requirements of a `"scope": "requirements"` case: what the chat added, that nothing is in force that a
    person did not accept (no statement, no status change), how many suggestions it stored, and the reply."""
    expect = case.get("expect", {})
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool) -> None:
        checks.append({"name": name, "ok": bool(ok)})

    check("turn finished without error", error is None)
    if "requirements_added" in expect:
        added = len(after.requirements) - len(before.requirements)
        check(f"{expect['requirements_added']} requirement(s) added", added == expect["requirements_added"])
    for text in expect.get("raw_contains", []):
        check(f"a requirement says {text}", any(_norm(text) in _norm(r.raw) for r in after.requirements))
    if expect.get("nothing_in_force"):
        untouched = all(
            r.statement == "" and r.status == "raw"
            for r in after.requirements
            if r.id in {b.id for b in before.requirements}
        )
        check("no statement or status set (people decide)", untouched)
    if "suggested" in expect:
        count = sum(r.suggestion is not None for r in after.requirements)
        check(f"at least {expect['suggested']} suggestion(s) stored", count >= expect["suggested"])
    for text in expect.get("reply_contains", []):
        check(f"reply mentions {text}", _norm(text) in _norm(reply))
    if "reply_contains_any" in expect:
        options = expect["reply_contains_any"]
        check(f"reply mentions one of {', '.join(options)}", any(_norm(t) in _norm(reply) for t in options))
    if "reply_max_chars" in expect:
        check(f"reply at most {expect['reply_max_chars']} characters", len(reply.strip()) <= expect["reply_max_chars"])
    if "max_operations" in expect:
        check(f"at most {expect['max_operations']} operations", applied <= expect["max_operations"])
    passed = sum(1 for c in checks if c["ok"])
    return passed / len(checks), checks


def run_requirements_case(provider: LLMProvider, case: dict[str, Any]) -> CaseResult:
    """A `"scope": "requirements"` case: the requirements chat on a project with the case's raw requirements."""
    with tempfile.TemporaryDirectory(prefix="arcviz-eval-") as tmp:
        store = FileStore(Path(tmp))
        root = ServiceRoot(store)
        user = Identity("eval", "Eval", None)  # type: ignore[arg-type]
        root.users.seen(user)
        svc = root.for_actor(user)
        svc.projects.create("Eval", PROJECT_ID)
        if case.get("requirements"):
            svc.requirements.import_items(PROJECT_ID, [{"raw": raw} for raw in case["requirements"]])
        before = svc.requirements.box(PROJECT_ID)
        started = time.monotonic()
        final: dict[str, Any] = {}
        error: str | None = None
        try:
            for event in RequirementsAgent(provider).chat(svc, PROJECT_ID, case["request"]):
                if event["type"] == "final":
                    final = event
        except Exception as exc:  # a broken provider must not stop the whole run
            error = f"{type(exc).__name__}: {exc}"
        seconds = time.monotonic() - started
        after = svc.requirements.box(PROJECT_ID)
    error = error or final.get("error")
    reply = final.get("reply", "")
    applied = len(final.get("operations") or [])
    score, checks = check_requirements_case(case, before, after, reply, applied, error)
    return CaseResult(
        id=case["id"],
        score=score,
        passed=score == 1.0,
        checks=checks,
        operations=applied,
        reply=reply,
        error=error,
        usage=final.get("usage") or {},
        seconds=round(seconds, 2),
    )


def run_case(provider: LLMProvider, case: dict[str, Any]) -> CaseResult:
    """Run one case through a fresh store and the real agent, then score the stored diagram."""
    if case.get("scope") == "requirements":
        return run_requirements_case(provider, case)
    with tempfile.TemporaryDirectory(prefix="arcviz-eval-") as tmp:
        store = FileStore(Path(tmp))
        before = start_diagram(case)
        project = case.get("scope") == "project"
        projects = ProjectService(store, store)
        diagrams = DiagramService(store, projects)
        models = ModelService(projects, diagrams) if case.get("model") else None
        if models is not None and not project:  # a shared model needs a project around the diagram
            projects.create("Eval", PROJECT_ID)
            diagrams.create(before.title, DIAGRAM_ID, before.model_copy(deep=True), PROJECT_ID)
            setup_model(models, case["model"])
            agent: DiagramAgent = DiagramAgent(provider, diagrams, compact_after=0, background=False, models=models)
            turn = agent.chat(DIAGRAM_ID, case["request"])
        elif project:
            projects.create("Eval", PROJECT_ID)
            diagrams.create(before.title, DIAGRAM_ID, before.model_copy(deep=True), PROJECT_ID)
            other_id, other_title, other_type = OTHER_DIAGRAM
            diagrams.create(other_title, other_id, Diagram(id=other_id, title=other_title, type=other_type), PROJECT_ID)
            if models is not None:
                setup_model(models, case["model"])
            agent = ProjectAgent(provider, diagrams, projects, models, compact_after=0, background=False)
            turn = agent.chat(PROJECT_ID, case["request"])
        else:
            store.create(before.title, DIAGRAM_ID, before.model_copy(deep=True))
            agent = DiagramAgent(provider, diagrams, compact_after=0, background=False)
            turn = agent.chat(DIAGRAM_ID, case["request"])
        started = time.monotonic()
        final: dict[str, Any] = {}
        error: str | None = None
        try:
            for event in turn:
                if event["type"] == "final":
                    final = event
        except Exception as exc:  # a broken provider must not stop the whole run
            error = f"{type(exc).__name__}: {exc}"
        seconds = time.monotonic() - started
        after = store.load(DIAGRAM_ID)
        created = [store.load(d["id"]) for d in final.get("diagrams") or [] if d.get("created")]
        relations = models.relations(PROJECT_ID) if models is not None else None
    error = error or final.get("error")
    reply = final.get("reply", "")
    applied = len(final.get("operations") or [])
    score, checks = check_case(case, before, after, reply, applied, error, created, relations)
    return CaseResult(
        id=case["id"],
        score=score,
        passed=score == 1.0,
        checks=checks,
        operations=applied,
        reply=reply,
        error=error,
        usage=final.get("usage") or {},
        seconds=round(seconds, 2),
    )
