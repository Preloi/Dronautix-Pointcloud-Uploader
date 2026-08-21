from dataclasses import dataclass
import sys

import pytest

from dronautix_uploader.qt_app.project_management import ModelPreview, PointcloudPreview, ProjectPreview
from dronautix_uploader.qt_app.project_management_actions import (
    ACTION_DELETE,
    ACTION_DISABLE_LINK,
    ACTION_DOWNLOAD,
    ACTION_ENABLE_LINK,
    ACTION_DUPLICATE,
    ACTION_RENAME,
    ACTION_REPLACE_ALL_POINTCLOUDS,
    ACTION_REPLACE_SINGLE_POINTCLOUD,
    ACTION_REPLACE_SINGLE_MODEL,
    ACTION_ADD_MODELS,
    ACTION_REMOVE_MODEL,
    ACTION_ADD_POINTCLOUDS,
    ACTION_REMOVE_POINTCLOUD,
    ProjectOperationSummary,
)
from dronautix_uploader.qt_app.project_management_controller import (
    DownloadProjectInput,
    DuplicateProjectInput,
    ProjectManagementController,
    RenameProjectInput,
    ReplaceAllPointcloudsInput,
    ReplaceSinglePointcloudInput,
    ReplaceSingleModelInput,
    RepairProjectCrsInput,
    AddModelsInput,
    AddPointcloudsInput,
)


@dataclass(frozen=True)
class OperationResult:
    status: str = "success"
    message: str = "Service call completed."
    warnings: tuple[str, ...] = ()
    uploaded_keys: tuple[str, ...] = ()
    deleted_keys: tuple[str, ...] = ()
    downloaded_files: tuple[str, ...] = ()
    download_dir: str = ""


class FakeService:
    def __init__(self):
        self.calls = []
        self.result = OperationResult()

    def rename_project(self, project_id, new_kunde, new_projekt, pointcloud_names=()):
        self.calls.append(("rename_project", project_id, new_kunde, new_projekt, tuple(pointcloud_names)))
        return self.result

    def duplicate_project(self, project_id, new_kunde, new_projekt, on_progress=None):
        self.calls.append(("duplicate_project", project_id, new_kunde, new_projekt))
        return self.result

    def delete_project(self, project_id):
        self.calls.append(("delete_project", project_id))
        return self.result

    def download_project(self, project_id, target_dir, on_progress=None, cancel_requested=None):
        self.calls.append(("download_project", project_id, target_dir, on_progress, cancel_requested))
        return self.result

    def set_project_link_state(self, project_id, disabled):
        self.calls.append(("set_project_link_state", project_id, disabled))
        return self.result

    def replace_project_pointclouds(self, project_id, prepared_clouds, on_progress=None):
        self.calls.append(("replace_project_pointclouds", project_id, tuple(prepared_clouds), on_progress))
        return self.result

    def replace_project_pointclouds_from_sources(
        self,
        project_id,
        source_paths,
        converter_path="",
        output_base_dir="",
        overwrite=False,
        on_progress=None,
        crs_info_by_source_path=None,
    ):
        self.calls.append(
            (
                "replace_project_pointclouds_from_sources",
                project_id,
                tuple(source_paths),
                converter_path,
                output_base_dir,
                overwrite,
                on_progress,
                crs_info_by_source_path,
            )
        )
        return self.result

    def replace_single_project_pointcloud(self, project_id, target_pointcloud_s3_path, prepared_cloud, on_progress=None):
        self.calls.append(
            ("replace_single_project_pointcloud", project_id, target_pointcloud_s3_path, prepared_cloud, on_progress)
        )
        return self.result

    def replace_single_project_pointcloud_from_source(
        self,
        project_id,
        target_pointcloud_s3_path,
        source_path,
        converter_path="",
        output_base_dir="",
        overwrite=False,
        on_progress=None,
        crs_info=None,
    ):
        self.calls.append(
            (
                "replace_single_project_pointcloud_from_source",
                project_id,
                target_pointcloud_s3_path,
                source_path,
                converter_path,
                output_base_dir,
                overwrite,
                on_progress,
                crs_info,
            )
        )
        return self.result

    def replace_single_project_model_from_source(
        self,
        project_id,
        target_model_s3_path,
        source_path,
        *,
        model_json_path="",
        on_progress=None,
        confirm_spatial_warning=None,
        confirm_crs_repair=None,
    ):
        self.calls.append(
            (
                "replace_single_project_model_from_source",
                project_id,
                target_model_s3_path,
                source_path,
                model_json_path,
                on_progress,
                confirm_spatial_warning,
                confirm_crs_repair,
            )
        )
        return self.result

    def add_project_models_from_sources(
        self,
        project_id,
        source_paths,
        *,
        model_json_by_source_path=None,
        on_progress=None,
        confirm_spatial_warning=None,
        confirm_crs_repair=None,
    ):
        self.calls.append(
            (
                "add_project_models_from_sources",
                project_id,
                tuple(source_paths),
                model_json_by_source_path,
                on_progress,
                confirm_spatial_warning,
                confirm_crs_repair,
            )
        )
        return self.result

    def repair_project_crs_metadata(self, project_id, crs_info, *, confirm_repair=None, allow_conflicting_overwrite=False):
        self.calls.append(
            ("repair_project_crs_metadata", project_id, crs_info, confirm_repair, allow_conflicting_overwrite)
        )
        return self.result

    def add_project_pointclouds(self, project_id, prepared_clouds, on_progress=None):
        self.calls.append(("add_project_pointclouds", project_id, tuple(prepared_clouds), on_progress))
        return self.result

    def add_project_pointclouds_from_sources(
        self,
        project_id,
        source_paths,
        converter_path="",
        output_base_dir="",
        overwrite=False,
        on_progress=None,
        crs_info_by_source_path=None,
    ):
        self.calls.append(
            (
                "add_project_pointclouds_from_sources",
                project_id,
                tuple(source_paths),
                converter_path,
                output_base_dir,
                overwrite,
                on_progress,
                crs_info_by_source_path,
            )
        )
        return self.result

    def remove_project_pointcloud(self, project_id, target_pointcloud_s3_path):
        self.calls.append(("remove_project_pointcloud", project_id, target_pointcloud_s3_path))
        return self.result

    def remove_project_model(self, project_id, target_model_s3_path):
        self.calls.append(("remove_project_model", project_id, target_model_s3_path))
        return self.result


def test_controller_imports_without_qt_bindings():
    assert "PySide6" not in sys.modules


def test_rename_project_routes_preview_id_and_request_to_service():
    service = FakeService()
    controller = ProjectManagementController(service)

    summary = controller.rename_project(
        _project(),
        RenameProjectInput(
            customer="Dronautix",
            project="Nordfluegel",
            pointcloud_names=("EG", "Dach"),
        ),
    )

    assert service.calls == [("rename_project", "project-1", "Dronautix", "Nordfluegel", ("EG", "Dach"))]
    assert isinstance(summary, ProjectOperationSummary)
    assert summary.statusbar_text == "Service call completed."


def test_duplicate_project_routes_preview_id_and_request_to_service():
    service = FakeService()
    controller = ProjectManagementController(service)

    controller.duplicate_project(
        _project(),
        DuplicateProjectInput(customer="Kunde B", project="Projektkopie"),
    )

    assert service.calls == [("duplicate_project", "project-1", "Kunde B", "Projektkopie")]


def test_delete_project_routes_preview_id_to_service():
    service = FakeService()
    controller = ProjectManagementController(service)

    controller.delete_project(_project())

    assert service.calls == [("delete_project", "project-1")]


def test_download_project_routes_preview_id_target_dir_and_progress_to_service():
    service = FakeService()
    controller = ProjectManagementController(service)

    def on_progress(event):
        return event

    summary = controller.download_project(
        _project(),
        DownloadProjectInput(target_dir="C:/Downloads"),
        on_progress=on_progress,
    )

    assert service.calls == [("download_project", "project-1", "C:/Downloads", on_progress, None)]
    assert isinstance(summary, ProjectOperationSummary)


def test_download_project_forwards_cancel_callback_to_service():
    service = FakeService()
    controller = ProjectManagementController(service)

    def cancel_requested():
        return True

    controller.download_project(
        _project(),
        DownloadProjectInput(target_dir="C:/Downloads"),
        cancel_requested=cancel_requested,
    )

    assert service.calls == [("download_project", "project-1", "C:/Downloads", None, cancel_requested)]


def test_link_state_actions_route_project_id_and_target_state_to_service():
    service = FakeService()
    controller = ProjectManagementController(service)

    controller.disable_project_link(_project())
    controller.enable_project_link(_project(disabled=True))

    assert service.calls == [
        ("set_project_link_state", "project-1", True),
        ("set_project_link_state", "project-1", False),
    ]


def test_replace_all_pointclouds_routes_prepared_clouds_to_service():
    service = FakeService()
    controller = ProjectManagementController(service)
    prepared_clouds = ("prepared-a", "prepared-b")

    controller.replace_all_pointclouds(_project(), ReplaceAllPointcloudsInput(prepared_clouds))

    assert service.calls == [("replace_project_pointclouds", "project-1", prepared_clouds, None)]


def test_replace_all_pointclouds_routes_source_payload_to_service():
    service = FakeService()
    controller = ProjectManagementController(service)

    controller.replace_all_pointclouds(
        _project(),
        ReplaceAllPointcloudsInput(
            source_paths=("scan.copc.laz", "raw.laz"),
            converter_path="PotreeConverter.exe",
            output_base_dir="out",
            overwrite=True,
            crs_info_by_source_path={"scan.copc.laz": {"value": "EPSG:25832"}},
        ),
    )

    assert service.calls == [
        (
            "replace_project_pointclouds_from_sources",
            "project-1",
            ("scan.copc.laz", "raw.laz"),
            "PotreeConverter.exe",
            "out",
            True,
            None,
            {"scan.copc.laz": {"value": "EPSG:25832"}},
        )
    ]


def test_replace_all_pointclouds_forwards_progress_callback_to_service():
    service = FakeService()
    controller = ProjectManagementController(service)

    def on_progress(event):
        return event

    controller.replace_all_pointclouds(
        _project(),
        ReplaceAllPointcloudsInput(source_paths=("raw.laz",), converter_path="PotreeConverter.exe", output_base_dir="out"),
        on_progress=on_progress,
    )

    assert service.calls[0][6] is on_progress


def test_replace_single_pointcloud_routes_project_id_target_s3_path_and_prepared_cloud():
    service = FakeService()
    controller = ProjectManagementController(service)
    pointcloud = _pointcloud("Fassade", s3_path="projects/project-1/fassade")

    controller.replace_single_pointcloud(
        _project(pointcloud),
        pointcloud,
        ReplaceSinglePointcloudInput("prepared-fassade"),
    )

    assert service.calls == [
        (
            "replace_single_project_pointcloud",
            "project-1",
            "projects/project-1/fassade",
            "prepared-fassade",
            None,
        )
    ]


def test_replace_single_pointcloud_routes_source_payload_to_service():
    service = FakeService()
    controller = ProjectManagementController(service)
    pointcloud = _pointcloud("Fassade", s3_path="projects/project-1/fassade")

    controller.replace_single_pointcloud(
        _project(pointcloud),
        pointcloud,
        ReplaceSinglePointcloudInput(
            source_path="new-fassade.copc.laz",
            converter_path="PotreeConverter.exe",
            output_base_dir="out",
            overwrite=True,
            crs_info={"value": "EPSG:25832"},
        ),
    )

    assert service.calls == [
        (
            "replace_single_project_pointcloud_from_source",
            "project-1",
            "projects/project-1/fassade",
            "new-fassade.copc.laz",
            "PotreeConverter.exe",
            "out",
            True,
            None,
            {"value": "EPSG:25832"},
        )
    ]


def test_replace_single_pointcloud_forwards_progress_callback_to_service():
    service = FakeService()
    controller = ProjectManagementController(service)
    pointcloud = _pointcloud("Fassade", s3_path="projects/project-1/fassade")

    def on_progress(event):
        return event

    controller.replace_single_pointcloud(
        _project(pointcloud),
        pointcloud,
        ReplaceSinglePointcloudInput(source_path="new-fassade.copc.laz"),
        on_progress=on_progress,
    )

    assert service.calls[0][7] is on_progress


def test_replace_single_model_routes_selected_package_and_optional_sidecar():
    service = FakeService()
    controller = ProjectManagementController(service)
    model = _model("Fassade")

    confirm = lambda message: bool(message)
    confirm_repair = lambda message: bool(message)
    controller.replace_single_model(
        _project(models=(model,)),
        model,
        ReplaceSingleModelInput("new-fassade.glb", "new-model.json"),
        confirm_spatial_warning=confirm,
        confirm_crs_repair=confirm_repair,
    )

    assert service.calls == [
        (
            "replace_single_project_model_from_source",
            "project-1",
            model.s3_path,
            "new-fassade.glb",
            "new-model.json",
            None,
            confirm,
            confirm_repair,
        )
    ]


def test_handle_action_rejects_pointcloud_as_glb_selection():
    controller = ProjectManagementController(FakeService())

    with pytest.raises(ValueError, match="GLB"):
        controller.handle_action(
            ACTION_REPLACE_SINGLE_MODEL,
            _project(),
            pointcloud_preview=_pointcloud("Scan"),
            payload=ReplaceSingleModelInput("replacement.glb"),
        )


def test_remove_model_routes_selected_package_to_service():
    service = FakeService()
    controller = ProjectManagementController(service)
    model = _model("Fassade")

    controller.handle_action(ACTION_REMOVE_MODEL, _project(models=(model,)), pointcloud_preview=model)

    assert service.calls == [("remove_project_model", "project-1", model.s3_path)]


def test_add_models_routes_multiple_glbs_and_sidecars_to_service():
    service = FakeService()
    controller = ProjectManagementController(service)

    confirm = lambda message: bool(message)
    confirm_repair = lambda message: bool(message)
    controller.handle_action(
        ACTION_ADD_MODELS,
        _project(),
        payload=AddModelsInput(
            source_paths=("fassade.glb", "dach.glb"),
            model_json_by_source_path={"dach.glb": "dach-model.json"},
        ),
        confirm_spatial_warning=confirm,
        confirm_crs_repair=confirm_repair,
    )

    assert service.calls == [
        (
            "add_project_models_from_sources",
            "project-1",
            ("fassade.glb", "dach.glb"),
            {"dach.glb": "dach-model.json"},
            None,
            confirm,
            confirm_repair,
        )
    ]


def test_repair_project_crs_routes_complete_manual_crs_and_confirmation():
    service = FakeService()
    controller = ProjectManagementController(service)
    confirm = lambda message: bool(message)

    controller.repair_project_crs_metadata(
        _project(),
        RepairProjectCrsInput("EPSG:31255", "EPSG:5778"),
        confirm_repair=confirm,
    )

    assert service.calls == [
        (
            "repair_project_crs_metadata",
            "project-1",
            {"value": "EPSG:31255", "projection": "EPSG:31255", "vertical_crs": "EPSG:5778"},
            confirm,
            False,
        )
    ]


def test_repair_project_crs_forwards_explicit_conflicting_overwrite():
    service = FakeService()
    controller = ProjectManagementController(service)

    controller.repair_project_crs_metadata(
        _project(),
        RepairProjectCrsInput("EPSG:31255", "EPSG:5778", allow_conflicting_overwrite=True),
    )

    assert service.calls[0][-1] is True


def test_add_pointclouds_routes_sources_and_progress_to_the_expected_service_method():
    service = FakeService()
    controller = ProjectManagementController(service)

    def on_progress(event):
        return event

    controller.add_pointclouds(
        _project(explicit=True),
        AddPointcloudsInput(
            source_paths=("scan.copc.laz",),
            converter_path="PotreeConverter.exe",
            output_base_dir="out",
            overwrite=True,
            crs_info_by_source_path={"scan.copc.laz": {"value": "EPSG:25832"}},
        ),
        on_progress=on_progress,
    )

    assert service.calls == [
        (
            "add_project_pointclouds_from_sources",
            "project-1",
            ("scan.copc.laz",),
            "PotreeConverter.exe",
            "out",
            True,
            on_progress,
            {"scan.copc.laz": {"value": "EPSG:25832"}},
        )
    ]


def test_add_and_remove_reject_legacy_projects_and_remove_routes_the_selected_s3_child():
    service = FakeService()
    controller = ProjectManagementController(service)
    first = _pointcloud("Scan A", "projects/project-1/a")
    second = _pointcloud("Scan B", "projects/project-1/b")
    explicit_project = _project(first, second, explicit=True)

    controller.remove_pointcloud(explicit_project, second)
    assert service.calls == [("remove_project_pointcloud", "project-1", "projects/project-1/b")]

    with pytest.raises(ValueError, match="pointclouds"):
        controller.add_pointclouds(_project(first), AddPointcloudsInput(prepared_clouds=("prepared",)))
    with pytest.raises(ValueError, match="letzte Punktwolke"):
        controller.remove_pointcloud(_project(first, explicit=True), first)


def test_handle_action_routes_add_and_remove_actions():
    service = FakeService()
    controller = ProjectManagementController(service)
    first = _pointcloud("Scan A", "projects/project-1/a")
    second = _pointcloud("Scan B", "projects/project-1/b")
    project = _project(first, second, explicit=True)

    controller.handle_action(ACTION_ADD_POINTCLOUDS, project, payload=AddPointcloudsInput(prepared_clouds=("new",)))
    controller.handle_action(ACTION_REMOVE_POINTCLOUD, project, pointcloud_preview=first)

    assert service.calls == [
        ("add_project_pointclouds", "project-1", ("new",), None),
        ("remove_project_pointcloud", "project-1", "projects/project-1/a"),
    ]


def test_missing_project_selection_raises_value_error_with_context():
    controller = ProjectManagementController(FakeService())

    with pytest.raises(ValueError, match="Projekt"):
        controller.delete_project(None)


def test_handle_action_requires_payload_for_request_based_actions():
    controller = ProjectManagementController(FakeService())

    with pytest.raises(ValueError, match="Payload|payload|Umbenennen|rename"):
        controller.handle_action(ACTION_RENAME, _project())

    with pytest.raises(ValueError, match="Payload|payload|Duplizieren|duplicate"):
        controller.handle_action(ACTION_DUPLICATE, _project())

    with pytest.raises(ValueError, match="ReplaceAllPointcloudsInput|Austausch"):
        controller.handle_action(ACTION_REPLACE_ALL_POINTCLOUDS, _project())

    with pytest.raises(ValueError, match="DownloadProjectInput|Download"):
        controller.handle_action(ACTION_DOWNLOAD, _project())


def test_handle_action_requires_pointcloud_and_payload_for_single_replace():
    controller = ProjectManagementController(FakeService())

    with pytest.raises(ValueError, match="Punktwolke|pointcloud"):
        controller.handle_action(
            ACTION_REPLACE_SINGLE_POINTCLOUD,
            _project(),
            payload=ReplaceSinglePointcloudInput("prepared"),
        )

    with pytest.raises(ValueError, match="ReplaceSinglePointcloudInput|Austausch"):
        controller.handle_action(
            ACTION_REPLACE_SINGLE_POINTCLOUD,
            _project(),
            pointcloud_preview=_pointcloud("Scan"),
        )


def test_handle_action_routes_action_ids_and_summarizes_service_result():
    service = FakeService()
    service.result = OperationResult(
        status="partial",
        message="Punktwolke ersetzt.",
        warnings=("Cleanup offen.",),
        uploaded_keys=("new/cloud.js",),
        deleted_keys=("old/cloud.js",),
    )
    controller = ProjectManagementController(service)
    pointcloud = _pointcloud("Scan", s3_path="projects/project-1/scan")

    summary = controller.handle_action(
        ACTION_REPLACE_SINGLE_POINTCLOUD,
        _project(pointcloud),
        pointcloud_preview=pointcloud,
        payload=ReplaceSinglePointcloudInput("prepared-scan"),
    )

    assert service.calls == [
        (
            "replace_single_project_pointcloud",
            "project-1",
            "projects/project-1/scan",
            "prepared-scan",
            None,
        )
    ]
    assert summary.status == "partial"
    assert summary.statusbar_text == (
        "Punktwolke ersetzt. (hochgeladen: new/cloud.js; gelöscht: old/cloud.js; Warnung: Cleanup offen.)"
    )
    assert summary.activity_lines[-1] == "Warnung: Cleanup offen."


def test_handle_action_routes_delete_without_payload():
    service = FakeService()
    controller = ProjectManagementController(service)

    summary = controller.handle_action(ACTION_DELETE, _project())

    assert service.calls == [("delete_project", "project-1")]
    assert summary.status == "success"


def test_handle_action_routes_link_state_without_payload():
    service = FakeService()
    controller = ProjectManagementController(service)

    controller.handle_action(ACTION_DISABLE_LINK, _project())
    controller.handle_action(ACTION_ENABLE_LINK, _project(disabled=True))

    assert service.calls == [
        ("set_project_link_state", "project-1", True),
        ("set_project_link_state", "project-1", False),
    ]


def test_handle_action_routes_download_and_summarizes_download_result():
    service = FakeService()
    service.result = OperationResult(
        status="success",
        message="Projekt wurde heruntergeladen.",
        downloaded_files=("C:/Downloads/cloud.js", "C:/Downloads/metadata.json"),
        download_dir="C:/Downloads/projekt",
    )
    controller = ProjectManagementController(service)

    summary = controller.handle_action(
        ACTION_DOWNLOAD,
        _project(),
        payload=DownloadProjectInput(target_dir="C:/Downloads"),
    )

    assert service.calls == [("download_project", "project-1", "C:/Downloads", None, None)]
    assert summary.statusbar_text == (
        "Projekt wurde heruntergeladen. (heruntergeladen: 2 Dateien; Ziel: C:/Downloads/projekt)"
    )
    assert summary.activity_lines[-1] == "Ziel: C:/Downloads/projekt"


def test_handle_action_forwards_download_cancel_callback():
    service = FakeService()
    controller = ProjectManagementController(service)

    def cancel_requested():
        return True

    controller.handle_action(
        ACTION_DOWNLOAD,
        _project(),
        payload=DownloadProjectInput(target_dir="C:/Downloads"),
        cancel_requested=cancel_requested,
    )

    assert service.calls == [("download_project", "project-1", "C:/Downloads", None, cancel_requested)]


def _project(
    *pointclouds: PointcloudPreview,
    disabled: bool = False,
    explicit: bool = False,
    models: tuple[ModelPreview, ...] = (),
) -> ProjectPreview:
    return ProjectPreview(
        project_id="project-1",
        project="Projekt 1",
        customer="Kunde",
        format="Multi",
        updated="Noch nicht geladen",
        link="viewer/projekte/project-1",
        disabled=disabled,
        pointclouds=pointclouds,
        models=models,
        s3_path="projects/project-1",
        has_explicit_pointclouds=explicit,
    )


def _pointcloud(name: str, s3_path: str = "projects/project-1/scan") -> PointcloudPreview:
    return PointcloudPreview(
        name=name,
        format="Potree",
        points="-",
        crs="EPSG:25832",
        s3_path=s3_path,
    )


def _model(name: str) -> ModelPreview:
    return ModelPreview(
        model_id="fassade",
        name=name,
        s3_path="pointclouds/kunde/project-1/projekt/models/fassade/versions/old",
        viewer_path="kunde/project-1/projekt/models/fassade/versions/old/model.json",
        crs="EPSG:25833",
        vertical_crs="EPSG:7837",
    )
