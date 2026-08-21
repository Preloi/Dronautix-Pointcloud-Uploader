"""UI-free upload workflow orchestration for V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import shutil
import struct
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
MODEL_POINTCLOUD_BOUNDS_TOLERANCE_METERS = 1.0


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
        confirm_spatial_warning: Callable[[str], bool] | None = None,
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
                spatial_warning = build_model_pointcloud_spatial_warning(prepared_sources, prepared_models)
                if spatial_warning:
                    _emit(on_progress, ProgressEvent(kind="warning", message=spatial_warning, phase="validation"))
                    if confirm_spatial_warning is None or not confirm_spatial_warning(spatial_warning):
                        return UploadResult(
                            status="cancelled",
                            message="Upload nicht gestartet: 3D-Modelle liegen außerhalb der Punktwolke.",
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


def build_model_pointcloud_spatial_warning(prepared_sources, prepared_models) -> str:
    """Describe models whose project bounds do not touch any pointcloud bounds."""

    pointcloud_bounds = tuple(
        bounds
        for source in prepared_sources
        if (bounds := _read_pointcloud_bounds(Path(source.source_path))) is not None
    )
    return build_model_pointcloud_spatial_warning_for_bounds(pointcloud_bounds, prepared_models)


def build_model_pointcloud_spatial_warning_for_bounds(pointcloud_bounds, prepared_models) -> str:
    """Describe models whose project bounds do not touch any supplied cloud bounds."""

    pointcloud_bounds = tuple(bounds for bounds in pointcloud_bounds if _validated_bounds(bounds) is not None)
    if not pointcloud_bounds:
        return ""

    distant_models: list[tuple[str, float]] = []
    for model in prepared_models:
        model_bounds = _validated_bounds((model.bounds_min, model.bounds_max))
        if model_bounds is None:
            continue
        distance = min(_horizontal_bounds_distance(model_bounds, bounds) for bounds in pointcloud_bounds)
        if distance > MODEL_POINTCLOUD_BOUNDS_TOLERANCE_METERS:
            distant_models.append((str(model.name or model.slug or "3D-Modell"), distance))
    if not distant_models:
        return ""

    details = "\n".join(f"• {name}: nächster Abstand ca. {_format_distance(distance)}" for name, distance in distant_models)
    return (
        "Die folgenden 3D-Modelle liegen vollständig außerhalb aller ausgewählten Punktwolken:\n"
        f"{details}\n\n"
        "Prüfe, ob die passenden Punktwolken und GLBs ausgewählt wurden."
    )


def _read_pointcloud_bounds(path: Path):
    if path.is_dir():
        for filename in ("metadata.json", "cloud.js"):
            candidate = path / filename
            if not candidate.is_file():
                continue
            try:
                text = candidate.read_text(encoding="utf-8").strip()
                if filename == "cloud.js" and text.startswith("cloud.js"):
                    text = text[len("cloud.js") :].strip().lstrip("=").strip().rstrip(";").strip()
                document = json.loads(text)
                bounds = _bounds_from_potree_document(document)
                if bounds is not None:
                    return bounds
            except (OSError, ValueError, TypeError):
                continue
        return None
    try:
        with path.open("rb") as stream:
            header = stream.read(227)
    except OSError:
        return None
    if len(header) < 227 or header[:4] != b"LASF":
        return None
    max_x, min_x, max_y, min_y, max_z, min_z = struct.unpack_from("<6d", header, 179)
    return _validated_bounds(((min_x, min_y, min_z), (max_x, max_y, max_z)))


def _bounds_from_potree_document(document):
    if not isinstance(document, dict):
        return None
    for key in ("tightBoundingBox", "boundingBox"):
        value = document.get(key)
        if not isinstance(value, dict):
            continue
        minimum = value.get("min")
        maximum = value.get("max")
        if isinstance(minimum, dict) and isinstance(maximum, dict):
            minimum = tuple(minimum.get(axis) for axis in "xyz")
            maximum = tuple(maximum.get(axis) for axis in "xyz")
        if minimum is None or maximum is None:
            minimum = tuple(value.get(name) for name in ("lx", "ly", "lz"))
            maximum = tuple(value.get(name) for name in ("ux", "uy", "uz"))
        bounds = _validated_bounds((minimum, maximum))
        if bounds is not None:
            return bounds
    return None


def _validated_bounds(bounds):
    try:
        minimum, maximum = tuple(tuple(float(value) for value in point) for point in bounds)
    except (TypeError, ValueError):
        return None
    if len(minimum) != 3 or len(maximum) != 3:
        return None
    if not all(math.isfinite(value) for value in (*minimum, *maximum)):
        return None
    if any(lower > upper for lower, upper in zip(minimum, maximum, strict=True)):
        return None
    return minimum, maximum


def _horizontal_bounds_distance(first, second) -> float:
    gaps = (
        max(second[0][axis] - first[1][axis], first[0][axis] - second[1][axis], 0.0)
        for axis in range(2)
    )
    return math.sqrt(sum(gap * gap for gap in gaps))


def _format_distance(distance: float) -> str:
    if distance >= 1000.0:
        return f"{distance / 1000.0:.1f} km".replace(".", ",")
    return f"{distance:.0f} m"


__all__ = [
    "GLB_UPLOAD_STAGING_ROOT_NAME",
    "GLB_UPLOAD_RUN_TEMP_PREFIX",
    "MODEL_POINTCLOUD_BOUNDS_TOLERANCE_METERS",
    "NewProjectUploadWorkflowRequest",
    "UploadWorkflowService",
    "build_model_pointcloud_spatial_warning",
    "build_model_pointcloud_spatial_warning_for_bounds",
    "cleanup_glb_upload_run_staging_root",
    "cleanup_prepared_glb_staging_dirs",
    "create_glb_upload_run_staging_root",
    "get_glb_upload_staging_root",
]
