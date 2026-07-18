import io
import json
import os

import pytest


def _import_qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtGui = pytest.importorskip("PySide6.QtGui")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtCore, QtGui, QtWidgets


def _app(QtWidgets):
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_main_window_constructs_with_service_backed_fake_runtime_when_qt_available():
    QtCore, QtGui, QtWidgets = _import_qt()
    _app(QtWidgets)

    from dronautix_uploader.qt_app.main_window import create_main_window
    from dronautix_uploader.qt_app.runtime_services import (
        ProjectManagementRuntimeConfig,
        create_runtime_controller_bundle,
    )

    config = ProjectManagementRuntimeConfig(
        aws_access_key_id="AKIA_TEST",
        aws_secret_access_key="secret",
        region_name="eu-central-1",
        bucket_name="runtime-bucket",
    )
    bundle = create_runtime_controller_bundle(config, s3_client=FakeS3Client())

    window = create_main_window(
        QtCore,
        QtGui,
        QtWidgets,
        project_provider=bundle.project_provider,
        project_controller=bundle.project_controller,
        upload_controller=bundle.upload_controller,
        project_runtime_status=bundle.status,
    )

    try:
        assert window.objectName() == "MainWindow"
        assert "Dashboard" not in window._pages
        assert "Projektverwaltung" in window._pages
        assert window.stack.currentWidget() is window._pages["Upload"]
        window._projects_page.reload_projects()
    finally:
        window.deleteLater()


def test_main_window_survives_project_provider_errors_when_qt_available():
    QtCore, QtGui, QtWidgets = _import_qt()
    _app(QtWidgets)

    from dronautix_uploader.qt_app.main_window import create_main_window

    window = create_main_window(
        QtCore,
        QtGui,
        QtWidgets,
        project_provider=FailingProvider(),
        project_runtime_status="S3 nicht erreichbar",
    )

    try:
        assert window.objectName() == "MainWindow"
        window._projects_page.reload_projects()
    finally:
        window.deleteLater()


def test_projects_page_keeps_all_matching_rows_visible_after_selection_when_qt_available():
    QtCore, QtGui, QtWidgets = _import_qt()
    _app(QtWidgets)

    from dronautix_uploader.qt_app.pages import create_projects_page
    from dronautix_uploader.qt_app.project_management import example_project_previews

    page = create_projects_page(
        QtCore,
        QtGui,
        QtWidgets,
        project_previews=example_project_previews(),
    )

    try:
        table = page.findChild(QtWidgets.QTableView, "ProjectsTable")
        assert table is not None
        model = table.model()
        assert model.rowCount() == 3

        second_index = model.index(1, 0)
        table.selectionModel().select(
            second_index,
            QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows,
        )

        assert model.rowCount() == 3
        assert [model.headerData(column, QtCore.Qt.Horizontal) for column in range(2)] == ["Kunde", "Projekt"]
        assert [model.index(row, 0).data() for row in range(model.rowCount())] == [
            "Dronautix",
            "Interner Test",
            "Kunde",
        ]
        assert [model.index(row, 1).data() for row in range(model.rowCount())] == [
            "Beispielprojekt Nord",
            "COPC Demo",
            "Deaktivierter Upload",
        ]
        assert model.index(0, 3).data(QtCore.Qt.ForegroundRole).color().name() == "#2ecc71"
        assert model.index(2, 3).data(QtCore.Qt.ForegroundRole).color().name() == "#e74c3c"
        assert page.findChild(QtWidgets.QPlainTextEdit, "ProjectHistoryLog").isHidden()
    finally:
        page.deleteLater()


def test_projects_page_shows_persistent_history_only_when_present_when_qt_available():
    QtCore, QtGui, QtWidgets = _import_qt()
    _app(QtWidgets)

    from dronautix_uploader.qt_app.pages import create_projects_page
    from dronautix_uploader.qt_app.project_management import make_project_preview

    preview = make_project_preview(
        {
            "id": "changed",
            "projekt": "Projekt",
            "kunde": "Kunde",
            "pointclouds": [{"name": "Scan", "crs": "EPSG:25832"}],
            "history": [
                {"timestamp": "2026-07-18T12:30:00", "message": "Punktwolke 'Scan' wurde ausgetauscht."}
            ],
        },
        disabled=False,
    )
    page = create_projects_page(QtCore, QtGui, QtWidgets, project_previews=(preview,))

    try:
        history_log = page.findChild(QtWidgets.QPlainTextEdit, "ProjectHistoryLog")
        assert not history_log.isHidden()
        assert history_log.toPlainText() == "18.07.2026 12:30 – Punktwolke 'Scan' wurde ausgetauscht."
    finally:
        page.deleteLater()


def test_project_status_cells_toggle_existing_link_state_actions_when_qt_available():
    QtCore, QtGui, QtWidgets = _import_qt()
    app = _app(QtWidgets)

    from dronautix_uploader.qt_app.pages import create_projects_page
    from dronautix_uploader.qt_app.project_management import example_project_previews
    from dronautix_uploader.qt_app.project_management_actions import ACTION_DISABLE_LINK, ACTION_ENABLE_LINK

    calls = []
    page = create_projects_page(
        QtCore,
        QtGui,
        QtWidgets,
        project_previews=example_project_previews(),
        on_project_action=lambda action, project, pointcloud: calls.append((action, project.project_id, pointcloud)),
    )

    try:
        table = page.findChild(QtWidgets.QTableView, "ProjectsTable")
        model = table.model()
        assert table.itemDelegateForColumn(3).objectName() == "ProjectStatusToggleDelegate"
        active_status = model.index(0, 3)
        inactive_status = model.index(2, 3)
        assert active_status.data() == "Aktiv"
        assert inactive_status.data() == "Inaktiv"
        assert active_status.data(QtCore.Qt.CheckStateRole) == QtCore.Qt.CheckState.Checked.value
        assert inactive_status.data(QtCore.Qt.CheckStateRole) == QtCore.Qt.CheckState.Unchecked.value

        model.setData(active_status, QtCore.Qt.CheckState.Unchecked, QtCore.Qt.CheckStateRole)
        app.processEvents()
        assert calls[-1] == (ACTION_DISABLE_LINK, "example-north", None)
        assert active_status.data(QtCore.Qt.CheckStateRole) == QtCore.Qt.CheckState.Checked.value

        model.setData(inactive_status, QtCore.Qt.CheckState.Checked, QtCore.Qt.CheckStateRole)
        app.processEvents()
        assert calls[-1] == (ACTION_ENABLE_LINK, "disabled-upload", None)
        assert inactive_status.data(QtCore.Qt.CheckStateRole) == QtCore.Qt.CheckState.Unchecked.value

        page.reload_projects()
        app.processEvents()
        assert len(calls) == 2
    finally:
        page.deleteLater()


def test_settings_page_exposes_direct_form_and_actions_when_qt_available():
    QtCore, QtGui, QtWidgets = _import_qt()
    _app(QtWidgets)

    from dronautix_uploader.qt_app.pages import create_settings_page
    from dronautix_uploader.qt_app.settings_controller import SettingsFormState

    actions = []
    state = SettingsFormState(
        aws_access_key_id="access",
        aws_secret_access_key="secret",
        region_name="eu-central-1",
        bucket_name="bucket",
        converter_path="C:/Tools/PotreeConverter.exe",
        output_base_dir="C:/Output",
        update_channel="Preview",
    )
    page = create_settings_page(
        QtCore,
        QtWidgets,
        settings_state=state,
        on_settings_action=lambda action, payload=None: actions.append((action, payload)),
    )

    try:
        assert page.findChild(QtWidgets.QLineEdit, "AwsAccessInput").text() == "access"
        assert page.findChild(QtWidgets.QLineEdit, "ConverterPathInput") is None

        buttons = {button.text(): button for button in page.findChildren(QtWidgets.QPushButton)}
        assert {"Speichern", "Verbindung testen", "Update prüfen", "Neu laden"} <= set(buttons)
        page.findChild(QtWidgets.QLineEdit, "S3BucketInput").setText("new-bucket")
        buttons["Speichern"].click()

        assert actions[-1][0] == "save"
        assert actions[-1][1].bucket_name == "new-bucket"
    finally:
        page.deleteLater()


def test_main_window_has_no_dashboard_sidebar_entry_when_qt_available():
    QtCore, QtGui, QtWidgets = _import_qt()
    _app(QtWidgets)

    from dronautix_uploader.qt_app.main_window import create_main_window

    window = create_main_window(QtCore, QtGui, QtWidgets)

    try:
        assert "Dashboard" not in window._buttons
        assert list(window._buttons) == [
            "Upload",
            "Projektverwaltung",
            "Einstellungen",
        ]
    finally:
        window.deleteLater()


def test_upload_page_shows_independent_process_progress_when_qt_available():
    QtCore, _QtGui, QtWidgets = _import_qt()
    _app(QtWidgets)

    from dronautix_uploader.core.contracts import ProgressEvent
    from dronautix_uploader.qt_app.pages import create_upload_page

    page = create_upload_page(QtCore, QtWidgets, on_start=lambda: None)

    try:
        page.set_running(True)
        preparation = page.findChild(QtWidgets.QProgressBar, "UploadPreparationProgress")
        conversion = page.findChild(QtWidgets.QProgressBar, "UploadConversionProgress")
        upload = page.findChild(QtWidgets.QProgressBar, "UploadTransferProgress")
        index = page.findChild(QtWidgets.QProgressBar, "UploadIndexProgress")

        assert all(bar is not None for bar in (preparation, conversion, upload, index))
        assert conversion.value() == 100
        assert conversion.format() == "Nicht erforderlich"

        page.handle_progress(ProgressEvent(kind="progress", percent=0.4, phase="preparation"))
        page.handle_progress(ProgressEvent(kind="progress", percent=0.65, phase="upload"))
        page.handle_progress(ProgressEvent(kind="progress", percent=1.0, phase="index"))

        assert preparation.value() == 40
        assert upload.value() == 65
        assert index.value() == 100
    finally:
        page.deleteLater()


def test_main_window_releases_busy_state_after_background_task_completes_when_qt_available():
    import time

    QtCore, QtGui, QtWidgets = _import_qt()
    app = _app(QtWidgets)

    from dronautix_uploader.qt_app.main_window import create_main_window

    window = create_main_window(QtCore, QtGui, QtWidgets)
    results = []

    try:
        window._start_background_task(lambda: 42, on_result=results.append)

        # Regression: sender() ist bei queued Cross-Thread-Signalen None; der
        # Task muss trotzdem abgeraeumt werden, sonst blockiert jede weitere
        # Aktion mit "Eine Aktion laeuft bereits".
        deadline = time.time() + 5
        while time.time() < deadline and window._has_active_background_tasks():
            app.processEvents()
            time.sleep(0.01)

        assert results == [42]
        assert not window._has_active_background_tasks()
        assert not window._task_records
    finally:
        window.deleteLater()


def test_main_window_opens_settings_page_when_runtime_is_not_connected_when_qt_available():
    QtCore, QtGui, QtWidgets = _import_qt()
    _app(QtWidgets)

    from dronautix_uploader.qt_app.main_window import create_main_window
    from dronautix_uploader.qt_app.settings_controller import SettingsFormState

    class FakeSettingsController:
        def load_state(self):
            return SettingsFormState()

        def preview(self):
            from dronautix_uploader.qt_app.dashboard_settings_model import example_settings_preview

            return example_settings_preview()

    window = create_main_window(
        QtCore,
        QtGui,
        QtWidgets,
        settings_controller=FakeSettingsController(),
        project_controller=None,
    )

    try:
        assert window.stack.currentWidget() is window._pages["Einstellungen"]
    finally:
        window.deleteLater()


class FakeS3Client:
    def __init__(self):
        self.objects = {
            "projects_index.json": {
                "projects": [{"id": "active", "projekt": "Aktiv", "kunde": "Kunde"}],
                "disabled_projects": [],
            }
        }

    def get_object(self, Bucket, Key):
        assert Bucket == "runtime-bucket"
        data = json.dumps(self.objects[Key]).encode("utf-8")
        return {"Body": io.BytesIO(data)}


class FailingProvider:
    def list_projects_for_management(self):
        raise RuntimeError("S3 unavailable")
