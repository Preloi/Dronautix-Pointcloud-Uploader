import os
import sys

import pytest

from dronautix_uploader.core.local_conversion_service import LocalConversionRequest
from dronautix_uploader.qt_app.local_conversion_controller import LocalConversionController
from dronautix_uploader.qt_app.local_conversion_dialog_models import LocalConversionDialogState
from dronautix_uploader.qt_app.project_management_actions import ProjectOperationSummary


def test_local_conversion_controller_imports_without_qt_or_tk_bindings():
    assert "PySide6" not in sys.modules
    assert "tkinter" not in sys.modules
    assert "customtkinter" not in sys.modules


def test_local_conversion_controller_accepts_core_request_and_forwards_progress(tmp_path):
    source = tmp_path / "scan.laz"
    converter = tmp_path / "PotreeConverter.exe"
    output = tmp_path / "scan_potree"
    source.write_bytes(b"laz")
    converter.write_bytes(b"exe")
    calls = []
    events = []

    def fake_runner(source_file, converter_path, output_dir, on_progress):
        calls.append((source_file, converter_path, output_dir, on_progress))
        os.makedirs(output_dir, exist_ok=True)
        if on_progress:
            on_progress(type("Event", (), {"kind": "log", "message": "runner progress"})())

    controller = LocalConversionController(converter_runner=fake_runner)
    summary = controller.run_conversion(
        LocalConversionRequest(str(source), str(output), str(converter)),
        on_progress=events.append,
    )

    assert isinstance(summary, ProjectOperationSummary)
    assert summary.status == "success"
    assert "Lokale Konvertierung abgeschlossen." in summary.statusbar_text
    assert str(output) in summary.statusbar_text
    assert calls == [(str(source), str(converter), str(output), events.append)]
    assert any(getattr(event, "message", "") == "runner progress" for event in events)


def test_local_conversion_controller_accepts_dialog_state(tmp_path):
    source = tmp_path / "scan.las"
    converter = tmp_path / "PotreeConverter.exe"
    output = tmp_path / "scan_potree"
    source.write_bytes(b"las")
    converter.write_bytes(b"exe")

    def fake_runner(source_file, converter_path, output_dir, on_progress):
        os.makedirs(output_dir, exist_ok=True)

    controller = LocalConversionController(converter_runner=fake_runner)
    summary = controller.run_conversion(
        LocalConversionDialogState(
            source_file=str(source),
            output_dir=str(output),
            converter_path=str(converter),
        )
    )

    assert summary.status == "success"
    assert output.is_dir()


def test_local_conversion_controller_rejects_unknown_payload_type():
    controller = LocalConversionController()

    with pytest.raises(ValueError, match="LocalConversionDialogState|LocalConversionRequest"):
        controller.run_conversion(object())
