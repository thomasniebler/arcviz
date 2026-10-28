"""Import and export of whole diagrams: a versioned JSON document (both ways) and PlantUML (export only);
project bundles with the project structure and every diagram document.

Specs: docs/architecture/specs/diagram-exchange.md, project-exchange.md. Parsing collects every problem
so a user can fix a file in one go; nothing here touches the store.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from pydantic import ValidationError

from . import decision_model as dm
from . import operations, plantuml_graph, sequence
from . import requirement_model as rqm
from .graph_types import field_errors, profile_of
from .mermaid import id_map
from .model import (
    DIAGRAM_TYPES,
    DIRECTIONS,
    EDGE_DIRECTIONS,
    Diagram,
    Edge,
    Node,
    activity_outgoing,
    edge_guard,
    is_else_guard,
    kinds_for,
    now_iso,
    single_line,
    slugify,
)
from .plantuml_text import MAX_PLANTUML_NESTING, TooDeep
from .plantuml_text import plain as _plain
from .project_model import (
    MAX_DRIVER_DESCRIPTION,
    MAX_DRIVER_TITLE,
    MAX_ELEMENTS,
    MAX_ID_LENGTH,
    RELATION_KINDS,
    Driver,
    Element,
    Relation,
    Vision,
    driver_number,
    duplicate_element_names,
    linkable_kinds,
    name_key,
    relation_kinds_for,
    relation_number,
    shorten_element_texts,
    shown_relation_kinds,
    view_connections,
    view_elements,
    vision_problems,
)
from .projects import Project, Section
from .requirement_model import RequirementBox

FORMAT = "arcviz-diagram"
SCHEMA_VERSION = 2  # the newest version this Arcviz reads and writes
# Deployment documents are still written as version 1 so older versions keep importing them; other types
# need version 2 (it added `type`), which older versions refuse instead of importing an empty diagram.
DEPLOYMENT_SCHEMA_VERSION = 1
PROJECT_FORMAT = "arcviz-project"
PROJECT_SCHEMA_VERSION = 10  # the newest project bundle version this Arcviz reads and writes
REQUIREMENTS_PROJECT_SCHEMA_VERSION = (
    7  # requirements, or links to them / to stakeholders on drivers, scenarios, decisions
)
VISION_PROJECT_SCHEMA_VERSION = (
    9  # a project vision (ADR-0203): older versions refuse the bundle instead of dropping it
)
TESTABILITY_PROJECT_SCHEMA_VERSION = (
    10  # decisions with assumptions or a falsification criterion (ADR-0209): older versions refuse the bundle
)
QUALITIES_PROJECT_SCHEMA_VERSION = 8  # scenarios with ISO 25010 characteristics (ADR-0202)
CODE_REFS_PROJECT_SCHEMA_VERSION = 6  # elements with code links (spec: agent-access.md); 5 added decision support
DECISION_SUPPORT_PROJECT_SCHEMA_VERSION = 5
DRIVERS_PROJECT_SCHEMA_VERSION = 4  # drivers without decision support; 5 adds stakeholders, scenarios, decisions (M29)
DECISION_SUPPORT_FIELDS = ("stakeholders", "scenarios", "decisions")
RELATIONS_PROJECT_SCHEMA_VERSION = 3  # relations without drivers; 4 adds architecture drivers (ADR-0163)
MODEL_PROJECT_SCHEMA_VERSION = 2  # a model with elements only (M18); 3 adds relations (M25, ADR-0135)
# Bundles of projects without a shared model are still written as version 1 (byte-identical to before M18), so
# older versions keep importing them; a model needs version 2, which older versions refuse instead of dropping it.
PLAIN_PROJECT_SCHEMA_VERSION = 1
# Diagram types with a PlantUML export (spec: docs/architecture/specs/diagram-types.md): all of them since M27 (#107).
PLANTUML_TYPES = frozenset(DIAGRAM_TYPES)


class ExchangeError(ValueError):
    """A document that cannot be imported; `errors` lists every problem found."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# ---- JSON document ----------------------------------------------------------------


def export_document(diagram: Diagram, model: tuple[list[Element], list[Relation]] | None = None) -> dict[str, Any]:
    """The JSON document of a diagram. `model` (`project_model.referenced_model`) is embedded under the optional key
    `model` when it holds anything (#153, ADR-0161): every release reads only `format`, `schema_version` and
    `diagram`, so documents with it stay importable everywhere and the schema version is unchanged."""
    document: dict[str, Any] = {
        "format": FORMAT,
        "schema_version": DEPLOYMENT_SCHEMA_VERSION if diagram.type == DIAGRAM_TYPES[0] else SCHEMA_VERSION,
        "exported_at": now_iso(),
        "diagram": diagram.model_dump(mode="json"),
    }
    if model is not None and (model[0] or model[1]):
        document["model"] = {
            "elements": [e.model_dump(mode="json") for e in model[0]],
            "relations": [r.model_dump(mode="json") for r in model[1]],
        }
    return document


class DocumentModel(NamedTuple):
    """The model a single diagram document carries (#153): what `parse_document_model` validated, and the warnings of
    the import (shortened texts, #212)."""

    elements: list[Element]
    relations: list[Relation]
    warnings: list[str]


def parse_document_model(document: Any) -> DocumentModel | None:
    """The model a diagram document carries (`model`, #153), validated like a bundle's model: element and relation
    fields, ids, kinds, parents, relation rules, unique names (case-insensitive), the limits, and names and descriptions
    shortened to the element bounds with a warning (#212); None when it carries none (an older export, a bare diagram).
    ExchangeError lists every problem, prefixed `model`."""
    if not isinstance(document, dict) or "model" not in document:
        return None
    raw = _one_line(document["model"])
    if not isinstance(raw, dict):
        raise ExchangeError(["model must be an object with 'elements' and 'relations'."])
    errors = [
        f"model.{key} must be a list." for key in ("elements", "relations") if not isinstance(raw.get(key, []), list)
    ]
    if errors:
        raise ExchangeError(errors)
    try:
        model = Project.model_validate(
            {
                "id": "model",
                "title": "model",
                "elements": raw.get("elements", []),
                "relations": raw.get("relations", []),
            }
        )
    except ValidationError as exc:
        raise ExchangeError(
            [f"model.{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()]
        ) from exc
    errors = [f"model: {e}" for e in model.integrity_errors()]
    errors.extend(
        f"model: element id '{e.id[:20]}…' is longer than {MAX_ID_LENGTH} characters"
        for e in model.elements
        if len(e.id) > MAX_ID_LENGTH
    )
    if len(model.elements) > MAX_ELEMENTS:
        errors.append(f"model: at most {MAX_ELEMENTS} model elements, got {len(model.elements)}")
    errors.extend(f"model: {e}" for e in duplicate_element_names(model.elements))  # unique, as add_element keeps them
    if errors:
        raise ExchangeError(errors)
    shortened, _ = shorten_element_texts(model.elements)  # the views take the element's name when they are linked
    return DocumentModel(model.elements, model.relations, [shortened] if shortened else [])


def _payload(document: Any) -> Any:
    if not isinstance(document, dict):
        raise ExchangeError(["The document must be a JSON object."])
    if "format" not in document:
        return document  # bare diagram, e.g. a stored latest.json
    errors: list[str] = []
    if document["format"] != FORMAT:
        errors.append(f"Unknown format '{document['format']}' (expected '{FORMAT}').")
    version = document.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        errors.append("schema_version must be a positive integer.")
    elif version > SCHEMA_VERSION:
        errors.append(
            f"schema_version {version} was written by a newer Arcviz (this one reads up to {SCHEMA_VERSION})."
        )
    if "diagram" not in document:
        errors.append("The document has no 'diagram'.")
    if errors:
        raise ExchangeError(errors)
    return document["diagram"]


def _rule_errors(diagram: Diagram) -> list[str]:
    """Everything an import must satisfy beyond the schema; dispatches on the diagram type."""
    errors = _graph_rule_errors(diagram)
    profile = profile_of(diagram.type)
    if profile is not None:
        errors += profile.rule_errors(diagram) + field_errors(diagram, profile)
    return errors + _ref_errors(diagram) + diagram.integrity_errors()


def _ref_errors(diagram: Diagram) -> list[str]:
    """References to project elements (spec: shared-project-model.md): an element id, only on view elements that
    can represent one. Whether the element exists is the project's business (a dangling ref is allowed)."""
    errors: list[str] = []
    for view in view_elements(diagram):
        if view.ref is None:
            continue
        if not view.ref or slugify(view.ref) != view.ref:
            errors.append(f"'{view.id}' has an invalid element reference '{view.ref}'")
        elif not linkable_kinds(diagram.type, view.kind):
            errors.append(f"'{view.id}' («{view.kind}») cannot represent a project element")
    # relations (spec: model-relations.md): links only on connections that can show one, between linked views
    for connection in view_connections(diagram):
        if connection.ref is None:
            continue
        if not connection.ref or slugify(connection.ref) != connection.ref:
            errors.append(f"'{connection.id}' has an invalid relation reference '{connection.ref}'")
        elif not relation_kinds_for(diagram.type, connection.kind):
            errors.append(f"'{connection.id}' («{connection.kind}») cannot show a model relation")
        elif connection.source_ref is None or connection.target_ref is None:
            errors.append(f"'{connection.id}' shows a model relation, but its ends do not stand for elements")
    shown = shown_relation_kinds(diagram.type) or RELATION_KINDS  # other types: integrity_errors says why
    errors.extend(
        f"complete: a {diagram.type} diagram cannot declare '{k}' complete (it can show: {', '.join(shown) or 'none'})"
        for k in diagram.complete
        if k not in shown
    )
    errors.extend(
        f"hidden_relations: invalid relation id '{r}'" for r in diagram.hidden_relations if not r or slugify(r) != r
    )
    return errors


def _graph_rule_errors(diagram: Diagram) -> list[str]:
    """Rules of node/edge diagrams (deployment, activity): kinds of the type, container parents, edges."""
    errors: list[str] = []
    kinds = kinds_for(diagram.type)
    for key, node in diagram.nodes.items():
        if key != node.id:
            errors.append(f"nodes key '{key}' does not match node id '{node.id}'")
        if not node.id.strip():
            errors.append(f"nodes key '{key}' has an empty id")
        if not node.name.strip():
            errors.append(f"node '{node.id}' has an empty name")
        if node.kind not in kinds.all_kinds:
            errors.append(f"node '{node.id}' has unknown kind '{node.kind}'")
        parent = diagram.nodes.get(node.parent) if node.parent is not None else None
        if parent is not None and parent.kind not in kinds.container_kinds:
            errors.append(f"node '{node.id}' has parent '{parent.id}' which is not a container")
    seen: set[str] = set()
    for edge in diagram.edges:
        if not edge.id.strip():
            errors.append(f"edge {edge.source} -> {edge.target} has an empty id")
        elif edge.id in seen:
            errors.append(f"duplicate edge id '{edge.id}'")
        seen.add(edge.id)
        if edge.kind not in kinds.edge_kinds:
            errors.append(f"edge '{edge.id}' has unknown kind '{edge.kind}'")
        if edge.direction is not None and edge.direction not in EDGE_DIRECTIONS:
            errors.append(f"edge '{edge.id}' has unknown direction '{edge.direction}'")
    if diagram.direction not in DIRECTIONS:
        errors.append(f"unknown direction '{diagram.direction}' (allowed: {', '.join(DIRECTIONS)})")
    return errors


def _one_line(payload: Any) -> Any:
    """The payload with every name, title, label and guard (`operations.ONE_LINE_ARGUMENTS`) of the model made one
    line, as the operations store them (#103): imported data renders and compares like data written since. User
    `properties` maps are data, not labels, and stay exactly as they are (a round trip never changes them)."""
    if isinstance(payload, dict):
        owned = isinstance(payload.get("id"), str)  # a node, edge or relation (a map of nodes by id has no string id)
        return {
            k: _one_line_properties(payload, v)
            if k == "properties" and owned
            else single_line(v)
            if k in operations.ONE_LINE_ARGUMENTS and isinstance(v, str)
            else _one_line(v)
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [_one_line(item) for item in payload]
    return payload


def _one_line_properties(owner: dict[str, Any], properties: Any) -> Any:
    """User properties stay as they are, except an activity edge's `guard`, which is a label the operations store
    one line (#103 review); an edge has a source and a target."""
    if not (isinstance(properties, dict) and "source" in owner and "target" in owner):
        return properties
    guard = properties.get("guard")
    return {**properties, "guard": single_line(guard)} if isinstance(guard, str) else properties


def parse_document(document: Any) -> Diagram:
    """Validate an exported document (or a bare diagram) and return the diagram; ExchangeError otherwise."""
    payload = _one_line(_payload(document))
    try:
        diagram = Diagram.model_validate(payload)
    except ValidationError as exc:
        raise ExchangeError(
            [f"{'.'.join(str(p) for p in err['loc']) or 'diagram'}: {err['msg']}" for err in exc.errors()]
        ) from exc
    errors = _rule_errors(diagram)
    if errors:
        raise ExchangeError(errors)
    return diagram


# ---- project bundles (spec: docs/architecture/specs/project-exchange.md) -----------------------------


def _relation_refs(diagram: Diagram) -> list[str]:
    return [c.ref for c in view_connections(diagram) if c.ref] + list(diagram.hidden_relations)


@dataclass
class ProjectBundle:
    """A validated bundle: the project structure and the diagrams by the ids in their documents."""

    title: str
    description: str
    sections: list[Section]
    members: list[tuple[str, str | None]]  # (diagram id in the bundle, section id), in order
    diagrams: dict[str, Diagram]
    elements: list[Element] = field(default_factory=list)  # the shared model (spec: shared-project-model.md)
    relations: list[Relation] = field(default_factory=list)  # spec: model-relations.md
    relation_seq: int = 0  # the highest generated relation number in use (ADR-0135)
    drivers: list[Driver] = field(default_factory=list)  # spec: architecture-drivers.md
    driver_seq: int = 0
    vision: Vision | None = None  # spec: project-vision.md
    decision_support: dict[str, Any] = field(default_factory=dict)  # stakeholders, scenarios, decisions and counters
    requirements: RequirementBox | None = None  # spec: requirements.md; ids renumbered, links follow
    warnings: list[str] = field(default_factory=list)  # what the import changed, e.g. shortened element texts (#212)


def _uses_requirement_fields(project: Project, requirements: RequirementBox | None) -> bool:
    """Whether the bundle holds anything an older version would drop: requirements, or a link to a requirement or a
    stakeholder on a driver, scenario or decision (spec: requirements.md)."""
    if requirements is not None and requirements.requirements:
        return True
    linked: list[Any] = [*project.drivers, *project.scenarios, *project.decisions]
    staffed: list[Any] = [*project.scenarios, *project.decisions]
    return any(item.requirements for item in linked) or any(item.stakeholders for item in staffed)


def export_project(
    project: Project, diagrams: list[Diagram], requirements: RequirementBox | None = None
) -> dict[str, Any]:
    """The bundle of `project` with the documents of `diagrams`, in membership order; members without a
    diagram in `diagrams` are left out. With `requirements` they are part of the bundle (`schema_version` 7)."""
    by_id = {d.id: d for d in diagrams}
    members = [m for m in project.diagrams if m.diagram in by_id]
    has_model = bool(project.elements) or any(s.element is not None for s in project.sections)
    model: dict[str, Any] = {"elements": [e.model_dump(mode="json") for e in project.elements]} if has_model else {}
    if project.relations:
        model["relations"] = [r.model_dump(mode="json") for r in project.relations]
    if project.relation_seq:  # generated relation ids are never reused, also not in the imported copy (ADR-0135)
        model["relation_seq"] = project.relation_seq
    support = {key: getattr(project, key) for key in DECISION_SUPPORT_FIELDS if getattr(project, key)}
    if project.drivers or project.driver_seq or support:  # drivers (ADR-0163); older bundles keep their version
        model["drivers"] = [d.model_dump(mode="json", exclude={"origin"}) for d in project.drivers]
        model["driver_seq"] = project.driver_seq
        schema_version = DRIVERS_PROJECT_SCHEMA_VERSION
    if support:  # decision support (spec: decision-support.md, ADR-0166)
        for key in DECISION_SUPPORT_FIELDS:
            model[key] = [item.model_dump(mode="json", exclude={"origin"}) for item in getattr(project, key)]
            model[f"{key[:-1]}_seq"] = getattr(project, f"{key[:-1]}_seq")
        schema_version = DECISION_SUPPORT_PROJECT_SCHEMA_VERSION
    elif project.drivers or project.driver_seq:
        pass  # DRIVERS_PROJECT_SCHEMA_VERSION, set above
    elif project.relations or project.relation_seq:
        schema_version = RELATIONS_PROJECT_SCHEMA_VERSION
    else:
        schema_version = MODEL_PROJECT_SCHEMA_VERSION if has_model else PLAIN_PROJECT_SCHEMA_VERSION
    if any(
        e.code_refs for e in project.elements
    ):  # code links: older versions refuse the bundle instead of dropping them
        schema_version = CODE_REFS_PROJECT_SCHEMA_VERSION
    if requirements is not None and requirements.requirements:
        model["requirements"] = [r.model_dump(mode="json") for r in requirements.requirements]
        model["requirement_seq"] = requirements.seq
    if _uses_requirement_fields(project, requirements):  # older versions refuse the bundle instead of dropping them
        schema_version = REQUIREMENTS_PROJECT_SCHEMA_VERSION
    if any(s.qualities for s in project.scenarios):  # ISO 25010 characteristics (ADR-0202): older versions refuse it
        schema_version = QUALITIES_PROJECT_SCHEMA_VERSION
    if project.vision is not None and not project.vision.is_empty():  # ADR-0203
        model["vision"] = project.vision.model_dump(mode="json")
        schema_version = VISION_PROJECT_SCHEMA_VERSION
    if any(d.assumptions or d.falsification for d in project.decisions):  # ADR-0209
        schema_version = TESTABILITY_PROJECT_SCHEMA_VERSION
    return {
        "format": PROJECT_FORMAT,
        "schema_version": schema_version,
        "exported_at": now_iso(),
        "project": {
            "id": project.id,
            "title": project.title,
            "description": project.description,
            "sections": [s.model_dump(mode="json") for s in project.sections],
            "diagrams": [{"diagram": m.diagram, "section": m.section} for m in members],
            **model,
        },
        "diagrams": [export_document(by_id[m.diagram]) for m in members],
    }


def parse_project(bundle: Any) -> ProjectBundle:
    """Validate a project bundle completely; ExchangeError lists every problem."""
    if not isinstance(bundle, dict):
        raise ExchangeError(["The bundle must be a JSON object."])
    errors: list[str] = []
    if bundle.get("format") != PROJECT_FORMAT:
        errors.append(f"Unknown format '{bundle.get('format')}' (expected '{PROJECT_FORMAT}').")
    version = bundle.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        errors.append("schema_version must be a positive integer.")
    elif version > PROJECT_SCHEMA_VERSION:
        errors.append(
            f"schema_version {version} was written by a newer Arcviz (this one reads up to {PROJECT_SCHEMA_VERSION})."
        )
    raw_project = _one_line(bundle.get("project"))
    if raw_project is None:
        errors.append("The bundle has no 'project'.")
    elif not isinstance(raw_project, dict):
        errors.append("'project' must be an object.")
    documents = bundle.get("diagrams")
    if documents is None:
        errors.append("The bundle has no 'diagrams'.")
    elif not isinstance(documents, list):
        errors.append("'diagrams' must be a list of diagram documents.")
    if errors:
        raise ExchangeError(errors)
    assert isinstance(raw_project, dict) and isinstance(documents, list)

    diagrams: dict[str, Diagram] = {}
    for n, document in enumerate(documents, start=1):
        label = (
            document.get("diagram", {}).get("id", "?")
            if isinstance(document, dict) and isinstance(document.get("diagram"), dict)
            else "?"
        )
        try:
            diagram = parse_document(document)
        except ExchangeError as exc:
            errors.extend(f"diagram {n} ({label}): {e}" for e in exc.errors)
            continue
        if diagram.id in diagrams:
            errors.append(f"duplicate diagram id '{diagram.id}' in the bundle")
        diagrams[diagram.id] = diagram

    try:
        project = Project.model_validate(
            {
                "id": "bundle",
                "title": raw_project.get("title"),
                "description": raw_project.get("description") or "",
                "sections": raw_project.get("sections") or [],
                "diagrams": [
                    {**m, "added_at": ""} if isinstance(m, dict) else m for m in raw_project.get("diagrams") or []
                ],
                "elements": raw_project.get("elements") or [],
                "relations": raw_project.get("relations") or [],
                "relation_seq": raw_project.get("relation_seq") or 0,
                "drivers": raw_project.get("drivers") or [],
                "driver_seq": raw_project.get("driver_seq") or 0,
                "vision": raw_project.get("vision"),
                **{key: raw_project.get(key) or [] for key in DECISION_SUPPORT_FIELDS},
                **{f"{key[:-1]}_seq": raw_project.get(f"{key[:-1]}_seq") or 0 for key in DECISION_SUPPORT_FIELDS},
            }
        )
    except ValidationError as exc:
        errors.extend(f"project.{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors())
        raise ExchangeError(errors) from exc
    if not project.title.strip():
        errors.append("project.title must not be empty.")
    errors.extend(f"project: {e}" for e in project.integrity_errors())
    errors.extend(
        f"project: invalid section id '{s.id}' (use lowercase letters, digits and '-')"
        for s in project.sections
        if not s.id or slugify(s.id) != s.id
    )
    errors.extend(
        f"project: element id '{e.id[:20]}…' is longer than {MAX_ID_LENGTH} characters"
        for e in project.elements
        if len(e.id) > MAX_ID_LENGTH
    )
    if project.relation_seq < 0:
        errors.append("project.relation_seq must not be negative")
    if project.driver_seq < 0:
        errors.append("project.driver_seq must not be negative")
    errors.extend(f"project: {e}" for e in _driver_bound_errors(project.drivers))
    if project.vision is not None:
        errors.extend(
            f"project: {e}"
            for e in vision_problems(project.vision.statement, project.vision.goals, project.vision.non_goals)
        )
    requirements, requirement_warnings = _parse_requirements(raw_project, project, errors)
    if len(project.elements) > MAX_ELEMENTS:
        errors.append(f"project: at most {MAX_ELEMENTS} model elements, got {len(project.elements)}")
    errors.extend(f"project: {e}" for e in duplicate_element_names(project.elements))  # names are unique (as M18)
    shortened, renamed = shorten_element_texts(project.elements)  # bounded like every new element, never refused
    for diagram in diagrams.values():  # the views of a shortened element follow it, as after a rename (#212)
        for element_id, name in renamed.items():
            operations.apply(diagram, "sync_element", {"element": element_id, "name": name})
    for section in project.sections:  # a section standing for an element carries its name (ADR-0074)
        element = project.find_element(section.element) if section.element else None
        if element is not None:
            section.title = element.name
    listed = [m.diagram for m in project.diagrams]
    errors.extend(f"project lists diagram '{d}', which is not in the bundle" for d in listed if d not in diagrams)
    errors.extend(f"diagram '{d}' is in the bundle but not in the project" for d in diagrams if d not in listed)
    if errors:
        raise ExchangeError(errors)
    return ProjectBundle(
        title=project.title.strip(),
        description=project.description.strip(),
        sections=project.sections,
        members=[(m.diagram, m.section) for m in project.diagrams],
        diagrams=diagrams,
        elements=project.elements,
        relations=project.relations,
        # above every r<n> of the relations and of every link and hidden marker, so none re-attaches (ADR-0135)
        relation_seq=max(
            [
                project.relation_seq,
                *(relation_number(r.id) or 0 for r in project.relations),
                *(relation_number(ref) or 0 for d in diagrams.values() for ref in _relation_refs(d)),
            ]
        ),
        drivers=project.drivers,
        driver_seq=max([project.driver_seq, *(driver_number(d.id) or 0 for d in project.drivers)]),
        vision=project.vision,
        decision_support=_decision_support(project),
        requirements=requirements,
        warnings=[*([shortened] if shortened else []), *requirement_warnings],
    )


def _parse_requirements(
    raw_project: dict[str, Any], project: Project, errors: list[str]
) -> tuple[RequirementBox | None, list[str]]:
    """The bundle's requirements, validated and renumbered `req1…`; the links of drivers, scenarios and decisions follow
    (a link to a requirement the bundle does not hold is dropped and reported). `origin` markers are never imported."""
    carriers: list[Any] = [*project.drivers, *project.scenarios, *project.decisions]
    for item in carriers:
        item.origin = ""
    raw = raw_project.get("requirements")
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        errors.append("project.requirements must be a list.")
        return None, []
    try:
        box = rqm.RequirementBox.model_validate({"requirements": raw})
    except ValidationError as exc:
        errors.extend(
            f"project.requirements.{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        return None, []
    if len(box.requirements) > rqm.MAX_REQUIREMENTS:
        errors.append(f"project: at most {rqm.MAX_REQUIREMENTS} requirements, got {len(box.requirements)}")
    for e in rqm.integrity_errors(box):
        errors.append(f"project.requirements: {e}")
    if errors:
        return None, []
    mapping = rqm.renumber(box)
    dropped = 0
    for item in carriers:
        kept = [mapping[r] for r in item.requirements if r in mapping]
        dropped += len(item.requirements) - len(kept)
        item.requirements = kept
    known = rqm.Known(
        stakeholders=frozenset(s.id for s in project.stakeholders), elements=frozenset(e.id for e in project.elements)
    )
    rqm.drop_dangling(box, known)
    warnings = [f"{dropped} link(s) to requirements that are not in the bundle were dropped."] if dropped else []
    return (box if box.requirements else None), warnings


def _decision_support(project: Project) -> dict[str, Any]:
    """The stakeholders, scenarios and decisions of a parsed bundle with counters above every generated id."""
    out: dict[str, Any] = {}
    for key, prefix in (("stakeholders", "s"), ("scenarios", "q"), ("decisions", "adr")):
        items = getattr(project, key)
        if not items and not getattr(project, f"{key[:-1]}_seq"):
            continue
        out[key] = items
        numbers = [dm.number_of(i.id, prefix) or 0 for i in items]
        out[f"{key[:-1]}_seq"] = max([getattr(project, f"{key[:-1]}_seq"), *numbers])
    return out


def _driver_bound_errors(drivers: list[Driver]) -> list[str]:
    """The bounds the driver operations check (integrity checks the rest)."""
    errors = [
        f"driver '{d.id}': title longer than {MAX_DRIVER_TITLE}" for d in drivers if len(d.title) > MAX_DRIVER_TITLE
    ]
    errors += [
        f"driver '{d.id}': description longer than {MAX_DRIVER_DESCRIPTION}"
        for d in drivers
        if len(d.description) > MAX_DRIVER_DESCRIPTION
    ]
    titles: set[str] = set()
    for d in drivers:
        if name_key(d.title) in titles:
            errors.append(f"driver '{d.id}': another driver has the title '{d.title}'")
        titles.add(name_key(d.title))
    return errors


# ---- PlantUML ---------------------------------------------------------------------

_PLANTUML_ELEMENTS = {
    "deployment": "node",
    "node": "node",
    "cluster": "node",
    "vnet": "cloud",
    "subnet": "frame",
    "region": "frame",
    "group": "package",
    "service": "component",
    "component": "component",
    "function": "component",
    "gateway": "component",
    "database": "database",
    "queue": "queue",
    "storage": "storage",
    "external": "cloud",
    "artifact": "artifact",
}

_PLANTUML_ARROWS = {
    "connection": "-->",
    "dependency": "..>",
    "deploys": "-[bold]->",
    "association": "--",
    "replication": "<-->",
}


def export_plantuml(diagram: Diagram) -> str:
    """PlantUML source of a diagram in the notation of its type (spec: diagram-exchange.md#plantuml)."""
    if diagram.type not in PLANTUML_TYPES:
        raise ExchangeError([f"PlantUML export is not supported for {diagram.type} diagrams."])
    # PlantUML 1.2026.8 refuses some titles written as they are (";", "-->", "%%", only symbols or emoji):
    # punctuation becomes entities there, and every character of a title without a letter or digit.
    title = _plain(diagram.title, string.punctuation)
    if not any(c.isalnum() for c in diagram.title):
        title = "".join(c if c == " " else f"&#{ord(c)};" for c in " ".join(diagram.title.split()))
    lines = ["@startuml", f"title {title}"]
    try:
        if diagram.type == "activity":
            lines += _activity_plantuml(diagram)
        elif diagram.type == "state_machine":
            lines += plantuml_graph.state_machine(diagram)
        elif diagram.type == "class":
            lines += plantuml_graph.class_diagram(diagram)
        elif diagram.type == "use_case":
            lines += plantuml_graph.use_case(diagram)
        elif diagram.type == "component":
            lines += plantuml_graph.component_diagram(diagram)
        elif diagram.interaction is not None:
            lines += _sequence_plantuml(diagram.interaction)
        else:
            lines += _deployment_plantuml(diagram)
    except TooDeep as exc:
        raise _too_deep(exc.what, exc.element_id) from exc
    except RecursionError as exc:  # a structure deeper than any bound above: refuse it, never a 500
        raise ExchangeError(["The diagram is nested too deeply for the PlantUML export."]) from exc
    lines.append("@enduml")
    return "\n".join(lines) + "\n"


def _too_deep(what: str, element_id: str) -> ExchangeError:
    return ExchangeError(
        [
            f"The diagram has {what} nested more than {MAX_PLANTUML_NESTING} levels deep (at '{element_id}'); the "
            f"PlantUML export supports at most {MAX_PLANTUML_NESTING}."
        ]
    )


def _deployment_plantuml(diagram: Diagram) -> list[str]:
    ids = id_map(diagram)
    lines = ["left to right direction" if diagram.direction in ("LR", "RL") else "top to bottom direction"]

    def emit(node: Node, depth: int) -> None:
        if depth >= MAX_PLANTUML_NESTING:
            raise _too_deep("containers", node.id)
        indent = "  " * depth
        element = _PLANTUML_ELEMENTS.get(node.kind, "rectangle")
        head = f'{indent}{element} "{_plain(node.name)}" <<{node.kind}>> as {ids[node.id]}'
        children = diagram.children(node.id)
        if node.is_container and children:
            lines.append(head + " {")
            for child in children:
                emit(child, depth + 1)
            lines.append(f"{indent}}}")
        else:
            lines.append(head)

    for node in diagram.children(None):
        emit(node, 0)
    for edge in diagram.edges:
        if edge.source not in ids or edge.target not in ids:
            continue
        arrow = _PLANTUML_ARROWS.get(edge.kind, "-->")
        label = f" : {_plain(edge.label)}" if edge.label else ""
        lines.append(f"{ids[edge.source]} {arrow} {ids[edge.target]}{label}")
    return lines


# ---- PlantUML: activity diagrams (#107) --------------------------------------------
# The new activity syntax is structured (if/switch, fork, swimlanes), an Arcviz activity is any graph. Decisions
# become `if`/`switch` and other nodes with several outgoing flows a `fork` (UML: an implicit fork), each closed at
# the first node all branches reach; a flow to a node drawn elsewhere (a loop, a branch running into another one)
# becomes a pair of UML connectors `(n)`, and a flow that ends without a final node `detach`.

_ACTIVITY_SHAPES = {  # PlantUML 1.2026.8 draws an action with the stereotype <<object>> as a rectangle
    "opaque_action": (":", ";"),
    "central_buffer_node": (":«centralBuffer»\\n", "; <<object>>"),
    "data_store_node": (":«datastore»\\n", "; <<object>>"),
}
_ACTIVITY_ENDS = {"initial_node": "start", "activity_final_node": "stop", "flow_final_node": "end"}
_LANE_FREE = " "  # the swimlane of nodes outside every partition, once there are partitions


_EXIT = "\0exit"  # the virtual node every flow end leads to, for the post-dominators


class _ActivityWriter:
    def __init__(self, diagram: Diagram):
        self.diagram = diagram
        self.nodes = {n.id: n for n in diagram.nodes.values() if n.kind != "activity_partition"}
        self.outgoing = activity_outgoing(diagram)
        self.lanes = any(self._lane(n) != _LANE_FREE for n in self.nodes.values())
        self.lines: list[tuple[int, str]] = []  # (depth, text)
        self.position: dict[str, tuple[int, int]] = {}  # node -> (index of the line its drawing starts at, depth)
        self.connectors: dict[str, int] = {}  # node -> connector number of the flows jumping to it
        self.lane: str | None = None
        self.post_dominator, self.order = self._post_dominators()

    def _post_dominators(self) -> tuple[dict[str, str], dict[str, int]]:
        """Immediate post-dominators (Cooper, Harvey, Kennedy on the reversed flows from a virtual exit after every
        node without outgoing flow), and each node's position in that order. Nodes that never reach an end (loops
        without exit) have none."""
        incoming: dict[str, list[str]] = {}
        for edges in self.outgoing.values():
            for edge in edges:
                incoming.setdefault(edge.target, []).append(edge.source)
        sinks = [n for n in self.nodes if not self.outgoing.get(n)]
        postorder: list[str] = []
        seen = {_EXIT}
        stack: list[tuple[str, int]] = [(_EXIT, 0)]
        while stack:  # depth first over the reversed flows, iteratively
            node, i = stack.pop()
            following = sinks if node == _EXIT else incoming.get(node, [])
            if i < len(following):
                stack.append((node, i + 1))
                if following[i] not in seen:
                    seen.add(following[i])
                    stack.append((following[i], 0))
            else:
                postorder.append(node)
        order = {node: k for k, node in enumerate(reversed(postorder))}
        dominator = {_EXIT: _EXIT}

        def meet(a: str, b: str) -> str:
            while a != b:
                while order[a] > order[b]:
                    a = dominator[a]
                while order[b] > order[a]:
                    b = dominator[b]
            return a

        changed = True
        sequence_order = list(reversed(postorder))[1:]  # every node but the exit, in reverse postorder
        while changed:
            changed = False
            for node in sequence_order:
                successors = [e.target for e in self.outgoing.get(node, []) if e.target in dominator]
                if not self.outgoing.get(node):
                    successors.append(_EXIT)
                if not successors:
                    continue
                new = successors[0]
                for other in successors[1:]:
                    new = meet(other, new)
                if dominator.get(node) != new:
                    dominator[node] = new
                    changed = True
        return dominator, order

    def _lane(self, node: Node) -> str:
        parent = self.diagram.nodes.get(node.parent) if node.parent else None
        return parent.name if parent is not None else _LANE_FREE

    def _line(self, depth: int, text: str) -> None:
        self.lines.append((depth, text))

    def _detachable(self) -> bool:
        """PlantUML accepts `detach` only right after an action or a connector (not first in a branch, not after
        `start`, a structure's end or a final node); elsewhere a flow ending in a control node just ends."""
        return bool(self.lines) and self.lines[-1][1].startswith((":", "("))

    def _jump(self, target: str, depth: int) -> None:
        number = self.connectors.setdefault(target, len(self.connectors) + 1)
        self._line(depth, f"({number})")
        self._line(depth, "detach")

    def _join(self, node: str, targets: list[str]) -> str | None:
        """Where the branches of `node` meet: its immediate post-dominator, if every branch reaches an end and that
        node is not drawn yet; else None (each branch ends on its own, jumps become connectors)."""
        if any(t not in self.order for t in targets):
            return None  # a branch loops forever: nothing is certain to follow
        join = self.post_dominator.get(node)
        if join is None or join in (_EXIT, node) or join in self.position:
            return None
        return join

    def chain(self, current: str | None, join: str | None, stops: frozenset[str], depth: int, label: str = "") -> None:
        """Draw the flow from `current` until it ends, reaches `join` (the end of the enclosing structure) or reaches
        a node drawn elsewhere or ending an outer structure (a connector)."""
        while current is not None:
            ends = current == join or current in self.position or current in stops
            if ends and label:  # the flow's guard and label stay on its arrow even when it jumps or merges
                self._line(depth, f"-> {_plain(label, ';')};")
            if current == join:
                return
            if current in self.position or current in stops:
                self._jump(current, depth)
                return
            node = self.nodes[current]
            lane = self._lane(node)
            draws = node.kind in _ACTIVITY_ENDS or node.kind in _ACTIVITY_SHAPES  # control nodes are structure
            if self.lanes and draws and lane != self.lane:
                self._line(depth, f"|{_plain(lane, '|') or _LANE_FREE}|")
                self.lane = lane
            if label:
                self._line(depth, f"-> {_plain(label, ';')};")
            self.position[current] = (len(self.lines), depth)
            if node.kind in _ACTIVITY_ENDS:
                self._line(depth, _ACTIVITY_ENDS[node.kind])
            elif node.kind in _ACTIVITY_SHAPES:
                head, tail = _ACTIVITY_SHAPES[node.kind]
                self._line(depth, f"{head}{_plain(node.name, ';')}{tail}")
            outgoing = self.outgoing.get(current, [])
            if node.kind in ("activity_final_node", "flow_final_node"):
                return
            if not outgoing:
                if self._detachable():
                    self._line(depth, "detach")
                return
            if len(outgoing) == 1:
                current, label = outgoing[0].target, _edge_label(outgoing[0])
                continue
            current = self._branches(node, outgoing, stops, depth)
            label = ""
        return

    def _branches(self, node: Node, outgoing: list[Edge], stops: frozenset[str], depth: int) -> str | None:
        if depth >= MAX_PLANTUML_NESTING:
            raise _too_deep("decisions and forks", node.id)
        join = self._join(node.id, [e.target for e in outgoing])
        inner = stops | ({join} if join else set())
        if node.kind == "decision_node":
            guards = [edge_guard(e) for e in outgoing]
            if len(outgoing) == 2:
                if is_else_guard(guards[0]):  # the else branch is drawn last
                    outgoing, guards = outgoing[::-1], guards[::-1]
                self._line(depth, f"if ({_plain(node.name, '()')}) then ({_plain(guards[0], '()')})")
                self.chain(outgoing[0].target, join, inner, depth + 1)
                self._line(depth, f"else ({_plain(guards[1], '()')})")
                self.chain(outgoing[1].target, join, inner, depth + 1)
                self._line(depth, "endif")
            else:
                self._line(depth, f"switch ({_plain(node.name, '()')})")
                for edge, guard in zip(outgoing, guards, strict=True):
                    self._line(depth, f"case ({_plain(guard, '()')})")
                    self.chain(edge.target, join, inner, depth + 1)
                self._line(depth, "endswitch")
        else:  # a ForkNode, or an implicit fork: every outgoing flow gets a token
            for k, edge in enumerate(outgoing):
                self._line(depth, "fork" if k == 0 else "fork again")
                self.chain(edge.target, join, inner, depth + 1, _edge_label(edge))
            self._line(depth, "end fork")
        return join

    def write(self) -> list[str]:
        initials = [n for n in self.nodes.values() if n.kind == "initial_node"]
        targets = {e.target for edges in self.outgoing.values() for e in edges}
        entries = [n for n in self.nodes.values() if n.kind != "initial_node" and n.id not in targets]
        for node in [*initials, *entries, *self.nodes.values()]:  # the rest: nodes on loops nothing leads into
            if node.id not in self.position:
                self.chain(node.id, None, frozenset(), 0)
        for target in sorted(self.connectors, key=lambda t: -self.position[t][0]):
            at, depth = self.position[target]  # may be the end: a node that draws nothing, drawn last
            self.lines.insert(at, (depth, f"({self.connectors[target]})"))
        lanes = [text for _, text in self.lines if text.startswith("|")]
        if lanes and not self.lines[0][1].startswith("|"):
            self.lines.insert(0, (0, lanes[0]))  # PlantUML wants the first swimlane before any structure
        return [f"{'  ' * depth}{text}" for depth, text in self.lines]


def _edge_label(edge: Edge) -> str:
    """What an arrow between two drawn nodes shows: the guard in brackets, then the label."""
    guard = edge_guard(edge)
    return " ".join(part for part in (f"[{guard}]" if guard else "", edge.label or "") if part)


def _activity_plantuml(diagram: Diagram) -> list[str]:
    return _ActivityWriter(diagram).write()


# ---- PlantUML: sequence diagrams (#107) --------------------------------------------
# Lifelines in their order, messages with UML arrows as drawn by sequence_svg (filled head: synchronous call and
# delete, open head: asynchronous, dashed: reply and create), found/lost messages from/to a dot. Operators without
# a PlantUML keyword become `group <operator>`; execution specifications become activate/deactivate after their
# start and finish messages (without finish: after the lifeline's last message). PlantUML sizes a fragment by its
# messages, so `covered` is not exported.

_SEQUENCE_ARROWS = {
    "synchCall": "->",
    "asynchCall": "->>",
    "asynchSignal": "->>",
    "reply": "-->>",
    "createMessage": "-->>",
    "deleteMessage": "->",
}
_FRAGMENT_KEYWORDS = frozenset({"alt", "opt", "loop", "par", "break", "critical"})


def _aliases(ids: list[str]) -> dict[str, str]:
    """PlantUML identifiers for lifeline ids: `L_<sanitized>`, made unique."""
    out: dict[str, str] = {}
    taken: set[str] = set()
    for element_id in ids:
        base = "L_" + "".join(c if c.isascii() and (c.isalnum() or c == "_") else "_" for c in element_id)
        alias, n = base, 2
        while alias in taken:
            alias, n = f"{base}_{n}", n + 1
        taken.add(alias)
        out[element_id] = alias
    return out


def _sequence_plantuml(interaction: sequence.Interaction) -> list[str]:
    alias = _aliases([lifeline.id for lifeline in interaction.lifelines])
    lines: list[str] = []
    for lifeline in interaction.lifelines:
        label = lifeline.name + (f" : {lifeline.type}" if lifeline.type else "")
        keyword = "actor" if lifeline.actor else "participant"
        lines.append(f'{keyword} "{_plain(label)}" as {alias[lifeline.id]}')
    all_messages = sequence.messages(interaction)
    created: dict[str, str] = {}  # createMessage id -> the lifeline it creates (its first message)
    seen: set[str] = set()
    last: dict[str, str] = {}  # lifeline -> its last message
    for message in all_messages:
        if message.sort == "createMessage" and message.receiver and message.receiver not in seen:
            created[message.id] = message.receiver
        for end in (message.sender, message.receiver):
            if end:
                seen.add(end)
                last[end] = message.id
    starts: dict[str, list[str]] = {}  # message id -> activate lines that follow it
    finishes: dict[str, list[str]] = {}  # message id -> deactivate lines that follow those
    for execution in interaction.executions:
        if execution.lifeline not in alias:
            continue
        finish = execution.finish or last.get(execution.lifeline)
        starts.setdefault(execution.start, []).append(f"activate {alias[execution.lifeline]}")
        if finish is not None:  # PlantUML refuses an activate after a deactivate of the same message
            finishes.setdefault(finish, []).insert(0, f"deactivate {alias[execution.lifeline]}")

    drawn: list[str] = []  # the messages written so far

    def emit(fragments: list[sequence.FragmentNode], depth: int) -> None:
        if depth > MAX_PLANTUML_NESTING:
            raise _too_deep("combined fragments", fragments[0].id if fragments else "?")
        pad = "  " * depth
        for fragment in fragments:
            if isinstance(fragment, sequence.Message):
                arrow = _SEQUENCE_ARROWS.get(fragment.sort, "->")
                text = f" : {_plain(fragment.name)}" if fragment.name.strip() else ""
                if fragment.id in created and fragment.sender != fragment.receiver:  # PlantUML: nothing creates itself
                    lines.append(f"{pad}create {alias[created[fragment.id]]}")
                sender = alias.get(fragment.sender or "")
                receiver = alias.get(fragment.receiver or "")
                if sender and receiver:
                    lines.append(f"{pad}{sender} {arrow} {receiver}{text}")
                elif receiver:
                    lines.append(f"{pad}[o{arrow} {receiver}{text}")  # found: from a dot
                elif sender:
                    lines.append(f"{pad}{sender} {arrow}o]{text}")  # lost: to a dot
                drawn.append(fragment.id)
                lines.extend(f"{pad}{line}" for line in [*starts.get(fragment.id, []), *finishes.get(fragment.id, [])])
                if fragment.sort == "deleteMessage" and receiver:
                    lines.append(f"{pad}destroy {receiver}")
            elif isinstance(fragment, sequence.CombinedFragment):
                for k, operand in enumerate(fragment.operands):
                    guard = operand.guard or ""
                    if k:
                        lines.append(f"{pad}else {_plain(guard)}".rstrip())
                    elif fragment.operator in _FRAGMENT_KEYWORDS:
                        lines.append(f"{pad}{fragment.operator} {_plain(guard)}".rstrip())
                    else:  # no PlantUML keyword: a group labelled with the operator, the guard as second label
                        listed = f" {{{_plain(', '.join(fragment.messages), '[]')}}}" if fragment.messages else ""
                        second = f" [{_plain(guard, '[]')}]" if guard else ""
                        lines.append(f"{pad}group {fragment.operator}{listed}{second}")
                    emit(operand.fragments, depth + 1)
                lines.append(f"{pad}end")
            elif isinstance(fragment, sequence.InteractionUse):
                over = ", ".join(alias[c] for c in fragment.covered if c in alias)
                lines.append(f"{pad}ref over {over} : {_plain(fragment.refers_to)}")
            elif fragment.lifeline in alias:
                target = alias[fragment.lifeline]
                if not drawn:  # PlantUML destroys a participant only after some message
                    lines.append(f"{pad}{target} -[hidden]> {target}")
                lines.append(f"{pad}destroy {target}")

    emit(interaction.fragments, 0)
    return lines
