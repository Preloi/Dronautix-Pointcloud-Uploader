"""Startup/manual update check, deferred offers and the installer download.

Mixin of the main window; see ``qt_app/main_window.py``.
"""

from __future__ import annotations

import threading

from ..activity_model import ACTION_UPDATE as ACTIVITY_ACTION_UPDATE


class UpdateMixin:
    def _resume_pending_update_if_idle(self):
        if self._has_active_background_tasks() or self._update_install_started:
            return
        pending_update = self._pending_update_result
        if pending_update is not None:
            self._pending_update_result = None
            self._pending_update_check_silent = None
            self.QtCore.QTimer.singleShot(0, lambda result=pending_update: self._offer_update_install(result))
            return
        pending_check_silent = self._pending_update_check_silent
        if pending_check_silent is not None:
            self._pending_update_check_silent = None
            self.QtCore.QTimer.singleShot(0, lambda silent=pending_check_silent: self._run_update_check(silent=silent))

    def _schedule_startup_update_check(self):
        if self._update_controller is None:
            return
        try:
            if not self._update_controller.checks_on_startup:
                return
        except Exception:
            return
        self.QtCore.QTimer.singleShot(1500, lambda: self._run_update_check(silent=True))

    def _run_update_check(self, *, silent: bool):
        if self._update_controller is None:
            self.statusBar().showMessage("Update-Prüfung nicht verfügbar.")
            return
        if self._update_install_started:
            self.statusBar().showMessage("Update-Installation läuft; bitte warten.")
            return
        if self._has_active_background_tasks():
            # Merken statt verwerfen: der Start-Check laeuft sonst nie, wenn die
            # Projektliste beim Start laenger als die Verzoegerung laedt.
            pending = self._pending_update_check_silent
            self._pending_update_check_silent = silent if pending is None else (pending and silent)
            if not silent:
                self.statusBar().showMessage("Update-Prüfung wird nach dem laufenden Vorgang gestartet.")
            return
        if not silent:
            self.statusBar().showMessage("Update-Prüfung läuft...")

        def handle_check_result(result):
            self._show_project_operation_summary(result, action=ACTIVITY_ACTION_UPDATE, actor="Updater")
            if getattr(result, "update_available", False):
                self._offer_update_install(result)
                return
            if silent:
                return
            if result.status == "failed":
                self.QtWidgets.QMessageBox.warning(self, "Update-Prüfung", result.message)
            else:
                self.QtWidgets.QMessageBox.information(self, "Update-Prüfung", result.message)

        def handle_check_error(error):
            self._show_task_error(error, action=ACTIVITY_ACTION_UPDATE, actor="Updater")
            if not silent:
                self.QtWidgets.QMessageBox.warning(self, "Update-Prüfung", str(error))

        self._start_background_task(
            self._update_controller.check_for_updates,
            on_result=handle_check_result,
            on_error=handle_check_error,
        )

    def _offer_update_install(self, result):
        if self._update_install_started:
            return
        if self._has_active_background_tasks():
            self._pending_update_result = result
            self.statusBar().showMessage("Update-Installation wartet auf den laufenden Vorgang.")
            return
        answer = self.QtWidgets.QMessageBox.question(
            self,
            "Update verfügbar",
            f"Version {result.remote_version} ist verfügbar.\n\n"
            "Jetzt herunterladen und installieren? Die Anwendung wird dazu beendet.",
            self.QtWidgets.QMessageBox.Yes | self.QtWidgets.QMessageBox.No,
            self.QtWidgets.QMessageBox.Yes,
        )
        if answer != self.QtWidgets.QMessageBox.Yes:
            self.statusBar().showMessage("Update verschoben.")
            return
        if self._has_active_background_tasks():
            self._pending_update_result = result
            self.statusBar().showMessage("Update-Installation wartet auf den laufenden Vorgang.")
            return
        self._update_install_started = True
        self.statusBar().showMessage(f"Update {result.remote_version} wird heruntergeladen...")

        cancel_event = threading.Event()
        dialog = self.QtWidgets.QProgressDialog(
            f"Update {result.remote_version} wird heruntergeladen...", "Abbrechen", 0, 0, self
        )
        dialog.setWindowTitle("Update")
        dialog.setWindowModality(self.QtCore.Qt.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setMinimumWidth(420)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(cancel_event.set)
        dialog.show()
        progress_emitter = self._qt_types.UpdateProgressEmitter()

        def show_download_progress(downloaded, total):
            if total:
                dialog.setRange(0, 100)
                dialog.setValue(min(int(downloaded * 100 / total), 100))
                dialog.setLabelText(
                    f"Update {result.remote_version} wird heruntergeladen...\n"
                    f"{downloaded / 1048576:.1f} MB von {total / 1048576:.1f} MB"
                )
            else:
                dialog.setLabelText(
                    f"Update {result.remote_version} wird heruntergeladen...\n{downloaded / 1048576:.1f} MB"
                )

        progress_emitter.progressed.connect(show_download_progress)

        def close_dialog():
            try:
                dialog.canceled.disconnect()
            except (RuntimeError, TypeError):
                pass
            dialog.close()

        def handle_install_result(summary):
            close_dialog()
            self._show_project_operation_summary(summary, action=ACTIVITY_ACTION_UPDATE, actor="Updater")
            if summary.status == "success":
                self._closing_for_update = True
                self.QtCore.QTimer.singleShot(200, self.close)
            elif summary.status == "cancelled":
                self._update_install_started = False
                self.statusBar().showMessage("Update-Download abgebrochen.")
            else:
                self._update_install_started = False
                self.QtWidgets.QMessageBox.critical(self, "Update", summary.message)

        def handle_install_error(error):
            close_dialog()
            self._update_install_started = False
            self._notify_task_error(
                error,
                "Update",
                action=ACTIVITY_ACTION_UPDATE,
                actor="Updater",
            )

        manifest = dict(result.manifest)
        self._update_progress_emitter = progress_emitter
        self._start_background_task(
            lambda: self._update_controller.download_and_install(
                manifest, on_progress=progress_emitter.progressed.emit, cancel_requested=cancel_event.is_set
            ),
            on_result=handle_install_result,
            on_error=handle_install_error,
        )
