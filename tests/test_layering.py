"""Import rules of the service layer (spec: docs/architecture/specs/application-services.md, ADR-0050).

Business logic depends on domain types and the abstract ports in `arcviz/ports.py`, never on the store
package or a storage library; `server.py` is the composition root that hands the repository to the
services and never calls it itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PACKAGE = ROOT / "arcviz"
# Modules allowed to know the store: the store itself, its Git mirror, and the composition roots.
STORAGE_SIDE = {"arcviz/server.py", "arcviz/cli.py", "arcviz/gitmirror.py"}
# azure.identity is a credential library, not storage: the LLM layer uses it for Entra ID tokens (ADR-0115).
STORAGE_LIBRARIES = ("sqlite3", "azure.cosmos")
SERVICE_MAY_IMPORT = {
    "arcviz.model",
    "arcviz.ports",
    "arcviz.operations",
    "arcviz.projects",
    "arcviz.project_model",
    "arcviz.exchange",
    "arcviz.workspaces",
    "arcviz.live",  # the in-process bus and presence registry (spec: collaboration.md); no package imports
    "arcviz.envconfig",  # reading start-up variables (writers.WriterConfig, #189); no package imports
    "arcviz.tokens",  # personal access tokens: the format and the stored shape (ADR-0173)
    "arcviz.proposals",  # stored proposals: the shape, the diff of a preview and the retention (ADR-0172)
    "arcviz.requirement_model",  # requirements: the types, the operations and the agent policy (ADR-0188)
}


def _module_name(file: Path) -> str:
    return file.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".")


def _imports(file: Path) -> list[tuple[str, list[str]]]:
    """(absolute module, imported names) of every import in `file`, also lazy ones inside functions."""
    package = _module_name(file).rsplit(".", 1)[0]
    if file.name == "__init__.py":
        package = _module_name(file).removesuffix(".__init__")
    out: list[tuple[str, list[str]]] = []
    for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.extend((alias.name, []) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names]
            if node.level:
                base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                module = f"{base}.{node.module}" if node.module else base
                if not node.module:  # `from . import x`: each name is a module
                    out.extend((f"{base}.{name}", []) for name in names)
                    continue
            else:
                module = node.module or ""
            out.append((module, names))
    return out


def _business_logic() -> list[Path]:
    files = []
    for file in sorted(PACKAGE.rglob("*.py")):
        rel = file.relative_to(ROOT).as_posix()
        if rel.startswith("arcviz/store/") or rel in STORAGE_SIDE:
            continue
        files.append(file)
    return files


@pytest.mark.parametrize("file", _business_logic(), ids=lambda f: f.relative_to(ROOT).as_posix())
def test_business_logic_does_not_import_the_store_or_a_storage_library(file):
    bad = [
        module
        for module, _ in _imports(file)
        if module == "arcviz.store"
        or module.startswith("arcviz.store.")
        or any(module == library or module.startswith(library + ".") for library in STORAGE_LIBRARIES)
    ]
    assert not bad, f"{file.relative_to(ROOT)} imports storage details: {bad} (use arcviz.ports / a service)"


def test_services_depend_only_on_domain_types_and_ports():
    files = sorted((PACKAGE / "services").rglob("*.py"))
    assert files, "arcviz/services/ exists"
    for file in files:
        for module, _ in _imports(file):
            if module.startswith("arcviz.") and not module.startswith("arcviz.services"):
                assert module in SERVICE_MAY_IMPORT, f"{file.relative_to(ROOT)} imports {module}"


def test_server_imports_only_the_store_factory():
    imported = [(m, names) for m, names in _imports(PACKAGE / "server.py") if m.startswith("arcviz.store")]
    assert imported == [("arcviz.store", ["create_store"])]


def test_routes_never_call_the_repository():
    """The repository is only handed to service constructors; every route goes through a service."""
    tree = ast.parse((PACKAGE / "server.py").read_text(encoding="utf-8"))
    uses = [
        f"store.{node.attr} (line {node.lineno})"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "store"
    ]
    assert not uses, f"server.py calls the repository directly: {uses}"


def test_ports_know_no_backend():
    for module, _ in _imports(PACKAGE / "ports.py"):
        # `live` imports nothing from the package; ports name its PresenceChannel protocol (ADR-0104)
        allowed = ("arcviz.model", "arcviz.projects", "arcviz.workspaces", "arcviz.live")
        assert not module.startswith("arcviz.") or module in allowed, module


# ---- authorization (spec: docs/architecture/specs/workspaces-and-sharing.md, ADR-0084) ----------------------

SERVICE_CLASSES = {
    "AccessPolicy",
    "DiagramService",
    "ModelService",
    "ProjectExchangeService",
    "ProjectService",
    "ServiceRoot",
    "UserService",
    "WorkspaceService",
}


def _create_app() -> ast.FunctionDef:
    tree = ast.parse((PACKAGE / "server.py").read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "create_app")


def test_the_server_builds_services_only_through_the_root():
    """Routes get services bound to the caller from the `Svc` dependency; the server never builds an unguarded
    service itself (only the one ServiceRoot) and never runs anything in the system context but the migration."""
    tree = ast.parse((PACKAGE / "server.py").read_text(encoding="utf-8"))
    built = [
        f"{node.func.id} (line {node.lineno})"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in SERVICE_CLASSES
    ]
    assert built == [built[0]] and built[0].startswith("ServiceRoot"), built
    forbidden = {"guard", "migrate_to_workspaces", "set_owner", "reconcile", "unplace", "ensure_default"}
    calls = [
        f"{node.func.attr} (line {node.lineno})"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in forbidden
    ]
    assert not calls, f"server.py bypasses the bound services: {calls}"


def test_every_route_with_data_gets_the_bound_services():
    """A route whose path names a workspace, project or diagram takes `svc: Svc` (the caller's services), or `svc:
    TurnSvc` (the same without the per-request project cache: chat turns and live streams, ADR-0121)."""
    missing = []
    for fn in _create_app().body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        routes = [
            d
            for d in fn.decorator_list
            if isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "app"
        ]
        if not routes or not routes[0].args or not isinstance(routes[0].args[0], ast.Constant):
            continue
        path = str(routes[0].args[0].value)
        if not path.startswith("/api/") or path in ("/api/config", "/api/me"):
            continue
        annotations = {a.arg: ast.unparse(a.annotation) if a.annotation else "" for a in fn.args.args}
        if annotations.get("svc") not in ("Svc", "TurnSvc"):
            missing.append(f"{fn.name} ({path})")
    assert not missing, f"routes without the caller's services: {missing}"
