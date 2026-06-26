"""UI-free upload workflow orchestration for V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from typing import Any, Callable
from uuid import uuid4

from .constants import BUCKET_NAME
from .contracts import PointcloudSource, ProgressCallback
from .local_conversion_service import ConverterRunner
from .metadata_service import write_potree_metadata_crs_for_sources
from .naming_service import build_project_paths
from .pointcloud_preparation_service import (
    PointcloudPreparationRequest,
    prepare_pointcloud_sources,
)
from .project_operations import build_new_project_upload, upload_new_project as upload_new_project_operation
from .project_repository import ProjectMetadataRepository
from .s3_service import delete_s3_objects


@dataclass(frozen=True)
class NewProjectUploadWorkflowRequest:
    source_paths: tuple[str, ...]
    kunde: str
    projekt: str
    converter_path: str = ""
    output_base_dir: str = ""
    overwrite: bool = False
    crs_info_by_source_path: dict[str, dict[str, Any]] | None = None


def _default_project_id() -> str:
    return uuid4().hex[:8]


def _default_timestamp() -> str:
    return datetime.now().isoformat()


@dataclass(frozen=True)
class UploadWorkflowService:
    repository: ProjectMetadataRepository
    s3_client: Any
    id_factory: Callable[[], str] = _default_project_id
    timestamp_factory: Callable[[], str] = _default_timestamp
    bucket_name: str | None = None

    @property
    def _bucket_name(self) -> str:
        return self.bucket_name or getattr(self.repository, "bucket_name", BUCKET_NAME)

    def upload_new_project(
        self,
        request: NewProjectUploadWorkflowRequest,
        on_progress: ProgressCallback | None = None,
        converter_runner: ConverterRunner | None = None,
    ):
        if not request.kunde.strip():
            raise ValueError("Kunde ist fuer den Upload erforderlich.")
        if not request.projekt.strip():
            raise ValueError("Projektname ist fuer den Upload erforderlich.")

        prepared_sources = prepare_pointcloud_sources(
            PointcloudPreparationRequest(
                sources=tuple(request.source_paths),
                converter_path=request.converter_path,
                output_base_dir=request.output_base_dir,
                overwrite=request.overwrite,
            ),
            on_progress=on_progress,
            converter_runner=converter_runner,
        )
        prepared_sources = _attach_crs_info(prepared_sources, request.source_paths, request.crs_info_by_source_path)
        write_potree_metadata_crs_for_sources(prepared_sources)

        project_id = self.id_factory()
        paths = build_project_paths(request.kunde, request.projekt, project_id)
        prepared_upload = build_new_project_upload(
            sources=prepared_sources,
            timestamp=self.timestamp_factory(),
            kunde=request.kunde,
            projekt=request.projekt,
            project_id=project_id,
            project_url=paths.project_url,
            project_viewer_root=paths.project_viewer_root,
            project_s3_prefix=paths.s3_prefix,
        )
        index_data = self.repository.load_projects_index()
        return upload_new_project_operation(
            s3_client=self.s3_client,
            index_data=index_data,
            prepared_upload=prepared_upload,
            save_index=self._save_projects_index,
            delete_keys=lambda keys: delete_s3_objects(self.s3_client, keys, bucket_name=self._bucket_name),
            on_progress=on_progress,
            bucket_name=self._bucket_name,
        )

    def _save_projects_index(self, index_data: dict[str, Any]) -> bool:
        result = self.repository.save_projects_index(index_data)
        return True if result is None else bool(result)


def _attach_crs_info(
    prepared_sources: tuple[PointcloudSource, ...],
    original_source_paths: tuple[str, ...],
    crs_info_by_source_path: dict[str, dict[str, Any]] | None,
) -> tuple[PointcloudSource, ...]:
    if not crs_info_by_source_path:
        return prepared_sources

    normalized_crs = {
        _normalize_path_key(path): crs_info
        for path, crs_info in crs_info_by_source_path.items()
        if isinstance(crs_info, dict)
    }
    updated_sources: list[PointcloudSource] = []
    for source, original_path in zip(prepared_sources, original_source_paths, strict=False):
        crs_info = normalized_crs.get(_normalize_path_key(original_path))
        if crs_info is None:
            updated_sources.append(source)
            continue
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


def _normalize_path_key(path: str) -> str:
    return os.path.abspath(str(path or "")).casefold()


__all__ = [
    "NewProjectUploadWorkflowRequest",
    "UploadWorkflowService",
]
