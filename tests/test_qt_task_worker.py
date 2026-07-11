import importlib
import os
import sys

import pytest


def test_task_worker_imports_without_qt_bindings():
    _assert_import_does_not_load_modules(
        ("dronautix_uploader.qt_app.task_worker",),
        forbidden_prefixes=("PySide6",),
    )
    module = importlib.import_module("dronautix_uploader.qt_app.task_worker")

    assert module is not None


def _assert_import_does_not_load_modules(module_names, *, forbidden_prefixes):
    before = _loaded_modules(forbidden_prefixes)
    for module_name in module_names:
        sys.modules.pop(module_name, None)
    for module_name in module_names:
        importlib.import_module(module_name)
    assert _loaded_modules(forbidden_prefixes) == before


def _loaded_modules(prefixes):
    return {name for name in sys.modules if any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes)}


def _qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_task_worker_rejects_non_callable_before_touching_qt():
    from dronautix_uploader.qt_app.task_worker import create_task_worker

    with pytest.raises(TypeError, match="callable"):
        create_task_worker(object(), None)


def test_task_worker_emits_result_and_finished_with_qt_when_available():
    QtCore = pytest.importorskip("PySide6.QtCore")
    from dronautix_uploader.qt_app.task_worker import create_task_worker

    app = _qt_app()
    assert app is not None

    results = []
    errors = []
    finished = []
    loop = QtCore.QEventLoop()
    bundle = create_task_worker(QtCore, lambda: "done")
    bundle.worker.result.connect(results.append)
    bundle.worker.error.connect(errors.append)
    bundle.worker.finished.connect(lambda: (finished.append(True), loop.quit()))
    bundle.thread.start()
    QtCore.QTimer.singleShot(3000, loop.quit)
    loop.exec()

    assert bundle.thread.wait(3000)
    assert results == ["done"]
    assert errors == []
    assert finished == [True]


def test_task_worker_emits_error_and_finished_with_qt_when_available():
    QtCore = pytest.importorskip("PySide6.QtCore")
    from dronautix_uploader.qt_app.task_worker import create_task_worker

    app = _qt_app()
    assert app is not None

    results = []
    errors = []
    finished = []
    loop = QtCore.QEventLoop()

    def failing_task():
        raise RuntimeError("worker failed")

    bundle = create_task_worker(QtCore, failing_task)
    bundle.worker.result.connect(results.append)
    bundle.worker.error.connect(errors.append)
    bundle.worker.finished.connect(lambda: (finished.append(True), loop.quit()))
    bundle.thread.start()
    QtCore.QTimer.singleShot(3000, loop.quit)
    loop.exec()

    assert bundle.thread.wait(3000)
    assert results == []
    assert len(errors) == 1
    assert str(errors[0]) == "worker failed"
    assert finished == [True]
