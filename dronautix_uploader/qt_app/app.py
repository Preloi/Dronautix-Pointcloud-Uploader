"""Runtime bootstrap for the PySide6 QtWidgets preview."""

from __future__ import annotations

import os
import sys

from dronautix_uploader.core.config_service import get_config_locations, migrate_legacy_config_if_missing

from .app_identity import QtAppIdentity, resolve_app_identity

RUNTIME_MODE_ENV = "DRONAUTIX_QT_APP_MODE"
FINAL_MODE_ARGS = {"--final", "--production", "--stable"}
PREVIEW_MODE_ARGS = {"--preview", "--v2-preview", "--qt-preview"}


def prepare_runtime_config(
    identity: str | QtAppIdentity | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> bool:
    """Copy legacy config before controllers load runtime settings."""

    resolved = resolve_app_identity(identity)
    locations = get_config_locations(preview=resolved.uses_preview_config, environ=environ)
    return migrate_legacy_config_if_missing(locations)


def prepare_preview_config(*, preview: bool = True, environ: dict[str, str] | None = None) -> bool:
    """Backward-compatible helper for tests and preview tooling."""

    return prepare_runtime_config("preview" if preview else "final", environ=environ)


def resolve_runtime_identity(
    argv: list[str] | tuple[str, ...] | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> QtAppIdentity:
    """Resolve Preview/Final mode without importing Qt."""

    args = list(sys.argv if argv is None else argv)
    arg_mode = _runtime_mode_from_args(args)
    if arg_mode:
        return resolve_app_identity(arg_mode)

    env = os.environ if environ is None else environ
    env_mode = str(env.get(RUNTIME_MODE_ENV, "") or "").strip()
    if env_mode:
        return resolve_app_identity(env_mode)

    return resolve_app_identity(_runtime_mode_from_executable(args[0] if args else ""))


def qt_argv_without_runtime_mode(argv: list[str] | tuple[str, ...] | None = None) -> list[str]:
    args = list(sys.argv if argv is None else argv)
    return [
        arg
        for arg in args
        if str(arg).strip().lower() not in FINAL_MODE_ARGS | PREVIEW_MODE_ARGS
    ]


def _runtime_mode_from_args(args: list[str]) -> str:
    normalized_args = {str(arg).strip().lower() for arg in args[1:]}
    if normalized_args & FINAL_MODE_ARGS:
        return "final"
    if normalized_args & PREVIEW_MODE_ARGS:
        return "preview"
    return ""


def _runtime_mode_from_executable(executable_path: str) -> str:
    from pathlib import Path

    from app_version import APP_EXE_NAME

    executable_name = Path(str(executable_path or "")).name.lower()
    production_name = APP_EXE_NAME.lower()
    if executable_name == production_name or Path(executable_name).stem == Path(production_name).stem:
        return "final"
    return "preview"


def run(
    argv: list[str] | None = None,
    *,
    mode: str | QtAppIdentity | None = None,
    environ: dict[str, str] | None = None,
) -> int:
    """Start the Qt preview application.

    PySide6 is imported here so importing this package stays safe in test
    environments where Qt is not installed.
    """

    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError as exc:
        raise RuntimeError(
            "PySide6 is required to run the Qt preview. Install PySide6 and "
            "start Dronautix_Pointcloud_Uploader_v2.py again."
        ) from exc

    from .main_window import create_main_window
    from .local_conversion_controller import LocalConversionController
    from .settings_controller import SettingsController
    from .update_controller import UpdateController
    from .runtime_services import (
        create_runtime_controller_bundle,
        load_project_management_runtime_config,
    )
    from .style import APP_STYLE

    raw_argv = list(sys.argv if argv is None else argv)
    identity = resolve_app_identity(mode) if mode is not None else resolve_runtime_identity(raw_argv, environ=environ)
    app = QtWidgets.QApplication(qt_argv_without_runtime_mode(raw_argv))
    app.setApplicationName(identity.application_name)
    app.setOrganizationName("Dronautix")
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)

    font = QtGui.QFont("Segoe UI", 10)
    app.setFont(font)

    prepare_runtime_config(identity)
    local_conversion_controller = LocalConversionController()
    settings_controller = SettingsController(preview=identity.uses_preview_config)
    update_controller = UpdateController(settings_controller=settings_controller)

    def load_runtime_bundle():
        runtime_config = load_project_management_runtime_config(preview=identity.uses_preview_config)
        return create_runtime_controller_bundle(runtime_config)

    runtime_bundle = load_runtime_bundle()

    window = create_main_window(
        QtCore,
        QtGui,
        QtWidgets,
        project_provider=runtime_bundle.project_provider,
        project_controller=runtime_bundle.project_controller,
        upload_controller=runtime_bundle.upload_controller,
        local_conversion_controller=local_conversion_controller,
        settings_controller=settings_controller,
        update_controller=update_controller,
        runtime_reloader=load_runtime_bundle,
        project_runtime_status=runtime_bundle.status or identity.default_runtime_status,
        window_title=identity.window_title,
        sidebar_badge=identity.sidebar_badge,
    )
    window.show()
    return app.exec()


__all__ = [
    "RUNTIME_MODE_ENV",
    "prepare_preview_config",
    "prepare_runtime_config",
    "qt_argv_without_runtime_mode",
    "resolve_runtime_identity",
    "run",
]
