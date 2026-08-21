from dataclasses import dataclass
import importlib
import sys

import pytest

from dronautix_uploader.core.upload_workflow_service import NewProjectUploadWorkflowRequest
from dronautix_uploader.qt_app.project_management_actions import ProjectOperationSummary


@dataclass(frozen=True)
class OperationResult:
    status: str = "success"
    message: str = "Upload abgeschlossen."
    warnings: tuple[str, ...] = ()
    uploaded_keys: tuple[str, ...] = ()
    deleted_keys: tuple[str, ...] = ()


class FakeService:
    def __init__(self):
        self.calls = []
        self.result = OperationResult()

    def upload_new_project(
        self,
        request,
        on_progress=None,
        cancel_requested=None,
        confirm_spatial_warning=None,
    ):
        self.calls.append((request, on_progress))
        self.cancel_callbacks = getattr(self, "cancel_callbacks", [])
        self.cancel_callbacks.append(cancel_requested)
        self.spatial_warning_callbacks = getattr(self, "spatial_warning_callbacks", [])
        self.spatial_warning_callbacks.append(confirm_spatial_warning)
        return self.result


@pytest.fixture()
def dialog_models():
    return importlib.import_module("dronautix_uploader.qt_app.upload_dialog_models")


@pytest.fixture()
def controller_module():
    return importlib.import_module("dronautix_uploader.qt_app.upload_workflow_controller")


def test_upload_workflow_controller_imports_without_qt_or_tk_bindings(controller_module, dialog_models):
    assert controller_module is not None
    assert dialog_models is not None
    _assert_import_does_not_load_modules(
        (
            "dronautix_uploader.qt_app.upload_dialog_models",
            "dronautix_uploader.qt_app.upload_workflow_controller",
        ),
        forbidden_prefixes=("PySide6", "tkinter", "customtkinter"),
    )


def _assert_import_does_not_load_modules(module_names, *, forbidden_prefixes):
    before = _loaded_modules(forbidden_prefixes)
    for module_name in module_names:
        sys.modules.pop(module_name, None)
    for module_name in module_names:
        importlib.import_module(module_name)
    assert _loaded_modules(forbidden_prefixes) == before


def _loaded_modules(prefixes):
    return {name for name in sys.modules if any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes)}


def test_upload_new_project_accepts_dialog_state_and_routes_validated_request(
    controller_module,
    dialog_models,
):
    service = FakeService()
    controller = controller_module.UploadWorkflowController(service)
    state = dialog_models.UploadDialogState(
        customer="  Kunde  ",
        project="  Projekt  ",
        source_paths=(" scan.copc.laz ",),
        horizontal_crs="EPSG:25832",
    )

    summary = controller.upload_new_project(state)

    assert isinstance(summary, ProjectOperationSummary)
    assert service.calls == [
        (
            NewProjectUploadWorkflowRequest(
                source_paths=("scan.copc.laz",),
                kunde="Kunde",
                projekt="Projekt",
                crs_info_by_source_path={
                    "scan.copc.laz": {
                        "value": "EPSG:25832",
                        "projection": "EPSG:25832",
                    }
                },
            ),
            None,
        )
    ]
    assert summary.status == "success"
    assert summary.statusbar_text == "Upload abgeschlossen."


def test_upload_new_project_accepts_core_request_and_forwards_progress_callback(controller_module):
    service = FakeService()
    controller = controller_module.UploadWorkflowController(service)
    request = NewProjectUploadWorkflowRequest(
        source_paths=("scan.copc.laz",),
        kunde="Kunde",
        projekt="Projekt",
    )

    def on_progress(event):
        return event

    controller.upload_new_project(request, on_progress=on_progress)

    assert service.calls == [(request, on_progress)]


def test_upload_new_project_forwards_spatial_warning_confirmation(controller_module):
    service = FakeService()
    controller = controller_module.UploadWorkflowController(service)
    request = NewProjectUploadWorkflowRequest(source_paths=("scan.copc.laz",), kunde="Kunde", projekt="Projekt")

    def confirm(message):
        return bool(message)

    controller.upload_new_project(request, confirm_spatial_warning=confirm)

    assert service.spatial_warning_callbacks == [confirm]


@pytest.mark.parametrize(
    ("result", "expected_statusbar", "expected_last_line"),
    [
        (
            OperationResult(
                status="success",
                message="Projekt hochgeladen.",
                uploaded_keys=("pointclouds/kunde/project/cloud.js",),
            ),
            "Projekt hochgeladen. (hochgeladen: pointclouds/kunde/project/cloud.js)",
            "Hochgeladen: pointclouds/kunde/project/cloud.js",
        ),
        (
            OperationResult(
                status="partial",
                message="Projekt teilweise hochgeladen.",
                warnings=("Index konnte nicht gespeichert werden.",),
                uploaded_keys=("a", "b"),
            ),
            "Projekt teilweise hochgeladen. (hochgeladen: 2 Keys; Warnung: Index konnte nicht gespeichert werden.)",
            "Warnung: Index konnte nicht gespeichert werden.",
        ),
        (
            OperationResult(status="failed", message=""),
            "Aktion fehlgeschlagen.",
            "Fehlgeschlagen: Aktion fehlgeschlagen.",
        ),
    ],
)
def test_upload_new_project_summarizes_success_partial_and_failed_results(
    controller_module,
    result,
    expected_statusbar,
    expected_last_line,
):
    service = FakeService()
    service.result = result
    controller = controller_module.UploadWorkflowController(service)

    summary = controller.upload_new_project(
        NewProjectUploadWorkflowRequest(
            source_paths=("scan.copc.laz",),
            kunde="Kunde",
            projekt="Projekt",
        )
    )

    assert summary.status == result.status
    assert summary.statusbar_text == expected_statusbar
    assert summary.activity_lines[-1] == expected_last_line


@pytest.mark.parametrize(
    ("state_kwargs", "message"),
    [
        ({"customer": "", "project": "Projekt", "source_paths": ("scan.copc.laz",)}, "Kunde"),
        ({"customer": "Kunde", "project": "", "source_paths": ("scan.copc.laz",)}, "Projekt"),
        ({"customer": "Kunde", "project": "Projekt", "source_paths": ()}, "Quelle|Punktwolke"),
        (
            {"customer": "Kunde", "project": "Projekt", "source_paths": ("raw.laz",)},
            "Potree Converter|Ausgabeordner",
        ),
    ],
)
def test_upload_new_project_rejects_invalid_dialog_state_before_service_call(
    controller_module,
    dialog_models,
    state_kwargs,
    message,
):
    service = FakeService()
    controller = controller_module.UploadWorkflowController(service)
    state = dialog_models.UploadDialogState(**state_kwargs)

    with pytest.raises(ValueError, match=message):
        controller.upload_new_project(state)

    assert service.calls == []


def test_upload_new_project_rejects_unknown_payload_type(controller_module):
    controller = controller_module.UploadWorkflowController(FakeService())

    with pytest.raises(ValueError, match="UploadDialogState|NewProjectUploadWorkflowRequest"):
        controller.upload_new_project(object())
