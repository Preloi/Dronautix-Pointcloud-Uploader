"""Check whether the V2 preview is locally ready without production cutover gates."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dronautix_uploader.core.golden_capture import (  # noqa: E402
    check_v2_output_freshness,
    check_v2_output_readiness,
)


DEFAULT_GOLDEN_MANIFEST = Path("tests/golden/manifest.json")
PREVIEW_MODULES = (
    "Dronautix_Pointcloud_Uploader_v2",
    "dronautix_uploader.qt_app",
    "dronautix_uploader.qt_app.app",
    "dronautix_uploader.qt_app.app_identity",
    "dronautix_uploader.qt_app.main_window",
    "dronautix_uploader.qt_app.pages",
    "dronautix_uploader.qt_app.project_management",
    "dronautix_uploader.qt_app.project_management_actions",
    "dronautix_uploader.qt_app.project_management_controller",
    "dronautix_uploader.qt_app.project_management_dialog_models",
    "dronautix_uploader.qt_app.project_management_dialogs",
    "dronautix_uploader.qt_app.runtime_services",
    "dronautix_uploader.qt_app.settings_controller",
    "dronautix_uploader.qt_app.upload_dialog_models",
    "dronautix_uploader.qt_app.upload_dialogs",
    "dronautix_uploader.qt_app.upload_workflow_controller",
    "dronautix_uploader.qt_app.upload_wizard_model",
)
REQUIRED_PREVIEW_FILES = (
    Path("Dronautix_Pointcloud_Uploader_v2.py"),
    Path("build_v2_preview.py"),
    Path("requirements-v2-preview.txt"),
    Path("icon.ico"),
    Path("bundled_tools/PotreeConverter/PotreeConverter.exe"),
    Path("bundled_tools/PotreeConverter/laszip.dll"),
)
OPTIONAL_BUILD_PACKAGES = (
    ("PyInstaller", "pyinstaller"),
    ("PySide6", "PySide6"),
)


@dataclass(frozen=True)
class PreviewGate:
    label: str
    status: str
    detail: str

    @property
    def passed(self) -> bool:
        return self.status == "ok"

    @property
    def warning(self) -> bool:
        return self.status == "warning"

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"


@dataclass(frozen=True)
class PreviewReadinessReport:
    gates: tuple[PreviewGate, ...]

    @property
    def ready(self) -> bool:
        return all(not gate.blocked for gate in self.gates)

    @property
    def warning_count(self) -> int:
        return sum(1 for gate in self.gates if gate.warning)


def build_preview_readiness_report(
    *,
    manifest_path: str | Path = DEFAULT_GOLDEN_MANIFEST,
    v2_output_root: str | Path | None = None,
    require_build_dependencies: bool = False,
) -> PreviewReadinessReport:
    gates = [
        _check_required_files(REQUIRED_PREVIEW_FILES),
        _check_preview_imports(PREVIEW_MODULES),
        _check_preview_build_isolation(),
        _check_v2_outputs(manifest_path, v2_output_root=v2_output_root),
        _check_v2_output_freshness(manifest_path, v2_output_root=v2_output_root),
        _check_build_dependencies(require_build_dependencies=require_build_dependencies),
    ]
    return PreviewReadinessReport(tuple(gates))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check local V2 preview readiness.")
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_GOLDEN_MANIFEST),
        help="Path to tests/golden/manifest.json.",
    )
    parser.add_argument(
        "--v2-output-root",
        default=None,
        help="Root containing V2 output fixtures by scenario.",
    )
    parser.add_argument(
        "--require-build-deps",
        action="store_true",
        help="Fail when PyInstaller/PySide6 are not installed.",
    )
    args = parser.parse_args(argv)

    report = build_preview_readiness_report(
        manifest_path=args.manifest,
        v2_output_root=args.v2_output_root,
        require_build_dependencies=args.require_build_deps,
    )
    for gate in report.gates:
        print(f"[{gate.status.upper()}] {gate.label}: {gate.detail}")

    if report.ready:
        suffix = f" ({report.warning_count} Warnung(en))" if report.warning_count else ""
        print(f"V2 preview gate: OK{suffix}")
        return 0
    print("V2 preview gate: BLOCKED")
    return 1


def _check_required_files(paths: Iterable[Path]) -> PreviewGate:
    missing = tuple(str(path) for path in paths if not path.exists())
    if missing:
        return PreviewGate("Preview-Dateien", "blocked", f"Fehlt: {', '.join(missing)}.")
    return PreviewGate("Preview-Dateien", "ok", "Entrypoint, Build-Skript, Icon und PotreeConverter vorhanden.")


def _check_preview_imports(module_names: Iterable[str]) -> PreviewGate:
    failed: list[str] = []
    for module_name in module_names:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            failed.append(f"{module_name} ({exc})")
    if failed:
        return PreviewGate("Import-Sicherheit", "blocked", f"Import fehlgeschlagen: {', '.join(failed)}.")
    return PreviewGate("Import-Sicherheit", "ok", "Preview-Module importieren auch ohne installierte PySide6.")


def _check_preview_build_isolation() -> PreviewGate:
    try:
        import build_v2_preview
    except Exception as exc:
        return PreviewGate("Preview-Build-Isolation", "blocked", f"Build-Skript nicht importierbar: {exc}")

    command = build_v2_preview.build_command()
    command_text = " ".join(command)
    if build_v2_preview.ENTRYPOINT != "Dronautix_Pointcloud_Uploader_v2.py":
        return PreviewGate("Preview-Build-Isolation", "blocked", "Preview-Build nutzt nicht den V2-Entrypoint.")
    if build_v2_preview.DIST_DIR in {"dist", "Output"} or build_v2_preview.BUILD_DIR == "build":
        return PreviewGate("Preview-Build-Isolation", "blocked", "Preview-Build nutzt produktive Build-Ordner.")
    forbidden = ("latest-release.json", "Dronautix_Pointcloud_Uploader.py", "installer_version.iss")
    leaked = tuple(value for value in forbidden if value in command_text)
    if leaked:
        return PreviewGate("Preview-Build-Isolation", "blocked", f"Preview-Befehl referenziert Release-Dateien: {', '.join(leaked)}.")
    return PreviewGate("Preview-Build-Isolation", "ok", "Separater Entrypoint und isolierte Preview-Build-Ordner.")


def _check_v2_outputs(manifest_path: str | Path, *, v2_output_root: str | Path | None) -> PreviewGate:
    try:
        report = check_v2_output_readiness(manifest_path, actual_root=v2_output_root)
    except Exception as exc:
        return PreviewGate("V2-Ausgaben", "blocked", f"Nicht pruefbar: {exc}")
    detail = f"{report.output_count}/{report.scenario_count} V2-Szenarien vorhanden und normalisierbar."
    if report.ready:
        return PreviewGate("V2-Ausgaben", "ok", detail)
    first_issue = report.issues[0] if report.issues else None
    if first_issue is not None:
        detail = f"{detail} {first_issue.scenario_id}: {first_issue.message}"
    return PreviewGate("V2-Ausgaben", "blocked", detail)


def _check_v2_output_freshness(manifest_path: str | Path, *, v2_output_root: str | Path | None) -> PreviewGate:
    try:
        report = check_v2_output_freshness(manifest_path, actual_root=v2_output_root)
    except Exception as exc:
        return PreviewGate("V2-Ausgaben aktuell", "blocked", f"Nicht pruefbar: {exc}")
    detail = f"{report.checked_count}/{report.scenario_count} V2-Szenarien frisch gegen Generator geprueft."
    if report.ready:
        return PreviewGate("V2-Ausgaben aktuell", "ok", detail)
    first_issue = report.issues[0] if report.issues else None
    if first_issue is not None:
        detail = f"{detail} {first_issue.scenario_id}: {first_issue.message}"
    return PreviewGate("V2-Ausgaben aktuell", "blocked", detail)


def _check_build_dependencies(*, require_build_dependencies: bool) -> PreviewGate:
    missing = tuple(package_name for module_name, package_name in OPTIONAL_BUILD_PACKAGES if importlib.util.find_spec(module_name) is None)
    if not missing:
        return PreviewGate("Build-Abhaengigkeiten", "ok", "PyInstaller und PySide6 installiert.")
    status = "blocked" if require_build_dependencies else "warning"
    detail = f"Fehlt fuer EXE-Build: {', '.join(missing)}. Installation: pip install -r requirements-v2-preview.txt"
    return PreviewGate("Build-Abhaengigkeiten", status, detail)


if __name__ == "__main__":
    raise SystemExit(main())
