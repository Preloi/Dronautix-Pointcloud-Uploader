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
        assert [model.index(row, 0).data() for row in range(model.rowCount())] == [
            "Beispielprojekt Nord",
            "COPC Demo",
            "Deaktivierter Upload",
        ]
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
        assert page.findChild(QtWidgets.QLineEdit, "ConverterPathInput").text() == "C:/Tools/PotreeConverter.exe"

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
            "Aktivitäten",
        ]
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
