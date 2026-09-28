"""Main window factory for the Qt preview.

The window class is assembled from mixins in ``qt_app/window/``; this module
keeps construction, navigation, shortcuts and the startup temp cleanup.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import glob
import logging
import os
import tempfile
import threading
import time

from .activity_model import ActivityLogStore
from .project_management import ProjectPreview
from .window.project_actions import ProjectActionsMixin
from .window.runtime import RuntimeMixin
from .window.support import (
    GLB_UPLOAD_STAGING_ROOT_NAME,
    GLB_UPLOAD_STALE_AGE_SECONDS,
    GLB_UPLOAD_TEMP_PREFIX,
    UPLOAD_TEMP_PREFIX,
    _remove_temp_dir_with_retry,
    make_qt_types,
    resolve_runtime_project_rows,
)
from .window.tasks import TaskRunnerMixin
from .window.updates import UpdateMixin
from .window.uploads import UploadMixin

__all__ = [
    "GLB_UPLOAD_STAGING_ROOT_NAME",
    "cleanup_stale_upload_temp_dirs",
    "create_main_window",
    "resolve_runtime_project_rows",
]

ProjectProvider = Callable[[], Iterable[ProjectPreview]]
ProjectActionCallback = Callable[..., None]

LOGGER = logging.getLogger(__name__)


def cleanup_stale_upload_temp_dirs(
    max_age_seconds: int = 1800,
    glb_max_age_seconds: int = GLB_UPLOAD_STALE_AGE_SECONDS,
) -> tuple[str, ...]:
    """Remove stale app-owned temp folders without ever touching source assets."""

    now = time.time()
    temp_root = tempfile.gettempdir()
    warnings: list[str] = []
    for path in glob.glob(os.path.join(temp_root, f"{UPLOAD_TEMP_PREFIX}*")):
        try:
            if not os.path.isdir(path) or not os.path.basename(path).startswith(UPLOAD_TEMP_PREFIX):
                continue
            if now - os.path.getmtime(path) < max_age_seconds:
                continue
            if glob.glob(os.path.join(path, f"{GLB_UPLOAD_TEMP_PREFIX}*")):
                continue
            _remove_temp_dir_with_retry(path, warnings)
        except OSError as error:
            warning = f"Temporären Upload-Ordner nicht geprüft: {error}"
            warnings.append(warning)
            LOGGER.warning(warning)
    # GLB originals may be selected from any temp directory. Only the
    # dedicated app-owned root is safe to sweep on startup.
    glb_patterns = (os.path.join(temp_root, GLB_UPLOAD_STAGING_ROOT_NAME, f"{GLB_UPLOAD_TEMP_PREFIX}*"),)
    for pattern in glb_patterns:
        for path in glob.glob(pattern):
            try:
                if not os.path.isdir(path) or not os.path.basename(path).startswith(GLB_UPLOAD_TEMP_PREFIX):
                    continue
                if now - os.path.getmtime(path) < glb_max_age_seconds:
                    continue
                _remove_temp_dir_with_retry(path, warnings)
            except OSError as error:
                warning = f"Temporären GLB-Ordner nicht geprüft: {error}"
                warnings.append(warning)
                LOGGER.warning(warning)
    return tuple(warnings)


def create_main_window(
    QtCore,
    QtGui,
    QtWidgets,
    *,
    project_previews: Iterable[ProjectPreview] | None = None,
    project_provider: ProjectProvider | None = None,
    on_project_action: ProjectActionCallback | None = None,
    project_controller=None,
    upload_controller=None,
    local_conversion_controller=None,
    settings_controller=None,
    update_controller=None,
    runtime_reloader=None,
    project_runtime_status: str = "Nicht verbunden - AWS-Zugangsdaten in den Einstellungen hinterlegen",
    window_title: str = "Dronautix Pointcloud Uploader",
    sidebar_badge: str = "",
):
    """Create the main window after PySide6 has been imported."""

    from .pages import (
        create_projects_page,
        create_settings_page,
        create_upload_page,
    )

    qt_types = make_qt_types(QtCore)

    class MainWindow(
        TaskRunnerMixin,
        UpdateMixin,
        ProjectActionsMixin,
        UploadMixin,
        RuntimeMixin,
        QtWidgets.QMainWindow,
    ):
        def __init__(self):
            super().__init__()
            # Closure values used by the mixins (Qt is imported lazily by the caller).
            self.QtCore, self.QtGui, self.QtWidgets = QtCore, QtGui, QtWidgets
            self._qt_types = qt_types
            self._project_previews = project_previews
            self._on_project_action_override = on_project_action
            self._local_conversion_controller = local_conversion_controller
            self._settings_controller = settings_controller
            self._update_controller = update_controller
            self._runtime_reloader = runtime_reloader
            self.setObjectName("MainWindow")
            self.setWindowTitle(window_title)
            self.resize(1280, 820)
            self.setMinimumSize(1040, 680)

            self._current_page_name = "Upload"
            self._settings_store = QtCore.QSettings("Dronautix", "PointcloudUploaderV2")
            saved_geometry = self._settings_store.value("window_geometry")
            if saved_geometry is not None:
                try:
                    self.restoreGeometry(saved_geometry)
                except (TypeError, ValueError):
                    pass

            self._buttons = {}
            self._pages = {}
            self._runtime = {
                "project_provider": project_provider,
                "project_controller": project_controller,
                "upload_controller": upload_controller,
                "status": project_runtime_status,
                "disconnected_project_previews": tuple(project_previews or ()),
            }
            self._activity_store = ActivityLogStore(())
            self._active_tasks = []
            self._task_records = {}
            self._progress_bridges = {}
            self._spatial_warning_emitter = qt_types.SpatialWarningEmitter()
            self._spatial_warning_emitter.requested.connect(self._show_spatial_warning)
            self._crs_repair_emitter = qt_types.CrsRepairEmitter()
            self._crs_repair_emitter.requested.connect(self._show_crs_repair_confirmation)
            self._closing_for_update = False
            self._pending_update_result = None
            self._pending_update_check_silent = None
            self._update_install_started = False
            # rmtree of large stale conversion folders must not delay the first paint.
            threading.Thread(
                target=cleanup_stale_upload_temp_dirs, name="startup-temp-cleanup", daemon=True
            ).start()
            root = QtWidgets.QWidget()
            root.setObjectName("AppRoot")
            layout = QtWidgets.QHBoxLayout(root)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            layout.addWidget(self._build_sidebar())

            self.stack = QtWidgets.QStackedWidget()
            self.stack.setObjectName("ContentStack")
            layout.addWidget(self.stack, 1)
            self.setCentralWidget(root)
            # Permanent connection state on the right; transient messages (update
            # checks, action results) use the left part and no longer hide it.
            self._connection_state = "checking" if self._runtime.get("project_provider") is not None else "none"
            self._connection_detail = ""
            self._connection_label = QtWidgets.QLabel()
            self._connection_label.setObjectName("ConnectionStatus")
            self.statusBar().addPermanentWidget(self._connection_label)
            self._show_connection_status()
            self.statusBar().showMessage(self._runtime["status"], 8000)

            self._upload_cancel_event = None
            self._upload_page = self._add_page(
                "Upload",
                lambda: create_upload_page(
                    QtCore,
                    QtWidgets,
                    on_start=self._handle_upload_action,
                    on_cancel=self._request_upload_cancel,
                    defaults_provider=self._settings_dialog_defaults,
                ),
            )
            self._projects_page = self._add_page(
                "Projektverwaltung",
                lambda: create_projects_page(
                    QtCore,
                    QtGui,
                    QtWidgets,
                    self._placeholder_action,
                    project_previews=project_previews,
                    project_provider=self._runtime_project_rows,
                    on_project_action=on_project_action or self._handle_project_action,
                    on_load_state_changed=self._resume_pending_update_if_idle,
                    can_start_load=lambda: not self._update_install_started,
                    empty_state_provider=self._projects_empty_state_reason,
                    on_open_settings=lambda: self._select_page("Einstellungen"),
                    on_load_finished=self._handle_project_load_finished,
                ),
            )
            self._activity_page = None
            self._settings_page = self._add_page(
                "Einstellungen",
                lambda: create_settings_page(
                    QtCore,
                    QtWidgets,
                    settings_state_provider=settings_controller.load_state if settings_controller is not None else None,
                    settings_provider=settings_controller.preview if settings_controller is not None else None,
                    on_settings_action=self._handle_settings_action,
                ),
            )

            # Ohne S3-Verbindung direkt in die Einstellungen leiten statt auf
            # einer leeren Upload-Seite zu starten.
            if settings_controller is not None and project_controller is None:
                self._select_page("Einstellungen")
            else:
                self._select_page("Upload")
            self._install_shortcuts()
            self._schedule_startup_update_check()

        def _install_shortcuts(self):
            page_order = list(self._buttons)
            for position, page_name in enumerate(page_order, start=1):
                shortcut = QtGui.QShortcut(QtGui.QKeySequence(f"Ctrl+{position}"), self)
                shortcut.activated.connect(lambda name=page_name: self._select_page(name))

            search_shortcut = QtGui.QShortcut(QtGui.QKeySequence.Find, self)
            search_shortcut.activated.connect(self._focus_project_search)

            refresh_shortcut = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_F5), self)
            refresh_shortcut.activated.connect(self._refresh_current_page)

            escape_shortcut = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape), self)
            escape_shortcut.activated.connect(self._handle_escape)

            for sequence in ("Ctrl+Return", "Ctrl+Enter"):
                upload_shortcut = QtGui.QShortcut(QtGui.QKeySequence(sequence), self)
                upload_shortcut.activated.connect(self._trigger_upload_shortcut)

        def _focus_project_search(self):
            self._select_page("Projektverwaltung")
            focus_search = getattr(self._projects_page, "focus_search", None)
            if callable(focus_search):
                focus_search()

        def _refresh_current_page(self):
            page = self._pages.get(self._current_page_name)
            for attr in ("reload_projects", "reload_activity", "reload_settings"):
                reload_callable = getattr(page, attr, None)
                if callable(reload_callable):
                    reload_callable()
                    self.statusBar().showMessage(f"{self._current_page_name} aktualisiert")
                    return

        def _handle_escape(self):
            if self._current_page_name == "Projektverwaltung":
                clear_search = getattr(self._projects_page, "clear_search", None)
                if callable(clear_search):
                    clear_search()

        def _trigger_upload_shortcut(self):
            if self._current_page_name == "Upload":
                self._handle_upload_action()

        def closeEvent(self, event):
            if self._has_active_background_tasks() and not getattr(self, "_closing_for_update", False):
                QtWidgets.QMessageBox.warning(
                    self,
                    "Vorgang läuft noch",
                    "Ein Vorgang (z. B. Upload) läuft noch. Bitte den Vorgang zuerst abbrechen "
                    "oder vollständig abschließen lassen.",
                )
                event.ignore()
                return
            try:
                self._settings_store.setValue("window_geometry", self.saveGeometry())
            except Exception:
                pass
            super().closeEvent(event)

        def _build_sidebar(self):
            sidebar = QtWidgets.QFrame()
            sidebar.setObjectName("Sidebar")
            sidebar.setFixedWidth(248)
            layout = QtWidgets.QVBoxLayout(sidebar)
            layout.setContentsMargins(18, 22, 18, 18)
            layout.setSpacing(10)

            title = QtWidgets.QLabel("Dronautix")
            title.setObjectName("SidebarTitle")
            subtitle = QtWidgets.QLabel("Pointcloud Uploader")
            subtitle.setObjectName("SidebarSubtitle")
            layout.addWidget(title)
            layout.addWidget(subtitle)
            layout.addSpacing(18)

            for name in (
                "Upload",
                "Projektverwaltung",
                "Einstellungen",
            ):
                button = QtWidgets.QPushButton(name)
                button.setObjectName("SidebarButton")
                button.setCheckable(True)
                button.setCursor(QtCore.Qt.PointingHandCursor)
                button.clicked.connect(lambda checked=False, page=name: self._select_page(page))
                self._buttons[name] = button
                layout.addWidget(button)

            layout.addStretch(1)

            if sidebar_badge:
                version_label = QtWidgets.QLabel(sidebar_badge)
                version_label.setObjectName("PreviewBadge")
                layout.addWidget(version_label)
            return sidebar

        def _add_page(self, name: str, factory: Callable):
            widget = factory()
            self._pages[name] = widget
            self.stack.addWidget(widget)
            return widget

        def _select_page(self, name: str):
            names = list(self._buttons)
            index = names.index(name)
            self.stack.setCurrentIndex(index)
            for button_name, button in self._buttons.items():
                button.setChecked(button_name == name)
            self._current_page_name = name
            focus_default = getattr(self._pages.get(name), "focus_default", None)
            if callable(focus_default):
                QtCore.QTimer.singleShot(0, focus_default)


        @QtCore.Slot(object)
        def _show_spatial_warning(self, warning: _SpatialWarningRequest):
            answer = QtWidgets.QMessageBox.warning(
                self,
                "Modelle außerhalb der Punktwolke",
                f"{warning.message}\n\nTrotzdem hochladen?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            warning.accepted = answer == QtWidgets.QMessageBox.Yes
            warning.completed.set()

        @QtCore.Slot(object)
        def _show_crs_repair_confirmation(self, repair: _CrsRepairRequest):
            answer = QtWidgets.QMessageBox.warning(
                self,
                "CRS-Metadaten reparieren",
                f"{repair.message}\n\nAudit-Hinweis: Die übernommenen CRS-Metadaten werden im Projektverlauf protokolliert.\n\nCRS-Metadaten jetzt reparieren und fortfahren?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            repair.accepted = answer == QtWidgets.QMessageBox.Yes
            repair.completed.set()


    return MainWindow()


