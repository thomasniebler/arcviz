"""Load extra element kinds from JSON files (ARCVIZ_KIND_PACKS, ARCVIZ_KINDS_FILE) into a KindCatalog.

Spec: docs/architecture/specs/configurable-kinds.md. The file only extends the built-in kinds; any
problem raises KindConfigError naming the file and the kind, so the server does not start with a
vocabulary that differs from what the operator wrote.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Any

from .model import BUILTIN_KINDS, EDGE_DIRECTIONS, EDGE_LINES, NODE_SHAPES, ROLES, TRAITS, TYPE_KINDS, KindCatalog

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
STYLE_RE = re.compile(r"^[A-Za-z0-9#:,.%() -]{1,200}$")
# Names that would clash with the renderer's own classes or Mermaid keywords.
RESERVED = frozenset(
    {
        "changed",
        "stub",
        "default",
        "end",
        "graph",
        "subgraph",
        "flowchart",
        "class",
        "classdef",
        "style",
        "linkstyle",
        "click",
        "call",
        "href",
        "direction",
    }
)
# Names no configured kind may take: the built-in kinds of every diagram type (names stay disjoint, ADR-0037).
BUILTIN = frozenset().union(*(c.all_kinds | c.edge_kinds for c in (BUILTIN_KINDS, *TYPE_KINDS.values())))
NODE_KEYS = frozenset({"container", "style", "shape", "icon", "role", "traits"})
EDGE_KEYS = frozenset({"line", "direction"})


class KindConfigError(ValueError):
    """The kinds file is missing, malformed or defines an invalid kind."""


def load_kind_catalog(*paths: str | Path) -> KindCatalog:
    """The built-in kinds extended by the kinds defined in the JSON files at `paths`, in order.

    Kind packs (`kind_pack_path`) are kinds files too. A kind may be defined only once across all files.
    """
    containers, leaves = set(BUILTIN_KINDS.container_kinds), set(BUILTIN_KINDS.leaf_kinds)
    edges = set(BUILTIN_KINDS.edge_kinds)
    styles: dict[str, str] = {}
    shapes: dict[str, str] = {}
    icons: dict[str, str] = {}
    roles: dict[str, str] = {}
    traits: dict[str, dict[str, str]] = {}
    lines: dict[str, tuple[str, str]] = {}
    defined_in: dict[str, str | Path] = {}
    for path in paths:
        node_defs, edge_defs = _read(path)

        def fail(message: str, path: str | Path = path) -> KindConfigError:
            return KindConfigError(f"kinds file {path}: {message}")

        for name in [*node_defs, *edge_defs]:
            if name in defined_in:
                raise fail(f"kind '{name}' is also defined in {defined_in[name]}")
        for name, spec in node_defs.items():
            _check_name(name, "node", BUILTIN, fail)
            if name in edge_defs:
                raise fail(f"'{name}' is both a node and an edge kind")
            spec = _definition(spec, f"node kind '{name}'", NODE_KEYS, fail)
            container = spec.get("container", False)
            if not isinstance(container, bool):
                raise fail(f"node kind '{name}': 'container' must be true or false")
            (containers if container else leaves).add(name)
            if "style" in spec:
                style = spec["style"]
                if not isinstance(style, str) or not STYLE_RE.match(style):
                    raise fail(
                        f"node kind '{name}': invalid style (a Mermaid classDef body such as "
                        "'fill:#fff4e5,stroke:#ff9900,color:#232f3e'; letters, digits and #:,.%()- only, at most 200)"
                    )
                styles[name] = style
            if "shape" in spec:
                shape = spec["shape"]
                if shape not in NODE_SHAPES:
                    raise fail(f"node kind '{name}': unknown shape '{shape}' (one of: {', '.join(NODE_SHAPES)})")
                shapes[name] = shape
            if "icon" in spec:
                if container:
                    raise fail(f"node kind '{name}': icons are drawn for leaf kinds only, not containers")
                icons[name] = _check_icon(spec["icon"], name, fail)
            if "role" in spec or "traits" in spec:
                if container:
                    raise fail(f"node kind '{name}': roles are for leaf kinds only, not containers")
                if "role" not in spec:
                    raise fail(f"node kind '{name}': traits need a role (one of: {', '.join(ROLES)})")
                role = spec["role"]
                if not isinstance(role, str) or role not in ROLES:
                    raise fail(f"node kind '{name}': unknown role {role!r} (one of: {', '.join(ROLES)})")
                roles[name] = role
                if "traits" in spec:
                    traits[name] = _check_traits(spec["traits"], name, fail)
            defined_in[name] = path

        for name, spec in edge_defs.items():
            _check_name(name, "edge", BUILTIN, fail)
            spec = _definition(spec, f"edge kind '{name}'", EDGE_KEYS, fail)
            line = spec.get("line", "solid")
            if line not in EDGE_LINES:
                raise fail(f"edge kind '{name}': unknown line '{line}' (one of: {', '.join(EDGE_LINES)})")
            direction = spec.get("direction", "forward")
            if direction not in EDGE_DIRECTIONS:
                raise fail(
                    f"edge kind '{name}': unknown direction '{direction}' (one of: {', '.join(EDGE_DIRECTIONS)})"
                )
            edges.add(name)
            lines[name] = (line, direction)
            defined_in[name] = path

    return KindCatalog(
        container_kinds=frozenset(containers),
        leaf_kinds=frozenset(leaves),
        edge_kinds=frozenset(edges),
        node_styles=styles,
        node_shapes=shapes,
        edge_lines=lines,
        node_icons=icons,
        node_roles=roles,
        node_traits=traits,
    )


def _read(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """The node and edge sections of one kinds file, with the file-level checks done."""

    def fail(message: str) -> KindConfigError:
        return KindConfigError(f"kinds file {path}: {message}")

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise fail(f"cannot be read ({exc.strerror or exc})") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise fail(f"not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise fail("must be a JSON object with 'node_kinds' and/or 'edge_kinds'")
    for key in data:
        if key not in ("node_kinds", "edge_kinds"):
            raise fail(f"unknown key '{key}' (expected 'node_kinds' and/or 'edge_kinds')")
    return _section(data, "node_kinds", fail), _section(data, "edge_kinds", fail)


# ---- icons and kind packs (spec: docs/architecture/specs/cloud-icons.md) ------------------------------

ICON_DIR = Path(__file__).parent / "static" / "vendor" / "icons"  # vendored Iconify JSON packs
KIND_PACK_DIR = Path(__file__).parent / "kindpacks"
ICON_RE = re.compile(r"^([a-z0-9-]+):([a-z0-9-]+)$")


def icon_packs() -> list[str]:
    """Names of the vendored icon packs."""
    return sorted(p.stem for p in ICON_DIR.glob("*.json"))


@functools.cache
def icon_names(pack: str) -> frozenset[str]:
    """The icons of a vendored pack (empty for an unknown pack)."""
    path = ICON_DIR / f"{pack}.json"
    if not path.is_file():
        return frozenset()
    return frozenset(json.loads(path.read_text(encoding="utf-8")).get("icons", {}))


def _check_traits(traits: Any, kind: str, fail: Any) -> dict[str, str]:
    if not isinstance(traits, dict):
        raise fail(f"node kind '{kind}': 'traits' must be an object of trait name -> value")
    for trait, value in traits.items():
        if trait not in TRAITS:
            raise fail(f"node kind '{kind}': unknown trait '{trait}' (one of: {', '.join(TRAITS)})")
        if value not in TRAITS[trait]:
            raise fail(
                f"node kind '{kind}': trait '{trait}' has unknown value {value!r} (one of: {', '.join(TRAITS[trait])})"
            )
    return dict(traits)


def _check_icon(icon: Any, kind: str, fail: Any) -> str:
    match = ICON_RE.match(icon) if isinstance(icon, str) else None
    if match is None:
        raise fail(f"node kind '{kind}': icon must be '<pack>:<icon>', e.g. 'aws:lambda'")
    pack, name = match.groups()
    if pack not in icon_packs():
        raise fail(f"node kind '{kind}': unknown icon pack '{pack}' (available: {', '.join(icon_packs())})")
    if name not in icon_names(pack):
        raise fail(
            f"node kind '{kind}': unknown icon '{icon}' (available in {pack}: {', '.join(sorted(icon_names(pack)))})"
        )
    return icon


def kind_packs() -> list[str]:
    return sorted(p.stem for p in KIND_PACK_DIR.glob("*.json"))


def kind_pack_path(name: str) -> Path:
    """The kinds file of a shipped kind pack (ARCVIZ_KIND_PACKS)."""
    if name not in kind_packs():
        raise KindConfigError(f"Unknown kind pack '{name}' in ARCVIZ_KIND_PACKS. Available: {', '.join(kind_packs())}")
    return KIND_PACK_DIR / f"{name}.json"


def _section(data: dict[str, Any], key: str, fail: Any) -> dict[str, Any]:
    section = data.get(key, {})
    if not isinstance(section, dict):
        raise fail(f"'{key}' must be an object of kind name -> definition")
    return section


def _check_name(name: str, what: str, builtin: frozenset[str], fail: Any) -> None:
    if name in builtin:
        raise fail(f"{what} kind '{name}': '{name}' is a built-in kind and cannot be redefined")
    if not NAME_RE.match(name):
        raise fail(f"invalid kind name '{name}' (lowercase letters, digits and _, starting with a letter, max 32)")
    if name in RESERVED:
        raise fail(f"kind name '{name}' is reserved")


def _definition(spec: Any, what: str, allowed: frozenset[str], fail: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise fail(f"{what} must be an object")
    for key in spec:
        if key not in allowed:
            raise fail(f"{what}: unknown key '{key}' (allowed: {', '.join(sorted(allowed))})")
    return spec
