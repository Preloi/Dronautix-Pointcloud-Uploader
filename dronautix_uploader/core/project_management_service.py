"""UI-free project management service for active and disabled projects."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
import struct
import tempfile
from typing import Any, Callable
from uuid import uuid4

from .constants import BUCKET_NAME, COPC_OBJECT_NAME
from .contracts import CancelCallback, DownloadResult, ModelUploadInput, ProgressEvent, ProjectOperationResult
from .crs_detection import detect_crs_from_metadata_dict, detect_las_crs
from .crs_service import CrsValidationError, extract_pointcloud_crs_metadata, normalize_crs_metadata
from .glb_optimization_service import GLBOptimizationService
from .naming_service import sanitize_folder_name
from .naming_service import build_project_paths
from .metadata_service import write_potree_metadata_crs_for_sources
from .pointcloud_preparation_service import PointcloudPreparationRequest, prepare_pointcloud_sources
from .project_index_service import (
    append_project_history,
    get_all_projects_for_management,
    update_project_in_index,
    update_project_link_disabled_state,
)
from .project_operations import (
    PreparedCloudUpload,
    add_project_models as add_project_models_operation,
    add_project_pointclouds as add_project_pointclouds_operation,
    apply_project_rename_metadata,
    delete_project as delete_project_operation,
    duplicate_project as duplicate_project_operation,
    download_project as download_project_operation,
    filter_pointcloud_object_keys,
    pointcloud_object_list_prefix,
    prepare_cloud_uploads,
    prepare_single_project_upload,
    rebase_prepared_cloud_upload,
    ProjectDownloadCancelledError,
    remove_project_model as remove_project_model_operation,
    remove_project_pointcloud as remove_project_pointcloud_operation,
    replace_project_pointclouds as replace_project_pointclouds_operation,
    replace_single_project_pointcloud as replace_single_project_pointcloud_operation,
    replace_single_project_model as replace_single_project_model_operation,
    resolve_unique_multi_project_child,
    validate_explicit_multi_project,
)
from .project_repository import ProjectMetadataRepository
from .s3_service import collect_project_objects, delete_s3_objects
from .upload_workflow_service import (
    build_model_pointcloud_spatial_warning_for_bounds,
    cleanup_glb_upload_run_staging_root,
    cleanup_prepared_glb_staging_dirs,
    create_glb_upload_run_staging_root,
    get_glb_upload_staging_root,
)


def _default_project_id() -> str:
    return uuid4().hex[:8]


def _default_timestamp() -> str:
    return datetime.now().isoformat()


def _default_data_version() -> str:
    return uuid4().hex[:12]


@dataclass(frozen=True)
class ProjectManagementService:
    """Coordinate project metadata repository access and project operations."""

    repository: ProjectMetadataRepository
    s3_client: Any
    id_factory: Callable[[], str] = _default_project_id
    timestamp_factory: Callable[[], str] = _default_timestamp
    data_version_factory: Callable[[], str] = _default_data_version
    bucket_name: str | None = None
    glb_service: GLBOptimizationService | None = None

    @property
    def _bucket_name(self) -> str:
        return self.bucket_name or getattr(self.repository, "bucket_name", BUCKET_NAME)

    def list_projects_for_management(self) -> list[tuple[dict[str, Any], bool]]:
        index_data = self.repository.load_projects_index()
        return get_all_projects_for_management(index_data)

    def rename_project(
        self,
        project_id: str,
        new_kunde: str,
        new_projekt: str,
        pointcloud_names: tuple[str, ...] | list[str] = (),
    ) -> ProjectOperationResult:
        index_data = self.repository.load_projects_index()
        renamed_project: dict[str, Any] | None = None
        timestamp = self.timestamp_factory()

        def apply_rename(project: dict[str, Any]) -> None:
            nonlocal renamed_project
            old_customer = str(project.get("kunde", ""))
            old_project = str(project.get("projekt", ""))
            old_pointcloud_names = [
                str(pointcloud.get("name", "")) if isinstance(pointcloud, dict) else ""
                for pointcloud in project.get("pointclouds", [])
            ]
            renamed_project = apply_project_rename_metadata(
                project,
                new_kunde,
                new_projekt,
                pointcloud_names,
            )
            changes = []
            if old_customer != new_kunde:
                changes.append(f"Kunde von '{old_customer}' zu '{new_kunde}' geändert")
            if old_project != new_projekt:
                changes.append(f"Projekt von '{old_project}' zu '{new_projekt}' umbenannt")
            for index, new_name in enumerate(pointcloud_names):
                old_name = old_pointcloud_names[index] if index < len(old_pointcloud_names) else ""
                if old_name != new_name:
                    changes.append(f"Punktwolke von '{old_name}' zu '{new_name}' umbenannt")
            append_project_history(renamed_project, timestamp, "; ".join(changes))
            project.clear()
            project.update(renamed_project)

        if not update_project_in_index(index_data, project_id, apply_rename):
            raise ValueError(f"Projekt mit ID '{project_id}' wurde nicht gefunden.")

        if not self._save_projects_index(index_data):
            raise RuntimeError("Projekt-Index konnte nicht gespeichert werden.")
        return ProjectOperationResult(
            status="success",
            project_id=project_id,
            message="Projekt wurde umbenannt.",
        )

    def delete_project(self, project_id: str):
        index_data = self.repository.load_projects_index()
        project_info, _is_disabled = self._find_project(index_data, project_id)
        deleted_data = self.repository.load_deleted_projects()

        return delete_project_operation(
            s3_client=self.s3_client,
            index_data=index_data,
            deleted_data=deleted_data,
            project_info=project_info,
            deleted_at=self.timestamp_factory(),
            save_index=self._save_projects_index,
            save_deleted=self._save_deleted_projects,
            bucket_name=self._bucket_name,
        )

    def duplicate_project(self, project_id: str, new_kunde: str, new_projekt: str, on_progress=None):
        index_data = self.repository.load_projects_index()
        source_project, _is_disabled = self._find_project(index_data, project_id)
        new_project_id = self.id_factory()
        paths = build_project_paths(new_kunde, new_projekt, new_project_id)

        return duplicate_project_operation(
            s3_client=self.s3_client,
            index_data=index_data,
            source_project=source_project,
            timestamp=self.timestamp_factory(),
            new_kunde=new_kunde,
            new_projekt=new_projekt,
            new_project_id=new_project_id,
            new_project_url=paths.project_url,
            new_viewer_root=paths.project_viewer_root,
            new_s3_prefix=paths.s3_prefix,
            save_index=self._save_projects_index,
            delete_keys=lambda keys: delete_s3_objects(self.s3_client, keys, bucket_name=self._bucket_name),
            bucket_name=self._bucket_name,
            on_progress=on_progress,
        )

    def download_project(
        self,
        project_id: str,
        target_dir: str,
        on_progress=None,
        cancel_requested: CancelCallback | None = None,
    ) -> DownloadResult:
        index_data = self.repository.load_projects_index()
        project_info, _is_disabled = self._find_project(index_data, project_id)
        try:
            download_dir, downloaded_files = download_project_operation(
                s3_client=self.s3_client,
                project_info=project_info,
                target_dir=target_dir,
                sanitize_func=sanitize_folder_name,
                on_progress=on_progress,
                cancel_requested=cancel_requested,
                bucket_name=self._bucket_name,
            )
        except ProjectDownloadCancelledError as exc:
            return DownloadResult(
                status="cancelled",
                download_dir=exc.download_dir,
                downloaded_files=exc.downloaded_files,
                message="Download wurde abgebrochen.",
            )
        return DownloadResult(
            status="success",
            download_dir=download_dir,
            downloaded_files=tuple(downloaded_files),
            message=f"Projekt wurde heruntergeladen: {download_dir}",
        )

    def set_project_link_state(self, project_id: str, disabled: bool):
        index_data = self.repository.load_projects_index()
        _project_info, is_disabled = self._find_project(index_data, project_id)
        if is_disabled == disabled:
            state_text = "deaktiviert" if disabled else "aktiv"
            return ProjectOperationResult(
                status="success",
                project_id=project_id,
                message=f"Projekt-Link ist bereits {state_text}.",
            )

        timestamp = self.timestamp_factory()
        changed_count = update_project_link_disabled_state(
            index_data,
            (project_id,),
            disabled,
            timestamp=timestamp,
        )
        if not changed_count:
            raise RuntimeError("Projekt-Link-Status konnte nicht aktualisiert werden.")
        update_project_in_index(
            index_data,
            project_id,
            lambda project: append_project_history(
                project,
                timestamp,
                "Projekt wurde inaktiv geschaltet." if disabled else "Projekt wurde aktiv geschaltet.",
            ),
        )
        if not self._save_projects_index(index_data):
            raise RuntimeError("Projekt-Index konnte nicht gespeichert werden.")
        action_text = "deaktiviert" if disabled else "aktiviert"
        return ProjectOperationResult(
            status="success",
            project_id=project_id,
            message=f"Projekt-Link wurde {action_text}.",
        )

    def replace_project_pointclouds(
        self,
        project_id: str,
        prepared_clouds: tuple[PreparedCloudUpload, ...] | list[PreparedCloudUpload],
        on_progress=None,
    ):
        index_data = self.repository.load_projects_index()
        project_info, _is_disabled = self._find_project(index_data, project_id)
        project_viewer_root, project_s3_prefix = self._stable_project_roots(project_info)
        if not project_s3_prefix:
            raise ValueError(f"Projekt mit ID '{project_id}' hat keinen S3-Pfad.")

        version_viewer_root, version_s3_prefix = self._versioned_roots(project_info)
        versioned_clouds = tuple(
            rebase_prepared_cloud_upload(cloud, version_viewer_root, version_s3_prefix)
            for cloud in prepared_clouds
        )
        existing_keys = collect_project_objects(
            self.s3_client,
            project_s3_prefix,
            bucket_name=self._bucket_name,
        )
        return replace_project_pointclouds_operation(
            s3_client=self.s3_client,
            index_data=index_data,
            project_id=project_id,
            base_viewer_path=project_viewer_root,
            s3_prefix=project_s3_prefix,
            prepared_clouds=versioned_clouds,
            existing_keys=tuple(existing_keys),
            save_index=self._save_projects_index,
            delete_keys=lambda keys: delete_s3_objects(self.s3_client, keys, bucket_name=self._bucket_name),
            on_progress=on_progress,
            bucket_name=self._bucket_name,
            timestamp=self.timestamp_factory(),
        )

    def replace_project_pointclouds_from_sources(
        self,
        project_id: str,
        source_paths: tuple[str, ...] | list[str],
        converter_path: str = "",
        output_base_dir: str = "",
        overwrite: bool = False,
        on_progress=None,
        converter_runner=None,
        crs_info_by_source_path: dict[str, dict[str, Any]] | None = None,
        source_overrides=None,
    ):
        index_data = self.repository.load_projects_index()
        project_info, _is_disabled = self._find_project(index_data, project_id)
        project_viewer_root, project_s3_prefix = self._stable_project_roots(project_info)
        version_viewer_root, version_s3_prefix = self._versioned_roots(project_info)
        prepared_sources = prepare_pointcloud_sources(
            PointcloudPreparationRequest(
                sources=tuple(source_paths),
                converter_path=converter_path,
                output_base_dir=output_base_dir,
                overwrite=overwrite,
            ),
            on_progress=on_progress,
            converter_runner=converter_runner,
        )
        prepared_sources = _attach_source_overrides(prepared_sources, source_overrides)
        prepared_sources = _attach_crs_info(prepared_sources, tuple(source_paths), crs_info_by_source_path)
        write_potree_metadata_crs_for_sources(prepared_sources)
        prepared_clouds = prepare_cloud_uploads(prepared_sources, version_viewer_root, version_s3_prefix)
        existing_keys = collect_project_objects(
            self.s3_client,
            project_s3_prefix,
            bucket_name=self._bucket_name,
        )
        return replace_project_pointclouds_operation(
            s3_client=self.s3_client,
            index_data=index_data,
            project_id=project_id,
            base_viewer_path=project_viewer_root,
            s3_prefix=project_s3_prefix,
            prepared_clouds=prepared_clouds,
            existing_keys=tuple(existing_keys),
            save_index=self._save_projects_index,
            delete_keys=lambda keys: delete_s3_objects(self.s3_client, keys, bucket_name=self._bucket_name),
            on_progress=on_progress,
            bucket_name=self._bucket_name,
            timestamp=self.timestamp_factory(),
        )

    def add_project_pointclouds(
        self,
        project_id: str,
        prepared_clouds: tuple[PreparedCloudUpload, ...] | list[PreparedCloudUpload],
        on_progress=None,
    ):
        index_data = self.repository.load_projects_index()
        project_info, _is_disabled = self._find_project(index_data, project_id)
        project_viewer_root, project_s3_prefix = self._stable_project_roots(project_info)
        validate_explicit_multi_project(project_info, project_viewer_root, project_s3_prefix)
        version_viewer_root, version_s3_prefix = self._versioned_roots(project_info)
        versioned_clouds = tuple(
            rebase_prepared_cloud_upload(cloud, version_viewer_root, version_s3_prefix)
            for cloud in prepared_clouds
        )
        return add_project_pointclouds_operation(
            s3_client=self.s3_client,
            index_data=index_data,
            project_id=project_id,
            project_viewer_root=project_viewer_root,
            project_s3_prefix=project_s3_prefix,
            prepared_clouds=versioned_clouds,
            save_index=self._save_projects_index,
            delete_keys=lambda keys: delete_s3_objects(self.s3_client, keys, bucket_name=self._bucket_name),
            on_progress=on_progress,
            bucket_name=self._bucket_name,
            timestamp=self.timestamp_factory(),
        )

    def add_project_pointclouds_from_sources(
        self,
        project_id: str,
        source_paths: tuple[str, ...] | list[str],
        converter_path: str = "",
        output_base_dir: str = "",
        overwrite: bool = False,
        on_progress=None,
        converter_runner=None,
        crs_info_by_source_path: dict[str, dict[str, Any]] | None = None,
        source_overrides=None,
    ):
        index_data = self.repository.load_projects_index()
        project_info, _is_disabled = self._find_project(index_data, project_id)
        project_viewer_root, project_s3_prefix = self._stable_project_roots(project_info)
        validate_explicit_multi_project(project_info, project_viewer_root, project_s3_prefix)
        prepared_sources = prepare_pointcloud_sources(
            PointcloudPreparationRequest(
                sources=tuple(source_paths),
                converter_path=converter_path,
                output_base_dir=output_base_dir,
                overwrite=overwrite,
            ),
            on_progress=on_progress,
            converter_runner=converter_runner,
        )
        prepared_sources = _attach_source_overrides(prepared_sources, source_overrides)
        prepared_sources = _attach_crs_info(prepared_sources, tuple(source_paths), crs_info_by_source_path)
        write_potree_metadata_crs_for_sources(prepared_sources)
        version_viewer_root, version_s3_prefix = self._versioned_roots(project_info)
        prepared_clouds = prepare_cloud_uploads(prepared_sources, version_viewer_root, version_s3_prefix)
        return add_project_pointclouds_operation(
            s3_client=self.s3_client,
            index_data=index_data,
            project_id=project_id,
            project_viewer_root=project_viewer_root,
            project_s3_prefix=project_s3_prefix,
            prepared_clouds=prepared_clouds,
            save_index=self._save_projects_index,
            delete_keys=lambda keys: delete_s3_objects(self.s3_client, keys, bucket_name=self._bucket_name),
            on_progress=on_progress,
            bucket_name=self._bucket_name,
            timestamp=self.timestamp_factory(),
        )

    def remove_project_pointcloud(
        self,
        project_id: str,
        target_pointcloud_s3_path: str,
    ):
        index_data = self.repository.load_projects_index()
        project_info, _is_disabled = self._find_project(index_data, project_id)
        project_viewer_root, project_s3_prefix = self._stable_project_roots(project_info)
        target = resolve_unique_multi_project_child(
            project_info,
            target_pointcloud_s3_path,
            project_viewer_root,
            project_s3_prefix,
        )
        list_prefix = pointcloud_object_list_prefix(target, project_viewer_root, project_s3_prefix)
        listed_keys = collect_project_objects(
            self.s3_client,
            list_prefix,
            bucket_name=self._bucket_name,
        )
        existing_target_keys = filter_pointcloud_object_keys(
            target,
            listed_keys,
            project_viewer_root,
            project_s3_prefix,
        )
        return remove_project_pointcloud_operation(
            index_data=index_data,
            project_id=project_id,
            project_viewer_root=project_viewer_root,
            project_s3_prefix=project_s3_prefix,
            target_pointcloud_s3_path=target_pointcloud_s3_path,
            existing_target_keys=existing_target_keys,
            save_index=self._save_projects_index,
            delete_keys=lambda keys: delete_s3_objects(self.s3_client, keys, bucket_name=self._bucket_name),
            timestamp=self.timestamp_factory(),
        )

    def remove_project_model(self, project_id: str, target_model_s3_path: str):
        index_data = self.repository.load_projects_index()
        project_info, _is_disabled = self._find_project(index_data, project_id)
        target_path = str(target_model_s3_path or "").strip().rstrip("/")
        models = project_info.get("models")
        if not isinstance(models, list) or not any(
            isinstance(model, dict) and str(model.get("s3_path", "")).strip().rstrip("/") == target_path
            for model in models
        ):
            raise ValueError("Das ausgewählte GLB wurde im Projekt nicht gefunden.")
        existing_target_keys = collect_project_objects(
            self.s3_client,
            target_path,
            bucket_name=self._bucket_name,
        )
        return remove_project_model_operation(
            index_data=index_data,
            project_id=project_id,
            target_model_s3_path=target_path,
            existing_target_keys=tuple(existing_target_keys),
            save_index=self._save_projects_index,
            delete_keys=lambda keys: delete_s3_objects(self.s3_client, keys, bucket_name=self._bucket_name),
            timestamp=self.timestamp_factory(),
        )

    def replace_single_project_pointcloud(
        self,
        project_id: str,
        target_pointcloud_s3_path: str,
        prepared_cloud: PreparedCloudUpload,
        on_progress=None,
    ):
        index_data = self.repository.load_projects_index()
        project_info, _is_disabled = self._find_project(index_data, project_id)
        target_path = str(target_pointcloud_s3_path or "").strip().rstrip("/")
        if not self._has_pointcloud_s3_path(project_info, target_path):
            raise ValueError(f"Punktwolke mit S3-Pfad '{target_pointcloud_s3_path}' wurde nicht gefunden.")

        project_viewer_root, project_s3_prefix = self._stable_project_roots(project_info)
        version_viewer_root, version_s3_prefix = self._versioned_roots(project_info)
        versioned_cloud = rebase_prepared_cloud_upload(
            prepared_cloud,
            version_viewer_root,
            version_s3_prefix,
            slug=prepared_cloud.slug if isinstance(project_info.get("pointclouds"), list) else "",
        )
        existing_target_keys = collect_project_objects(
            self.s3_client,
            target_path,
            bucket_name=self._bucket_name,
        )
        return replace_single_project_pointcloud_operation(
            s3_client=self.s3_client,
            index_data=index_data,
            project_id=project_id,
            base_viewer_path=project_viewer_root,
            s3_prefix=project_s3_prefix,
            prepared_cloud=versioned_cloud,
            target_pointcloud_s3_path=target_path,
            existing_target_keys=tuple(existing_target_keys),
            save_index=self._save_projects_index,
            delete_keys=lambda keys: delete_s3_objects(self.s3_client, keys, bucket_name=self._bucket_name),
            on_progress=on_progress,
            bucket_name=self._bucket_name,
            timestamp=self.timestamp_factory(),
        )

    def replace_single_project_pointcloud_from_source(
        self,
        project_id: str,
        target_pointcloud_s3_path: str,
        source_path: str,
        converter_path: str = "",
        output_base_dir: str = "",
        overwrite: bool = False,
        on_progress=None,
        converter_runner=None,
        crs_info: dict[str, Any] | None = None,
    ):
        index_data = self.repository.load_projects_index()
        project_info, _is_disabled = self._find_project(index_data, project_id)
        target_path = str(target_pointcloud_s3_path or "").strip().rstrip("/")
        if not self._has_pointcloud_s3_path(project_info, target_path):
            raise ValueError(f"Punktwolke mit S3-Pfad '{target_pointcloud_s3_path}' wurde nicht gefunden.")

        prepared_sources = prepare_pointcloud_sources(
            PointcloudPreparationRequest(
                sources=(source_path,),
                converter_path=converter_path,
                output_base_dir=output_base_dir,
                overwrite=overwrite,
            ),
            on_progress=on_progress,
            converter_runner=converter_runner,
        )
        prepared_sources = _attach_crs_info(
            prepared_sources,
            (source_path,),
            {source_path: crs_info} if crs_info else None,
        )
        write_potree_metadata_crs_for_sources(prepared_sources)
        project_viewer_root, project_s3_prefix = self._stable_project_roots(project_info)
        version_viewer_root, version_s3_prefix = self._versioned_roots(project_info)
        prepared_cloud = prepare_cloud_uploads(
            prepared_sources,
            version_viewer_root,
            version_s3_prefix,
        )[0]
        if not isinstance(project_info.get("pointclouds"), list) and self._has_pointcloud_s3_path(project_info, target_path):
            prepared_cloud = prepare_single_project_upload(
                prepared_sources[0],
                version_viewer_root,
                version_s3_prefix,
            )
        existing_target_keys = collect_project_objects(
            self.s3_client,
            target_path,
            bucket_name=self._bucket_name,
        )
        return replace_single_project_pointcloud_operation(
            s3_client=self.s3_client,
            index_data=index_data,
            project_id=project_id,
            base_viewer_path=project_viewer_root,
            s3_prefix=project_s3_prefix,
            prepared_cloud=prepared_cloud,
            target_pointcloud_s3_path=target_path,
            existing_target_keys=tuple(existing_target_keys),
            save_index=self._save_projects_index,
            delete_keys=lambda keys: delete_s3_objects(self.s3_client, keys, bucket_name=self._bucket_name),
            on_progress=on_progress,
            bucket_name=self._bucket_name,
            timestamp=self.timestamp_factory(),
        )

    def replace_single_project_model_from_source(
        self,
        project_id: str,
        target_model_s3_path: str,
        source_path: str,
        *,
        model_json_path: str = "",
        on_progress=None,
        confirm_spatial_warning: Callable[[str], bool] | None = None,
        confirm_crs_repair: Callable[[str], bool] | None = None,
    ):
        index_data = self.repository.load_projects_index()
        project_info, _is_disabled = self._find_project(index_data, project_id)
        target_path = str(target_model_s3_path or "").strip().rstrip("/")
        models = project_info.get("models")
        matches = [
            model
            for model in models if isinstance(model, dict) and str(model.get("s3_path", "")).strip().rstrip("/") == target_path
        ] if isinstance(models, list) else []
        if len(matches) != 1:
            raise ValueError("Das ausgewählte GLB konnte nicht eindeutig gefunden werden.")
        target_model = matches[0]
        model_id = str(target_model.get("id", "")).strip()
        model_name = str(target_model.get("name", "")).strip() or model_id
        if not model_id:
            raise ValueError("Das ausgewählte GLB hat keine Modell-ID.")
        project_crs_info, crs_repair_plan = _resolve_project_model_crs(
            project_info, self.s3_client, bucket_name=self._bucket_name
        )
        if crs_repair_plan and not _confirm_crs_repair(crs_repair_plan, confirm_crs_repair, on_progress):
            return _crs_repair_cancelled_result(project_id, crs_repair_plan)

        project_viewer_root, project_s3_prefix = self._stable_project_roots(project_info)
        staging_root = get_glb_upload_staging_root()
        staging_run_root = create_glb_upload_run_staging_root(staging_root=staging_root)
        prepared_models = ()
        try:
            prepared_model = (self.glb_service or GLBOptimizationService()).prepare(
                ModelUploadInput(
                    source_path=source_path,
                    name=model_name,
                    slug=model_id,
                    model_json_path=model_json_path,
                ),
                project_crs_info=project_crs_info,
                staging_root=staging_run_root,
                project_viewer_root=project_viewer_root,
                project_s3_prefix=project_s3_prefix,
                on_progress=on_progress,
            )
            prepared_models = (prepared_model,)
            spatial_warning = _existing_project_model_spatial_warning(
                project_info,
                self.s3_client,
                bucket_name=self._bucket_name,
                prepared_models=prepared_models,
            )
            if spatial_warning and not _confirm_spatial_warning(
                spatial_warning,
                project_id=project_id,
                on_progress=on_progress,
                confirm_spatial_warning=confirm_spatial_warning,
            ):
                return _spatial_warning_cancelled_result(project_id, spatial_warning)
            existing_target_keys = collect_project_objects(
                self.s3_client,
                target_path,
                bucket_name=self._bucket_name,
            )
            original_index_data = copy.deepcopy(index_data)
            try:
                remote_backups = _repair_s3_potree_crs_metadata(
                    project_info,
                    self.s3_client,
                    bucket_name=self._bucket_name,
                    crs_info=project_crs_info,
                    children=crs_repair_plan["children"],
                ) if crs_repair_plan else ()
                _apply_crs_repair_plan(index_data, project_id, crs_repair_plan, timestamp=self.timestamp_factory())
                result = replace_single_project_model_operation(
                    s3_client=self.s3_client,
                    index_data=index_data,
                    project_id=project_id,
                    prepared_model=prepared_model,
                    target_model_s3_path=target_path,
                    existing_target_keys=tuple(existing_target_keys),
                    save_index=self._save_projects_index,
                    delete_keys=lambda keys: delete_s3_objects(self.s3_client, keys, bucket_name=self._bucket_name),
                    on_progress=on_progress,
                    bucket_name=self._bucket_name,
                    timestamp=self.timestamp_factory(),
                )
            except Exception as error:
                _restore_index_data(index_data, original_index_data)
                rollback_errors = _rollback_crs_repair_failure(
                    self._save_projects_index,
                    index_data,
                    self.s3_client,
                    self._bucket_name,
                    locals().get("remote_backups", ()),
                    repair_attempted=bool(crs_repair_plan),
                )
                if rollback_errors:
                    raise RuntimeError(f"CRS-Reparatur-Rollback unvollständig: {'; '.join(rollback_errors)}") from error
                raise
            return result
        finally:
            cleanup_prepared_glb_staging_dirs(
                prepared_models,
                staging_root=staging_root,
                on_progress=on_progress,
            )
            cleanup_glb_upload_run_staging_root(
                staging_run_root,
                staging_root=staging_root,
                on_progress=on_progress,
            )

    def add_project_models_from_sources(
        self,
        project_id: str,
        source_paths: tuple[str, ...] | list[str],
        *,
        model_json_by_source_path: dict[str, str] | None = None,
        on_progress=None,
        confirm_spatial_warning: Callable[[str], bool] | None = None,
        confirm_crs_repair: Callable[[str], bool] | None = None,
    ):
        paths = tuple(str(path or "").strip() for path in source_paths if str(path or "").strip())
        if not paths:
            raise ValueError("Mindestens eine GLB-Datei ist erforderlich.")
        normalized_paths = tuple(os.path.normcase(os.path.abspath(path)) for path in paths)
        if len(set(normalized_paths)) != len(normalized_paths):
            raise ValueError("Dasselbe GLB-Modell wurde mehrfach ausgewählt.")

        index_data = self.repository.load_projects_index()
        project_info, _is_disabled = self._find_project(index_data, project_id)
        project_crs_info, crs_repair_plan = _resolve_project_model_crs(
            project_info, self.s3_client, bucket_name=self._bucket_name
        )
        if crs_repair_plan and not _confirm_crs_repair(crs_repair_plan, confirm_crs_repair, on_progress):
            return _crs_repair_cancelled_result(project_id, crs_repair_plan)
        existing_models = project_info.get("models", [])
        if not isinstance(existing_models, list):
            raise ValueError("Projekt enthält ungültige models[]-Metadaten.")
        used_slugs = {
            str(model.get("id", "")).strip()
            for model in existing_models
            if isinstance(model, dict) and str(model.get("id", "")).strip()
        }
        project_viewer_root, project_s3_prefix = self._stable_project_roots(project_info)
        staging_root = get_glb_upload_staging_root()
        staging_run_root = create_glb_upload_run_staging_root(staging_root=staging_root)
        prepared_models = []
        glb_service = self.glb_service or GLBOptimizationService()
        sidecars = model_json_by_source_path or {}
        try:
            for source_path in paths:
                prepared_models.append(
                    glb_service.prepare(
                        ModelUploadInput(
                            source_path=source_path,
                            model_json_path=str(sidecars.get(source_path, "") or "").strip(),
                        ),
                        project_crs_info=project_crs_info,
                        staging_root=staging_run_root,
                        used_slugs=used_slugs,
                        project_viewer_root=project_viewer_root,
                        project_s3_prefix=project_s3_prefix,
                        on_progress=on_progress,
                    )
                )
            spatial_warning = _existing_project_model_spatial_warning(
                project_info,
                self.s3_client,
                bucket_name=self._bucket_name,
                prepared_models=tuple(prepared_models),
            )
            if spatial_warning and not _confirm_spatial_warning(
                spatial_warning,
                project_id=project_id,
                on_progress=on_progress,
                confirm_spatial_warning=confirm_spatial_warning,
            ):
                return _spatial_warning_cancelled_result(project_id, spatial_warning)
            original_index_data = copy.deepcopy(index_data)
            try:
                remote_backups = _repair_s3_potree_crs_metadata(
                    project_info,
                    self.s3_client,
                    bucket_name=self._bucket_name,
                    crs_info=project_crs_info,
                    children=crs_repair_plan["children"],
                ) if crs_repair_plan else ()
                _apply_crs_repair_plan(index_data, project_id, crs_repair_plan, timestamp=self.timestamp_factory())
                result = add_project_models_operation(
                    s3_client=self.s3_client,
                    index_data=index_data,
                    project_id=project_id,
                    project_viewer_root=project_viewer_root,
                    project_s3_prefix=project_s3_prefix,
                    prepared_models=tuple(prepared_models),
                    save_index=self._save_projects_index,
                    delete_keys=lambda keys: delete_s3_objects(self.s3_client, keys, bucket_name=self._bucket_name),
                    on_progress=on_progress,
                    bucket_name=self._bucket_name,
                    timestamp=self.timestamp_factory(),
                )
            except Exception as error:
                _restore_index_data(index_data, original_index_data)
                rollback_errors = _rollback_crs_repair_failure(
                    self._save_projects_index,
                    index_data,
                    self.s3_client,
                    self._bucket_name,
                    locals().get("remote_backups", ()),
                    repair_attempted=bool(crs_repair_plan),
                )
                if rollback_errors:
                    raise RuntimeError(f"CRS-Reparatur-Rollback unvollständig: {'; '.join(rollback_errors)}") from error
                raise
            return result
        finally:
            cleanup_prepared_glb_staging_dirs(
                tuple(prepared_models),
                staging_root=staging_root,
                on_progress=on_progress,
            )
            cleanup_glb_upload_run_staging_root(
                staging_run_root,
                staging_root=staging_root,
                on_progress=on_progress,
            )

    def repair_project_crs_metadata(
        self,
        project_id: str,
        crs_info: dict[str, Any],
        *,
        confirm_repair: Callable[[str], bool] | None = None,
        allow_conflicting_overwrite: bool = False,
    ) -> ProjectOperationResult:
        """Explicitly backfill a complete manual CRS without moving model geometry."""

        complete_crs = _complete_crs_info(crs_info)
        if complete_crs is None:
            raise ValueError("Manuelle CRS-Reparatur benötigt ein eindeutiges horizontales und vertikales CRS.")
        index_data = self.repository.load_projects_index()
        project, _is_disabled = self._find_project(index_data, project_id)
        plan = _manual_crs_repair_plan(
            project,
            self.s3_client,
            bucket_name=self._bucket_name,
            crs_info=complete_crs,
            allow_conflicting_overwrite=allow_conflicting_overwrite,
        )
        if not plan:
            return ProjectOperationResult(status="success", project_id=project_id, message="CRS-Metadaten sind bereits vollständig.")
        if not _confirm_crs_repair(plan, confirm_repair, None):
            return _crs_repair_cancelled_result(project_id, plan)
        snapshot = copy.deepcopy(index_data)
        remote_backups = ()
        try:
            remote_backups = _repair_s3_potree_crs_metadata(
                project,
                self.s3_client,
                bucket_name=self._bucket_name,
                crs_info=complete_crs,
                children=plan["children"],
                overwrite=allow_conflicting_overwrite,
            )
            _apply_crs_repair_plan(
                index_data,
                project_id,
                plan,
                overwrite=allow_conflicting_overwrite,
                timestamp=self.timestamp_factory(),
            )
            if not self._save_projects_index(index_data):
                raise RuntimeError("Projekt-Index konnte nicht gespeichert werden.")
        except Exception as error:
            _restore_index_data(index_data, snapshot)
            rollback_errors = _rollback_crs_repair_failure(
                self._save_projects_index,
                index_data,
                self.s3_client,
                self._bucket_name,
                remote_backups,
                repair_attempted=True,
            )
            if rollback_errors:
                raise RuntimeError(f"CRS-Reparatur-Rollback unvollständig: {'; '.join(rollback_errors)}") from error
            raise
        return ProjectOperationResult(status="success", project_id=project_id, message="CRS-Metadaten wurden repariert.")

    def _find_project(self, index_data: dict[str, Any], project_id: str) -> tuple[dict[str, Any], bool]:
        normalized_project_id = str(project_id).strip()
        for project, is_disabled in get_all_projects_for_management(index_data):
            if str(project.get("id", "")).strip() == normalized_project_id:
                return project, is_disabled
        raise ValueError(f"Projekt mit ID '{project_id}' wurde nicht gefunden.")

    def _project_viewer_root(self, project: dict[str, Any]) -> str:
        viewer_path = str(project.get("viewer_path", "")).strip().rstrip("/")
        if viewer_path.endswith(f"/{COPC_OBJECT_NAME}"):
            return viewer_path[: -len(f"/{COPC_OBJECT_NAME}")]
        return viewer_path

    def _project_s3_prefix(self, project: dict[str, Any]) -> str:
        s3_path = str(project.get("s3_path", "")).strip().rstrip("/")
        if s3_path.endswith(f"/{COPC_OBJECT_NAME}"):
            return s3_path[: -len(f"/{COPC_OBJECT_NAME}")]
        return s3_path

    def _has_pointcloud_s3_path(self, project: dict[str, Any], target_s3_path: str) -> bool:
        pointclouds = project.get("pointclouds")
        normalized_target = str(target_s3_path or "").strip().rstrip("/")
        project_path = str(project.get("s3_path", "")).strip().rstrip("/")
        if project_path and project_path == normalized_target:
            return True
        if not isinstance(pointclouds, list):
            return False
        for pointcloud in pointclouds:
            if not isinstance(pointcloud, dict):
                continue
            if str(pointcloud.get("s3_path", "")).strip().rstrip("/") == normalized_target:
                return True
        return False

    def _stable_project_roots(self, project: dict[str, Any]) -> tuple[str, str]:
        return (
            _strip_data_version(self._project_viewer_root(project)),
            _strip_data_version(self._project_s3_prefix(project)),
        )

    def _versioned_roots(self, project: dict[str, Any]) -> tuple[str, str]:
        viewer_root, s3_root = self._stable_project_roots(project)
        version = str(self.data_version_factory()).strip()
        if not version or "/" in version or "\\" in version:
            raise ValueError("Ungueltige Datenversion fuer den Punktwolken-Upload.")
        return f"{viewer_root}/versions/{version}", f"{s3_root}/versions/{version}"

    def _save_projects_index(self, index_data: dict[str, Any]) -> bool:
        result = self.repository.save_projects_index(index_data)
        return True if result is None else bool(result)

    def _save_deleted_projects(self, deleted_data: dict[str, Any]) -> bool:
        result = self.repository.save_deleted_projects(deleted_data)
        return True if result is None else bool(result)


def _attach_crs_info(
    prepared_sources,
    original_source_paths: tuple[str, ...],
    crs_info_by_source_path: dict[str, dict[str, Any]] | None,
):
    if not crs_info_by_source_path:
        return prepared_sources

    import os

    normalized_crs = {
        os.path.abspath(str(path or "")).casefold(): crs_info
        for path, crs_info in crs_info_by_source_path.items()
        if isinstance(crs_info, dict)
    }
    updated_sources = []
    for source, original_path in zip(prepared_sources, original_source_paths, strict=False):
        crs_info = normalized_crs.get(os.path.abspath(str(original_path or "")).casefold())
        if crs_info is None:
            updated_sources.append(source)
            continue
        from .contracts import PointcloudSource

        updated_sources.append(
            PointcloudSource(
                source_path=source.source_path,
                name=source.name,
                slug=source.slug,
                input_format=source.input_format,
                source_type=source.source_type,
                crs_info=dict(crs_info),
            )
        )
    return tuple(updated_sources)


def _attach_source_overrides(prepared_sources, source_overrides):
    if not source_overrides:
        return prepared_sources

    from .contracts import PointcloudSource

    updated_sources = []
    for prepared_source, override in zip(prepared_sources, source_overrides, strict=False):
        if not isinstance(override, PointcloudSource):
            updated_sources.append(prepared_source)
            continue
        updated_sources.append(
            PointcloudSource(
                source_path=prepared_source.source_path,
                name=override.name or prepared_source.name,
                slug=override.slug or prepared_source.slug,
                input_format=prepared_source.input_format,
                source_type=prepared_source.source_type,
                crs_info=override.crs_info if isinstance(override.crs_info, dict) else prepared_source.crs_info,
            )
        )
    if len(updated_sources) < len(prepared_sources):
        updated_sources.extend(prepared_sources[len(updated_sources) :])
    return tuple(updated_sources)


def _strip_data_version(path: str) -> str:
    marker = "/versions/"
    normalized = str(path or "").strip().rstrip("/")
    stable_root, separator, version = normalized.rpartition(marker)
    return stable_root if separator and version and "/" not in version else normalized


def _confirm_spatial_warning(
    warning: str,
    *,
    project_id: str,
    on_progress,
    confirm_spatial_warning: Callable[[str], bool] | None,
) -> bool:
    if on_progress is not None:
        on_progress(ProgressEvent(kind="warning", message=warning, phase="validation"))
    try:
        return bool(confirm_spatial_warning and confirm_spatial_warning(warning))
    except Exception:
        return False


def _spatial_warning_cancelled_result(project_id: str, warning: str) -> ProjectOperationResult:
    return ProjectOperationResult(
        status="cancelled",
        project_id=project_id,
        warnings=(warning,),
        message="Upload nicht gestartet: 3D-Modelle liegen außerhalb der Punktwolke oder deren Bounds sind nicht prüfbar.",
    )


def _existing_project_model_spatial_warning(project: dict[str, Any], s3_client, *, bucket_name: str, prepared_models) -> str:
    """Check existing cloud bounds before immutable model objects are uploaded.

    Missing or unreadable cloud bounds deliberately require confirmation: silently
    accepting a model would recreate the invisible-model failure this guard avoids.
    """

    bounds, bounds_unreadable = _existing_project_pointcloud_bounds(project, s3_client, bucket_name)
    if bounds_unreadable or not bounds:
        return (
            "Die Bounds der bestehenden Punktwolke konnten nicht sicher ermittelt werden.\n\n"
            "Der Upload wird nur nach ausdrücklicher Bestätigung fortgesetzt. Prüfe Punktwolke und GLB-Referenzierung."
        )
    return build_model_pointcloud_spatial_warning_for_bounds(bounds, prepared_models)


def _existing_project_pointcloud_bounds(project: dict[str, Any], s3_client, bucket_name: str):
    pointclouds = project.get("pointclouds")
    entries = pointclouds if isinstance(pointclouds, list) else (project,)
    metadata_bounds = []
    s3_bounds = []
    unreadable = False
    for entry in entries:
        if not isinstance(entry, dict):
            unreadable = True
            continue
        entry_metadata_bounds = _bounds_from_metadata(entry)
        if entry_metadata_bounds is not None:
            metadata_bounds.append(entry_metadata_bounds)
        cloud_path = str(entry.get("s3_path", "")).strip().rstrip("/")
        if not cloud_path:
            if entry_metadata_bounds is None:
                unreadable = True
            continue
        entry_s3_bounds = _s3_pointcloud_bounds(
            s3_client,
            bucket_name,
            cloud_path,
            str(entry.get("format", project.get("format", ""))).strip().casefold(),
        )
        if entry_s3_bounds:
            s3_bounds.extend(entry_s3_bounds)
        elif entry_metadata_bounds is None:
            unreadable = True
    # S3 metadata/header is the current source of truth; index bounds only
    # rescue legacy projects whose cloud files cannot be read.
    return tuple(dict.fromkeys(s3_bounds or metadata_bounds)), unreadable


def _bounds_from_metadata(entry: dict[str, Any]):
    for value in (entry, entry.get("bounds"), entry.get("boundingBox"), entry.get("tightBoundingBox")):
        if not isinstance(value, dict):
            continue
        minimum = value.get("min") or value.get("minimum") or value.get("bounds_min")
        maximum = value.get("max") or value.get("maximum") or value.get("bounds_max")
        if isinstance(minimum, dict) and isinstance(maximum, dict):
            minimum = tuple(minimum.get(axis) for axis in "xyz")
            maximum = tuple(maximum.get(axis) for axis in "xyz")
        if minimum is None or maximum is None:
            minimum = tuple(value.get(name) for name in ("lx", "ly", "lz"))
            maximum = tuple(value.get(name) for name in ("ux", "uy", "uz"))
        if (bounds := _validated_bounds(minimum, maximum)) is not None:
            return bounds
    return None


def _s3_pointcloud_bounds(s3_client, bucket_name: str, cloud_path: str, cloud_format: str = ""):
    if cloud_format == "copc" and not cloud_path.casefold().endswith((".las", ".laz")):
        cloud_path = f"{cloud_path}/{COPC_OBJECT_NAME}"
    if cloud_path.casefold().endswith((".las", ".laz")):
        bounds = _read_s3_las_bounds(s3_client, bucket_name, cloud_path)
        return (bounds,) if bounds is not None else ()
    return tuple(
        bounds
        for filename in ("metadata.json", "cloud.js")
        if (bounds := _read_s3_potree_bounds(s3_client, bucket_name, f"{cloud_path}/{filename}")) is not None
    )


def _read_s3_potree_bounds(s3_client, bucket_name: str, key: str):
    try:
        text = _read_s3_body(s3_client.get_object(Bucket=bucket_name, Key=key)).decode("utf-8").strip()
        if key.casefold().endswith("cloud.js") and text.startswith("cloud.js"):
            text = text[len("cloud.js") :].strip().lstrip("=").strip().rstrip(";").strip()
        document = json.loads(text)
    except Exception:
        return None
    return _bounds_from_metadata(document)


def _read_s3_las_bounds(s3_client, bucket_name: str, key: str):
    try:
        data = _read_s3_body(s3_client.get_object(Bucket=bucket_name, Key=key, Range="bytes=0-226"))
    except Exception:
        return None
    if len(data) < 227 or data[:4] != b"LASF":
        return None
    max_x, min_x, max_y, min_y, max_z, min_z = struct.unpack_from("<6d", data, 179)
    return _validated_bounds((min_x, min_y, min_z), (max_x, max_y, max_z))


def _read_s3_body(response) -> bytes:
    body = response.get("Body") if isinstance(response, dict) else None
    data = body.read() if body is not None else b""
    return data if isinstance(data, bytes) else bytes(data)


def _validated_bounds(minimum, maximum):
    try:
        lower = tuple(float(value) for value in minimum)
        upper = tuple(float(value) for value in maximum)
    except (TypeError, ValueError):
        return None
    if len(lower) != 3 or len(upper) != 3 or not all(math.isfinite(value) for value in (*lower, *upper)):
        return None
    if any(first > second for first, second in zip(lower, upper, strict=True)):
        return None
    return lower, upper


def _complete_crs_info(crs_info):
    try:
        normalized = normalize_crs_metadata(crs_info)
    except CrsValidationError:
        return None
    if normalized and normalized.get("value") and normalized.get("vertical_crs"):
        return normalized
    return None


def _crs_conflicts_with(crs_info, complete_crs_info) -> bool:
    try:
        normalized = normalize_crs_metadata(crs_info)
    except CrsValidationError:
        return True
    if not normalized:
        return False
    return any(
        normalized.get(key) and normalized.get(key) != complete_crs_info.get(key)
        for key in ("value", "vertical_crs")
    )


def _cloud_entries(project: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    pointclouds = project.get("pointclouds")
    return tuple(entry for entry in pointclouds if isinstance(entry, dict)) if isinstance(pointclouds, list) else (project,)


def _cloud_label(entry: dict[str, Any], index: int) -> str:
    return str(entry.get("name") or entry.get("slug") or f"Punktwolke {index + 1}").strip()


def _cloud_labels(entries: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    raw_labels = tuple(_cloud_label(entry, index) for index, entry in enumerate(entries))
    counts = {label: raw_labels.count(label) for label in raw_labels}
    seen: dict[str, int] = {}
    labels = []
    for label in raw_labels:
        seen[label] = seen.get(label, 0) + 1
        labels.append(f"{label} (#{seen[label]})" if counts[label] > 1 else label)
    return tuple(labels)


def _is_missing_s3_object_error(error: Exception) -> bool:
    if isinstance(error, (KeyError, FileNotFoundError)):
        return True
    response = getattr(error, "response", None)
    code = str((response or {}).get("Error", {}).get("Code", "")).casefold() if isinstance(response, dict) else ""
    return code in {"404", "nosuchkey", "notfound", "nosuchobject"}


def _s3_potree_documents(s3_client, bucket_name: str, entry: dict[str, Any], project: dict[str, Any]):
    cloud_path = str(entry.get("s3_path", "")).strip().rstrip("/")
    cloud_format = str(entry.get("format", project.get("format", ""))).strip().casefold()
    if not cloud_path or cloud_format == "copc" or cloud_path.casefold().endswith((".las", ".laz")):
        return ()
    documents = []
    for filename in ("metadata.json", "cloud.js"):
        key = f"{cloud_path}/{filename}"
        try:
            response = s3_client.get_object(Bucket=bucket_name, Key=key)
        except Exception as error:
            if _is_missing_s3_object_error(error):
                continue
            raise RuntimeError(f"Potree-Metadaten konnten nicht gelesen werden: {key}: {error}") from error
        try:
            raw = _read_s3_body(response)
            text = raw.decode("utf-8").strip()
            if filename == "cloud.js" and text.startswith("cloud.js"):
                text = text[len("cloud.js") :].strip().lstrip("=").strip().rstrip(";").strip()
            document = json.loads(text)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError(f"Potree-Metadaten sind ungültig: {key}: {error}") from error
        if not isinstance(document, dict):
            raise ValueError(f"Potree-Metadaten sind ungültig: {key}")
        headers = _s3_object_headers(response)
        headers.setdefault("ContentType", "application/javascript" if filename == "cloud.js" else "application/json")
        documents.append((key, document, raw, filename == "cloud.js", headers))
    return tuple(documents)


def _s3_copc_crs(s3_client, bucket_name: str, entry: dict[str, Any], project: dict[str, Any]):
    cloud_path = str(entry.get("s3_path", "")).strip().rstrip("/")
    cloud_format = str(entry.get("format", project.get("format", ""))).strip().casefold()
    if cloud_format != "copc":
        return None
    if not cloud_path.casefold().endswith((".las", ".laz")):
        cloud_path = f"{cloud_path}/{COPC_OBJECT_NAME}"
    try:
        header = _read_s3_body(s3_client.get_object(Bucket=bucket_name, Key=cloud_path, Range="bytes=0-374"))
        if len(header) < 104 or header[:4] != b"LASF":
            raise ValueError("ungültiger LAS/COPC-Header")
        point_data_offset = struct.unpack_from("<I", header, 96)[0]
        if not 0 < point_data_offset <= 1024 * 1024:
            raise ValueError("ungültiger oder zu großer VLR-Bereich")
        records = _read_s3_body(
            s3_client.get_object(Bucket=bucket_name, Key=cloud_path, Range=f"bytes=0-{point_data_offset - 1}")
        )
    except Exception as error:
        raise RuntimeError(f"COPC-CRS konnte nicht sicher gelesen werden: {cloud_path}: {error}") from error
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="dronautix-crs-", suffix=".copc.laz", delete=False) as temporary:
            temporary.write(records)
            temp_path = temporary.name
        crs_info = detect_las_crs(temp_path)
    except Exception as error:
        raise RuntimeError(f"COPC-CRS konnte nicht sicher gelesen werden: {cloud_path}: {error}") from error
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
    if not crs_info:
        raise ValueError(f"COPC-CRS enthält keine technische Referenz: {cloud_path}")
    return crs_info


def _resolve_project_model_crs(project: dict[str, Any], s3_client, *, bucket_name: str):
    """Return a complete CRS and an explicit repair plan for legacy metadata."""

    entries = _cloud_entries(project)
    labels = _cloud_labels(entries)
    candidates: list[tuple[str, dict[str, Any]]] = []
    project_index_crs = extract_pointcloud_crs_metadata(project)
    if (complete := _complete_crs_info(project_index_crs)) is not None:
        candidates.append(("Projektindex", complete))
    indexed_crs = [extract_pointcloud_crs_metadata(entry) for entry in entries]
    complete_index_crs = [_complete_crs_info(source) for source in indexed_crs]
    if _complete_crs_info(project_index_crs) is not None and all(complete_index_crs):
        common_index_crs = _complete_crs_info(project_index_crs)
        if all(
            source.get("value") == common_index_crs.get("value")
            and source.get("vertical_crs") == common_index_crs.get("vertical_crs")
            for source in complete_index_crs
        ):
            return common_index_crs, None
    entry_sources: list[tuple[int, list[dict[str, Any]]]] = []
    for index, entry in enumerate(entries):
        sources = [indexed_crs[index]]
        for _key, document, _raw, _cloudjs, _headers in _s3_potree_documents(s3_client, bucket_name, entry, project):
            sources.append(detect_crs_from_metadata_dict(document))
        sources.append(_s3_copc_crs(s3_client, bucket_name, entry, project))
        valid_sources = [source for source in sources if source]
        for source in valid_sources:
            if (complete := _complete_crs_info(source)) is not None:
                candidates.append((labels[index], complete))
        entry_sources.append((index, valid_sources))
    if not candidates:
        raise ValueError("Projekt-CRS und Höhenbezug fehlen oder sind nicht eindeutig bestimmbar.")
    common = candidates[0][1]
    if any(
        candidate.get("value") != common.get("value") or candidate.get("vertical_crs") != common.get("vertical_crs")
        for _label, candidate in candidates[1:]
    ):
        raise ValueError("Projekt-/Punktwolken-CRS sind widersprüchlich; keine automatische CRS-Reparatur durchgeführt.")
    if _crs_conflicts_with(project_index_crs, common):
        raise ValueError("Projektindex-CRS widerspricht den vorhandenen Punktwolken-Metadaten.")
    missing_children: list[int] = []
    for index, sources in entry_sources:
        if any(_crs_conflicts_with(source, common) for source in sources):
            raise ValueError("Punktwolken-CRS widersprechen den vorhandenen Metadaten; keine automatische CRS-Reparatur durchgeführt.")
        if _complete_crs_info(indexed_crs[index]) is None:
            missing_children.append(index)
    repair_project = _complete_crs_info(project_index_crs) is None
    if not repair_project and not missing_children:
        return common, None
    donor = next((label for label, candidate in candidates if label != "Projektindex" and candidate == common), candidates[0][0])
    targets = ["Projektindex"] if repair_project else []
    targets.extend(labels[index] for index in missing_children)
    return common, {
        "crs_info": common,
        "repair_project": repair_project,
        "children": tuple(missing_children),
        "message": (
            f"CRS-Reparatur erforderlich: {donor} ({common['value']}, Vertikal {common['vertical_crs']}) -> "
            f"{', '.join(targets)}. Nur fehlende CRS-Felder werden ergänzt und im Projektverlauf dokumentiert."
        ),
    }


def _manual_crs_repair_plan(
    project: dict[str, Any],
    s3_client,
    *,
    bucket_name: str,
    crs_info: dict[str, Any],
    allow_conflicting_overwrite: bool,
):
    entries = _cloud_entries(project)
    labels = _cloud_labels(entries)
    sources = [extract_pointcloud_crs_metadata(project)]
    child_indices = []
    for index, entry in enumerate(entries):
        index_crs = extract_pointcloud_crs_metadata(entry)
        sources.append(index_crs)
        for _key, document, _raw, _cloudjs, _headers in _s3_potree_documents(s3_client, bucket_name, entry, project):
            sources.append(detect_crs_from_metadata_dict(document))
        sources.append(_s3_copc_crs(s3_client, bucket_name, entry, project))
        if _complete_crs_info(index_crs) is None or (allow_conflicting_overwrite and _crs_conflicts_with(index_crs, crs_info)):
            child_indices.append(index)
    if any(_crs_conflicts_with(source, crs_info) for source in sources if source) and not allow_conflicting_overwrite:
        raise ValueError("Vorhandene Projekt-/Punktwolken-CRS widersprechen dem manuellen Wert; Überschreiben ist nicht bestätigt.")
    repair_project = _complete_crs_info(extract_pointcloud_crs_metadata(project)) is None or allow_conflicting_overwrite
    if not repair_project and not child_indices:
        return None
    targets = ["Projektindex"] if repair_project else []
    targets.extend(labels[index] for index in child_indices)
    action = "überschrieben" if allow_conflicting_overwrite else "ergänzt"
    return {
        "crs_info": crs_info,
        "repair_project": repair_project,
        "children": tuple(child_indices),
        "message": (
            f"Manuelle CRS-Reparatur: ({crs_info['value']}, Vertikal {crs_info['vertical_crs']}) -> "
            f"{', '.join(targets)}. Bestehende CRS-Felder werden nur {action}; keine Modellgeometrie wird verändert."
        ),
    }


def _confirm_crs_repair(plan, callback, on_progress) -> bool:
    message = str(plan.get("message", "") or "")
    if on_progress is not None:
        on_progress(ProgressEvent(kind="warning", message=message, phase="validation"))
    try:
        return bool(callback and callback(message))
    except Exception:
        return False


def _crs_repair_cancelled_result(project_id: str, plan) -> ProjectOperationResult:
    return ProjectOperationResult(
        status="cancelled",
        project_id=project_id,
        warnings=(str(plan.get("message", "")),),
        message="Upload nicht gestartet: CRS-Reparatur wurde nicht bestätigt.",
    )


def _backfill_crs_fields(target: dict[str, Any], crs_info: dict[str, Any], *, overwrite: bool = False) -> None:
    pairs = {
        "crs": crs_info.get("value"), "projection": crs_info.get("projection"), "epsg": crs_info.get("epsg"),
        "crs_name": crs_info.get("crs_name"), "vertical_crs": crs_info.get("vertical_crs"),
        "vertical_epsg": crs_info.get("vertical_epsg"), "vertical_projection": crs_info.get("vertical_projection"),
        "vertical_name": crs_info.get("vertical_name"), "vertical_datum": crs_info.get("vertical_datum"),
    }
    for key, value in pairs.items():
        if value and (overwrite or not str(target.get(key, "")).strip()):
            target[key] = value
    existing = target.get("crs_info")
    crs_metadata = dict(existing) if isinstance(existing, dict) else {}
    for key, value in crs_info.items():
        if value and (overwrite or not str(crs_metadata.get(key, "")).strip()):
            crs_metadata[key] = value
    target["crs_info"] = crs_metadata
    srs = target.get("srs")
    if isinstance(srs, dict):
        srs = dict(srs)
        horizontal = str(crs_info.get("value", ""))
        vertical = str(crs_info.get("vertical_crs", ""))
        if horizontal.upper().startswith("EPSG:"):
            if overwrite or not str(srs.get("authority", "")).strip():
                srs["authority"] = "EPSG"
            if overwrite or not str(srs.get("horizontal", "")).strip():
                srs["horizontal"] = horizontal.split(":", 1)[1]
        if vertical.upper().startswith("EPSG:") and (overwrite or not str(srs.get("vertical", "")).strip()):
            srs["vertical"] = vertical.split(":", 1)[1]
        target["srs"] = srs


def _apply_crs_repair_plan(
    index_data: dict[str, Any], project_id: str, plan, *, overwrite: bool = False, timestamp: str = ""
) -> None:
    if not plan:
        return
    target_indices = set(plan["children"])
    crs_info = plan["crs_info"]
    def update(project):
        if plan["repair_project"]:
            _backfill_crs_fields(project, crs_info, overwrite=overwrite)
        pointclouds = project.get("pointclouds")
        if isinstance(pointclouds, list):
            for index, entry in enumerate(pointclouds):
                if isinstance(entry, dict) and index in target_indices:
                    _backfill_crs_fields(entry, crs_info, overwrite=overwrite)
        append_project_history(project, timestamp, "CRS-Metadaten wurden nach bestätigter Prüfung ergänzt.")
    if not update_project_in_index(index_data, project_id, update):
        raise ValueError(f"Projekt mit ID '{project_id}' wurde nicht gefunden.")


def _repair_s3_potree_crs_metadata(
    project,
    s3_client,
    *,
    bucket_name: str,
    crs_info,
    children: tuple[int, ...] = (),
    overwrite: bool = False,
):
    backups = []
    try:
        target_indices = set(children)
        for index, entry in enumerate(_cloud_entries(project)):
            if index not in target_indices:
                continue
            for key, document, raw, cloudjs, headers in _s3_potree_documents(s3_client, bucket_name, entry, project):
                existing_crs = detect_crs_from_metadata_dict(document)
                if _complete_crs_info(existing_crs) is not None and not _crs_conflicts_with(existing_crs, crs_info):
                    continue
                if _crs_conflicts_with(existing_crs, crs_info) and not overwrite:
                    raise ValueError("Potree-Metadaten widersprechen dem bestätigten CRS; keine CRS-Reparatur durchgeführt.")
                updated = copy.deepcopy(document)
                _backfill_crs_fields(updated, crs_info, overwrite=overwrite)
                payload = (
                    ("cloud.js = " + json.dumps(updated, indent=2, ensure_ascii=False) + ";").encode("utf-8")
                    if cloudjs else json.dumps(updated, indent=2, ensure_ascii=False).encode("utf-8")
                )
                if payload == raw:
                    continue
                _put_s3_metadata(s3_client, bucket_name, key, payload, headers)
                backups.append((key, raw, headers))
    except Exception as error:
        try:
            _restore_s3_metadata(s3_client, bucket_name, backups)
        except RuntimeError as rollback_error:
            raise RuntimeError(f"CRS-S3-Metadaten-Rollback unvollständig: {rollback_error}") from error
        raise
    return tuple(backups)


def _restore_s3_metadata(s3_client, bucket_name: str, backups) -> None:
    failures = []
    for key, raw, headers in reversed(tuple(backups)):
        try:
            _put_s3_metadata(s3_client, bucket_name, key, raw, headers)
        except Exception as error:
            failures.append(f"{key}: {error}")
    if failures:
        raise RuntimeError("; ".join(failures))


def _s3_object_headers(response) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    return {
        key: response[key]
        for key in ("ContentType", "CacheControl", "ContentDisposition", "ContentEncoding", "ContentLanguage", "Metadata")
        if response.get(key) is not None
    }


def _put_s3_metadata(s3_client, bucket_name: str, key: str, payload: bytes, headers: dict[str, Any]) -> None:
    arguments = dict(headers)
    arguments.setdefault("ContentType", "application/json")
    s3_client.put_object(
        Bucket=bucket_name,
        Key=key,
        Body=payload,
        **arguments,
    )


def _rollback_crs_repair_failure(
    save_index,
    index_data: dict[str, Any],
    s3_client,
    bucket_name: str,
    remote_backups,
    *,
    repair_attempted: bool,
) -> tuple[str, ...]:
    """Best-effort restore without re-entering any model operation."""

    failures = []
    if remote_backups:
        try:
            _restore_s3_metadata(s3_client, bucket_name, remote_backups)
        except RuntimeError as error:
            failures.append(f"S3-Metadaten: {error}")
    if repair_attempted:
        try:
            if not save_index(index_data):
                failures.append("projects_index: Speichern wurde abgelehnt")
        except Exception as error:
            failures.append(f"projects_index: {error}")
    return tuple(failures)


def _restore_index_data(index_data: dict[str, Any], snapshot: dict[str, Any]) -> None:
    index_data.clear()
    index_data.update(copy.deepcopy(snapshot))


__all__ = ["ProjectManagementService"]
