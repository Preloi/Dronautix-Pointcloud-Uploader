"""Settings actions, runtime (S3) reload, page refresh and the connection status.

Mixin of the main window; see ``qt_app/main_window.py``.
"""

from __future__ import annotations

from ..activity_model import ACTION_UPDATE as ACTIVITY_ACTION_UPDATE
from .support import resolve_runtime_project_rows


class RuntimeMixin:
    def _handle_settings_action(self, action_id: str, state=None):
        if self._settings_controller is None:
            self.statusBar().showMessage("Einstellungen sind in dieser Umgebung nicht verfügbar.")
            return
        if self._update_install_started:
            self.statusBar().showMessage("Update-Installation läuft; bitte warten.")
            return

        if action_id == "save":
            try:
                summary = self._settings_controller.save_state(state or self._settings_controller.load_state())
            except Exception as error:
                self.statusBar().showMessage(str(error))
                self.QtWidgets.QMessageBox.critical(self, "Einstellungen", str(error))
                return
            self._show_project_operation_summary(summary, action=ACTIVITY_ACTION_UPDATE, actor="Einstellungen")
            self._notify_operation_summary(summary, "Einstellungen")
            self._reload_runtime_services()
            self._refresh_settings_page()
            return

        if action_id == "edit":
            from ..settings_dialogs import prompt_settings

            try:
                state = self._settings_controller.load_state()
                updated_state = prompt_settings(self.QtWidgets, self, state)
                if updated_state is None:
                    return
                summary = self._settings_controller.save_state(updated_state)
            except Exception as error:
                self.statusBar().showMessage(str(error))
                self.QtWidgets.QMessageBox.critical(self, "Einstellungen", str(error))
                return
            self._show_project_operation_summary(summary, action=ACTIVITY_ACTION_UPDATE, actor="Einstellungen")
            self._notify_operation_summary(summary, "Einstellungen")
            self._reload_runtime_services()
            self._refresh_settings_page()
            return

        if action_id == "test_connection":
            if self._has_active_background_tasks():
                self.statusBar().showMessage("Eine Aktion läuft bereits; bitte warten.")
                return
            self.statusBar().showMessage("S3-Verbindungstest gestartet...")

            has_unsaved = getattr(self._settings_page, "has_unsaved_changes", None)
            tests_saved_settings = not (callable(has_unsaved) and has_unsaved())

            def show_test_result(summary):
                self._show_project_operation_summary(
                    summary, action=ACTIVITY_ACTION_UPDATE, actor="Einstellungen"
                )
                # Only a test of the saved settings describes the running connection.
                if tests_saved_settings and self._runtime.get("project_provider") is not None:
                    ok = summary.status == "success"
                    self._connection_state = "ok" if ok else "failed"
                    self._connection_detail = "" if ok else summary.message
                    self._show_connection_status()
                self._notify_operation_summary(summary, "Verbindung testen")

            self._start_background_task(
                lambda: self._settings_controller.test_connection(state),
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
            self._run_update_check(silent=False)
            return

        if action_id == "clear_credentials":
            answer = self.QtWidgets.QMessageBox.question(
                self,
                "Zugangsdaten entfernen",
                "Die gespeicherten AWS-Zugangsdaten werden von diesem Rechner entfernt.\n"
                "Uploads und Projektverwaltung sind danach erst nach erneuter Eingabe möglich.\n\n"
                "Fortfahren?",
                self.QtWidgets.QMessageBox.Yes | self.QtWidgets.QMessageBox.No,
                self.QtWidgets.QMessageBox.No,
            )
            if answer != self.QtWidgets.QMessageBox.Yes:
                return
            summary = self._settings_controller.clear_credentials()
            self._show_project_operation_summary(summary, action=ACTIVITY_ACTION_UPDATE, actor="Einstellungen")
            self._notify_operation_summary(summary, "Zugangsdaten entfernen")
            self._reload_runtime_services()
            self._refresh_settings_page()
            return

        self.statusBar().showMessage(f"Unbekannte Einstellungsaktion: {action_id}")

    def _refresh_projects_page(self):
        if self._update_install_started:
            self.statusBar().showMessage("Update-Installation läuft; Projektaktualisierung wurde zurückgestellt.")
            return
        refresh = getattr(self._projects_page, "reload_projects", None)
        if callable(refresh):
            refresh()

    def _refresh_activity_page(self):
        refresh = getattr(self._activity_page, "reload_activity", None)
        if callable(refresh):
            refresh()

    def _refresh_settings_page(self):
        # Called after saving/clearing: the stored state is authoritative.
        refresh = getattr(self._settings_page, "render_saved_settings", None) or getattr(
            self._settings_page, "reload_settings", None
        )
        if callable(refresh):
            refresh()

    def _projects_empty_state_reason(self) -> str:
        if self._runtime.get("project_provider") is not None:
            return ""
        status = str(self._runtime.get("status", "") or "").strip()
        return (
            f"{status}. " if status and not status.endswith(".") else (f"{status} " if status else "")
        ) + "Ohne S3-Verbindung können keine Projekte angezeigt werden."

    def _runtime_project_rows(self):
        provider = self._runtime.get("project_provider")
        fallback_rows = () if provider is not None else self._runtime.get("disconnected_project_previews", ())
        return resolve_runtime_project_rows(provider, fallback_rows=fallback_rows)

    def _reload_runtime_services(self):
        if self._runtime_reloader is None:
            return
        try:
            bundle = self._runtime_reloader()
        except Exception as error:
            self._runtime.update(
                {
                    "project_provider": None,
                    "project_controller": None,
                    "upload_controller": None,
                    "status": f"Nicht verbunden: {error}",
                }
            )
            self.statusBar().showMessage(str(self._runtime["status"]))
            self._show_connection_status()
            self._refresh_projects_page()
            return
        self._runtime.update(
            {
                "project_provider": getattr(bundle, "project_provider", None),
                "project_controller": getattr(bundle, "project_controller", None),
                "upload_controller": getattr(bundle, "upload_controller", None),
                "status": getattr(bundle, "status", "Runtime neu geladen"),
            }
        )
        self.statusBar().showMessage(str(self._runtime["status"]))
        self._connection_state = "checking" if self._runtime.get("project_provider") is not None else "none"
        self._connection_detail = ""
        self._show_connection_status()
        self._refresh_projects_page()

    def _handle_project_load_finished(self, ok: bool, reason: str):
        # Only a real S3 round trip (loading the project index) proves the
        # connection; having credentials and a client object does not.
        if self._runtime.get("project_provider") is None:
            self._connection_state, self._connection_detail = "none", ""
        else:
            self._connection_state = "ok" if ok else "failed"
            self._connection_detail = "" if ok else reason
        self._show_connection_status()

    def _show_connection_status(self):
        if self._runtime.get("project_provider") is None:
            self._connection_state = "none"
        texts = {
            "ok": "● S3 verbunden",
            "checking": "● Verbindung wird geprüft...",
            "failed": "● S3-Verbindung fehlgeschlagen",
            "none": "● Nicht verbunden",
        }
        state = self._connection_state
        self._connection_label.setText(texts.get(state, texts["none"]))
        self._connection_label.setProperty("connection", state)
        self._connection_label.setToolTip(
            self._connection_detail if state == "failed" and self._connection_detail
            else str(self._runtime.get("status", "") or "")
        )
        self._connection_label.style().unpolish(self._connection_label)
        self._connection_label.style().polish(self._connection_label)
