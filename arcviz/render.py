"""The renderer of each diagram type behind one interface (spec: docs/architecture/specs/diagram-types.md).

ADR-0032: Mermaid for deployment (and activity) diagrams, an own SVG renderer for sequence diagrams.
ADR-0033: a render payload names its renderer and carries the source under a key per format.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from . import mermaid, sequence_svg
from .model import Diagram

FORMATS = ("mermaid", "svg")


class Renderer(Protocol):
    name: str  # the format of the source: one of FORMATS
    supports_focus: bool  # drill-down into a container's subtree

    def render(
        self,
        diagram: Diagram,
        *,
        highlight: Iterable[str] = (),
        focus: str | None = None,
        marks: Mapping[str, str] | None = None,
        edge_marks: Mapping[str, str] | None = None,
    ) -> str: ...

    def ids(self, diagram: Diagram) -> dict[str, str]:
        """Model id -> id in the rendering (empty when the rendering uses the model ids)."""
        ...


class MermaidRenderer:
    name = "mermaid"
    supports_focus = True

    def render(
        self,
        diagram: Diagram,
        *,
        highlight: Iterable[str] = (),
        focus: str | None = None,
        marks: Mapping[str, str] | None = None,
        edge_marks: Mapping[str, str] | None = None,
    ) -> str:
        return mermaid.render(diagram, highlight=highlight, focus=focus, marks=marks, edge_marks=edge_marks)

    def ids(self, diagram: Diagram) -> dict[str, str]:
        return mermaid.id_map(diagram)


class SequenceSvgRenderer:
    """Own SVG renderer of sequence diagrams (ADR-0035); element ids are the model ids (`data-id`)."""

    name = "svg"
    supports_focus = False

    def render(
        self,
        diagram: Diagram,
        *,
        highlight: Iterable[str] = (),
        focus: str | None = None,
        marks: Mapping[str, str] | None = None,
        edge_marks: Mapping[str, str] | None = None,
    ) -> str:
        return sequence_svg.render(diagram, highlight=highlight, marks={**(marks or {}), **(edge_marks or {})})

    def ids(self, diagram: Diagram) -> dict[str, str]:
        return {}


RENDERERS: dict[str, Renderer] = {
    "deployment": MermaidRenderer(),
    "activity": MermaidRenderer(),  # UML activity notation via Mermaid's flowchart shapes
    "state_machine": MermaidRenderer(),  # UML state notation via Mermaid's flowchart shapes (ADR-0184)
    "class": MermaidRenderer(),  # classifiers with compartments via Mermaid's flowchart shapes (ADR-0184)
    "use_case": MermaidRenderer(),  # actors, use cases and the system boundary via Mermaid's flowchart shapes
    "component": MermaidRenderer(),  # components, interfaces and ports via Mermaid's flowchart shapes
    "sequence": SequenceSvgRenderer(),
}


def renderer_for(diagram: Diagram) -> Renderer:
    return RENDERERS[diagram.type]


def payload(
    diagram: Diagram,
    *,
    highlight: Iterable[str] = (),
    focus: str | None = None,
    marks: Mapping[str, str] | None = None,
    edge_marks: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """`renderer`, the source under its format's key (the other format's key is null) and the id map.

    A renderer without focus support renders the whole diagram whatever `focus` says.
    """
    renderer = renderer_for(diagram)
    source = renderer.render(
        diagram,
        highlight=highlight,
        focus=focus if renderer.supports_focus else None,
        marks=marks,
        edge_marks=edge_marks,
    )
    out: dict[str, Any] = {"renderer": renderer.name, **{name: None for name in FORMATS}}
    out[renderer.name] = source
    out["mermaid_ids"] = renderer.ids(diagram) if renderer.name == "mermaid" else {}
    return out


def file_name(diagram: Diagram) -> str:
    """File name of a rendering in the Git mirror."""
    return "diagram.mmd" if renderer_for(diagram).name == "mermaid" else "diagram.svg"
