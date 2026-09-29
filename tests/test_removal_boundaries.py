"""The removed V1 app and V2 cutover tooling must not come back.

Roadmap step 3 first proved with this test that no kept module imported them,
then deleted them. It now guards against re-introducing a dependency.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "dronautix_uploader"

REMOVED_MODULES = {
    # V1 (CustomTkinter) application
    "dronautix_uploader.main",
    "dronautix_uploader.views",
    "dronautix_uploader.config",
    "dronautix_uploader.converter",
    "dronautix_uploader.crs",
    "dronautix_uploader.project_ops",
    "dronautix_uploader.s3_operations",
    "dronautix_uploader.ui_helpers",
    "dronautix_uploader.updater",
    "dronautix_uploader.utils",
    "dronautix_uploader.adapters.legacy_project_ops",
    # finished V2 cutover / Golden-capture tooling
    "dronautix_uploader.core.golden_capture",
    "dronautix_uploader.core.golden_normalization",
    "dronautix_uploader.core.cutover_acceptance",
    "dronautix_uploader.core.legacy_update_acceptance",
    "dronautix_uploader.core.s3_acceptance_smoke",
    "dronautix_uploader.core.github_asset_verification",
    "dronautix_uploader.core.v2_golden_output",
    "dronautix_uploader.qt_app.cutover_readiness_controller",
}

REMOVED_ROOT_FILES = (
    "Dronautix_Pointcloud_Uploader.py",  # compatibility entry point, unused by build and installer
    "pointcloud-uploader-memory.md",  # notes from the V1 era (1.7.8)
)


def _module_name(path: Path) -> str:
    relative = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_removed(module: str) -> bool:
    return any(module == removed or module.startswith(removed + ".") for removed in REMOVED_MODULES)


def _imported_modules(path: Path, module: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                target = ".".join([*base, node.module] if node.module else base)
            else:
                target = node.module or ""
            imported.add(target)
            imported |= {f"{target}.{alias.name}" for alias in node.names}
    return imported


def _kept_modules():
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = _module_name(path)
        if not _is_removed(module):
            yield path, module


def test_no_kept_module_imports_code_scheduled_for_removal():
    violations = [
        f"{path.relative_to(REPO_ROOT)} -> {target}"
        for path, module in _kept_modules()
        for target in sorted(_imported_modules(path, module))
        if _is_removed(target)
    ]

    assert violations == []


def test_release_entry_points_do_not_use_removed_code():
    for entry in (
        "Dronautix_Pointcloud_Uploader_v2_final.py",
        "Dronautix_Pointcloud_Uploader_v2.py",
        "build_exe.py",
        "build_v2_preview.py",
    ):
        path = REPO_ROOT / entry
        targets = _imported_modules(path, path.stem)
        assert not [target for target in targets if _is_removed(target)], entry


def test_removed_modules_stay_deleted():
    leftovers = []
    for module in REMOVED_MODULES:
        relative = Path(*module.split("."))
        package_dir = REPO_ROOT / relative
        has_sources = package_dir.is_dir() and any(package_dir.rglob("*.py"))
        if (REPO_ROOT / relative.with_suffix(".py")).exists() or has_sources:
            leftovers.append(module)
    leftovers += [name for name in REMOVED_ROOT_FILES if (REPO_ROOT / name).exists()]
    assert leftovers == []
