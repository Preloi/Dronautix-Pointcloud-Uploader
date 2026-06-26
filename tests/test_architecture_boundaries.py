import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
V2_SOURCE_ROOTS = (
    REPO_ROOT / "dronautix_uploader" / "core",
    REPO_ROOT / "dronautix_uploader" / "adapters",
    REPO_ROOT / "dronautix_uploader" / "qt_app",
)
UI_FREE_SOURCE_ROOTS = (
    REPO_ROOT / "dronautix_uploader" / "core",
    REPO_ROOT / "dronautix_uploader" / "adapters",
)


def test_v2_sources_do_not_reintroduce_tk_messagebox_or_root_after():
    forbidden_imports = ("customtkinter", "tkinter")
    forbidden_names = ("messagebox", "QMessageBox")
    forbidden_attributes = (("root", "after"),)

    violations = _scan_ast(
        V2_SOURCE_ROOTS,
        forbidden_import_roots=forbidden_imports,
        forbidden_names=forbidden_names,
        forbidden_attributes=forbidden_attributes,
    )

    assert violations == []


def test_core_and_adapters_remain_free_of_qt_imports():
    violations = _scan_ast(
        UI_FREE_SOURCE_ROOTS,
        forbidden_import_roots=("PySide6",),
        forbidden_names=("QtCore", "QtGui", "QtWidgets", "QThread", "Signal"),
        forbidden_attributes=(),
    )

    assert violations == []


def test_v2_sources_use_project_management_label_not_legacy_overview_label():
    forbidden_text = (
        "Projektübersicht",
        "Projektuebersicht",
        "Project overview",
    )

    violations = []
    for source_path in _python_sources(V2_SOURCE_ROOTS):
        text = source_path.read_text(encoding="utf-8")
        for token in forbidden_text:
            if token in text:
                violations.append(f"{source_path.relative_to(REPO_ROOT)} contains {token}")

    assert sorted(violations) == []


def _scan_ast(
    roots,
    *,
    forbidden_import_roots,
    forbidden_names,
    forbidden_attributes,
):
    violations = []
    for source_path in _python_sources(roots):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            violation = _node_violation(
                node,
                forbidden_import_roots=forbidden_import_roots,
                forbidden_names=forbidden_names,
                forbidden_attributes=forbidden_attributes,
            )
            if violation:
                violations.append(f"{source_path.relative_to(REPO_ROOT)}:{node.lineno} {violation}")
    return sorted(violations)


def _node_violation(
    node,
    *,
    forbidden_import_roots,
    forbidden_names,
    forbidden_attributes,
):
    if isinstance(node, ast.Import):
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in forbidden_import_roots:
                return f"imports {alias.name}"
    if isinstance(node, ast.ImportFrom):
        root = (node.module or "").split(".", 1)[0]
        if root in forbidden_import_roots:
            return f"imports from {node.module}"
    if isinstance(node, ast.Name) and node.id in forbidden_names:
        return f"references {node.id}"
    if isinstance(node, ast.Attribute):
        base_name = node.value.id if isinstance(node.value, ast.Name) else ""
        if (base_name, node.attr) in forbidden_attributes:
            return f"references {base_name}.{node.attr}"
    return ""


def _python_sources(roots):
    for root in roots:
        for source_path in root.rglob("*.py"):
            if "__pycache__" in source_path.parts:
                continue
            yield source_path
