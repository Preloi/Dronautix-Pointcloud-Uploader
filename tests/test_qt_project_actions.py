"""Flow tests for every project action through the real main window.

Each action: input dialog -> worker -> controller call -> cleanup (progress
dialog closed, progress bridge released, temp folder removed), plus the
cancel and error paths of the input dialog.
"""

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

# action_id, dialog function (None = no input), controller method, needs a pointcloud, uses a temp folder
ACTIONS = [
    ("rename", "prompt_rename_project", "rename_project", False, False),
    ("duplicate", "prompt_duplicate_project", "duplicate_project", False, False),
    ("delete", "confirm_delete_project", "delete_project", False, False),
    ("download", "prompt_download_project", "download_project", False, False),
    ("disable_link", None, "disable_project_link", False, False),
    ("enable_link", None, "enable_project_link", False, False),
    ("replace_all_pointclouds", "prompt_replace_all_pointclouds", "replace_all_pointclouds", False, True),
    ("add_pointclouds", "prompt_add_project_pointclouds", "add_pointclouds", False, True),
    ("add_models", "prompt_add_project_models", "add_models", False, False),
    ("repair_crs_metadata", "prompt_repair_project_crs", "repair_project_crs_metadata", False, False),
    ("replace_single_pointcloud", "prompt_replace_single_pointcloud", "replace_single_pointcloud", True, True),
    ("replace_single_model", "prompt_replace_single_model", "replace_single_model", True, False),
    ("remove_pointcloud", "confirm_remove_pointcloud", "remove_pointcloud", True, False),
    ("remove_model", "confirm_remove_model", "remove_model", True, False),
]
WITH_INPUT = [action for action in ACTIONS if action[1] is not None]


def _import_qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtGui = pytest.importorskip("PySide6.QtGui")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtCore, QtGui, QtWidgets


def _process_until(app, predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        app.processEvents()
        time.sleep(0.01)
    return predicate()


class RecordingController:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        from dronautix_uploader.qt_app.project_management_actions import ProjectOperationSummary

        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return ProjectOperationSummary(status="success", message="ok")

        return record


@pytest.fixture
def action_window(monkeypatch, tmp_path):
    QtCore, QtGui, QtWidgets = _import_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from dronautix_uploader.qt_app import main_window
    from dronautix_uploader.qt_app.project_management import make_project_preview

    monkeypatch.setattr(main_window, "cleanup_stale_upload_temp_dirs", lambda: ())
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(main_window.tempfile, "tempdir", str(tmp_path))  # temp folders land in tmp_path
    controller = RecordingController()
    window = main_window.create_main_window(QtCore, QtGui, QtWidgets, project_controller=controller)
    project = make_project_preview(
        {"id": "p1", "projekt": "P", "kunde": "K", "s3_path": "pointclouds/k/p1/p", "viewer_path": "k/p1/p"}, False
    )
    pointcloud = SimpleNamespace(name="Wolke", s3_path="pointclouds/k/p1/c1", viewer_path="k/p1/c1")
    # The projects page loads its list in the background right after start.
    assert _process_until(app, lambda: not window._has_active_background_tasks())
    yield SimpleNamespace(
        app=app, QtWidgets=QtWidgets, window=window, controller=controller, project=project, pointcloud=pointcloud,
        tmp_path=tmp_path,
    )
    window._close_action_progress_dialog(None)
    window.deleteLater()


def _payload(tmp_path):
    # CRS info is already set, so the CRS helpers pass the payload through unchanged.
    return SimpleNamespace(
        target_dir=str(tmp_path / "download"),
        source_paths=(str(tmp_path / "neu.laz"),),
        source_path=str(tmp_path / "neu.laz"),
        crs_info={"value": "EPSG:31256"},
        crs_info_by_source_path={str(tmp_path / "neu.laz"): {"value": "EPSG:31256"}},
    )


def _install_dialog(monkeypatch, dialog_name, behaviour, seen_temp_dirs):
    from dronautix_uploader.qt_app import project_management_dialogs

    def dialog(*args, **kwargs):
        seen_temp_dirs.extend(arg for arg in args if isinstance(arg, str) and Path(arg).is_dir())
        return behaviour()

    monkeypatch.setattr(project_management_dialogs, dialog_name, dialog)


def _visible_progress_dialogs(env):
    return [dialog for dialog in env.window.findChildren(env.QtWidgets.QProgressDialog) if dialog.isVisible()]


def _run(env, action_id, needs_pointcloud):
    env.window._handle_project_action(action_id, env.project, env.pointcloud if needs_pointcloud else None)


@pytest.mark.parametrize(("action_id", "dialog_name", "method", "needs_pointcloud", "uses_temp"), ACTIONS)
def test_project_action_runs_the_controller_in_the_worker_and_cleans_up(
    action_window, monkeypatch, action_id, dialog_name, method, needs_pointcloud, uses_temp
):
    env = action_window
    seen_temp_dirs = []
    payload = _payload(env.tmp_path)
    if dialog_name is not None:
        answer = True if dialog_name.startswith("confirm_") else payload
        _install_dialog(monkeypatch, dialog_name, lambda: answer, seen_temp_dirs)

    _run(env, action_id, needs_pointcloud)

    assert _process_until(env.app, lambda: bool(env.controller.calls) and not env.window._has_active_background_tasks())
    [(called, args, _kwargs)] = env.controller.calls
    assert called == method
    assert args[0] is env.project
    if needs_pointcloud:
        assert args[1] is env.pointcloud
    assert _process_until(env.app, lambda: not _visible_progress_dialogs(env))
    assert env.window._progress_bridges == {}
    if uses_temp:
        assert len(seen_temp_dirs) == 1 and not Path(seen_temp_dirs[0]).exists()


@pytest.mark.parametrize(("action_id", "dialog_name", "method", "needs_pointcloud", "uses_temp"), WITH_INPUT)
def test_cancelling_the_input_dialog_starts_nothing_and_removes_the_temp_folder(
    action_window, monkeypatch, action_id, dialog_name, method, needs_pointcloud, uses_temp
):
    env = action_window
    seen_temp_dirs = []
    cancelled = False if dialog_name.startswith("confirm_") else None
    _install_dialog(monkeypatch, dialog_name, lambda: cancelled, seen_temp_dirs)

    _run(env, action_id, needs_pointcloud)
    env.app.processEvents()

    assert env.controller.calls == []
    assert env.window._active_tasks == []
    assert _visible_progress_dialogs(env) == []
    if uses_temp:
        assert len(seen_temp_dirs) == 1 and not Path(seen_temp_dirs[0]).exists()


@pytest.mark.parametrize(("action_id", "dialog_name", "method", "needs_pointcloud", "uses_temp"), WITH_INPUT)
def test_a_failing_input_dialog_is_reported_and_leaves_nothing_behind(
    action_window, monkeypatch, action_id, dialog_name, method, needs_pointcloud, uses_temp
):
    env = action_window
    seen_temp_dirs = []

    def fail():
        raise RuntimeError("Dialog kaputt")

    _install_dialog(monkeypatch, dialog_name, fail, seen_temp_dirs)

    _run(env, action_id, needs_pointcloud)
    env.app.processEvents()

    assert env.window.statusBar().currentMessage() == "Dialog kaputt"
    assert env.controller.calls == []
    assert env.window._active_tasks == []
    if uses_temp:
        assert len(seen_temp_dirs) == 1 and not Path(seen_temp_dirs[0]).exists()


def test_actions_that_need_a_pointcloud_do_nothing_without_one(action_window):
    env = action_window
    for action_id, _dialog, _method, needs_pointcloud, _temp in ACTIONS:
        if needs_pointcloud:
            _run(env, action_id, needs_pointcloud=False)
    env.app.processEvents()

    assert env.controller.calls == []
    assert env.window._active_tasks == []
