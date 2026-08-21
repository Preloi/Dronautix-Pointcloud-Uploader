import io
import json
import os
import threading
import time

import pytest


def _import_qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtGui = pytest.importorskip("PySide6.QtGui")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtCore, QtGui, QtWidgets


def _app(QtWidgets):
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_startup_cleanup_removes_only_dedicated_glb_stages_older_than_24_hours(tmp_path, monkeypatch):
    from dronautix_uploader.qt_app import main_window

    old_stage = tmp_path / ".glb-upload-old"
    old_stage.mkdir()
    dedicated_root = tmp_path / main_window.GLB_UPLOAD_STAGING_ROOT_NAME
    dedicated_root.mkdir()
    dedicated_old_stage = dedicated_root / ".glb-upload-old"
    dedicated_old_stage.mkdir()
    nested_parent = tmp_path / "dronautix_potree_old"
    nested_parent.mkdir()
    nested_old_stage = nested_parent / ".glb-upload-old"
    nested_old_stage.mkdir()
    fresh_stage = tmp_path / ".glb-upload-fresh"
    fresh_stage.mkdir()
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "original.glb").write_bytes(b"original")
    old_timestamp = time.time() - (25 * 60 * 60)
    os.utime(old_stage, (old_timestamp, old_timestamp))
    os.utime(dedicated_old_stage, (old_timestamp, old_timestamp))
    os.utime(nested_old_stage, (old_timestamp, old_timestamp))
    monkeypatch.setattr(main_window.tempfile, "gettempdir", lambda: str(tmp_path))

    warnings = main_window.cleanup_stale_upload_temp_dirs()

    assert warnings == ()
    assert old_stage.exists()
    assert not dedicated_old_stage.exists()
    assert nested_old_stage.exists()
    assert fresh_stage.exists()
    assert (source_dir / "original.glb").exists()


def test_startup_cleanup_retries_locked_dedicated_glb_stages_and_reports_failure(tmp_path, monkeypatch):
    from dronautix_uploader.qt_app import main_window

    dedicated_root = tmp_path / main_window.GLB_UPLOAD_STAGING_ROOT_NAME
    old_stage = dedicated_root / ".glb-upload-locked"
    old_stage.mkdir(parents=True)
    old_timestamp = time.time() - (25 * 60 * 60)
    os.utime(old_stage, (old_timestamp, old_timestamp))
    calls = []

    def locked(path):
        calls.append(path)
        raise OSError("locked")

    monkeypatch.setattr(main_window.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(main_window.shutil, "rmtree", locked)

    warnings = main_window.cleanup_stale_upload_temp_dirs()

    assert len(calls) == 2
    assert old_stage.exists()
    assert len(warnings) == 1
    assert "nach erneutem Versuch" in warnings[0]


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


def test_spatial_warning_is_marshaled_to_modal_dialog_with_no_as_default(monkeypatch):
    QtCore, QtGui, QtWidgets = _import_qt()
    app = _app(QtWidgets)
    from dronautix_uploader.qt_app.main_window import create_main_window

    calls = []

    def answer_no(parent, title, text, buttons, default):
        calls.append((title, text, buttons, default))
        return QtWidgets.QMessageBox.No

    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", answer_no)
    window = create_main_window(QtCore, QtGui, QtWidgets)
    result = []
    worker = threading.Thread(target=lambda: result.append(window._confirm_spatial_warning("Halle: ca. 6,7 km")))
    try:
        worker.start()
        deadline = time.time() + 2.0
        while worker.is_alive() and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        worker.join(timeout=1.0)

        assert result == [False]
        assert calls[0][0] == "Modelle außerhalb der Punktwolke"
        assert "6,7 km" in calls[0][1]
        assert "Trotzdem hochladen?" in calls[0][1]
        assert calls[0][3] == QtWidgets.QMessageBox.No
    finally:
        window.deleteLater()


def test_crs_repair_confirmation_is_marshaled_to_modal_dialog_with_no_as_default(monkeypatch):
    QtCore, QtGui, QtWidgets = _import_qt()
    app = _app(QtWidgets)
    from dronautix_uploader.qt_app.main_window import create_main_window

    calls = []

    def answer_no(parent, title, text, buttons, default):
        calls.append((title, text, buttons, default))
        return QtWidgets.QMessageBox.No

    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", answer_no)
    window = create_main_window(QtCore, QtGui, QtWidgets)
    result = []
    worker = threading.Thread(
        target=lambda: result.append(
            window._confirm_crs_repair(
                "Mellitzgraben (EPSG:31255, Höhenbezug EPSG:5778) → Terra Hydron. "
                "Fehlende Metadaten: Projekt und Punktwolke Bestand."
            )
        )
    )
    try:
        worker.start()
        deadline = time.time() + 2.0
        while worker.is_alive() and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        worker.join(timeout=1.0)

        assert result == [False]
        assert calls[0][0] == "CRS-Metadaten reparieren"
        assert "Mellitzgraben (EPSG:31255, Höhenbezug EPSG:5778) → Terra Hydron" in calls[0][1]
        assert "Punktwolke Bestand" in calls[0][1]
        assert "Audit-Hinweis" in calls[0][1]
        assert calls[0][3] == QtWidgets.QMessageBox.No
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
            "datum": "2026-07-01T08:00:00",
            "pointclouds": [{"name": "Scan", "crs": "EPSG:25832"}],
            "history": [
                {"timestamp": "2026-07-18T12:30:00", "message": "Punktwolke 'Scan' wurde ausgetauscht."}
            ],
        },
        disabled=False,
    )
    page = create_projects_page(QtCore, QtGui, QtWidgets, project_previews=(preview,))

    try:
        table = page.findChild(QtWidgets.QTableView, "ProjectsTable")
        history_log = page.findChild(QtWidgets.QPlainTextEdit, "ProjectHistoryLog")
        assert [
            table.model().headerData(column, QtCore.Qt.Horizontal)
            for column in range(table.model().columnCount())
        ] == ["Kunde", "Projekt", "Format", "Status", "Erstellt am", "Aktualisiert"]
        assert table.model().index(0, 4).data() == "01.07.2026 08:00"
        assert table.model().index(0, 5).data() == "18.07.2026 12:30"
        assert "Erstellt am" in {label.text() for label in page.findChildren(QtWidgets.QLabel)}
        assert "01.07.2026 08:00" in {label.text() for label in page.findChildren(QtWidgets.QLabel)}
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


def test_projects_page_lists_models_and_routes_selected_glb_replace_when_qt_available():
    QtCore, QtGui, QtWidgets = _import_qt()
    app = _app(QtWidgets)

    from dronautix_uploader.qt_app.pages import create_projects_page
    from dronautix_uploader.qt_app.project_management import make_project_preview
    from dronautix_uploader.qt_app.project_management_actions import ACTION_ADD_MODELS, ACTION_REPLACE_SINGLE_MODEL

    preview = make_project_preview(
        {
            "id": "project-with-model",
            "kunde": "Kunde",
            "projekt": "Projekt mit GLB",
            "s3_path": "pointclouds/kunde/project/projekt",
            "pointclouds": [{"name": "Scan", "crs": "EPSG:25833"}],
            "models": [
                {
                    "id": "fassade",
                    "name": "Fassade",
                    "format": "glb",
                    "viewer_path": "kunde/project/projekt/models/fassade/versions/old/model.json",
                    "s3_path": "pointclouds/kunde/project/projekt/models/fassade/versions/old",
                    "crs": "EPSG:25833",
                    "vertical_crs": "EPSG:7837",
                }
            ],
        },
        disabled=False,
    )
    calls = []
    page = create_projects_page(
        QtCore,
        QtGui,
        QtWidgets,
        project_previews=(preview,),
        on_project_action=lambda action, project, resource: calls.append((action, project, resource)),
    )

    try:
        model_list = page.findChild(QtWidgets.QListWidget, "ModelList")
        assert model_list is not None
        assert model_list.count() == 1
        assert "Fassade - GLB" in model_list.item(0).text()
        model_list.setCurrentRow(0)
        replace_action = next(
            action
            for action in page.findChildren(QtGui.QAction)
            if action.text() == "Gewähltes 3D-Modell ersetzen"
        )
        assert replace_action.isEnabled()
        replace_action.trigger()
        app.processEvents()

        assert calls[0][0] == ACTION_REPLACE_SINGLE_MODEL
        assert calls[0][1].project_id == "project-with-model"
        assert calls[0][2].model_id == "fassade"
        remove_action = next(
            action
            for action in page.findChildren(QtGui.QAction)
            if action.text() == "Gewähltes 3D-Modell entfernen"
        )
        assert remove_action.isEnabled()
        remove_action.trigger()
        app.processEvents()
        assert calls[1][0] == "remove_model"
        assert calls[1][2].model_id == "fassade"
        add_action = next(
            action
            for action in page.findChildren(QtGui.QAction)
            if action.text() == "3D-Modell hinzufügen"
        )
        add_action.trigger()
        app.processEvents()
        assert calls[2][0] == ACTION_ADD_MODELS
        assert calls[2][1].project_id == "project-with-model"
        assert calls[2][2] is None
    finally:
        page.deleteLater()


def test_upload_page_accepts_optional_native_glbs_when_qt_available(tmp_path):
    QtCore, _QtGui, QtWidgets = _import_qt()
    _app(QtWidgets)

    from dronautix_uploader.core.contracts import ProgressEvent
    from dronautix_uploader.qt_app.pages import create_upload_page

    glb_path = tmp_path / "Haus.glb"
    glb_path.write_bytes(b"glTF")
    page = create_upload_page(QtCore, QtWidgets, on_start=lambda: None)

    try:
        page.add_source_paths(("scan.copc.laz",))
        label_texts = {label.text() for label in page.findChildren(QtWidgets.QLabel)}
        assert "3D-Modelle (GLB)" in label_texts
        assert not any("nativ X=Ost" in text for text in label_texts)
        page.findChild(QtWidgets.QLineEdit, "UploadCustomerInput").setText("Kunde")
        page.findChild(QtWidgets.QLineEdit, "UploadProjectInput").setText("Projekt")
        horizontal = [field for field in page.findChildren(QtWidgets.QLineEdit) if field.placeholderText() == "automatisch erkennen"][0]
        vertical = [field for field in page.findChildren(QtWidgets.QLineEdit) if field.placeholderText() == "optional"][0]
        horizontal.setText("EPSG:25832")
        vertical.setText("EPSG:7837")

        page.add_model_paths((str(glb_path), str(glb_path), str(tmp_path / "not-supported.obj")))
        model_list = page.findChild(QtWidgets.QListWidget, "UploadModelList")
        assert model_list.count() == 1
        assert "Haus.glb" in model_list.item(0).text()
        assert "Georeferenzierung aus GLB" in model_list.item(0).text()
        assert "Optimierung:" in model_list.item(0).text()
        assert page.findChild(QtWidgets.QLabel, "UploadModelCount").text() == "1 Modell"
        assert page.findChild(QtWidgets.QLineEdit, "UploadModelEastInput") is None

        model_input = page.model_inputs()[0]
        assert model_input.source_path == str(glb_path)
        assert model_input.name == ""
        assert model_input.slug == ""
        assert model_input.model_json_path == ""

        page.handle_progress(
            ProgressEvent(
                kind="detail",
                message="[MODELL] Haus: original, 2048 Bytes",
                detail=json.dumps(
                    {
                        "model_path": str(glb_path),
                        "optimization_status": "original",
                        "output_size": 2048,
                    }
                ),
                phase="optimization",
            )
        )
        assert "Optimierung: original" in model_list.item(0).text()
        assert "Ergebnisgröße: 2048 Bytes" in model_list.item(0).text()

        remove_button = next(
            button for button in page.findChildren(QtWidgets.QPushButton) if button.toolTip().startswith("Markierte Modelle")
        )
        model_list.setCurrentRow(0)
        remove_button.click()
        assert model_list.count() == 0
    finally:
        page.deleteLater()


def test_upload_page_passes_only_an_explicit_model_json_sidecar_to_the_core(tmp_path, monkeypatch):
    QtCore, _QtGui, QtWidgets = _import_qt()
    _app(QtWidgets)

    from dronautix_uploader.qt_app.pages import create_upload_page

    glb_path = tmp_path / "Haus.glb"
    glb_path.write_bytes(b"glTF")
    sidecar_path = tmp_path / "model.json"
    sidecar_path.write_text("{}", encoding="utf-8")
    page = create_upload_page(QtCore, QtWidgets, on_start=lambda: None)

    try:
        page.add_source_paths(("scan.copc.laz",))
        horizontal = [field for field in page.findChildren(QtWidgets.QLineEdit) if field.placeholderText() == "automatisch erkennen"][0]
        vertical = [field for field in page.findChildren(QtWidgets.QLineEdit) if field.placeholderText() == "optional"][0]
        horizontal.setText("EPSG:25832")
        vertical.setText("EPSG:7837")
        page.add_model_paths((str(glb_path),))
        model_list = page.findChild(QtWidgets.QListWidget, "UploadModelList")
        model_list.setCurrentRow(0)
        monkeypatch.setattr(
            QtWidgets.QFileDialog,
            "getOpenFileName",
            lambda *_args, **_kwargs: (str(sidecar_path), "model.json (model.json)"),
        )
        page.findChild(QtWidgets.QPushButton, "UploadModelSidecarButton").click()

        model_input = page.model_inputs()[0]
        assert model_input.source_path == str(glb_path)
        assert model_input.model_json_path == str(sidecar_path)
        assert "Sidecar: model.json" in model_list.item(0).text()
        assert page.findChild(QtWidgets.QPushButton, "UploadModelSidecarButton") is not None
    finally:
        page.deleteLater()


def test_upload_page_rejects_ambiguous_model_json_drop_without_assigning_any_model(tmp_path):
    QtCore, _QtGui, QtWidgets = _import_qt()
    _app(QtWidgets)

    from dronautix_uploader.qt_app.pages import create_upload_page

    first = tmp_path / "A.glb"
    second = tmp_path / "B.glb"
    sidecar = tmp_path / "model.json"
    first.write_bytes(b"glTF")
    second.write_bytes(b"glTF")
    sidecar.write_text("{}", encoding="utf-8")
    page = create_upload_page(QtCore, QtWidgets, on_start=lambda: None)

    try:
        page.add_model_paths((str(first), str(second), str(sidecar)))

        assert page.findChild(QtWidgets.QListWidget, "UploadModelList").count() == 0
        assert page.model_inputs() == ()
        assert "genau einem GLB" in page.findChild(QtWidgets.QLabel, "ErrorText").text()
    finally:
        page.deleteLater()


def test_upload_page_requires_pointcloud_crs_and_hides_models_for_local_conversion_when_qt_available(tmp_path):
    QtCore, _QtGui, QtWidgets = _import_qt()
    _app(QtWidgets)

    from dronautix_uploader.qt_app.pages import create_upload_page

    glb_path = tmp_path / "Haus.glb"
    glb_path.write_bytes(b"glTF")
    page = create_upload_page(QtCore, QtWidgets, on_start=lambda: None)

    try:
        page.add_source_paths(("scan.copc.laz",))
        horizontal = [field for field in page.findChildren(QtWidgets.QLineEdit) if field.placeholderText() == "automatisch erkennen"][0]
        vertical = [field for field in page.findChildren(QtWidgets.QLineEdit) if field.placeholderText() == "optional"][0]
        horizontal.setText("EPSG:25832")
        page.add_model_paths((str(glb_path),))

        with pytest.raises(ValueError, match="Höhenbezug"):
            page.model_inputs()

        vertical.setText("EPSG:7837")
        assert page.model_inputs()[0].source_path == str(glb_path)

        next(button for button in page.findChildren(QtWidgets.QPushButton) if button.text() == "Nur konvertieren").click()
        assert page.findChild(QtWidgets.QFrame, "UploadModelsPanel").isHidden()
        assert page.model_inputs() == ()
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
