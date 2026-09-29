"""Acceptance tests: CRS detection never blocks the GUI thread (roadmap step 2)."""

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


def _process_until(app, predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        app.processEvents()
        time.sleep(0.01)
    return predicate()


class ControlledDetector:
    """Detector whose calls block until released; records call order."""

    def __init__(self):
        self.calls = []
        self.gates = {}
        self.results = {}
        self.lock = threading.Lock()

    def gate(self, path, occurrence=1):
        key = (path, occurrence)
        with self.lock:
            return self.gates.setdefault(key, threading.Event())

    def __call__(self, path):
        with self.lock:
            occurrence = sum(1 for called in self.calls if called == path) + 1
            self.calls.append(path)
        self.gate(path, occurrence).wait(10)
        return self.results.get((path, occurrence), {"value": f"EPSG:{25830 + occurrence}"})


def _page(QtCore, QtWidgets, detector, on_start=lambda: None):
    from dronautix_uploader.qt_app.pages import create_upload_page

    return create_upload_page(QtCore, QtWidgets, on_start=on_start, crs_detector=detector)


def _button(QtWidgets, page, text):
    return next(button for button in page.findChildren(QtWidgets.QPushButton) if button.text() == text)


def _source_texts(QtWidgets, page):
    source_list = page.findChild(QtWidgets.QListWidget, "UploadSourceList")
    return [source_list.item(row).text() for row in range(source_list.count())]


def test_adding_sources_returns_immediately_and_upload_waits_for_detection():
    QtCore, _QtGui, QtWidgets = _import_qt()
    app = _app(QtWidgets)
    detector = ControlledDetector()
    page = _page(QtCore, QtWidgets, detector)
    try:
        started = time.monotonic()
        page.add_source_paths(("nas/a.laz", "nas/b.laz"))
        assert time.monotonic() - started < 0.5  # the GUI thread did not wait for the NAS

        start = _button(QtWidgets, page, "Hochladen")
        assert page.crs_detection_pending() and not start.isEnabled()
        assert all("wird erkannt" in text for text in _source_texts(QtWidgets, page))
        labels = [label.text() for label in page.findChildren(QtWidgets.QLabel)]
        assert "2 Quellen · CRS wird erkannt ..." in labels

        detector.gate("nas/a.laz").set()
        detector.gate("nas/b.laz").set()
        assert _process_until(app, lambda: not page.crs_detection_pending())
        assert start.isEnabled()
        assert all("EPSG:25831" in text for text in _source_texts(QtWidgets, page))
    finally:
        page.deleteLater()


def test_result_for_removed_source_is_dropped_and_readded_path_gets_its_new_result():
    QtCore, _QtGui, QtWidgets = _import_qt()
    app = _app(QtWidgets)
    detector = ControlledDetector()
    detector.results[("nas/a.laz", 1)] = {"value": "EPSG:1111"}  # stale answer
    detector.results[("nas/a.laz", 2)] = {"value": "EPSG:2222"}
    page = _page(QtCore, QtWidgets, detector)
    source_list = page.findChild(QtWidgets.QListWidget, "UploadSourceList")
    try:
        page.add_source_paths(("nas/a.laz",))
        _process_until(app, lambda: detector.calls == ["nas/a.laz"])
        source_list.selectAll()
        _button(QtWidgets, page, "Entfernen").click()
        assert not page.crs_detection_pending()

        page.add_source_paths(("nas/a.laz",))
        detector.gate("nas/a.laz", 2).set()
        assert _process_until(app, lambda: not page.crs_detection_pending())
        detector.gate("nas/a.laz", 1).set()  # the old request finishes last
        _process_until(app, lambda: False, timeout=0.3)

        assert "EPSG:2222" in _source_texts(QtWidgets, page)[0]
        assert page.crs_info_by_source_path()["nas/a.laz"]["value"] == "EPSG:2222"
    finally:
        detector.gate("nas/a.laz", 1).set()
        page.deleteLater()


def test_mode_switch_during_detection_keeps_results_for_both_modes():
    QtCore, _QtGui, QtWidgets = _import_qt()
    app = _app(QtWidgets)
    detector = ControlledDetector()
    page = _page(QtCore, QtWidgets, detector)
    try:
        page.add_source_paths(("nas/a.laz", "nas/b.laz"))
        _button(QtWidgets, page, "Nur lokal konvertieren").click()
        detector.gate("nas/a.laz").set()
        detector.gate("nas/b.laz").set()
        _process_until(app, lambda: len(detector.calls) == 2)
        _process_until(app, lambda: not page.crs_detection_pending())
        assert "EPSG:25831" in _source_texts(QtWidgets, page)[0]

        _button(QtWidgets, page, "Upload zu S3").click()
        assert not page.crs_detection_pending()
        assert all("EPSG:25831" in text for text in _source_texts(QtWidgets, page))
    finally:
        page.deleteLater()


def test_source_removed_in_convert_mode_is_detected_again_after_switching_back_to_upload():
    QtCore, _QtGui, QtWidgets = _import_qt()
    app = _app(QtWidgets)
    detector = ControlledDetector()
    detector.results[("nas/b.laz", 2)] = {"value": "EPSG:31256"}
    page = _page(QtCore, QtWidgets, detector)
    source_list = page.findChild(QtWidgets.QListWidget, "UploadSourceList")
    start = _button(QtWidgets, page, "Hochladen")
    try:
        page.add_source_paths(("nas/a.laz", "nas/b.laz"))
        detector.gate("nas/a.laz").set()
        detector.gate("nas/b.laz").set()
        assert _process_until(app, lambda: not page.crs_detection_pending())

        _button(QtWidgets, page, "Nur lokal konvertieren").click()
        source_list.selectAll()
        _button(QtWidgets, page, "Entfernen").click()  # forgets the CRS of nas/b.laz
        _button(QtWidgets, page, "Upload zu S3").click()  # both sources are back

        assert page.crs_detection_pending() and not start.isEnabled()
        assert _process_until(app, lambda: detector.calls.count("nas/b.laz") == 2)
        detector.gate("nas/b.laz", 2).set()
        assert _process_until(app, lambda: not page.crs_detection_pending())
        assert start.isEnabled()
        assert page.crs_info_by_source_path()["nas/b.laz"]["value"] == "EPSG:31256"
    finally:
        detector.gate("nas/b.laz", 2).set()
        page.deleteLater()


def test_manual_crs_is_never_overwritten_by_a_late_detection_result():
    QtCore, _QtGui, QtWidgets = _import_qt()
    app = _app(QtWidgets)
    detector = ControlledDetector()
    page = _page(QtCore, QtWidgets, detector)
    try:
        page.add_source_paths(("nas/a.laz",))
        horizontal = next(
            field for field in page.findChildren(QtWidgets.QLineEdit) if field.placeholderText() == "automatisch erkennen"
        )
        horizontal.setText("EPSG:31256")
        detector.gate("nas/a.laz").set()
        assert _process_until(app, lambda: not page.crs_detection_pending())

        assert "EPSG:31256" in _source_texts(QtWidgets, page)[0] and "manuell" in _source_texts(QtWidgets, page)[0]
        assert page.crs_info_by_source_path()["nas/a.laz"]["value"] == "EPSG:31256"
    finally:
        page.deleteLater()


def test_cache_redetects_potree_folder_when_its_metadata_file_changes(tmp_path):
    from dronautix_uploader.qt_app.crs_detection_worker import CrsDetectionCache

    folder = tmp_path / "potree"
    folder.mkdir()
    metadata = folder / "metadata.json"
    metadata.write_text('{"projection": "EPSG:25832"}', encoding="utf-8")
    calls = []
    cache = CrsDetectionCache()

    def detector(path):
        calls.append(path)
        return {"value": "EPSG:25832"}

    cache.detect(str(folder), detector)
    cache.detect(str(folder), detector)
    assert len(calls) == 1  # cached while metadata.json is unchanged

    stat = metadata.stat()
    metadata.write_text('{"projection": "EPSG:25833", "changed": true}', encoding="utf-8")
    os.utime(metadata, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    cache.detect(str(folder), detector)
    assert len(calls) == 2


def test_cache_follows_cloud_js_when_metadata_json_has_no_crs(tmp_path):
    from dronautix_uploader.core.crs_detection import detect_pointcloud_crs
    from dronautix_uploader.qt_app.crs_detection_worker import CrsDetectionCache

    folder = tmp_path / "potree"
    folder.mkdir()
    (folder / "metadata.json").write_text('{"version": "2.0"}', encoding="utf-8")  # no CRS -> cloud.js is read
    cloud_js = folder / "cloud.js"
    cloud_js.write_text('{"projection": "EPSG:25832"}', encoding="utf-8")
    cache = CrsDetectionCache()
    assert cache.detect(str(folder), detect_pointcloud_crs)["value"] == "EPSG:25832"

    stat = cloud_js.stat()
    cloud_js.write_text('{"projection": "EPSG:31256"}', encoding="utf-8")
    os.utime(cloud_js, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    assert detect_pointcloud_crs(str(folder))["value"] == "EPSG:31256"
    assert cache.detect(str(folder), detect_pointcloud_crs)["value"] == "EPSG:31256"


def test_window_closes_promptly_while_detection_hangs_forever(monkeypatch):
    QtCore, QtGui, QtWidgets = _import_qt()
    app = _app(QtWidgets)
    from dronautix_uploader.qt_app import crs_detection_worker
    from dronautix_uploader.qt_app import main_window

    hang = threading.Event()
    threads = []
    real_start = crs_detection_worker.start_crs_detection

    def recording_start(*args, **kwargs):
        thread = real_start(*args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(main_window, "cleanup_stale_upload_temp_dirs", lambda: ())
    monkeypatch.setattr("dronautix_uploader.qt_app.pages.upload_page.start_crs_detection", recording_start)
    monkeypatch.setattr("dronautix_uploader.qt_app.pages.upload_page.detect_pointcloud_crs", lambda path: hang.wait(60) or {})
    window = main_window.create_main_window(QtCore, QtGui, QtWidgets)
    try:
        window.show()
        # A local path: the hang is simulated by the detector. (A real dead UNC
        # host already blocks os.stat for ~20 s, which is why stat runs in the
        # worker as well; that would only slow down this test's cleanup.)
        window._upload_page.add_source_paths(("nas/scan.laz",))
        assert threads and threads[0].daemon and threads[0].is_alive()

        started = time.monotonic()
        window.close()
        window.deleteLater()
        _process_until(app, lambda: False, timeout=0.2)
        assert time.monotonic() - started < 2.0  # nothing waited for the hung NAS
    finally:
        hang.set()  # let the daemon thread finish; its late result is dropped quietly
        if threads:
            threads[0].join(2)
            assert not threads[0].is_alive()


def test_ctrl_enter_does_not_start_upload_while_crs_is_still_detected(monkeypatch):
    QtCore, QtGui, QtWidgets = _import_qt()
    _app(QtWidgets)
    from dronautix_uploader.qt_app import main_window

    hang = threading.Event()
    monkeypatch.setattr(main_window, "cleanup_stale_upload_temp_dirs", lambda: ())
    monkeypatch.setattr("dronautix_uploader.qt_app.pages.upload_page.detect_pointcloud_crs", lambda path: hang.wait(10) or {})
    window = main_window.create_main_window(QtCore, QtGui, QtWidgets)
    started = []
    monkeypatch.setattr(window, "_run_new_upload", lambda form: started.append(form))
    try:
        window._upload_page.add_source_paths(("nas/a.laz",))
        window._select_page("Upload")
        window._trigger_upload_shortcut()

        assert started == []
        assert "CRS" in window.statusBar().currentMessage()
    finally:
        hang.set()
        window.deleteLater()


def test_project_replace_detects_crs_in_the_worker_thread_not_the_gui_thread(monkeypatch, tmp_path):
    QtCore, QtGui, QtWidgets = _import_qt()
    app = _app(QtWidgets)
    from dronautix_uploader.qt_app import main_window, project_management_dialogs
    from dronautix_uploader.qt_app.project_management import make_project_preview
    from dronautix_uploader.qt_app.project_management_actions import ProjectOperationSummary
    from dronautix_uploader.qt_app.project_management_controller import ReplaceAllPointcloudsInput
    from dronautix_uploader.qt_app.window import support as window_support

    detection_threads = []
    received = []

    def recording_detector(path):
        detection_threads.append(threading.current_thread())
        return {"value": "EPSG:25832"}

    class Controller:
        def replace_all_pointclouds(self, project, payload, on_progress=None, cancel_requested=None):
            received.append(payload)
            return ProjectOperationSummary(status="success", message="ok")

    monkeypatch.setattr(main_window, "cleanup_stale_upload_temp_dirs", lambda: ())
    monkeypatch.setattr(window_support, "detect_pointcloud_crs", recording_detector)
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *args, **kwargs: None)
    source = tmp_path / "neu.laz"
    source.write_bytes(b"LASF")
    monkeypatch.setattr(
        project_management_dialogs,
        "prompt_replace_all_pointclouds",
        lambda *args, **kwargs: ReplaceAllPointcloudsInput(source_paths=(str(source),)),
    )
    window = main_window.create_main_window(QtCore, QtGui, QtWidgets, project_controller=Controller())
    project = make_project_preview({"id": "p1", "projekt": "P", "kunde": "K", "s3_path": "pointclouds/k/p1/p"}, False)
    try:
        window._handle_project_action("replace_all_pointclouds", project)
        assert _process_until(app, lambda: bool(received) and not window._has_active_background_tasks())

        assert detection_threads and all(thread is not threading.main_thread() for thread in detection_threads)
        assert received[0].crs_info_by_source_path[str(source)]["value"] == "EPSG:25832"
    finally:
        window.deleteLater()


def test_project_search_is_debounced_and_can_be_applied_immediately():
    QtCore, QtGui, QtWidgets = _import_qt()
    app = _app(QtWidgets)
    from dronautix_uploader.qt_app.pages import PROJECT_SEARCH_DEBOUNCE_MS, create_projects_page
    from dronautix_uploader.qt_app.project_management import make_project_preview

    projects = tuple(make_project_preview({"id": f"p{i}", "projekt": f"Projekt {i}", "kunde": "K"}, False) for i in range(30))
    page = create_projects_page(QtCore, QtGui, QtWidgets, project_previews=projects)
    try:
        table = page.findChild(QtWidgets.QTableView, "ProjectsTable")
        search = next(field for field in page.findChildren(QtWidgets.QLineEdit) if field.placeholderText() == "Projekte suchen")
        search.setText("Projekt 2")
        assert table.model().rowCount() == 30  # not filtered on the keystroke itself
        assert _process_until(app, lambda: table.model().rowCount() < 30, timeout=PROJECT_SEARCH_DEBOUNCE_MS / 1000 + 1)

        search.setText("Projekt 29")
        page.apply_search_now()
        assert table.model().rowCount() == 1
    finally:
        page.deleteLater()
