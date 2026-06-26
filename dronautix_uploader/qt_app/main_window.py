"""Main window factory for the Qt preview."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import dataclasses
import shutil
import tempfile
import threading
from typing import Any

from .activity_model import (
    ACTION_CONVERT as ACTIVITY_ACTION_CONVERT,
    ACTION_DELETE as ACTIVITY_ACTION_DELETE,
    ACTION_DOWNLOAD as ACTIVITY_ACTION_DOWNLOAD,
    ACTION_REPLACE as ACTIVITY_ACTION_REPLACE,
    ACTION_UPDATE as ACTIVITY_ACTION_UPDATE,
    ACTION_UPLOAD as ACTIVITY_ACTION_UPLOAD,
    ActivityLogStore,
    example_activity_preview,
)
from .project_management import ProjectPreview, example_project_previews
from .project_management_actions import (
    ACTION_DELETE,
    ACTION_DISABLE_LINK,
    ACTION_COPY_LINK,
    ACTION_DOWNLOAD,
    ACTION_ENABLE_LINK,
    ACTION_DUPLICATE,
    ACTION_OPEN_LINK,
    ACTION_RENAME,
    ACTION_REPLACE_ALL_POINTCLOUDS,
    ACTION_REPLACE_SINGLE_POINTCLOUD,
    ProjectOperationSummary,
    action_by_id,
)
from .service_bridge import QtServiceBridge
from .task_worker import create_task_worker


ProjectProvider = Callable[[], Iterable[ProjectPreview]]
ProjectActionCallback = Callable[..., None]


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
    cutover_readiness_controller=None,
    runtime_reloader=None,
    project_runtime_status: str = "Qt Preview - keine Service-Integration aktiv",
    window_title: str = "Dronautix Pointcloud Uploader",
    sidebar_badge: str = "QtWidgets Preview",
):
    """Create the preview main window after PySide6 has been imported."""

    from .pages import (
        create_activity_page,
        create_projects_page,
        create_settings_page,
        create_upload_page,
    )

    class _MainThreadTaskDispatcher(QtCore.QObject):
        """Marshals worker-thread signals onto the GUI thread.

        Instances are created on the main thread, so connecting the worker's
        cross-thread signals to these slots uses a queued connection and the
        wrapped callbacks run on the GUI thread.
        """

        def __init__(self, on_result, on_error, on_finished):
            super().__init__()
            self._on_result = on_result
            self._on_error = on_error
            self._on_finished = on_finished

        @QtCore.Slot(object)
        def dispatch_result(self, value):
            if self._on_result is not None:
                self._on_result(value)

        @QtCore.Slot(object)
        def dispatch_error(self, error):
            if self._on_error is not None:
                self._on_error(error)

        @QtCore.Slot()
        def dispatch_finished(self):
            if self._on_finished is not None:
                self._on_finished()

    class MainWindow(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()
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
                "disconnected_project_previews": tuple(project_previews or example_project_previews()),
            }
            initial_activity = (
                ()
                if (
                    self._runtime["project_controller"] is not None
                    or self._runtime["upload_controller"] is not None
                    or local_conversion_controller is not None
                    or settings_controller is not None
                    or update_controller is not None
                )
                else example_activity_preview().entries
            )
            self._activity_store = ActivityLogStore(initial_activity)
            self._active_tasks = []
            self._task_records = {}
            self._progress_bridges = {}
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
            self.statusBar().showMessage(self._runtime["status"])

            self._upload_page = self._add_page(
                "Upload",
                lambda: create_upload_page(
                    QtCore,
                    QtWidgets,
                    on_start=self._handle_upload_action,
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
                ),
            )
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
            self._activity_page = self._add_page(
                "Aktivitäten",
                lambda: create_activity_page(
                    QtCore,
                    QtGui,
                    QtWidgets,
                    activity_provider=self._activity_store.preview,
                ),
            )

            self._select_page("Upload")
            self._install_shortcuts()

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
                "Aktivitäten",
            ):
                button = QtWidgets.QPushButton(name)
                button.setObjectName("SidebarButton")
                button.setCheckable(True)
                button.setCursor(QtCore.Qt.PointingHandCursor)
                button.clicked.connect(lambda checked=False, page=name: self._select_page(page))
                self._buttons[name] = button
                layout.addWidget(button)

            layout.addStretch(1)

            preview_label = QtWidgets.QLabel(sidebar_badge)
            preview_label.setObjectName("PreviewBadge")
            layout.addWidget(preview_label)
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
            self.statusBar().showMessage(f"{name} geöffnet")
            focus_default = getattr(self._pages.get(name), "focus_default", None)
            if callable(focus_default):
                QtCore.QTimer.singleShot(0, focus_default)

        def _placeholder_action(self, action_id: str, project: ProjectPreview | None = None, pointcloud=None):
            try:
                action_label = action_by_id(action_id).label
            except KeyError:
                action_label = action_id
            if project is None:
                self.statusBar().showMessage(f"{action_label}: UI-Platzhalter ohne Service-Integration")
                return
            if pointcloud is not None:
                self.statusBar().showMessage(
                    f"{action_label}: {project.project} / {pointcloud.name} - UI-Platzhalter ohne Service-Integration"
                )
                return
            self.statusBar().showMessage(f"{action_label}: {project.project} - UI-Platzhalter ohne Service-Integration")

        def _handle_upload_action(self):
            if self._has_active_background_tasks():
                self.statusBar().showMessage("Eine Aktion läuft bereits; bitte warten.")
                return
            form = self._upload_page.read_form()
            if form.mode == "convert":
                self._run_local_conversion(form)
            else:
                self._run_new_upload(form)

        def _run_new_upload(self, form):
            upload_controller = self._runtime.get("upload_controller")
            if upload_controller is None:
                self._upload_page.show_error("Upload: keine Service-Integration aktiv (AWS-Zugangsdaten pruefen).")
                self.statusBar().showMessage("Upload: UI-Platzhalter ohne Service-Integration")
                return
            from .upload_dialog_models import UploadDialogState, validate_upload_dialog_state

            # Convert into a temporary, app-writable folder instead of the user's
            # output directory. It is removed once the upload finishes.
            temp_output_dir = tempfile.mkdtemp(prefix="dronautix_potree_")

            try:
                request = validate_upload_dialog_state(
                    UploadDialogState(
                        customer=form.customer,
                        project=form.project,
                        source_paths=form.source_paths,
                        converter_path=form.converter_path,
                        output_base_dir=temp_output_dir,
                        overwrite=True,
                        horizontal_crs=form.horizontal_crs,
                        vertical_crs=form.vertical_crs,
                    )
                )
            except (ValueError, FileExistsError) as error:
                shutil.rmtree(temp_output_dir, ignore_errors=True)
                self._upload_page.show_error(str(error))
                return

            # Attach automatically detected (and/or manually overridden) per-source CRS.
            detected_crs = self._upload_page.crs_info_by_source_path()
            if detected_crs:
                request = dataclasses.replace(request, crs_info_by_source_path=detected_crs)

            self._upload_page.set_running(True)
            progress_callback = self._make_progress_callback(
                ACTIVITY_ACTION_UPLOAD,
                project=request.projekt,
                customer=request.kunde,
                actor="Upload",
                extra_sink=self._upload_page.handle_progress,
            )

            def run_upload():
                return upload_controller.upload_new_project(request, on_progress=progress_callback)

            def handle_result(summary: ProjectOperationSummary):
                self._show_project_operation_summary(
                    summary,
                    action=ACTIVITY_ACTION_UPLOAD,
                    project=request.projekt,
                    customer=request.kunde,
                    actor="Upload",
                )
                self._notify_operation_summary(summary, "Upload")
                self._refresh_projects_page()

            def finish_upload():
                self._upload_page.append_log("Räume temporäre Konvertierungsdateien auf...")
                self._upload_page.set_status("Temporäre Dateien werden aufgeräumt...")
                shutil.rmtree(temp_output_dir, ignore_errors=True)
                self._upload_page.append_log("Fertig.")
                self._upload_page.set_status("Fertig.")
                self._upload_page.set_running(False)
                self._release_progress_callback(progress_callback)

            self.statusBar().showMessage("Upload gestartet")
            self._start_background_task(
                run_upload,
                on_result=handle_result,
                on_error=lambda error: self._notify_task_error(
                    error,
                    "Upload",
                    action=ACTIVITY_ACTION_UPLOAD,
                    project=request.projekt,
                    customer=request.kunde,
                    actor="Upload",
                ),
                on_finished=finish_upload,
            )

        def _run_local_conversion(self, form):
            if local_conversion_controller is None:
                self._upload_page.show_error("Lokale Konvertierung: keine Service-Integration aktiv.")
                self.statusBar().showMessage("Lokale Konvertierung: UI-Platzhalter ohne Service-Integration")
                return
            from .local_conversion_dialog_models import (
                LocalConversionDialogState,
                validate_local_conversion_dialog_state,
            )

            source_file = form.source_paths[0] if form.source_paths else ""
            try:
                request = validate_local_conversion_dialog_state(
                    LocalConversionDialogState(
                        source_file=source_file,
                        output_dir=form.output_base_dir,
                        converter_path=form.converter_path,
                        overwrite=form.overwrite,
                    )
                )
            except (ValueError, FileExistsError) as error:
                self._upload_page.show_error(str(error))
                return

            self._upload_page.set_running(True)
            progress_callback = self._make_progress_callback(
                ACTIVITY_ACTION_CONVERT,
                actor="Lokale Konvertierung",
                source_path=request.source_file,
                target_path=request.output_dir,
                extra_sink=self._upload_page.handle_progress,
            )

            def run_conversion():
                return local_conversion_controller.run_conversion(request, on_progress=progress_callback)

            def handle_result(summary: ProjectOperationSummary):
                self._show_project_operation_summary(
                    summary,
                    action=ACTIVITY_ACTION_CONVERT,
                    actor="Lokale Konvertierung",
                    source_path=request.source_file,
                    target_path=request.output_dir,
                )
                self._notify_operation_summary(summary, "Lokale Konvertierung")

            def finish_conversion():
                self._upload_page.set_running(False)
                self._release_progress_callback(progress_callback)

            self.statusBar().showMessage("Lokale Konvertierung gestartet")
            self._start_background_task(
                run_conversion,
                on_result=handle_result,
                on_error=lambda error: self._notify_task_error(
                    error,
                    "Lokale Konvertierung",
                    action=ACTIVITY_ACTION_CONVERT,
                    actor="Lokale Konvertierung",
                    source_path=request.source_file,
                    target_path=request.output_dir,
                ),
                on_finished=finish_conversion,
            )

        def _handle_project_action(self, action_id: str, project: ProjectPreview | None = None, pointcloud=None):
            if action_id in {ACTION_OPEN_LINK, ACTION_COPY_LINK}:
                self._handle_project_link_action(action_id, project)
                return
            if self._has_active_background_tasks():
                self.statusBar().showMessage("Eine Aktion läuft bereits; bitte warten.")
                return

            project_controller = self._runtime.get("project_controller")
            if project_controller is None:
                self._placeholder_action(action_id, project, pointcloud)
                return

            from .project_management_dialogs import (
                confirm_delete_project,
                confirm_set_project_link_state,
                prompt_download_project,
                prompt_duplicate_project,
                prompt_rename_project,
                prompt_replace_all_pointclouds,
                prompt_replace_single_pointcloud,
            )

            progress_callback = None
            progress_dialog = None
            try:
                if action_id == ACTION_RENAME:
                    payload = prompt_rename_project(QtWidgets, self, project)
                    if payload is None:
                        return
                    operation = lambda: project_controller.rename_project(project, payload)
                elif action_id == ACTION_DUPLICATE:
                    payload = prompt_duplicate_project(QtWidgets, self, project)
                    if payload is None:
                        return
                    operation = lambda: project_controller.duplicate_project(project, payload)
                elif action_id == ACTION_DELETE:
                    if project is None or not confirm_delete_project(QtWidgets, self, project):
                        return
                    operation = lambda: project_controller.delete_project(project)
                elif action_id == ACTION_DOWNLOAD:
                    if project is None:
                        self._placeholder_action(action_id, project, pointcloud)
                        return
                    payload = prompt_download_project(QtWidgets, self, project)
                    if payload is None:
                        return
                    download_cancel_event = threading.Event()
                    progress_dialog = QtWidgets.QProgressDialog(
                        "Download wird vorbereitet...",
                        "Abbrechen",
                        0,
                        0,
                        self,
                    )
                    progress_dialog.setWindowTitle("Projekt herunterladen")
                    progress_dialog.setMinimumDuration(0)
                    progress_dialog.setAutoClose(False)
                    progress_dialog.canceled.connect(download_cancel_event.set)
                    progress_dialog.canceled.connect(
                        lambda: self.statusBar().showMessage("Download wird abgebrochen...")
                    )
                    progress_dialog.show()
                    progress_callback = self._make_progress_callback(
                        ACTIVITY_ACTION_DOWNLOAD,
                        project=project.project,
                        customer=project.customer,
                        actor="Projektverwaltung",
                        source_path=project.s3_path or project.viewer_path,
                        target_path=payload.target_dir,
                    )
                    operation = lambda: project_controller.download_project(
                        project,
                        payload,
                        on_progress=progress_callback,
                        cancel_requested=download_cancel_event.is_set,
                    )
                elif action_id == ACTION_DISABLE_LINK:
                    if project is None or not confirm_set_project_link_state(QtWidgets, self, project, True):
                        return
                    operation = lambda: project_controller.disable_project_link(project)
                elif action_id == ACTION_ENABLE_LINK:
                    if project is None or not confirm_set_project_link_state(QtWidgets, self, project, False):
                        return
                    operation = lambda: project_controller.enable_project_link(project)
                elif action_id == ACTION_REPLACE_ALL_POINTCLOUDS:
                    if project is None:
                        self._placeholder_action(action_id, project, pointcloud)
                        return
                    payload = prompt_replace_all_pointclouds(
                        QtWidgets,
                        self,
                        project,
                        self._settings_dialog_defaults(),
                    )
                    if payload is None:
                        return
                    progress_callback = self._make_progress_callback(
                        ACTIVITY_ACTION_REPLACE,
                        project=project.project,
                        customer=project.customer,
                        actor="Projektverwaltung",
                        target_path=project.s3_path or project.viewer_path,
                    )
                    operation = lambda: project_controller.replace_all_pointclouds(
                        project,
                        payload,
                        on_progress=progress_callback,
                    )
                elif action_id == ACTION_REPLACE_SINGLE_POINTCLOUD:
                    if project is None or pointcloud is None:
                        self._placeholder_action(action_id, project, pointcloud)
                        return
                    payload = prompt_replace_single_pointcloud(
                        QtWidgets,
                        self,
                        project,
                        pointcloud,
                        self._settings_dialog_defaults(),
                    )
                    if payload is None:
                        return
                    progress_callback = self._make_progress_callback(
                        ACTIVITY_ACTION_REPLACE,
                        project=project.project,
                        customer=project.customer,
                        actor="Projektverwaltung",
                        source_path=getattr(payload, "source_path", ""),
                        target_path=pointcloud.s3_path or pointcloud.viewer_path,
                    )
                    operation = lambda: project_controller.replace_single_pointcloud(
                        project,
                        pointcloud,
                        payload,
                        on_progress=progress_callback,
                    )
                else:
                    self._placeholder_action(action_id, project, pointcloud)
                    return
            except Exception as error:
                self.statusBar().showMessage(str(error))
                return

            action_label = action_by_id(action_id).label

            def handle_result(summary: ProjectOperationSummary):
                self._show_project_operation_summary(
                    summary,
                    action=_activity_action_for_project_action(action_id),
                    project=project.project if project is not None else "",
                    customer=project.customer if project is not None else "",
                    actor="Projektverwaltung",
                    target_path=(project.s3_path or project.viewer_path) if project is not None else "",
                )
                self._notify_operation_summary(summary, action_label)
                self._refresh_projects_page()

            self.statusBar().showMessage(f"{action_label} gestartet")

            def finish_project_action():
                if progress_dialog is not None:
                    progress_dialog.close()
                if progress_callback:
                    self._release_progress_callback(progress_callback)

            self._start_background_task(
                operation,
                on_result=handle_result,
                on_error=lambda error: self._notify_task_error(
                    error,
                    action_label,
                    action=_activity_action_for_project_action(action_id),
                    project=project.project if project is not None else "",
                    customer=project.customer if project is not None else "",
                    actor="Projektverwaltung",
                    target_path=(project.s3_path or project.viewer_path) if project is not None else "",
                ),
                on_finished=finish_project_action,
            )

        def _has_active_background_tasks(self) -> bool:
            return bool(self._active_tasks)

        def _handle_project_link_action(self, action_id: str, project: ProjectPreview | None):
            if project is None:
                self.statusBar().showMessage("Kein Projekt ausgewählt.")
                return
            if not project.link:
                self.statusBar().showMessage("Projekt hat keinen Link.")
                return
            if action_id == ACTION_OPEN_LINK:
                if project.disabled:
                    self.statusBar().showMessage("Projekt-Link ist deaktiviert.")
                    return
                QtGui.QDesktopServices.openUrl(QtCore.QUrl(project.link))
                summary = ProjectOperationSummary(status="success", message="Projekt-Link im Browser geöffnet.")
            elif action_id == ACTION_COPY_LINK:
                QtWidgets.QApplication.clipboard().setText(project.link)
                summary = ProjectOperationSummary(status="success", message="Projekt-Link in die Zwischenablage kopiert.")
            else:
                self.statusBar().showMessage(f"Unbekannte Link-Aktion: {action_id}")
                return
            self._show_project_operation_summary(
                summary,
                action=ACTIVITY_ACTION_UPDATE,
                project=project.project,
                customer=project.customer,
                actor="Projektverwaltung",
                target_path=project.link,
            )

        def _settings_dialog_defaults(self):
            if settings_controller is None:
                return None
            try:
                return settings_controller.load_state()
            except Exception:
                return None

        def _handle_settings_action(self, action_id: str, state=None):
            if settings_controller is None:
                self.statusBar().showMessage("Einstellungen: UI-Platzhalter ohne Service-Integration")
                return

            if action_id == "save":
                try:
                    summary = settings_controller.save_state(state or settings_controller.load_state())
                except Exception as error:
                    self.statusBar().showMessage(str(error))
                    QtWidgets.QMessageBox.critical(self, "Einstellungen", str(error))
                    return
                self._show_project_operation_summary(summary, action=ACTIVITY_ACTION_UPDATE, actor="Einstellungen")
                self._notify_operation_summary(summary, "Einstellungen")
                self._reload_runtime_services()
                self._refresh_settings_page()
                return

            if action_id == "edit":
                from .settings_dialogs import prompt_settings

                try:
                    state = settings_controller.load_state()
                    updated_state = prompt_settings(QtWidgets, self, state)
                    if updated_state is None:
                        return
                    summary = settings_controller.save_state(updated_state)
                except Exception as error:
                    self.statusBar().showMessage(str(error))
                    QtWidgets.QMessageBox.critical(self, "Einstellungen", str(error))
                    return
                self._show_project_operation_summary(summary, action=ACTIVITY_ACTION_UPDATE, actor="Einstellungen")
                self._notify_operation_summary(summary, "Einstellungen")
                self._reload_runtime_services()
                self._refresh_settings_page()
                return

            if action_id == "test_connection":
                self.statusBar().showMessage("S3-Verbindungstest gestartet...")

                def show_test_result(summary):
                    self._show_project_operation_summary(
                        summary, action=ACTIVITY_ACTION_UPDATE, actor="Einstellungen"
                    )
                    self._notify_operation_summary(summary, "Verbindung testen")

                self._start_background_task(
                    lambda: settings_controller.test_connection(state),
                    on_result=show_test_result,
                    on_error=lambda error: self._notify_task_error(
                        error,
                        "Verbindung testen",
                        action=ACTIVITY_ACTION_UPDATE,
                        actor="Einstellungen",
                    ),
                )
                return

            if action_id == "check_update":
                if update_controller is None:
                    self.statusBar().showMessage("Update-Prüfung: Preview ohne Update-Controller")
                    return
                self.statusBar().showMessage("Update-Prüfung gestartet...")

                def show_update_result(summary):
                    self._show_project_operation_summary(
                        summary, action=ACTIVITY_ACTION_UPDATE, actor="Updater"
                    )
                    self._notify_operation_summary(summary, "Update-Prüfung")

                self._start_background_task(
                    update_controller.check_for_updates,
                    on_result=show_update_result,
                    on_error=lambda error: self._notify_task_error(
                        error,
                        "Update-Prüfung",
                        action=ACTIVITY_ACTION_UPDATE,
                        actor="Updater",
                    ),
                )
                return

            self.statusBar().showMessage(f"Unbekannte Einstellungsaktion: {action_id}")

        def _show_project_operation_summary(
            self,
            summary: ProjectOperationSummary,
            *,
            action: str = ACTIVITY_ACTION_UPDATE,
            project: str = "",
            customer: str = "",
            actor: str = "Service",
            source_path: str = "",
            target_path: str = "",
        ):
            self._activity_store.record_operation_summary(
                summary,
                action=action,
                project=project,
                customer=customer,
                actor=actor,
                source_path=source_path,
                target_path=target_path,
            )
            self._refresh_activity_page()
            self.statusBar().showMessage(summary.statusbar_text)

        def _show_task_error(
            self,
            error,
            *,
            action: str = ACTIVITY_ACTION_UPDATE,
            project: str = "",
            customer: str = "",
            actor: str = "Service",
            source_path: str = "",
            target_path: str = "",
        ):
            self._activity_store.record_error(
                error,
                action=action,
                project=project,
                customer=customer,
                actor=actor,
                source_path=source_path,
                target_path=target_path,
            )
            self._refresh_activity_page()
            self.statusBar().showMessage(str(error))

        def _make_progress_callback(
            self,
            action: str,
            *,
            project: str = "",
            customer: str = "",
            actor: str = "Service",
            source_path: str = "",
            target_path: str = "",
            extra_sink: Callable[[Any], None] | None = None,
        ):
            bridge = QtServiceBridge()
            emitter = bridge.attach_qt_progress_signal()

            def receive_progress(event: Any):
                message = str(getattr(event, "message", "") or "")
                if message:
                    self.statusBar().showMessage(message)
                if extra_sink is not None:
                    extra_sink(event)
                if getattr(event, "kind", "") not in {"log", "step", "detail", "warning", "error"}:
                    return
                self._activity_store.record_progress_event(
                    event,
                    action=action,
                    project=project,
                    customer=customer,
                    actor=actor,
                    source_path=source_path,
                    target_path=target_path,
                )
                self._refresh_activity_page()

            emitter.progress.connect(receive_progress)
            progress_callback = bridge.progress_callback
            self._progress_bridges[progress_callback] = (bridge, emitter)
            return progress_callback

        def _release_progress_callback(self, progress_callback):
            self._progress_bridges.pop(progress_callback, None)

        def _notify_operation_summary(self, summary: ProjectOperationSummary, title: str):
            status = str(getattr(summary, "status", "") or "")
            text = "\n".join(getattr(summary, "activity_lines", ()) or (summary.message,))
            if status == "failed":
                QtWidgets.QMessageBox.critical(self, title, text)
            elif status in {"partial", "cancelled"}:
                QtWidgets.QMessageBox.warning(self, title, text)
            else:
                QtWidgets.QMessageBox.information(self, title, text)

        def _notify_task_error(self, error, title: str, **record_kwargs):
            self._show_task_error(error, **record_kwargs)
            QtWidgets.QMessageBox.critical(self, title, str(error))

        def _start_background_task(
            self,
            task,
            *,
            on_result,
            on_error=None,
            on_finished=None,
        ):
            bundle = create_task_worker(QtCore, task)
            self._active_tasks.append(bundle)

            def handle_error(error):
                if on_error is not None:
                    on_error(error)
                else:
                    self._show_task_error(error)

            # The worker emits from its own thread. Connecting the signals to a
            # QObject that lives on the GUI thread forces a queued connection, so
            # the result/error/finished callbacks (which touch widgets and dialogs)
            # always run on the main thread instead of the worker thread.
            dispatcher = _MainThreadTaskDispatcher(on_result, handle_error, on_finished)
            self._task_records[bundle.thread] = (bundle, dispatcher)
            bundle.worker.result.connect(dispatcher.dispatch_result)
            bundle.worker.error.connect(dispatcher.dispatch_error)
            bundle.worker.finished.connect(dispatcher.dispatch_finished)
            bundle.thread.finished.connect(self._cleanup_task_thread)
            bundle.thread.start()
            return bundle

        @QtCore.Slot()
        def _cleanup_task_thread(self):
            thread = self.sender()
            record = self._task_records.pop(thread, None)
            if record is None:
                return
            bundle, _dispatcher = record
            if bundle in self._active_tasks:
                self._active_tasks.remove(bundle)

        def _refresh_projects_page(self):
            refresh = getattr(self._projects_page, "reload_projects", None)
            if callable(refresh):
                refresh()

        def _refresh_activity_page(self):
            refresh = getattr(self._activity_page, "reload_activity", None)
            if callable(refresh):
                refresh()

        def _refresh_settings_page(self):
            refresh = getattr(self._settings_page, "reload_settings", None)
            if callable(refresh):
                refresh()

        def _runtime_project_rows(self):
            provider = self._runtime.get("project_provider")
            fallback_rows = () if provider is not None else self._runtime.get("disconnected_project_previews", ())
            return resolve_runtime_project_rows(provider, fallback_rows=fallback_rows)

        def _reload_runtime_services(self):
            if runtime_reloader is None:
                return
            bundle = runtime_reloader()
            self._runtime.update(
                {
                    "project_provider": getattr(bundle, "project_provider", None),
                    "project_controller": getattr(bundle, "project_controller", None),
                    "upload_controller": getattr(bundle, "upload_controller", None),
                    "status": getattr(bundle, "status", "Runtime neu geladen"),
                }
            )
            self.statusBar().showMessage(str(self._runtime["status"]))
            self._refresh_projects_page()

    return MainWindow()


def _activity_action_for_project_action(action_id: str) -> str:
    if action_id in {ACTION_REPLACE_ALL_POINTCLOUDS, ACTION_REPLACE_SINGLE_POINTCLOUD}:
        return ACTIVITY_ACTION_REPLACE
    if action_id == ACTION_DELETE:
        return ACTIVITY_ACTION_DELETE
    if action_id == ACTION_DOWNLOAD:
        return ACTIVITY_ACTION_DOWNLOAD
    return ACTIVITY_ACTION_UPDATE


def build_runtime_upload_preview(settings_state=None):
    """Build the Upload page preview from runtime settings instead of example projects."""

    from .upload_wizard_model import UploadWizardState, build_upload_wizard_preview

    return build_upload_wizard_preview(
        UploadWizardState(
            converter_path=str(getattr(settings_state, "converter_path", "") or ""),
            output_base_dir=str(getattr(settings_state, "output_base_dir", "") or ""),
        )
    )


def build_runtime_local_conversion_preview(settings_state=None):
    """Build the local conversion page preview from runtime settings."""

    from .local_conversion_model import build_local_conversion_preview

    return build_local_conversion_preview(
        converter_path=str(getattr(settings_state, "converter_path", "") or ""),
        output_dir=str(getattr(settings_state, "output_base_dir", "") or ""),
    )


def resolve_runtime_project_rows(provider, *, fallback_rows=()):
    """Return project-management rows from callable or service-style providers."""

    if provider is None:
        return tuple(fallback_rows)
    if callable(provider):
        return provider()
    return provider.list_projects_for_management()
