import importlib
import sys

import pytest

from dronautix_uploader.core.upload_workflow_service import NewProjectUploadWorkflowRequest


@pytest.fixture()
def dialog_models():
    return importlib.import_module("dronautix_uploader.qt_app.upload_dialog_models")


def test_upload_dialog_models_import_without_qt_or_tk_bindings(dialog_models):
    assert dialog_models is not None
    _assert_import_does_not_load_modules(
        ("dronautix_uploader.qt_app.upload_dialog_models",),
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


def test_validate_upload_dialog_state_trims_customer_project_sources_and_options(dialog_models):
    state = dialog_models.UploadDialogState(
        customer="  Kunde A  ",
        project="  Projekt Nord  ",
        source_paths=("  scan.copc.laz  ", "", "  potree-output  "),
        converter_path="  C:/Tools/PotreeConverter.exe  ",
        output_base_dir="  C:/tmp/converted  ",
        overwrite=True,
    )

    request = dialog_models.validate_upload_dialog_state(state)

    assert request == NewProjectUploadWorkflowRequest(
        source_paths=("scan.copc.laz", "potree-output"),
        kunde="Kunde A",
        projekt="Projekt Nord",
        converter_path="C:/Tools/PotreeConverter.exe",
        output_base_dir="C:/tmp/converted",
        overwrite=True,
    )


@pytest.mark.parametrize(
    ("customer", "project", "sources", "message"),
    [
        ("", "Projekt", ("scan.copc.laz",), "Kunde"),
        ("   ", "Projekt", ("scan.copc.laz",), "Kunde"),
        ("Kunde", "", ("scan.copc.laz",), "Projekt"),
        ("Kunde", "   ", ("scan.copc.laz",), "Projekt"),
        ("Kunde", "Projekt", (), "Quelle|Punktwolke"),
        ("Kunde", "Projekt", ("", "  "), "Quelle|Punktwolke"),
    ],
)
def test_validate_upload_dialog_state_rejects_required_empty_fields(
    dialog_models,
    customer,
    project,
    sources,
    message,
):
    state = dialog_models.UploadDialogState(customer=customer, project=project, source_paths=sources)

    with pytest.raises(ValueError, match=message):
        dialog_models.validate_upload_dialog_state(state)


@pytest.mark.parametrize(
    ("source", "converter_path", "output_base_dir", "message"),
    [
        ("scan.las", "", "out", "Potree Converter"),
        ("scan.laz", "PotreeConverter.exe", "", "Ausgabeordner"),
        ("SCAN.LAS", "", "out", "Potree Converter"),
    ],
)
def test_validate_upload_dialog_state_requires_converter_settings_for_raw_las_laz_sources(
    dialog_models,
    source,
    converter_path,
    output_base_dir,
    message,
):
    state = dialog_models.UploadDialogState(
        customer="Kunde",
        project="Projekt",
        source_paths=(source,),
        converter_path=converter_path,
        output_base_dir=output_base_dir,
    )

    with pytest.raises(ValueError, match=message):
        dialog_models.validate_upload_dialog_state(state)


def test_validate_upload_dialog_state_treats_copc_laz_as_direct_upload(dialog_models):
    state = dialog_models.UploadDialogState(
        customer="Kunde",
        project="Projekt",
        source_paths=("scan.copc.laz",),
    )

    request = dialog_models.validate_upload_dialog_state(state)

    assert request.source_paths == ("scan.copc.laz",)
    assert request.converter_path == ""
    assert request.output_base_dir == ""


def test_validate_upload_dialog_state_builds_crs_info_for_every_source(dialog_models):
    state = dialog_models.UploadDialogState(
        customer="Kunde",
        project="Projekt",
        source_paths=("  scan.copc.laz  ", " potree-output "),
        horizontal_crs="  EPSG:25832  ",
        vertical_crs="  EPSG:7837  ",
    )

    request = dialog_models.validate_upload_dialog_state(state)

    assert request.crs_info_by_source_path == {
        "scan.copc.laz": {
            "value": "EPSG:25832",
            "projection": "EPSG:25832",
            "vertical_crs": "EPSG:7837",
            "vertical_epsg": "EPSG:7837",
            "vertical_projection": "EPSG:7837",
        },
        "potree-output": {
            "value": "EPSG:25832",
            "projection": "EPSG:25832",
            "vertical_crs": "EPSG:7837",
            "vertical_epsg": "EPSG:7837",
            "vertical_projection": "EPSG:7837",
        },
    }


def test_validate_upload_dialog_state_omits_empty_crs_info(dialog_models):
    state = dialog_models.UploadDialogState(
        customer="Kunde",
        project="Projekt",
        source_paths=("scan.copc.laz",),
        horizontal_crs="  ",
        vertical_crs="",
    )

    request = dialog_models.validate_upload_dialog_state(state)

    assert request.crs_info_by_source_path is None
