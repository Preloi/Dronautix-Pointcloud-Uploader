"""Dataclass-based service API facade for UI and legacy adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import (
    CancelCallback,
    DownloadRequest,
    MultiReplacementRequest,
    PointcloudAddRequest,
    PointcloudRemoveRequest,
    PointcloudSource,
    ProjectDeleteRequest,
    ProjectLinkStateUpdate,
    ProgressCallback,
    ProjectMetadataUpdate,
    ProjectOperationResult,
    ReplacementRequest,
    UploadRequest,
)
from .project_management_service import ProjectManagementService
from .upload_workflow_service import NewProjectUploadWorkflowRequest, UploadWorkflowService


@dataclass(frozen=True)
class CoreServiceApi:
    """Route the public dataclass contracts to the service implementations."""

    upload_service: UploadWorkflowService
    project_service: ProjectManagementService

    def upload_project(
        self,
        request: UploadRequest,
        *,
        on_progress: ProgressCallback | None = None,
        converter_runner=None,
    ):
        return self.upload_service.upload_new_project(
            build_upload_workflow_request(request),
            on_progress=on_progress,
            converter_runner=converter_runner,
        )

    def rename_project_metadata(self, request: ProjectMetadataUpdate):
        self.project_service.rename_project(
            request.project_id,
            request.kunde,
            request.projekt,
            request.pointcloud_names,
        )
        return ProjectOperationResult(
            status="success",
            project_id=request.project_id,
            message="Projektmetadaten wurden aktualisiert.",
        )

    def duplicate_project(self, project_id: str, new_kunde: str, new_projekt: str):
        return self.project_service.duplicate_project(
            str(project_id or "").strip(),
            str(new_kunde or "").strip(),
            str(new_projekt or "").strip(),
        )

    def delete_project(self, request: ProjectDeleteRequest):
        return self.project_service.delete_project(str(request.project_id or "").strip())

    def set_project_link_state(self, request: ProjectLinkStateUpdate):
        return self.project_service.set_project_link_state(
            str(request.project_id or "").strip(),
            bool(request.disabled),
        )

    def download_project(
        self,
        request: DownloadRequest,
        *,
        on_progress: ProgressCallback | None = None,
        cancel_requested: CancelCallback | None = None,
    ):
        return self.project_service.download_project(
            _project_id_from_contract_project(request.project),
            request.target_dir,
            on_progress=on_progress,
            cancel_requested=cancel_requested,
        )

    def replace_pointcloud(
        self,
        request: ReplacementRequest,
        *,
        on_progress: ProgressCallback | None = None,
        converter_runner=None,
    ):
        return self.project_service.replace_single_project_pointcloud_from_source(
            _project_id_from_contract_project(request.project),
            _target_pointcloud_s3_path(request.project, request.target_pointcloud),
            request.replacement.source_path,
            converter_path=request.converter_path,
            output_base_dir=request.output_base_dir,
            on_progress=on_progress,
            converter_runner=converter_runner,
            crs_info=_source_crs_info(request.replacement, request.crs_input, request.vertical_input),
            overwrite=request.overwrite,
        )

    def replace_pointclouds(
        self,
        request: MultiReplacementRequest,
        *,
        on_progress: ProgressCallback | None = None,
        converter_runner=None,
    ):
        return self.project_service.replace_project_pointclouds_from_sources(
            _project_id_from_contract_project(request.project),
            tuple(source.source_path for source in request.replacements),
            converter_path=request.converter_path,
            output_base_dir=request.output_base_dir,
            on_progress=on_progress,
            converter_runner=converter_runner,
            crs_info_by_source_path=_crs_info_by_source_path(
                request.replacements,
                request.crs_input,
                request.vertical_input,
            ),
            source_overrides=request.replacements,
            overwrite=request.overwrite,
        )

    def add_pointclouds(
        self,
        request: PointcloudAddRequest,
        *,
        on_progress: ProgressCallback | None = None,
        converter_runner=None,
    ):
        return self.project_service.add_project_pointclouds_from_sources(
            _project_id_from_contract_project(request.project),
            tuple(source.source_path for source in request.additions),
            converter_path=request.converter_path,
            output_base_dir=request.output_base_dir,
            on_progress=on_progress,
            converter_runner=converter_runner,
            crs_info_by_source_path=_crs_info_by_source_path(
                request.additions,
                request.crs_input,
                request.vertical_input,
            ),
            source_overrides=request.additions,
            overwrite=request.overwrite,
        )

    def remove_pointcloud(self, request: PointcloudRemoveRequest):
        return self.project_service.remove_project_pointcloud(
            _project_id_from_contract_project(request.project),
            _target_pointcloud_s3_path(request.project, request.target_pointcloud),
        )


def build_upload_workflow_request(request: UploadRequest) -> NewProjectUploadWorkflowRequest:
    return NewProjectUploadWorkflowRequest(
        source_paths=tuple(source.source_path for source in request.sources),
        kunde=request.kunde,
        projekt=request.projekt,
        converter_path=request.converter_path,
        output_base_dir=request.output_base_dir,
        crs_info_by_source_path=_crs_info_by_source_path(
            request.sources,
            request.crs_input,
            request.vertical_input,
        ),
        overwrite=request.overwrite,
        model_inputs=request.model_inputs,
    )


def _project_id_from_contract_project(project: dict[str, Any]) -> str:
    for key in ("id", "project_id"):
        project_id = str(project.get(key, "") or "").strip()
        if project_id:
            return project_id
    raise ValueError("Projekt-ID fehlt im Service-API-Request.")


def _target_pointcloud_s3_path(
    project: dict[str, Any],
    target_pointcloud: dict[str, Any] | None,
) -> str:
    if target_pointcloud is not None:
        target_path = str(target_pointcloud.get("s3_path", "") or "").strip()
        if target_path:
            return target_path
        raise ValueError("Ziel-Punktwolke hat keinen S3-Pfad.")

    pointclouds = project.get("pointclouds")
    if isinstance(pointclouds, list):
        cloud_paths = [
            str(pointcloud.get("s3_path", "") or "").strip()
            for pointcloud in pointclouds
            if isinstance(pointcloud, dict) and str(pointcloud.get("s3_path", "") or "").strip()
        ]
        if len(cloud_paths) == 1:
            return cloud_paths[0]
        raise ValueError("Eine konkrete Ziel-Punktwolke ist fuer Multi-Cloud-Replacement erforderlich.")

    project_path = str(project.get("s3_path", "") or "").strip()
    if project_path:
        return project_path
    raise ValueError("Projekt hat keinen S3-Pfad fuer den Punktwolkenaustausch.")


def _crs_info_by_source_path(
    sources: tuple[PointcloudSource, ...],
    crs_input: str = "",
    vertical_input: str = "",
) -> dict[str, dict[str, Any]] | None:
    fallback_crs_info = _crs_info_from_inputs(crs_input, vertical_input)
    mapped: dict[str, dict[str, Any]] = {}
    for source in sources:
        if not source.source_path:
            continue
        crs_info = _source_crs_info(source, crs_input, vertical_input)
        if crs_info:
            mapped[source.source_path] = crs_info
        elif fallback_crs_info:
            mapped[source.source_path] = dict(fallback_crs_info)
    return mapped or None


def _source_crs_info(
    source: PointcloudSource,
    crs_input: str = "",
    vertical_input: str = "",
) -> dict[str, Any] | None:
    if isinstance(source.crs_info, dict) and source.crs_info:
        return dict(source.crs_info)
    return _crs_info_from_inputs(crs_input, vertical_input) or None


def _crs_info_from_inputs(crs_input: str = "", vertical_input: str = "") -> dict[str, Any]:
    horizontal = str(crs_input or "").strip()
    vertical = str(vertical_input or "").strip()
    crs_info: dict[str, Any] = {}
    if horizontal:
        crs_info["value"] = horizontal
        crs_info["projection"] = horizontal
    if vertical:
        crs_info["vertical_crs"] = vertical
        crs_info["vertical_epsg"] = vertical
        crs_info["vertical_projection"] = vertical
    return crs_info


__all__ = [
    "CoreServiceApi",
    "build_upload_workflow_request",
]
