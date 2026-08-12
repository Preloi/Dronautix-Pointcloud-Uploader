"""UI-free controller for Qt project management actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dronautix_uploader.core.contracts import CancelCallback, ProgressCallback

from .project_management import PointcloudPreview, ProjectPreview
from .project_management_actions import (
    ACTION_DELETE,
    ACTION_DISABLE_LINK,
    ACTION_DOWNLOAD,
    ACTION_ENABLE_LINK,
    ACTION_DUPLICATE,
    ACTION_RENAME,
    ACTION_REPLACE_ALL_POINTCLOUDS,
    ACTION_REPLACE_SINGLE_POINTCLOUD,
    ACTION_ADD_POINTCLOUDS,
    ACTION_REMOVE_POINTCLOUD,
    ProjectOperationSummary,
    summarize_project_operation_result,
)


@dataclass(frozen=True)
class RenameProjectInput:
    customer: str
    project: str
    pointcloud_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class DuplicateProjectInput:
    customer: str
    project: str


@dataclass(frozen=True)
class DownloadProjectInput:
    target_dir: str


@dataclass(frozen=True)
class ReplaceAllPointcloudsInput:
    prepared_clouds: tuple[Any, ...] = ()
    source_paths: tuple[str, ...] = ()
    converter_path: str = ""
    output_base_dir: str = ""
    overwrite: bool = False
    crs_info_by_source_path: dict[str, dict[str, Any]] | None = None


@dataclass(frozen=True)
class ReplaceSinglePointcloudInput:
    prepared_cloud: Any = None
    source_path: str = ""
    converter_path: str = ""
    output_base_dir: str = ""
    overwrite: bool = False
    crs_info: dict[str, Any] | None = None


@dataclass(frozen=True)
class AddPointcloudsInput:
    prepared_clouds: tuple[Any, ...] = ()
    source_paths: tuple[str, ...] = ()
    converter_path: str = ""
    output_base_dir: str = ""
    overwrite: bool = False
    crs_info_by_source_path: dict[str, dict[str, Any]] | None = None


class ProjectManagementController:
    """Route UI action IDs to the service-oriented project management core."""

    def __init__(self, service: Any) -> None:
        self.service = service

    def rename_project(
        self,
        project_preview: ProjectPreview | None,
        request: RenameProjectInput,
    ) -> ProjectOperationSummary:
        project = _require_project(project_preview)
        if not request.customer.strip() or not request.project.strip():
            raise ValueError("Kunde und Projektname sind für Umbenennen erforderlich.")
        result = self.service.rename_project(
            project.project_id,
            request.customer,
            request.project,
            request.pointcloud_names,
        )
        return summarize_project_operation_result(result)

    def duplicate_project(
        self,
        project_preview: ProjectPreview | None,
        request: DuplicateProjectInput,
        on_progress: ProgressCallback | None = None,
    ) -> ProjectOperationSummary:
        project = _require_project(project_preview)
        if not request.customer.strip() or not request.project.strip():
            raise ValueError("Kunde und Projektname sind für Duplizieren erforderlich.")
        result = self.service.duplicate_project(
            project.project_id,
            request.customer,
            request.project,
            on_progress=on_progress,
        )
        return summarize_project_operation_result(result)

    def delete_project(self, project_preview: ProjectPreview | None) -> ProjectOperationSummary:
        project = _require_project(project_preview)
        result = self.service.delete_project(project.project_id)
        return summarize_project_operation_result(result)

    def download_project(
        self,
        project_preview: ProjectPreview | None,
        request: DownloadProjectInput,
        on_progress: ProgressCallback | None = None,
        cancel_requested: CancelCallback | None = None,
    ) -> ProjectOperationSummary:
        project = _require_project(project_preview)
        if not request.target_dir.strip():
            raise ValueError("Zielordner ist für Download erforderlich.")
        result = self.service.download_project(
            project.project_id,
            request.target_dir,
            on_progress=on_progress,
            cancel_requested=cancel_requested,
        )
        return summarize_project_operation_result(result)

    def disable_project_link(self, project_preview: ProjectPreview | None) -> ProjectOperationSummary:
        project = _require_project(project_preview)
        result = self.service.set_project_link_state(project.project_id, True)
        return summarize_project_operation_result(result)

    def enable_project_link(self, project_preview: ProjectPreview | None) -> ProjectOperationSummary:
        project = _require_project(project_preview)
        result = self.service.set_project_link_state(project.project_id, False)
        return summarize_project_operation_result(result)

    def replace_all_pointclouds(
        self,
        project_preview: ProjectPreview | None,
        request: ReplaceAllPointcloudsInput,
        on_progress: ProgressCallback | None = None,
    ) -> ProjectOperationSummary:
        project = _require_project(project_preview)
        if request.source_paths:
            result = self.service.replace_project_pointclouds_from_sources(
                project.project_id,
                request.source_paths,
                converter_path=request.converter_path,
                output_base_dir=request.output_base_dir,
                overwrite=request.overwrite,
                on_progress=on_progress,
                crs_info_by_source_path=request.crs_info_by_source_path,
            )
            return summarize_project_operation_result(result)
        if not request.prepared_clouds:
            raise ValueError("Mindestens eine vorbereitete Punktwolke ist erforderlich.")
        result = self.service.replace_project_pointclouds(
            project.project_id,
            request.prepared_clouds,
            on_progress=on_progress,
        )
        return summarize_project_operation_result(result)

    def replace_single_pointcloud(
        self,
        project_preview: ProjectPreview | None,
        pointcloud_preview: PointcloudPreview | None,
        request: ReplaceSinglePointcloudInput,
        on_progress: ProgressCallback | None = None,
    ) -> ProjectOperationSummary:
        project = _require_project(project_preview)
        pointcloud = _require_pointcloud(pointcloud_preview)
        if not pointcloud.s3_path:
            raise ValueError("Ausgewählte Punktwolke hat keinen S3-Pfad.")
        if request.source_path:
            result = self.service.replace_single_project_pointcloud_from_source(
                project.project_id,
                pointcloud.s3_path,
                request.source_path,
                converter_path=request.converter_path,
                output_base_dir=request.output_base_dir,
                overwrite=request.overwrite,
                on_progress=on_progress,
                crs_info=request.crs_info,
            )
            return summarize_project_operation_result(result)
        if request.prepared_cloud is None:
            raise ValueError("Eine vorbereitete Ersatz-Punktwolke ist erforderlich.")
        result = self.service.replace_single_project_pointcloud(
            project.project_id,
            pointcloud.s3_path,
            request.prepared_cloud,
            on_progress=on_progress,
        )
        return summarize_project_operation_result(result)

    def add_pointclouds(
        self,
        project_preview: ProjectPreview | None,
        request: AddPointcloudsInput,
        on_progress: ProgressCallback | None = None,
    ) -> ProjectOperationSummary:
        project = _require_explicit_pointcloud_project(project_preview)
        if request.source_paths:
            result = self.service.add_project_pointclouds_from_sources(
                project.project_id,
                request.source_paths,
                converter_path=request.converter_path,
                output_base_dir=request.output_base_dir,
                overwrite=request.overwrite,
                on_progress=on_progress,
                crs_info_by_source_path=request.crs_info_by_source_path,
            )
            return summarize_project_operation_result(result)
        if not request.prepared_clouds:
            raise ValueError("Mindestens eine vorbereitete Punktwolke ist erforderlich.")
        result = self.service.add_project_pointclouds(
            project.project_id,
            request.prepared_clouds,
            on_progress=on_progress,
        )
        return summarize_project_operation_result(result)

    def remove_pointcloud(
        self,
        project_preview: ProjectPreview | None,
        pointcloud_preview: PointcloudPreview | None,
    ) -> ProjectOperationSummary:
        project = _require_explicit_pointcloud_project(project_preview)
        pointcloud = _require_pointcloud(pointcloud_preview)
        if len(project.pointclouds) <= 1:
            raise ValueError("Die letzte Punktwolke eines Projekts kann nicht entfernt werden.")
        if not pointcloud.s3_path:
            raise ValueError("Ausgewählte Punktwolke hat keinen S3-Pfad.")
        result = self.service.remove_project_pointcloud(project.project_id, pointcloud.s3_path)
        return summarize_project_operation_result(result)

    def handle_action(
        self,
        action_id: str,
        project_preview: ProjectPreview | None,
        pointcloud_preview: PointcloudPreview | None = None,
        payload: Any = None,
        on_progress: ProgressCallback | None = None,
        cancel_requested: CancelCallback | None = None,
    ) -> ProjectOperationSummary:
        if action_id == ACTION_RENAME:
            if not isinstance(payload, RenameProjectInput):
                raise ValueError("RenameProjectInput ist für Umbenennen erforderlich.")
            return self.rename_project(project_preview, payload)
        if action_id == ACTION_DUPLICATE:
            if not isinstance(payload, DuplicateProjectInput):
                raise ValueError("DuplicateProjectInput ist für Duplizieren erforderlich.")
            return self.duplicate_project(project_preview, payload, on_progress=on_progress)
        if action_id == ACTION_DELETE:
            return self.delete_project(project_preview)
        if action_id == ACTION_DOWNLOAD:
            if not isinstance(payload, DownloadProjectInput):
                raise ValueError("DownloadProjectInput ist für Download erforderlich.")
            return self.download_project(
                project_preview,
                payload,
                on_progress=on_progress,
                cancel_requested=cancel_requested,
            )
        if action_id == ACTION_DISABLE_LINK:
            return self.disable_project_link(project_preview)
        if action_id == ACTION_ENABLE_LINK:
            return self.enable_project_link(project_preview)
        if action_id == ACTION_REPLACE_ALL_POINTCLOUDS:
            if not isinstance(payload, ReplaceAllPointcloudsInput):
                raise ValueError("ReplaceAllPointcloudsInput ist für vollständigen Austausch erforderlich.")
            return self.replace_all_pointclouds(project_preview, payload, on_progress=on_progress)
        if action_id == ACTION_REPLACE_SINGLE_POINTCLOUD:
            if not isinstance(payload, ReplaceSinglePointcloudInput):
                raise ValueError("ReplaceSinglePointcloudInput ist für einzelnen Austausch erforderlich.")
            return self.replace_single_pointcloud(project_preview, pointcloud_preview, payload, on_progress=on_progress)
        if action_id == ACTION_ADD_POINTCLOUDS:
            if not isinstance(payload, AddPointcloudsInput):
                raise ValueError("AddPointcloudsInput ist für das Hinzufügen erforderlich.")
            return self.add_pointclouds(project_preview, payload, on_progress=on_progress)
        if action_id == ACTION_REMOVE_POINTCLOUD:
            return self.remove_pointcloud(project_preview, pointcloud_preview)
        raise ValueError(f"Unbekannte Projektverwaltungsaktion: {action_id}")


def _require_project(project_preview: ProjectPreview | None) -> ProjectPreview:
    if project_preview is None:
        raise ValueError("Kein Projekt ausgewählt.")
    if not project_preview.project_id:
        raise ValueError("Ausgewähltes Projekt hat keine ID.")
    return project_preview


def _require_pointcloud(pointcloud_preview: PointcloudPreview | None) -> PointcloudPreview:
    if pointcloud_preview is None:
        raise ValueError("Keine Punktwolke ausgewählt.")
    return pointcloud_preview


def _require_explicit_pointcloud_project(project_preview: ProjectPreview | None) -> ProjectPreview:
    project = _require_project(project_preview)
    if not project.has_explicit_pointclouds:
        raise ValueError("Punktwolken können nur in Projekten mit expliziter pointclouds[]-Liste verwaltet werden.")
    return project


__all__ = [
    "DownloadProjectInput",
    "AddPointcloudsInput",
    "DuplicateProjectInput",
    "ProjectManagementController",
    "RenameProjectInput",
    "ReplaceAllPointcloudsInput",
    "ReplaceSinglePointcloudInput",
]
