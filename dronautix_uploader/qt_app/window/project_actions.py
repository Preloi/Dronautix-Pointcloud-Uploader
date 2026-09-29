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

@dataclasses.dataclass
class _ProjectActionRun:
    """What one project action set up; released on every exit path."""

    action_id: str
    project: ProjectPreview | None
    pointcloud: object
    controller: object
    cancel_event: threading.Event = dataclasses.field(default_factory=threading.Event)
    progress_dialog: object = None
    progress_callback: object = None
    temp_dir: str | None = None



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
        builder = _PROJECT_ACTION_BUILDERS.get(action_id)
        if project_controller is None or builder is None:
            self._placeholder_action(action_id, project, pointcloud)
            return

        from .. import project_management_dialogs

        run = _ProjectActionRun(action_id, project, pointcloud, project_controller)
        try:
            operation = builder(self, run, project_management_dialogs)
        except Exception as error:
            self._release_project_action_run(run)
            self.statusBar().showMessage(str(error))
            return
        if operation is None:  # cancelled in the input dialog or not applicable
            self._release_project_action_run(run)
            return

        # The input dialogs above are modal; an update or another task may
        # have started meanwhile (e.g. the delayed startup update check).
        if self._update_install_started or self._has_active_background_tasks():
            self._release_project_action_run(run)
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
            on_finished=lambda: self._release_project_action_run(run),
        )

    def _release_project_action_run(self, run: _ProjectActionRun):
        """Undo what a builder set up; safe on every exit path, also after an error."""

        self._close_action_progress_dialog(run.progress_dialog)
        if run.progress_callback:
            self._release_progress_callback(run.progress_callback)
        if run.temp_dir is not None:
            shutil.rmtree(run.temp_dir, ignore_errors=True)

    def _project_action_progress(self, run: _ProjectActionRun, activity_action: str, **paths):
        run.progress_callback = self._make_progress_callback(
            activity_action,
            project=run.project.project,
            customer=run.project.customer,
            actor="Projektverwaltung",
            extra_sink=self._progress_dialog_sink(run.progress_dialog),
            **paths,
        )
        return run.progress_callback

    # Builders: ask for input, set up dialog/progress on ``run`` and return the
    # worker operation, or None when the user cancelled. They run on the GUI
    # thread; everything slow (S3, CRS detection on a NAS) belongs in the operation.

    def _build_rename_action(self, run: _ProjectActionRun, dialogs):
        project = run.project
        payload = dialogs.prompt_rename_project(self.QtWidgets, self, project)
        if payload is None:
            return None
        run.progress_dialog = self._create_action_progress_dialog(
            "Projekt umbenennen",
            f"„{project.project}“ wird umbenannt...",
        )
        return lambda: run.controller.rename_project(project, payload)

    def _build_duplicate_action(self, run: _ProjectActionRun, dialogs):
        project = run.project
        payload = dialogs.prompt_duplicate_project(self.QtWidgets, self, project)
        if payload is None:
            return None
        run.progress_dialog = self._create_action_progress_dialog(
            "Projekt duplizieren",
            f"„{project.project}“ wird dupliziert...",
            cancel_event=run.cancel_event,
        )
        on_progress = self._project_action_progress(
            run, ACTIVITY_ACTION_UPDATE, source_path=project.s3_path or project.viewer_path
        )
        return lambda: run.controller.duplicate_project(
            project,
            payload,
            on_progress=on_progress,
            cancel_requested=run.cancel_event.is_set,
        )

    def _build_delete_action(self, run: _ProjectActionRun, dialogs):
        project = run.project
        if project is None or not dialogs.confirm_delete_project(self.QtWidgets, self, project):
            return None
        run.progress_dialog = self._create_action_progress_dialog(
            "Projekt löschen",
            f"„{project.project}“ wird gelöscht...",
        )
        return lambda: run.controller.delete_project(project)

    def _build_download_action(self, run: _ProjectActionRun, dialogs):
        project = run.project
        if project is None:
            self._placeholder_action(run.action_id, project, run.pointcloud)
            return None
        payload = dialogs.prompt_download_project(self.QtWidgets, self, project)
        if payload is None:
            return None
        progress_dialog = self.QtWidgets.QProgressDialog(
            "Download wird vorbereitet...",
            "Abbrechen",
            0,
            0,
            self,
        )
        run.progress_dialog = progress_dialog
        progress_dialog.setWindowTitle("Projekt herunterladen")
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setAutoClose(False)
        progress_dialog.canceled.connect(run.cancel_event.set)
        progress_dialog.canceled.connect(
            lambda: self.statusBar().showMessage("Download wird abgebrochen...")
        )
        progress_dialog.show()
        on_progress = self._project_action_progress(
            run,
            ACTIVITY_ACTION_DOWNLOAD,
            source_path=project.s3_path or project.viewer_path,
            target_path=payload.target_dir,
        )
        return lambda: run.controller.download_project(
            project,
            payload,
            on_progress=on_progress,
            cancel_requested=run.cancel_event.is_set,
        )

    def _build_disable_link_action(self, run: _ProjectActionRun, dialogs):
        project = run.project
        if project is None:
            return None
        return lambda: run.controller.disable_project_link(project)

    def _build_enable_link_action(self, run: _ProjectActionRun, dialogs):
        project = run.project
        if project is None:
            return None
        return lambda: run.controller.enable_project_link(project)

    def _build_replace_all_pointclouds_action(self, run: _ProjectActionRun, dialogs):
        project = run.project
        if project is None:
            self._placeholder_action(run.action_id, project, run.pointcloud)
            return None
        run.temp_dir = tempfile.mkdtemp(prefix=UPLOAD_TEMP_PREFIX)
        payload = dialogs.prompt_replace_all_pointclouds(
            self.QtWidgets,
            self,
            project,
            self._settings_dialog_defaults(),
            run.temp_dir,
        )
        if payload is None:
            return None
        run.progress_dialog = self._create_action_progress_dialog(
            "Punktwolken austauschen",
            f"Punktwolken in „{project.project}“ werden ausgetauscht...",
            cancel_event=run.cancel_event,
        )
        on_progress = self._project_action_progress(
            run, ACTIVITY_ACTION_REPLACE, target_path=project.s3_path or project.viewer_path
        )
        # CRS detection reads the sources (maybe on a NAS): worker thread.
        return lambda: run.controller.replace_all_pointclouds(
            project,
            self._with_detected_crs_all(payload),
            on_progress=on_progress,
            cancel_requested=run.cancel_event.is_set,
        )

    def _build_add_pointclouds_action(self, run: _ProjectActionRun, dialogs):
        project = run.project
        if project is None:
            self._placeholder_action(run.action_id, project, run.pointcloud)
            return None
        run.temp_dir = tempfile.mkdtemp(prefix=UPLOAD_TEMP_PREFIX)
        payload = dialogs.prompt_add_project_pointclouds(
            self.QtWidgets,
            self,
            project,
            self._settings_dialog_defaults(),
            run.temp_dir,
        )
        if payload is None:
            return None
        run.progress_dialog = self._create_action_progress_dialog(
            "Punktwolken hinzufügen",
            f"Punktwolken werden zu „{project.project}“ hinzugefügt...",
            cancel_event=run.cancel_event,
        )
        on_progress = self._project_action_progress(
            run, ACTIVITY_ACTION_UPLOAD, target_path=project.s3_path or project.viewer_path
        )
        return lambda: run.controller.add_pointclouds(
            project,
            self._with_detected_crs_all(payload),
            on_progress=on_progress,
            cancel_requested=run.cancel_event.is_set,
        )

    def _build_add_models_action(self, run: _ProjectActionRun, dialogs):
        project = run.project
        if project is None:
            self._placeholder_action(run.action_id, project, run.pointcloud)
            return None
        payload = dialogs.prompt_add_project_models(self.QtWidgets, self, project)
        if payload is None:
            return None
        run.progress_dialog = self._create_action_progress_dialog(
            "3D-Modelle hinzufügen",
            f"3D-Modelle werden zu „{project.project}“ hinzugefügt...",
            cancel_event=run.cancel_event,
        )
        on_progress = self._project_action_progress(
            run,
            ACTIVITY_ACTION_UPLOAD,
            source_path="; ".join(payload.source_paths),
            target_path=project.s3_path or project.viewer_path,
        )
        return lambda: run.controller.add_models(
            project,
            payload,
            on_progress=on_progress,
            cancel_requested=run.cancel_event.is_set,
            confirm_spatial_warning=self._confirm_spatial_warning,
            confirm_crs_repair=self._confirm_crs_repair,
        )

    def _build_repair_crs_metadata_action(self, run: _ProjectActionRun, dialogs):
        project = run.project
        if project is None:
            self._placeholder_action(run.action_id, project, run.pointcloud)
            return None
        payload = dialogs.prompt_repair_project_crs(self.QtWidgets, self, project)
        if payload is None:
            return None
        run.progress_dialog = self._create_action_progress_dialog(
            "CRS-Metadaten reparieren",
            f"CRS-Metadaten in „{project.project}“ werden geprüft...",
        )
        return lambda: run.controller.repair_project_crs_metadata(
            project,
            payload,
            confirm_repair=self._confirm_crs_repair,
        )

    def _build_replace_single_pointcloud_action(self, run: _ProjectActionRun, dialogs):
        project, pointcloud = run.project, run.pointcloud
        if project is None or pointcloud is None:
            self._placeholder_action(run.action_id, project, pointcloud)
            return None
        run.temp_dir = tempfile.mkdtemp(prefix=UPLOAD_TEMP_PREFIX)
        payload = dialogs.prompt_replace_single_pointcloud(
            self.QtWidgets,
            self,
            project,
            pointcloud,
            self._settings_dialog_defaults(),
            run.temp_dir,
        )
        if payload is None:
            return None
        run.progress_dialog = self._create_action_progress_dialog(
            "Punktwolke austauschen",
            f"„{pointcloud.name}“ wird ausgetauscht...",
            cancel_event=run.cancel_event,
        )
        on_progress = self._project_action_progress(
            run,
            ACTIVITY_ACTION_REPLACE,
            source_path=getattr(payload, "source_path", ""),
            target_path=pointcloud.s3_path or pointcloud.viewer_path,
        )
        return lambda: run.controller.replace_single_pointcloud(
            project,
            pointcloud,
            self._with_detected_crs_single(payload),
            on_progress=on_progress,
            cancel_requested=run.cancel_event.is_set,
        )

    def _build_replace_single_model_action(self, run: _ProjectActionRun, dialogs):
        project, pointcloud = run.project, run.pointcloud
        if project is None or pointcloud is None:
            self._placeholder_action(run.action_id, project, pointcloud)
            return None
        payload = dialogs.prompt_replace_single_model(self.QtWidgets, self, project, pointcloud)
        if payload is None:
            return None
        run.progress_dialog = self._create_action_progress_dialog(
            "GLB austauschen",
            f"„{pointcloud.name}“ wird vorbereitet und ausgetauscht...",
            cancel_event=run.cancel_event,
        )
        on_progress = self._project_action_progress(
            run,
            ACTIVITY_ACTION_REPLACE,
            source_path=payload.source_path,
            target_path=pointcloud.s3_path or pointcloud.viewer_path,
        )
        return lambda: run.controller.replace_single_model(
            project,
            pointcloud,
            payload,
            on_progress=on_progress,
            cancel_requested=run.cancel_event.is_set,
            confirm_spatial_warning=self._confirm_spatial_warning,
            confirm_crs_repair=self._confirm_crs_repair,
        )

    def _build_remove_pointcloud_action(self, run: _ProjectActionRun, dialogs):
        project, pointcloud = run.project, run.pointcloud
        if project is None or pointcloud is None:
            self._placeholder_action(run.action_id, project, pointcloud)
            return None
        if not dialogs.confirm_remove_pointcloud(self.QtWidgets, self, project, pointcloud):
            return None
        run.progress_dialog = self._create_action_progress_dialog(
            "Punktwolke entfernen",
            f"„{pointcloud.name}“ wird entfernt...",
        )
        return lambda: run.controller.remove_pointcloud(project, pointcloud)

    def _build_remove_model_action(self, run: _ProjectActionRun, dialogs):
        project, pointcloud = run.project, run.pointcloud
        if project is None or pointcloud is None:
            self._placeholder_action(run.action_id, project, pointcloud)
            return None
        if not dialogs.confirm_remove_model(self.QtWidgets, self, project, pointcloud):
            return None
        run.progress_dialog = self._create_action_progress_dialog(
            "3D-Modell entfernen",
            f"„{pointcloud.name}“ wird entfernt...",
        )
        return lambda: run.controller.remove_model(project, pointcloud)

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


# Actions without a builder (or without S3) end in _placeholder_action.
_PROJECT_ACTION_BUILDERS = {
    ACTION_RENAME: ProjectActionsMixin._build_rename_action,
    ACTION_DUPLICATE: ProjectActionsMixin._build_duplicate_action,
    ACTION_DELETE: ProjectActionsMixin._build_delete_action,
    ACTION_DOWNLOAD: ProjectActionsMixin._build_download_action,
    ACTION_DISABLE_LINK: ProjectActionsMixin._build_disable_link_action,
    ACTION_ENABLE_LINK: ProjectActionsMixin._build_enable_link_action,
    ACTION_REPLACE_ALL_POINTCLOUDS: ProjectActionsMixin._build_replace_all_pointclouds_action,
    ACTION_ADD_POINTCLOUDS: ProjectActionsMixin._build_add_pointclouds_action,
    ACTION_ADD_MODELS: ProjectActionsMixin._build_add_models_action,
    ACTION_REPAIR_CRS_METADATA: ProjectActionsMixin._build_repair_crs_metadata_action,
    ACTION_REPLACE_SINGLE_POINTCLOUD: ProjectActionsMixin._build_replace_single_pointcloud_action,
    ACTION_REPLACE_SINGLE_MODEL: ProjectActionsMixin._build_replace_single_model_action,
    ACTION_REMOVE_POINTCLOUD: ProjectActionsMixin._build_remove_pointcloud_action,
    ACTION_REMOVE_MODEL: ProjectActionsMixin._build_remove_model_action,
}
