"""UI-free upload workflow orchestration for V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable
from uuid import uuid4

from .constants import BUCKET_NAME
from .contracts import (
    CancelCallback,
    ModelUploadInput,
    OperationCancelledError,
    PointcloudSource,
    ProgressCallback,
    ProgressEvent,
    UploadResult,
    make_cancel_guarded_progress,
)
from .local_conversion_service import ConverterRunner
from .glb_optimization_service import GLBOptimizationService
from .metadata_service import get_common_crs_info
from .metadata_service import write_potree_metadata_crs_for_sources
from .naming_service import build_project_paths
from .pointcloud_preparation_service import (
    PointcloudPreparationRequest,
    prepare_pointcloud_sources,
)
from .project_operations import build_new_project_upload, upload_new_project as upload_new_project_operation
from .project_repository import ProjectMetadataRepository
from .s3_service import delete_s3_objects

GLB_UPLOAD_STAGING_ROOT_NAME = "dronautix_glb_upload"
GLB_UPLOAD_RUN_TEMP_PREFIX = ".glb-upload-run-"


@dataclass(frozen=True)
class NewProjectUploadWorkflowRequest:
    source_paths: tuple[str, ...]
    kunde: str
    projekt: str
    converter_path: str = ""
    output_base_dir: str = ""
    overwrite: bool = False
    crs_info_by_source_path: dict[str, dict[str, Any]] | None = None
    model_inputs: tuple[ModelUploadInput, ...] = ()


def _default_project_id() -> str:
    return uuid4().hex[:8]


def _default_timestamp() -> str:
    return datetime.now().isoformat()


def _emit(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    if callback is not None:
        callback(event)


def get_glb_upload_staging_root() -> str:
    """Return the app-owned temporary root for generated GLB upload files."""

    return str(Path(tempfile.gettempdir()) / GLB_UPLOAD_STAGING_ROOT_NAME)


def create_glb_upload_run_staging_root(*, staging_root: str) -> str:
    """Create one app-owned staging root for a single GLB upload run."""

    root = Path(staging_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.mkdtemp(prefix=GLB_UPLOAD_RUN_TEMP_PREFIX, dir=root)


def cleanup_prepared_glb_staging_dirs(prepared_models, *, staging_root: str, on_progress: ProgressCallback | None = None) -> None:
    """Delete only the service-owned GLB staging folders, retrying once on Windows locks."""

    safe_root = Path(staging_root).resolve()
    for prepared_model in prepared_models:
        stage_dir = Path(str(getattr(prepared_model, "staging_dir", "") or "")).resolve()
        try:
            stage_dir.relative_to(safe_root)
        except ValueError:
            _emit(on_progress, ProgressEvent(kind="warning", message=f"[MODELL] Unsicheren Temp-Pfad nicht gelöscht: {stage_dir}"))
            continue
        if not stage_dir.name.startswith(".glb-upload-"):
            _emit(on_progress, ProgressEvent(kind="warning", message=f"[MODELL] Unbekannten Temp-Pfad nicht gelöscht: {stage_dir}"))
            continue
        for _attempt in range(2):
            try:
                shutil.rmtree(stage_dir)
                break
            except FileNotFoundError:
                break
            except OSError as error:
                cleanup_error = error
        else:
            _emit(on_progress, ProgressEvent(kind="warning", message=f"[MODELL] Temp-Cleanup fehlgeschlagen: {cleanup_error}"))
            continue
        _emit(on_progress, ProgressEvent(kind="log", message=f"[MODELL] Temporäre Dateien entfernt: {stage_dir.name}"))


def cleanup_glb_upload_run_staging_root(
    staging_run_root: str,
    *,
    staging_root: str,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Remove the per-run root, including a stage a failed preparer left behind."""

    safe_root = Path(staging_root).resolve()
    run_root = Path(staging_run_root).resolve()
    if run_root.parent != safe_root or not run_root.name.startswith(GLB_UPLOAD_RUN_TEMP_PREFIX):
        _emit(on_progress, ProgressEvent(kind="warning", message=f"[MODELL] Unsicheren Temp-Pfad nicht gelöscht: {run_root}"))
        return
    for _attempt in range(2):
        try:
            shutil.rmtree(run_root)
            break
        except FileNotFoundError:
            break
        except OSError as error:
            cleanup_error = error
    else:
        _emit(on_progress, ProgressEvent(kind="warning", message=f"[MODELL] Temp-Cleanup fehlgeschlagen: {cleanup_error}"))
        return
    _emit(on_progress, ProgressEvent(kind="log", message=f"[MODELL] Temporäre Dateien entfernt: {run_root.name}"))


@dataclass(frozen=True)
class UploadWorkflowService:
    repository: ProjectMetadataRepository
    s3_client: Any
    id_factory: Callable[[], str] = _default_project_id
    timestamp_factory: Callable[[], str] = _default_timestamp
    bucket_name: str | None = None
    glb_service: GLBOptimizationService | None = None

    @property
    def _bucket_name(self) -> str:
        return self.bucket_name or getattr(self.repository, "bucket_name", BUCKET_NAME)

    def upload_new_project(
        self,
        request: NewProjectUploadWorkflowRequest,
        on_progress: ProgressCallback | None = None,
        converter_runner: ConverterRunner | None = None,
        cancel_requested: CancelCallback | None = None,
    ):
        if not request.kunde.strip():
            raise ValueError("Kunde ist fuer den Upload erforderlich.")
        if not request.projekt.strip():
            raise ValueError("Projektname ist fuer den Upload erforderlich.")

        # Die Konvertierungsphase kennt keinen eigenen Abbruch-Parameter;
        # der Guard prueft bei jedem Progress-Event (Potree loggt laufend).
        guarded_progress = make_cancel_guarded_progress(on_progress, cancel_requested)
        prepared_models = ()
        staging_root = get_glb_upload_staging_root()
        staging_run_root = ""
        try:
            prepared_sources = prepare_pointcloud_sources(
                PointcloudPreparationRequest(
                    sources=tuple(request.source_paths),
                    converter_path=request.converter_path,
                    output_base_dir=request.output_base_dir,
                    overwrite=request.overwrite,
                ),
                on_progress=guarded_progress,
                converter_runner=converter_runner,
            )
            if cancel_requested is not None and cancel_requested():
                raise OperationCancelledError()
            prepared_sources = _attach_crs_info(
                prepared_sources,
                request.source_paths,
                request.crs_info_by_source_path,
            )
            write_potree_metadata_crs_for_sources(prepared_sources)

            project_id = self.id_factory()
            paths = build_project_paths(request.kunde, request.projekt, project_id)
            if request.model_inputs:
                staging_run_root = create_glb_upload_run_staging_root(staging_root=staging_root)
                project_crs_info = get_common_crs_info(source.crs_info for source in prepared_sources)
                prepared_models = (self.glb_service or GLBOptimizationService()).prepare_many(
                    request.model_inputs,
                    project_crs_info=project_crs_info,
                    staging_root=staging_run_root,
                    project_viewer_root=paths.project_viewer_root,
                    project_s3_prefix=paths.s3_prefix,
                    on_progress=guarded_progress,
                    cancel_requested=cancel_requested,
                )
            prepared_upload = build_new_project_upload(
                sources=prepared_sources,
                timestamp=self.timestamp_factory(),
                kunde=request.kunde,
                projekt=request.projekt,
                project_id=project_id,
                project_url=paths.project_url,
                project_viewer_root=paths.project_viewer_root,
                project_s3_prefix=paths.s3_prefix,
                models=prepared_models,
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
                cancel_requested=cancel_requested,
            )
        except OperationCancelledError:
            return UploadResult(status="cancelled", message="Upload abgebrochen.")
        finally:
            cleanup_prepared_glb_staging_dirs(
                prepared_models,
                staging_root=staging_root,
                on_progress=on_progress,
            )
            if staging_run_root:
                cleanup_glb_upload_run_staging_root(
                    staging_run_root,
                    staging_root=staging_root,
                    on_progress=on_progress,
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
    "GLB_UPLOAD_STAGING_ROOT_NAME",
    "GLB_UPLOAD_RUN_TEMP_PREFIX",
    "NewProjectUploadWorkflowRequest",
    "UploadWorkflowService",
    "cleanup_glb_upload_run_staging_root",
    "cleanup_prepared_glb_staging_dirs",
    "create_glb_upload_run_staging_root",
    "get_glb_upload_staging_root",
]
