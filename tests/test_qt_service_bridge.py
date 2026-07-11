import importlib
import sys

import pytest

from dronautix_uploader.core.contracts import ProgressEvent


def test_qt_service_bridge_imports_without_pyside6():
    module = importlib.import_module("dronautix_uploader.qt_app.service_bridge")

    assert module.QtServiceBridge


def test_qt_service_bridge_dispatches_to_recorder_and_sinks():
    from dronautix_uploader.qt_app.service_bridge import QtServiceBridge

    seen = []
    bridge = QtServiceBridge()
    bridge.connect_progress_sink(seen.append)

    event = ProgressEvent(kind="log", message="uploaded")
    bridge.progress_callback(event)

    assert bridge.events == (event,)
    assert seen == [event]


def test_qt_service_bridge_callback_is_compatible_with_core_progress_events():
    from dronautix_uploader.core.s3_service import upload_files_to_s3
    from dronautix_uploader.qt_app.service_bridge import QtServiceBridge

    class FakeS3Client:
        def upload_file(self, local_path, bucket, key, ExtraArgs=None, Callback=None):
            if Callback:
                Callback(4)

    bridge = QtServiceBridge()
    files = [(__file__, "prefix/test_qt_service_bridge.py")]

    upload_files_to_s3(
        FakeS3Client(),
        files,
        bucket_name="bucket",
        on_progress=bridge.progress_callback,
    )

    assert bridge.events[0].kind == "log"
    assert bridge.events[-1].message == "[UPLOAD] Alle Dateien hochgeladen"
    assert any(event.kind == "progress" and event.percent == 1.0 for event in bridge.events)


def test_create_qt_progress_emitter_reports_missing_pyside6(monkeypatch):
    from dronautix_uploader.qt_app import service_bridge

    original_import = __import__

    def guarded_import(name, *args, **kwargs):
        if name == "PySide6":
            raise ImportError("blocked for test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)
    sys.modules.pop("PySide6", None)

    with pytest.raises(RuntimeError, match="PySide6 is required"):
        service_bridge.create_qt_progress_emitter()
