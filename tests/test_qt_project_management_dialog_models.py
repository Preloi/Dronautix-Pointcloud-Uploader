import importlib
import sys

import pytest

from dronautix_uploader.qt_app.project_management import PointcloudPreview, ProjectPreview
from dronautix_uploader.qt_app.project_management_controller import (
    DownloadProjectInput,
    DuplicateProjectInput,
    RenameProjectInput,
    ReplaceAllPointcloudsInput,
    ReplaceSinglePointcloudInput,
)


@pytest.fixture()
def dialog_models():
    return importlib.import_module("dronautix_uploader.qt_app.project_management_dialog_models")


def test_dialog_models_import_without_qt_or_tk_bindings(dialog_models):
    assert dialog_models is not None
    assert "PySide6" not in sys.modules
    assert "tkinter" not in sys.modules
    assert "customtkinter" not in sys.modules


def test_build_rename_dialog_state_uses_project_and_single_pointcloud_name(dialog_models):
    project = _project(
        project="Bestand Nord",
        customer="Dronautix",
        pointclouds=(_pointcloud("Scan Bestand"),),
    )

    state = dialog_models.build_rename_dialog_state(project)

    assert isinstance(state, dialog_models.ProjectRenameDialogState)
    assert state.customer == "Dronautix"
    assert state.project == "Bestand Nord"
    assert state.pointcloud_names == ("Scan Bestand",)


def test_validate_rename_dialog_state_trims_customer_project_and_single_pointcloud(dialog_models):
    state = dialog_models.ProjectRenameDialogState(
        customer="  Kunde A  ",
        project="  Projekt A  ",
        pointcloud_names=("  Neuer Scan  ",),
    )

    payload = dialog_models.validate_rename_dialog_state(state)

    assert payload == RenameProjectInput(
        customer="Kunde A",
        project="Projekt A",
        pointcloud_names=("Neuer Scan",),
    )


def test_validate_rename_dialog_state_falls_back_to_existing_multi_pointcloud_names(dialog_models):
    project = _project(
        pointclouds=(
            _pointcloud("Bestand EG"),
            _pointcloud("Bestand OG"),
            _pointcloud("Dach"),
        )
    )
    state = dialog_models.build_rename_dialog_state(project)
    edited_state = dialog_models.ProjectRenameDialogState(
        customer=f"  {state.customer}  ",
        project=f"  {state.project}  ",
        pointcloud_names=("  Neuer Bestand EG  ", "   ", ""),
        fallback_pointcloud_names=state.fallback_pointcloud_names,
    )

    payload = dialog_models.validate_rename_dialog_state(edited_state)

    assert payload == RenameProjectInput(
        customer="Kunde",
        project="Projekt 1",
        pointcloud_names=("Neuer Bestand EG", "Bestand OG", "Dach"),
    )


def test_validate_rename_dialog_state_generates_numbered_fallback_for_missing_existing_name(dialog_models):
    state = dialog_models.ProjectRenameDialogState(
        customer="Kunde",
        project="Projekt",
        pointcloud_names=("Scan A", "", "   "),
    )

    payload = dialog_models.validate_rename_dialog_state(state)

    assert payload.pointcloud_names == ("Scan A", "Punktwolke 2", "Punktwolke 3")


@pytest.mark.parametrize(
    ("customer", "project", "message"),
    [
        ("", "Projekt", "Kunde"),
        ("   ", "Projekt", "Kunde"),
        ("Kunde", "", "Projekt"),
        ("Kunde", "   ", "Projekt"),
    ],
)
def test_validate_rename_dialog_state_rejects_empty_customer_or_project(
    dialog_models,
    customer,
    project,
    message,
):
    state = dialog_models.ProjectRenameDialogState(customer=customer, project=project)

    with pytest.raises(ValueError, match=message):
        dialog_models.validate_rename_dialog_state(state)


def test_build_duplicate_dialog_state_uses_existing_customer_and_copy_project_default(dialog_models):
    project = _project(project="Bestand Nord", customer="Dronautix")

    state = dialog_models.build_duplicate_dialog_state(project)

    assert isinstance(state, dialog_models.ProjectDuplicateDialogState)
    assert state.customer == "Dronautix"
    assert state.project == "Bestand Nord Kopie"


def test_validate_duplicate_dialog_state_trims_customer_and_project(dialog_models):
    state = dialog_models.ProjectDuplicateDialogState(customer="  Kunde B  ", project="  Kopie  ")

    payload = dialog_models.validate_duplicate_dialog_state(state)

    assert payload == DuplicateProjectInput(customer="Kunde B", project="Kopie")


@pytest.mark.parametrize(
    ("customer", "project", "message"),
    [
        ("", "Kopie", "Kunde"),
        ("Kunde", "", "Projekt"),
    ],
)
def test_validate_duplicate_dialog_state_rejects_empty_customer_or_project(
    dialog_models,
    customer,
    project,
    message,
):
    state = dialog_models.ProjectDuplicateDialogState(customer=customer, project=project)

    with pytest.raises(ValueError, match=message):
        dialog_models.validate_duplicate_dialog_state(state)


def test_build_delete_dialog_state_contains_project_identity_and_s3_warning(dialog_models):
    project = _project(project_id="abc-123", project="Bestand Nord", customer="Dronautix")

    state = dialog_models.build_delete_dialog_state(project)

    assert isinstance(state, dialog_models.ProjectDeleteDialogState)
    assert state.requires_confirmation is True
    assert "Dronautix" in state.project_label
    assert "Bestand Nord" in state.project_label
    assert "abc-123" in state.detail_text
    assert "S3" in state.detail_text
    assert "gelöscht" in state.detail_text or "loescht" in state.detail_text


def test_build_download_dialog_state_contains_project_identity_and_s3_path(dialog_models):
    project = _project(project_id="abc-123", project="Bestand Nord", customer="Dronautix")

    state = dialog_models.build_download_dialog_state(project)

    assert isinstance(state, dialog_models.ProjectDownloadDialogState)
    assert "Dronautix" in state.project_label
    assert "Bestand Nord" in state.project_label
    assert "abc-123" in state.detail_text
    assert project.s3_path in state.detail_text


def test_build_link_state_dialog_state_contains_project_identity_and_action(dialog_models):
    project = _project(project_id="abc-123", project="Bestand Nord", customer="Dronautix")

    disable_state = dialog_models.build_link_state_dialog_state(project, True)
    enable_state = dialog_models.build_link_state_dialog_state(project, False)

    assert isinstance(disable_state, dialog_models.ProjectLinkStateDialogState)
    assert "Dronautix" in disable_state.project_label
    assert "Bestand Nord" in disable_state.project_label
    assert "abc-123" in disable_state.detail_text
    assert "deaktiviert" in disable_state.detail_text
    assert disable_state.confirmation_label == "Link deaktivieren"
    assert enable_state.confirmation_label == "Link aktivieren"


def test_validate_download_dialog_state_trims_target_dir(dialog_models):
    state = dialog_models.ProjectDownloadDialogState(target_dir="  C:/Downloads  ")

    payload = dialog_models.validate_download_dialog_state(state)

    assert payload == DownloadProjectInput(target_dir="C:/Downloads")


def test_validate_download_dialog_state_rejects_empty_target_dir(dialog_models):
    with pytest.raises(ValueError, match="Zielordner"):
        dialog_models.validate_download_dialog_state(dialog_models.ProjectDownloadDialogState(target_dir="  "))


def test_validate_replace_all_dialog_state_accepts_multiple_trimmed_sources(dialog_models):
    state = dialog_models.ProjectReplaceDialogState(
        source_paths=("  scan.copc.laz  ", "", " potree-output "),
    )

    payload = dialog_models.validate_replace_all_dialog_state(state)

    assert payload == ReplaceAllPointcloudsInput(source_paths=("scan.copc.laz", "potree-output"))


def test_validate_replace_all_dialog_state_maps_crs_to_each_replacement_source(dialog_models):
    state = dialog_models.ProjectReplaceDialogState(
        source_paths=(" scan-a.copc.laz ", " scan-b.copc.laz "),
        horizontal_crs=" EPSG:25832 ",
        vertical_crs=" DHHN2016 ",
    )

    payload = dialog_models.validate_replace_all_dialog_state(state)

    assert payload.crs_info_by_source_path == {
        "scan-a.copc.laz": {
            "value": "EPSG:25832",
            "projection": "EPSG:25832",
            "vertical_crs": "DHHN2016",
            "vertical_epsg": "DHHN2016",
            "vertical_projection": "DHHN2016",
        },
        "scan-b.copc.laz": {
            "value": "EPSG:25832",
            "projection": "EPSG:25832",
            "vertical_crs": "DHHN2016",
            "vertical_epsg": "DHHN2016",
            "vertical_projection": "DHHN2016",
        },
    }


def test_validate_replace_single_dialog_state_requires_exactly_one_source(dialog_models):
    with pytest.raises(ValueError, match="genau eine"):
        dialog_models.validate_replace_single_dialog_state(dialog_models.ProjectReplaceDialogState(source_paths=()))

    with pytest.raises(ValueError, match="genau eine"):
        dialog_models.validate_replace_single_dialog_state(
            dialog_models.ProjectReplaceDialogState(source_paths=("a.copc.laz", "b.copc.laz"))
        )

    payload = dialog_models.validate_replace_single_dialog_state(
        dialog_models.ProjectReplaceDialogState(source_paths=(" a.copc.laz ",), horizontal_crs="EPSG:25832")
    )

    assert payload == ReplaceSinglePointcloudInput(
        source_path="a.copc.laz",
        crs_info={"value": "EPSG:25832", "projection": "EPSG:25832"},
    )


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (
            lambda models: models.ProjectReplaceDialogState(source_paths=("raw.laz",), output_base_dir="out"),
            "Potree Converter",
        ),
        (
            lambda models: models.ProjectReplaceDialogState(source_paths=("raw.las",), converter_path="converter.exe"),
            "Ausgabeordner",
        ),
    ],
)
def test_validate_replace_dialog_state_requires_converter_settings_for_raw_sources(dialog_models, state, message):
    with pytest.raises(ValueError, match=message):
        dialog_models.validate_replace_all_dialog_state(state(dialog_models))


def _project(
    project_id: str = "project-1",
    project: str = "Projekt 1",
    customer: str = "Kunde",
    pointclouds: tuple[PointcloudPreview, ...] | None = None,
) -> ProjectPreview:
    if pointclouds is None:
        pointclouds = (_pointcloud("Scan 1"),)
    return ProjectPreview(
        project_id=project_id,
        project=project,
        customer=customer,
        format="Multi",
        updated="Noch nicht geladen",
        link=f"viewer/projekte/{project_id}",
        disabled=False,
        pointclouds=pointclouds,
        s3_path=f"projects/{project_id}",
    )


def _pointcloud(name: str) -> PointcloudPreview:
    return PointcloudPreview(
        name=name,
        format="Potree",
        points="-",
        crs="EPSG:25832",
        s3_path=f"projects/project-1/{name.lower().replace(' ', '-')}",
    )
