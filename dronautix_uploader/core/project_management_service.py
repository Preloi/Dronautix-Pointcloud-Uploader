"""UI-free project management service for active and disabled projects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from .constants import BUCKET_NAME, COPC_OBJECT_NAME
from .contracts import CancelCallback, DownloadResult, ProjectOperationResult
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
    apply_project_rename_metadata,
    delete_project as delete_project_operation,
    duplicate_project as duplicate_project_operation,
    download_project as download_project_operation,
    prepare_cloud_uploads,
    prepare_single_project_upload,
    rebase_prepared_cloud_upload,
    ProjectDownloadCancelledError,
    replace_project_pointclouds as replace_project_pointclouds_operation,
    replace_single_project_pointcloud as replace_single_project_pointcloud_operation,
)
from .project_repository import ProjectMetadataRepository
from .s3_service import collect_project_objects, delete_s3_objects


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


__all__ = ["ProjectManagementService"]
