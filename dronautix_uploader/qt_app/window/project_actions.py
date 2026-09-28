"""Project management actions: input dialogs, progress dialogs, workers.

Mixin of the main window; see ``qt_app/main_window.py``.
"""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
import threading

from ..activity_model import (
    ACTION_DOWNLOAD as ACTIVITY_ACTION_DOWNLOAD,
    ACTION_REPLACE as ACTIVITY_ACTION_REPLACE,
    ACTION_UPDATE as ACTIVITY_ACTION_UPDATE,
    ACTION_UPLOAD as ACTIVITY_ACTION_UPLOAD,
    normalize_progress_value,
)
from ..project_management import ProjectPreview
from ..project_management_actions import (
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
    ACTION_REPLACE_SINGLE_MODEL,
    ACTION_REPAIR_CRS_METADATA,
    ACTION_ADD_POINTCLOUDS,
    ACTION_ADD_MODELS,
    ACTION_REMOVE_MODEL,
    ACTION_REMOVE_POINTCLOUD,
    ProjectOperationSummary,
    action_by_id,
)
from .support import UPLOAD_TEMP_PREFIX, _CrsRepairRequest, _SpatialWarningRequest, _activity_action_for_project_action, _detect_crs_or_none


class ProjectActionsMixin:
    def _placeholder_action(self, action_id: str, project: ProjectPreview | None = None, pointcloud=None):
        try:
            action_label = action_by_id(action_id).label
        except KeyError:
            action_label = action_id
        self.statusBar().showMessage(
            f"{action_label}: keine S3-Verbindung - AWS-Zugangsdaten in den Einstellungen prüfen."
        )

    def _confirm_spatial_warning(self, message: str) -> bool:
        warning = _SpatialWarningRequest(message=message)
        self._spatial_warning_emitter.requested.emit(warning)
        while not warning.completed.wait(0.1):
            if self._upload_cancel_event is not None and self._upload_cancel_event.is_set():
                return False
        return warning.accepted

    def _confirm_crs_repair(self, message: str) -> bool:
        repair = _CrsRepairRequest(message=message)
        self._crs_repair_emitter.requested.emit(repair)
        repair.completed.wait()
        return repair.accepted

    def _handle_project_action(self, action_id: str, project: ProjectPreview | None = None, pointcloud=None):
        if action_id in {ACTION_OPEN_LINK, ACTION_COPY_LINK}:
            self._handle_project_link_action(action_id, project)
            return
        if self._update_install_started:
            self.statusBar().showMessage("Update-Installation läuft; bitte warten.")
            return
        if self._has_active_background_tasks():
            self.statusBar().showMessage("Eine Aktion läuft bereits; bitte warten.")
            return

        project_controller = self._runtime.get("project_controller")
        if project_controller is None:
            self._placeholder_action(action_id, project, pointcloud)
            return

        from ..project_management_dialogs import (
            confirm_delete_project,
            confirm_remove_model,
            confirm_remove_pointcloud,
            prompt_add_project_pointclouds,
            prompt_add_project_models,
            prompt_download_project,
            prompt_duplicate_project,
            prompt_rename_project,
            prompt_replace_all_pointclouds,
            prompt_replace_single_pointcloud,
            prompt_replace_single_model,
            prompt_repair_project_crs,
        )

        progress_callback = None
        progress_dialog = None
        replace_temp_dir = None
        action_cancel_event = threading.Event()
        try:
            if action_id == ACTION_RENAME:
                payload = prompt_rename_project(self.QtWidgets, self, project)
                if payload is None:
                    return
                progress_dialog = self._create_action_progress_dialog(
                    "Projekt umbenennen",
                    f"„{project.project}“ wird umbenannt...",
                )
                operation = lambda: project_controller.rename_project(project, payload)
            elif action_id == ACTION_DUPLICATE:
                payload = prompt_duplicate_project(self.QtWidgets, self, project)
                if payload is None:
                    return
                progress_dialog = self._create_action_progress_dialog(
                    "Projekt duplizieren",
                    f"„{project.project}“ wird dupliziert...",
                    cancel_event=action_cancel_event,
                )
                progress_callback = self._make_progress_callback(
                    ACTIVITY_ACTION_UPDATE,
                    project=project.project,
                    customer=project.customer,
                    actor="Projektverwaltung",
                    source_path=project.s3_path or project.viewer_path,
                    extra_sink=self._progress_dialog_sink(progress_dialog),
                )
                operation = lambda: project_controller.duplicate_project(
                    project,
                    payload,
                    on_progress=progress_callback,
                    cancel_requested=action_cancel_event.is_set,
                )
            elif action_id == ACTION_DELETE:
                if project is None or not confirm_delete_project(self.QtWidgets, self, project):
                    return
                progress_dialog = self._create_action_progress_dialog(
                    "Projekt löschen",
                    f"„{project.project}“ wird gelöscht...",
                )
                operation = lambda: project_controller.delete_project(project)
            elif action_id == ACTION_DOWNLOAD:
                if project is None:
                    self._placeholder_action(action_id, project, pointcloud)
                    return
                payload = prompt_download_project(self.QtWidgets, self, project)
                if payload is None:
                    return
                download_cancel_event = threading.Event()
                progress_dialog = self.QtWidgets.QProgressDialog(
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
                    extra_sink=self._progress_dialog_sink(progress_dialog),
                )
                operation = lambda: project_controller.download_project(
                    project,
                    payload,
                    on_progress=progress_callback,
                    cancel_requested=download_cancel_event.is_set,
                )
            elif action_id == ACTION_DISABLE_LINK:
                if project is None:
                    return
                operation = lambda: project_controller.disable_project_link(project)
            elif action_id == ACTION_ENABLE_LINK:
                if project is None:
                    return
                operation = lambda: project_controller.enable_project_link(project)
            elif action_id == ACTION_REPLACE_ALL_POINTCLOUDS:
                if project is None:
                    self._placeholder_action(action_id, project, pointcloud)
                    return
                replace_temp_dir = tempfile.mkdtemp(prefix=UPLOAD_TEMP_PREFIX)
                payload = prompt_replace_all_pointclouds(
                    self.QtWidgets,
                    self,
                    project,
                    self._settings_dialog_defaults(),
                    replace_temp_dir,
                )
                if payload is None:
                    shutil.rmtree(replace_temp_dir, ignore_errors=True)
                    return
                progress_dialog = self._create_action_progress_dialog(
                    "Punktwolken austauschen",
                    f"Punktwolken in „{project.project}“ werden ausgetauscht...",
                    cancel_event=action_cancel_event,
                )
                progress_callback = self._make_progress_callback(
                    ACTIVITY_ACTION_REPLACE,
                    project=project.project,
                    customer=project.customer,
                    actor="Projektverwaltung",
                    target_path=project.s3_path or project.viewer_path,
                    extra_sink=self._progress_dialog_sink(progress_dialog),
                )
                # CRS detection reads the sources (maybe on a NAS): worker thread.
                operation = lambda: project_controller.replace_all_pointclouds(
                    project,
                    self._with_detected_crs_all(payload),
                    on_progress=progress_callback,
                    cancel_requested=action_cancel_event.is_set,
                )
            elif action_id == ACTION_ADD_POINTCLOUDS:
                if project is None:
                    self._placeholder_action(action_id, project, pointcloud)
                    return
                replace_temp_dir = tempfile.mkdtemp(prefix=UPLOAD_TEMP_PREFIX)
                payload = prompt_add_project_pointclouds(
                    self.QtWidgets,
                    self,
                    project,
                    self._settings_dialog_defaults(),
                    replace_temp_dir,
                )
                if payload is None:
                    shutil.rmtree(replace_temp_dir, ignore_errors=True)
                    return
                progress_dialog = self._create_action_progress_dialog(
                    "Punktwolken hinzufügen",
                    f"Punktwolken werden zu „{project.project}“ hinzugefügt...",
                    cancel_event=action_cancel_event,
                )
                progress_callback = self._make_progress_callback(
                    ACTIVITY_ACTION_UPLOAD,
                    project=project.project,
                    customer=project.customer,
                    actor="Projektverwaltung",
                    target_path=project.s3_path or project.viewer_path,
                    extra_sink=self._progress_dialog_sink(progress_dialog),
                )
                operation = lambda: project_controller.add_pointclouds(
                    project,
                    self._with_detected_crs_all(payload),
                    on_progress=progress_callback,
                    cancel_requested=action_cancel_event.is_set,
                )
            elif action_id == ACTION_ADD_MODELS:
                if project is None:
                    self._placeholder_action(action_id, project, pointcloud)
                    return
                payload = prompt_add_project_models(self.QtWidgets, self, project)
                if payload is None:
                    return
                progress_dialog = self._create_action_progress_dialog(
                    "3D-Modelle hinzufügen",
                    f"3D-Modelle werden zu „{project.project}“ hinzugefügt...",
                    cancel_event=action_cancel_event,
                )
                progress_callback = self._make_progress_callback(
                    ACTIVITY_ACTION_UPLOAD,
                    project=project.project,
                    customer=project.customer,
                    actor="Projektverwaltung",
                    source_path="; ".join(payload.source_paths),
                    target_path=project.s3_path or project.viewer_path,
                    extra_sink=self._progress_dialog_sink(progress_dialog),
                )
                operation = lambda: project_controller.add_models(
                    project,
                    payload,
                    on_progress=progress_callback,
                    cancel_requested=action_cancel_event.is_set,
                    confirm_spatial_warning=self._confirm_spatial_warning,
                    confirm_crs_repair=self._confirm_crs_repair,
                )
            elif action_id == ACTION_REPAIR_CRS_METADATA:
                if project is None:
                    self._placeholder_action(action_id, project, pointcloud)
                    return
                payload = prompt_repair_project_crs(self.QtWidgets, self, project)
                if payload is None:
                    return
                progress_dialog = self._create_action_progress_dialog(
                    "CRS-Metadaten reparieren",
                    f"CRS-Metadaten in „{project.project}“ werden geprüft...",
                )
                operation = lambda: project_controller.repair_project_crs_metadata(
                    project,
                    payload,
                    confirm_repair=self._confirm_crs_repair,
                )
            elif action_id == ACTION_REPLACE_SINGLE_POINTCLOUD:
                if project is None or pointcloud is None:
                    self._placeholder_action(action_id, project, pointcloud)
                    return
                replace_temp_dir = tempfile.mkdtemp(prefix=UPLOAD_TEMP_PREFIX)
                payload = prompt_replace_single_pointcloud(
                    self.QtWidgets,
                    self,
                    project,
                    pointcloud,
                    self._settings_dialog_defaults(),
                    replace_temp_dir,
                )
                if payload is None:
                    shutil.rmtree(replace_temp_dir, ignore_errors=True)
                    return
                progress_dialog = self._create_action_progress_dialog(
                    "Punktwolke austauschen",
                    f"„{pointcloud.name}“ wird ausgetauscht...",
                    cancel_event=action_cancel_event,
                )
                progress_callback = self._make_progress_callback(
                    ACTIVITY_ACTION_REPLACE,
                    project=project.project,
                    customer=project.customer,
                    actor="Projektverwaltung",
                    source_path=getattr(payload, "source_path", ""),
                    target_path=pointcloud.s3_path or pointcloud.viewer_path,
                    extra_sink=self._progress_dialog_sink(progress_dialog),
                )
                operation = lambda: project_controller.replace_single_pointcloud(
                    project,
                    pointcloud,
                    self._with_detected_crs_single(payload),
                    on_progress=progress_callback,
                    cancel_requested=action_cancel_event.is_set,
                )
            elif action_id == ACTION_REPLACE_SINGLE_MODEL:
                if project is None or pointcloud is None:
                    self._placeholder_action(action_id, project, pointcloud)
                    return
                payload = prompt_replace_single_model(self.QtWidgets, self, project, pointcloud)
                if payload is None:
                    return
                progress_dialog = self._create_action_progress_dialog(
                    "GLB austauschen",
                    f"„{pointcloud.name}“ wird vorbereitet und ausgetauscht...",
                    cancel_event=action_cancel_event,
                )
                progress_callback = self._make_progress_callback(
                    ACTIVITY_ACTION_REPLACE,
                    project=project.project,
                    customer=project.customer,
                    actor="Projektverwaltung",
                    source_path=payload.source_path,
                    target_path=pointcloud.s3_path or pointcloud.viewer_path,
                    extra_sink=self._progress_dialog_sink(progress_dialog),
                )
                operation = lambda: project_controller.replace_single_model(
                    project,
                    pointcloud,
                    payload,
                    on_progress=progress_callback,
                    cancel_requested=action_cancel_event.is_set,
                    confirm_spatial_warning=self._confirm_spatial_warning,
                    confirm_crs_repair=self._confirm_crs_repair,
                )
            elif action_id == ACTION_REMOVE_POINTCLOUD:
                if project is None or pointcloud is None:
                    self._placeholder_action(action_id, project, pointcloud)
                    return
                if not confirm_remove_pointcloud(self.QtWidgets, self, project, pointcloud):
                    return
                progress_dialog = self._create_action_progress_dialog(
                    "Punktwolke entfernen",
                    f"„{pointcloud.name}“ wird entfernt...",
                )
                operation = lambda: project_controller.remove_pointcloud(project, pointcloud)
            elif action_id == ACTION_REMOVE_MODEL:
                if project is None or pointcloud is None:
                    self._placeholder_action(action_id, project, pointcloud)
                    return
                if not confirm_remove_model(self.QtWidgets, self, project, pointcloud):
                    return
                progress_dialog = self._create_action_progress_dialog(
                    "3D-Modell entfernen",
                    f"„{pointcloud.name}“ wird entfernt...",
                )
                operation = lambda: project_controller.remove_model(project, pointcloud)
            else:
                self._placeholder_action(action_id, project, pointcloud)
                return
        except Exception as error:
            self._close_action_progress_dialog(progress_dialog)
            if replace_temp_dir is not None:
                shutil.rmtree(replace_temp_dir, ignore_errors=True)
            self.statusBar().showMessage(str(error))
            return

        # The input dialogs above are modal; an update or another task may
        # have started meanwhile (e.g. the delayed startup update check).
        if self._update_install_started or self._has_active_background_tasks():
            self._close_action_progress_dialog(progress_dialog)
            if progress_callback:
                self._release_progress_callback(progress_callback)
            if replace_temp_dir is not None:
                shutil.rmtree(replace_temp_dir, ignore_errors=True)
            self.statusBar().showMessage(
                "Inzwischen läuft eine andere Aktion; bitte danach erneut starten."
            )
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
            if action_id not in {ACTION_DISABLE_LINK, ACTION_ENABLE_LINK} or summary.status != "success":
                self._notify_operation_summary(summary, action_label)
            self._refresh_projects_page()

        self.statusBar().showMessage(f"{action_label} gestartet")

        def finish_project_action():
            self._close_action_progress_dialog(progress_dialog)
            if progress_callback:
                self._release_progress_callback(progress_callback)
            if replace_temp_dir is not None:
                shutil.rmtree(replace_temp_dir, ignore_errors=True)

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

    def _close_action_progress_dialog(self, dialog):
        """Close a project action dialog for good.

        ``close()`` emits ``canceled``; without disconnecting first, a
        cancellable dialog would report a cancel and show itself again.
        """

        if dialog is None:
            return
        dialog.setProperty("action_finished", True)
        try:
            dialog.canceled.disconnect()
        except (RuntimeError, TypeError):
            pass
        dialog.close()

    def _create_action_progress_dialog(self, title: str, label: str, cancel_event=None):
        """Beschaeftigt-Dialog für Projektaktionen; mit ``cancel_event`` abbrechbar."""

        dialog = self.QtWidgets.QProgressDialog(label, "Abbrechen" if cancel_event is not None else "", 0, 0, self)
        dialog.setWindowTitle(title)
        if cancel_event is None:
            dialog.setCancelButton(None)
        else:
            def request_cancel():
                cancel_event.set()
                self.statusBar().showMessage(f"{title} wird abgebrochen...")

                def show_rollback_state():
                    # QProgressDialog versteckt sich beim Abbrechen; bis der
                    # Rollback fertig ist, bleibt der Dialog ohne Button sichtbar.
                    # Ist die Aufgabe inzwischen beendet, darf er nie wieder
                    # erscheinen (sonst blockiert er das Fenster dauerhaft).
                    if dialog.property("action_finished"):
                        return
                    dialog.setCancelButton(None)
                    dialog.setLabelText("Wird abgebrochen - bereits übertragene Daten werden entfernt...")
                    dialog.show()

                self.QtCore.QTimer.singleShot(0, show_rollback_state)

            dialog.canceled.connect(request_cancel)
        dialog.setWindowModality(self.QtCore.Qt.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setMinimumWidth(420)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.show()
        return dialog

    def _progress_dialog_sink(self, dialog):
        state = {"headline": ""}

        def apply_event(event):
            message = str(getattr(event, "message", "") or "")
            detail = str(getattr(event, "detail", "") or "")
            kind = str(getattr(event, "kind", "") or "")
            if message and not message.startswith("["):
                state["headline"] = message
                dialog.setLabelText(f"{message}\n{detail}" if detail else message)
            elif detail:
                if kind == "detail":
                    # z. B. Download: "Lade Datei 3/12: octree.bin"
                    state["headline"] = detail
                    dialog.setLabelText(detail)
                else:
                    # Byte-Zwischenstand als zweite Zeile unter der
                    # zuletzt gemeldeten Phase anzeigen.
                    headline = state["headline"]
                    dialog.setLabelText(f"{headline}\n{detail}" if headline else detail)
            # Ohne Prozentwerte bleibt der Balken als Marquee animiert,
            # damit die Aktion nie eingefroren wirkt; Schritt-Events
            # aktualisieren nur den Text.
            percent = getattr(event, "percent", None)
            if percent is not None:
                dialog.setRange(0, 100)
                dialog.setValue(normalize_progress_value(percent))
        return apply_event

    def _with_detected_crs_all(self, payload):
        if getattr(payload, "crs_info_by_source_path", None):
            return payload
        detected = {}
        for source_path in getattr(payload, "source_paths", ()) or ():
            info = _detect_crs_or_none(source_path)
            if info:
                detected[source_path] = info
        return dataclasses.replace(payload, crs_info_by_source_path=detected) if detected else payload

    def _with_detected_crs_single(self, payload):
        if getattr(payload, "crs_info", None):
            return payload
        source_path = getattr(payload, "source_path", "")
        info = _detect_crs_or_none(source_path) if source_path else None
        return dataclasses.replace(payload, crs_info=info) if info else payload

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
            self.QtGui.QDesktopServices.openUrl(self.QtCore.QUrl(project.link))
            summary = ProjectOperationSummary(status="success", message="Projekt-Link im Browser geöffnet.")
        elif action_id == ACTION_COPY_LINK:
            self.QtWidgets.QApplication.clipboard().setText(project.link)
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
