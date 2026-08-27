"""UI-free project workflow helpers for V2 upload and replacement operations."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from .constants import BUCKET_NAME, COPC_OBJECT_NAME, S3_INDEX_CACHE_CONTROL
from .contracts import (
    CancelCallback,
    OperationCancelledError,
    PointcloudSource,
    PreparedModelUpload,
    ProgressCallback,
    ProgressEvent,
    ProjectOperationResult,
    UploadedKeyLedger,
    UploadResult,
)
from .crs_service import extract_pointcloud_crs_metadata
from .metadata_service import apply_crs_metadata, create_pointcloud_index_entry, get_common_crs_info
from .naming_service import get_pointcloud_display_name, make_unique_slug, sanitize_folder_name
from .project_index_service import append_project_history, apply_common_crs_or_clear, update_project_in_index
from .project_index_service import remove_project_from_index
from .s3_service import (
    UploadFile,
    collect_project_object_entries,
    collect_project_objects,
    collect_upload_files,
    copy_project_objects,
    delete_s3_objects,
    DownloadCancelledError,
    download_project_objects,
    upload_files_to_s3,
    verify_uploaded_model_files,
)


def _emit(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    if callback:
        callback(event)


@dataclass(frozen=True)
class PreparedCloudUpload:
    name: str
    slug: str
    input_format: str
    viewer_path: str
    s3_path: str
    s3_prefix: str
    files_to_upload: tuple[UploadFile, ...]
    crs_info: dict[str, Any] | None = None

    @property
    def index_entry(self) -> dict[str, Any]:
        return create_pointcloud_index_entry(
            self.name,
            self.input_format,
            self.viewer_path,
            self.s3_path,
            self.crs_info,
        )


@dataclass(frozen=True)
class PreparedProjectUpload:
    project_metadata: dict[str, Any]
    files_to_upload: tuple[UploadFile, ...]


class ProjectDownloadCancelledError(RuntimeError):
    """Raised when project download is cancelled after the target directory is known."""

    def __init__(self, download_dir: str, downloaded_files: tuple[str, ...]) -> None:
        super().__init__("Download wurde abgebrochen.")
        self.download_dir = download_dir
        self.downloaded_files = downloaded_files


def rebase_prepared_cloud_upload(
    cloud: PreparedCloudUpload,
    viewer_root: str,
    s3_root: str,
    slug: str | None = None,
) -> PreparedCloudUpload:
    """Move an upload plan to a fresh immutable data prefix."""

    target_slug = cloud.slug if slug is None else slug
    viewer_prefix = f"{viewer_root}/{target_slug}" if target_slug else viewer_root
    s3_prefix = f"{s3_root}/{target_slug}" if target_slug else s3_root
    files_to_upload = tuple(
        (
            local_path,
            f"{s3_prefix}/{s3_key[len(cloud.s3_prefix):].lstrip('/')}",
        )
        for local_path, s3_key in cloud.files_to_upload
    )
    if cloud.input_format == "copc":
        viewer_path = f"{viewer_prefix}/{COPC_OBJECT_NAME}"
        s3_path = f"{s3_prefix}/{COPC_OBJECT_NAME}"
    else:
        viewer_path = viewer_prefix
        s3_path = s3_prefix

    return PreparedCloudUpload(
        name=cloud.name,
        slug=target_slug,
        input_format=cloud.input_format,
        viewer_path=viewer_path,
        s3_path=s3_path,
        s3_prefix=s3_prefix,
        files_to_upload=files_to_upload,
        crs_info=cloud.crs_info,
    )


def prepare_cloud_uploads(
    sources: tuple[PointcloudSource, ...] | list[PointcloudSource],
    project_viewer_root: str,
    project_s3_prefix: str,
) -> tuple[PreparedCloudUpload, ...]:
    """Create S3/index upload plans for already prepared COPC or Potree sources."""

    prepared: list[PreparedCloudUpload] = []
    used_slugs: set[str] = set()
    for source in sources:
        name = source.name or get_pointcloud_display_name(source.source_path)
        slug = source.slug or make_unique_slug(name, used_slugs)
        input_format = source.input_format
        if input_format not in {"copc", "potree"}:
            raise ValueError(f"Nicht unterstütztes Punktwolkenformat: {input_format}")

        cloud_viewer_path = f"{project_viewer_root}/{slug}"
        cloud_s3_prefix = f"{project_s3_prefix}/{slug}"
        if input_format == "copc":
            files_to_upload = collect_upload_files(
                "copc",
                cloud_s3_prefix,
                source_file=source.source_path,
            )
            viewer_path = f"{cloud_viewer_path}/{COPC_OBJECT_NAME}"
            s3_path = f"{cloud_s3_prefix}/{COPC_OBJECT_NAME}"
        else:
            files_to_upload = collect_upload_files(
                "potree",
                cloud_s3_prefix,
                output_dir=source.source_path,
            )
            viewer_path = cloud_viewer_path
            s3_path = cloud_s3_prefix

        prepared.append(
            PreparedCloudUpload(
                name=name,
                slug=slug,
                input_format=input_format,
                viewer_path=viewer_path,
                s3_path=s3_path,
                s3_prefix=cloud_s3_prefix,
                files_to_upload=tuple(files_to_upload),
                crs_info=source.crs_info,
            )
        )

    return tuple(prepared)


def prepare_single_project_upload(
    source: PointcloudSource,
    project_viewer_root: str,
    project_s3_prefix: str,
) -> PreparedCloudUpload:
    """Create the legacy single-cloud upload plan without a child slug."""

    input_format = source.input_format
    if input_format not in {"copc", "potree"}:
        raise ValueError(f"Nicht unterstütztes Punktwolkenformat: {input_format}")

    name = source.name or get_pointcloud_display_name(source.source_path)
    if input_format == "copc":
        files_to_upload = collect_upload_files("copc", project_s3_prefix, source_file=source.source_path)
        viewer_path = f"{project_viewer_root}/{COPC_OBJECT_NAME}"
        s3_path = f"{project_s3_prefix}/{COPC_OBJECT_NAME}"
    else:
        files_to_upload = collect_upload_files("potree", project_s3_prefix, output_dir=source.source_path)
        viewer_path = project_viewer_root
        s3_path = project_s3_prefix

    return PreparedCloudUpload(
        name=name,
        slug="",
        input_format=input_format,
        viewer_path=viewer_path,
        s3_path=s3_path,
        s3_prefix=project_s3_prefix,
        files_to_upload=tuple(files_to_upload),
        crs_info=source.crs_info,
    )


def build_single_project_metadata(
    *,
    timestamp: str,
    kunde: str,
    projekt: str,
    project_id: str,
    project_url: str,
    prepared_cloud: PreparedCloudUpload,
) -> dict[str, Any]:
    metadata = {
        "datum": timestamp,
        "kunde": kunde,
        "id": project_id,
        "projekt": projekt,
        "format": prepared_cloud.input_format,
        "link": project_url,
        "viewer_path": prepared_cloud.viewer_path,
        "s3_path": prepared_cloud.s3_prefix,
    }
    apply_crs_metadata(metadata, prepared_cloud.crs_info)
    return metadata


def build_new_project_upload(
    *,
    sources: tuple[PointcloudSource, ...] | list[PointcloudSource],
    timestamp: str,
    kunde: str,
    projekt: str,
    project_id: str,
    project_url: str,
    project_viewer_root: str,
    project_s3_prefix: str,
    models: tuple[PreparedModelUpload, ...] | list[PreparedModelUpload] = (),
) -> PreparedProjectUpload:
    """Build metadata and upload files for a new single- or multi-cloud project."""

    source_tuple = tuple(sources)
    model_tuple = tuple(models)
    if not source_tuple:
        raise ValueError("Bitte mindestens eine Punktwolke auswählen.")

    if len(source_tuple) == 1 and not model_tuple:
        prepared_cloud = prepare_single_project_upload(
            source_tuple[0],
            project_viewer_root,
            project_s3_prefix,
        )
        return PreparedProjectUpload(
            project_metadata=build_single_project_metadata(
                timestamp=timestamp,
                kunde=kunde,
                projekt=projekt,
                project_id=project_id,
                project_url=project_url,
                prepared_cloud=prepared_cloud,
            ),
            files_to_upload=prepared_cloud.files_to_upload,
        )

    prepared_clouds = prepare_cloud_uploads(source_tuple, project_viewer_root, project_s3_prefix)
    pointcloud_entries = [cloud.index_entry for cloud in prepared_clouds]
    project_metadata = build_multi_project_metadata(
        project={
            "datum": timestamp,
            "kunde": kunde,
            "id": project_id,
            "projekt": projekt,
            "link": project_url,
        },
        base_viewer_path=project_viewer_root,
        s3_prefix=project_s3_prefix,
        pointcloud_entries=pointcloud_entries,
    )
    if model_tuple:
        model_entries = []
        model_files: list[UploadFile] = []
        model_ids: set[str] = set()
        for prepared_model in model_tuple:
            entry = prepared_model.index_entry
            if entry is None:
                raise ValueError("Vorbereitetes GLB-Modell hat keinen Zielpfad.")
            from .glb_optimization_service import build_model_index_entry

            expected_entry = build_model_index_entry(
                prepared_model,
                project_viewer_root=project_viewer_root,
                project_s3_prefix=project_s3_prefix,
            )
            if entry != expected_entry:
                raise ValueError(
                    "GLB-Upload abgebrochen: Modellpfad und data_version stimmen nicht ueberein. "
                    "Es wurden keine S3-Daten geaendert."
                )
            if entry.id in model_ids:
                raise ValueError(f"Doppelte Modell-ID: {entry.id}")
            model_ids.add(entry.id)
            model_entries.append(entry.as_dict())
            model_version_prefix = entry.s3_path.rstrip("/")
            model_files.extend(
                (
                    (prepared_model.scene_path, f"{model_version_prefix}/scene.glb"),
                    (prepared_model.manifest_path, f"{model_version_prefix}/model.json"),
                )
            )
        project_metadata["models"] = model_entries
    else:
        model_files = []
    return PreparedProjectUpload(
        project_metadata=project_metadata,
        files_to_upload=tuple(
            file_to_upload
            for cloud in prepared_clouds
            for file_to_upload in cloud.files_to_upload
        ) + tuple(model_files),
    )


def build_multi_project_metadata(
    project: dict[str, Any],
    base_viewer_path: str,
    s3_prefix: str,
    pointcloud_entries: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Return updated project metadata for a full multi-cloud replacement."""

    updated_project = dict(project)
    updated_project.update(
        {
            "format": "multi",
            "viewer_path": base_viewer_path,
            "s3_path": s3_prefix,
            "pointcloud_count": len(pointcloud_entries),
            "pointclouds": [dict(entry) for entry in pointcloud_entries],
        }
    )
    common_crs = get_common_crs_info(
        entry.get("crs_info") for entry in updated_project.get("pointclouds", [])
    )
    apply_common_crs_or_clear(updated_project, common_crs, _apply_crs_to_project)
    return updated_project


def _apply_crs_to_project(project: dict[str, Any], crs_info: dict[str, Any]) -> None:
    from .metadata_service import apply_crs_metadata

    apply_crs_metadata(project, crs_info)


def collect_upload_file_keys(prepared_clouds: tuple[PreparedCloudUpload, ...]) -> tuple[str, ...]:
    return tuple(s3_key for cloud in prepared_clouds for _local_path, s3_key in cloud.files_to_upload)


def compute_orphaned_keys(existing_keys, replacement_keys) -> tuple[str, ...]:
    replacement_key_set = set(replacement_keys)
    return tuple(sorted(key for key in existing_keys if key not in replacement_key_set))


def _normalize_s3_path(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").strip("/")


def exclude_model_object_keys(
    object_keys: tuple[str, ...] | list[str],
    project_s3_prefix: str,
) -> tuple[str, ...]:
    """Keep project-model objects out of pointcloud-only cleanup operations."""

    normalized_root = _normalize_s3_path(project_s3_prefix)
    if not normalized_root:
        return tuple(object_keys)
    models_prefix = f"{normalized_root}/models/"
    return tuple(
        key
        for key in object_keys
        if not _normalize_s3_path(key).startswith(models_prefix)
    )


def _pointcloud_matches_s3_path(pointcloud: dict[str, Any], target_s3_path: str) -> bool:
    cloud_path = _normalize_s3_path(str(pointcloud.get("s3_path", "")))
    target_path = _normalize_s3_path(target_s3_path)
    if not cloud_path or not target_path:
        return False
    return cloud_path == target_path


def _project_matches_s3_path(project: dict[str, Any], target_s3_path: str) -> bool:
    project_path = _normalize_s3_path(str(project.get("s3_path", "")))
    target_path = _normalize_s3_path(target_s3_path)
    if not project_path or not target_path:
        return False
    return project_path == target_path


def _restore_index(index_data: dict[str, Any], snapshot: dict[str, Any]) -> None:
    index_data.clear()
    index_data.update(snapshot)


def _safe_child_path(path: str, root: str) -> bool:
    normalized_path = _normalize_s3_path(path)
    normalized_root = _normalize_s3_path(root)
    if not normalized_path or not normalized_root or not normalized_path.startswith(f"{normalized_root}/"):
        return False
    return all(part not in {"", ".", ".."} for part in normalized_path.split("/"))


def _pointcloud_storage_boundary(
    pointcloud: dict[str, Any],
    project_viewer_root: str,
    project_s3_prefix: str,
) -> tuple[str, str]:
    """Return the child S3 boundary as ``(kind, path)`` after strict validation."""

    input_format = str(pointcloud.get("format", "")).strip().lower()
    s3_path = _normalize_s3_path(str(pointcloud.get("s3_path", "")))
    viewer_path = _normalize_s3_path(str(pointcloud.get("viewer_path", "")))
    if not _safe_child_path(s3_path, project_s3_prefix) or not _safe_child_path(viewer_path, project_viewer_root):
        raise ValueError("Punktwolkenpfad liegt nicht innerhalb des Multi-Projekts.")
    if input_format == "copc":
        suffix = f"/{COPC_OBJECT_NAME}"
        if not s3_path.endswith(suffix) or not viewer_path.endswith(suffix):
            raise ValueError("COPC-Punktwolken muessen auf die exakte COPC-Datei zeigen.")
        return "exact", s3_path
    if input_format == "potree":
        return "prefix", s3_path
    raise ValueError("Multi-Projekt enthaelt ein nicht unterstuetztes Punktwolkenformat.")


def _pointcloud_slug(pointcloud: dict[str, Any], project_viewer_root: str, project_s3_prefix: str) -> str:
    kind, s3_path = _pointcloud_storage_boundary(pointcloud, project_viewer_root, project_s3_prefix)
    parent = s3_path[: -len(f"/{COPC_OBJECT_NAME}")] if kind == "exact" else s3_path
    return parent.rsplit("/", 1)[-1]


def validate_explicit_multi_project(
    project: dict[str, Any],
    project_viewer_root: str,
    project_s3_prefix: str,
) -> tuple[dict[str, Any], ...]:
    """Validate a multi-only edit target without widening any child S3 boundary."""

    if str(project.get("format", "")).strip().lower() != "multi":
        raise ValueError("Punktwolken koennen nur in expliziten Multi-Projekten verwaltet werden.")
    pointclouds = project.get("pointclouds")
    if not isinstance(pointclouds, list) or not pointclouds:
        raise ValueError("Multi-Projekt enthaelt keine verwaltbaren Punktwolken.")

    seen_paths: set[str] = set()
    seen_slugs: set[str] = set()
    validated: list[dict[str, Any]] = []
    for pointcloud in pointclouds:
        if not isinstance(pointcloud, dict):
            raise ValueError("Multi-Projekt enthaelt einen ungueltigen Punktwolken-Eintrag.")
        _kind, s3_path = _pointcloud_storage_boundary(pointcloud, project_viewer_root, project_s3_prefix)
        slug = _pointcloud_slug(pointcloud, project_viewer_root, project_s3_prefix)
        if s3_path in seen_paths or slug in seen_slugs:
            raise ValueError("Multi-Projekt enthaelt keine eindeutigen Punktwolkenpfade.")
        seen_paths.add(s3_path)
        seen_slugs.add(slug)
        validated.append(pointcloud)
    return tuple(validated)


def resolve_unique_multi_project_child(
    project: dict[str, Any],
    target_s3_path: str,
    project_viewer_root: str,
    project_s3_prefix: str,
) -> dict[str, Any]:
    """Return exactly one child selected by its persisted S3 path."""

    target_path = _normalize_s3_path(target_s3_path)
    pointclouds = validate_explicit_multi_project(project, project_viewer_root, project_s3_prefix)
    matches = [
        pointcloud
        for pointcloud in pointclouds
        if _normalize_s3_path(str(pointcloud.get("s3_path", ""))) == target_path
    ]
    if len(matches) != 1:
        raise ValueError(f"Punktwolke mit S3-Pfad '{target_s3_path}' wurde nicht eindeutig gefunden.")
    return matches[0]


def pointcloud_object_list_prefix(
    pointcloud: dict[str, Any],
    project_viewer_root: str,
    project_s3_prefix: str,
) -> str:
    """Return the narrowest safe S3 ListObjects prefix for one child cloud."""

    kind, s3_path = _pointcloud_storage_boundary(pointcloud, project_viewer_root, project_s3_prefix)
    return s3_path if kind == "exact" else f"{s3_path}/"


def filter_pointcloud_object_keys(
    pointcloud: dict[str, Any],
    object_keys: tuple[str, ...] | list[str],
    project_viewer_root: str,
    project_s3_prefix: str,
) -> tuple[str, ...]:
    """Keep only exact COPC or directory-bound Potree child keys for cleanup."""

    kind, s3_path = _pointcloud_storage_boundary(pointcloud, project_viewer_root, project_s3_prefix)
    safe_prefix = f"{s3_path}/"
    return tuple(
        key
        for key in object_keys
        if (kind == "exact" and _normalize_s3_path(key) == s3_path)
        or (kind == "prefix" and _normalize_s3_path(key).startswith(safe_prefix))
    )


def _validate_new_multi_clouds(
    prepared_clouds: tuple[PreparedCloudUpload, ...],
    existing_clouds: tuple[dict[str, Any], ...],
    project_viewer_root: str,
    project_s3_prefix: str,
) -> None:
    if not prepared_clouds:
        raise ValueError("Bitte mindestens eine Punktwolke hinzufuegen.")

    existing_slugs = {
        _pointcloud_slug(pointcloud, project_viewer_root, project_s3_prefix)
        for pointcloud in existing_clouds
    }
    new_slugs: set[str] = set()
    version_s3_prefix = f"{_normalize_s3_path(project_s3_prefix)}/versions/"
    version_viewer_prefix = f"{_normalize_s3_path(project_viewer_root)}/versions/"
    for cloud in prepared_clouds:
        slug = str(cloud.slug or "").strip()
        if not slug or slug != sanitize_folder_name(slug) or slug in existing_slugs or slug in new_slugs:
            raise ValueError("Punktwolken-Slug kollidiert mit einer vorhandenen Punktwolke.")
        entry = cloud.index_entry
        _kind, s3_path = _pointcloud_storage_boundary(entry, project_viewer_root, project_s3_prefix)
        viewer_path = _normalize_s3_path(str(entry.get("viewer_path", "")))
        if not s3_path.startswith(version_s3_prefix) or not viewer_path.startswith(version_viewer_prefix):
            raise ValueError("Neue Punktwolken muessen in einem unveraenderlichen Datenstand abgelegt werden.")
        expected_parent = s3_path[: -len(f"/{COPC_OBJECT_NAME}")] if cloud.input_format == "copc" else s3_path
        if expected_parent.rsplit("/", 1)[-1] != slug or _normalize_s3_path(cloud.s3_prefix) != expected_parent:
            raise ValueError("Punktwolken-Slug und Zielpfad stimmen nicht ueberein.")
        if not cloud.files_to_upload:
            raise ValueError("Keine Dateien zum Hochladen fuer die Punktwolke gefunden.")
        for _local_path, key in cloud.files_to_upload:
            normalized_key = _normalize_s3_path(key)
            if (cloud.input_format == "copc" and normalized_key != s3_path) or (
                cloud.input_format == "potree" and not normalized_key.startswith(f"{s3_path}/")
            ):
                raise ValueError("Punktwolken-Upload wuerde ausserhalb des Child-Pfads schreiben.")
        new_slugs.add(slug)


def _replace_multi_project_pointclouds(
    project: dict[str, Any],
    pointclouds: list[dict[str, Any]],
) -> None:
    project["pointclouds"] = [copy.deepcopy(pointcloud) for pointcloud in pointclouds]
    project["pointcloud_count"] = len(pointclouds)
    common_crs = get_common_crs_info(
        _pointcloud_crs_metadata(pointcloud) for pointcloud in project["pointclouds"]
    )
    apply_common_crs_or_clear(project, common_crs, _apply_crs_to_project)


def _pointcloud_crs_metadata(pointcloud: dict[str, Any]) -> dict[str, Any] | None:
    crs_info = extract_pointcloud_crs_metadata(pointcloud)
    if crs_info:
        return crs_info
    legacy_pointcloud = dict(pointcloud)
    legacy_pointcloud.pop("crs_info", None)
    return extract_pointcloud_crs_metadata(legacy_pointcloud)


def _project_from_snapshot(index_data: dict[str, Any], project_id: str) -> dict[str, Any]:
    project_id = str(project_id or "").strip()
    for key in ("projects", "disabled_projects"):
        for project in index_data.get(key, []):
            if isinstance(project, dict) and str(project.get("id", "")).strip() == project_id:
                return project
    raise ValueError(f"Projekt mit ID '{project_id}' wurde nicht gefunden.")


def add_project_pointclouds(
    *,
    s3_client,
    index_data: dict[str, Any],
    project_id: str,
    project_viewer_root: str,
    project_s3_prefix: str,
    prepared_clouds: tuple[PreparedCloudUpload, ...] | list[PreparedCloudUpload],
    save_index: Callable[[dict[str, Any]], bool],
    delete_keys: Callable[[tuple[str, ...]], None],
    on_progress: ProgressCallback | None = None,
    bucket_name: str = BUCKET_NAME,
    timestamp: str = "",
) -> ProjectOperationResult:
    """Append child clouds to an explicit multi-project without changing project identity."""

    snapshot = copy.deepcopy(index_data)
    original_project = _project_from_snapshot(snapshot, project_id)
    existing_clouds = validate_explicit_multi_project(
        original_project,
        project_viewer_root,
        project_s3_prefix,
    )
    additions = tuple(prepared_clouds)
    _validate_new_multi_clouds(additions, existing_clouds, project_viewer_root, project_s3_prefix)
    ledger = UploadedKeyLedger()

    try:
        upload_files_to_s3(
            s3_client,
            [file_to_upload for cloud in additions for file_to_upload in cloud.files_to_upload],
            bucket_name=bucket_name,
            on_progress=on_progress,
            ledger=ledger,
        )

        def update_project(project: dict[str, Any]) -> None:
            original = _project_from_snapshot(snapshot, project_id)
            current_clouds = validate_explicit_multi_project(
                original,
                project_viewer_root,
                project_s3_prefix,
            )
            _replace_multi_project_pointclouds(
                project,
                [*current_clouds, *(cloud.index_entry for cloud in additions)],
            )
            append_project_history(project, timestamp, f"{len(additions)} Punktwolke(n) wurden hinzugefuegt.")

        if not update_project_in_index(index_data, project_id, update_project):
            raise RuntimeError("Projekt konnte im Index nicht gefunden werden.")
        if not save_index(index_data):
            raise RuntimeError("Projekt-Index konnte nicht gespeichert werden.")
    except Exception as operation_error:
        _restore_index(index_data, snapshot)
        if ledger.uploaded_keys:
            try:
                delete_keys(ledger.as_tuple())
            except Exception as cleanup_error:
                orphaned_keys = ", ".join(ledger.as_tuple())
                raise RuntimeError(
                    f"Punktwolken konnten nicht hinzugefuegt werden: {operation_error}. "
                    f"Upload-Cleanup fehlgeschlagen ({cleanup_error}); verwaiste S3-Keys: {orphaned_keys}"
                ) from operation_error
        raise

    return ProjectOperationResult(
        status="success",
        project_id=project_id,
        uploaded_keys=ledger.as_tuple(),
        message="Punktwolke(n) wurden hinzugefuegt.",
    )


def remove_project_pointcloud(
    *,
    index_data: dict[str, Any],
    project_id: str,
    project_viewer_root: str,
    project_s3_prefix: str,
    target_pointcloud_s3_path: str,
    existing_target_keys: tuple[str, ...] | list[str],
    save_index: Callable[[dict[str, Any]], bool],
    delete_keys: Callable[[tuple[str, ...]], None],
    timestamp: str = "",
) -> ProjectOperationResult:
    """Remove one unique multi-project child after its index entry is safely saved."""

    snapshot = copy.deepcopy(index_data)
    original_project = _project_from_snapshot(snapshot, project_id)
    existing_clouds = validate_explicit_multi_project(
        original_project,
        project_viewer_root,
        project_s3_prefix,
    )
    target = resolve_unique_multi_project_child(
        original_project,
        target_pointcloud_s3_path,
        project_viewer_root,
        project_s3_prefix,
    )
    if len(existing_clouds) <= 1:
        raise ValueError("Die letzte Punktwolke eines Multi-Projekts kann nicht entfernt werden.")
    target_keys = filter_pointcloud_object_keys(
        target,
        existing_target_keys,
        project_viewer_root,
        project_s3_prefix,
    )
    target_name = str(target.get("name", "Punktwolke"))

    try:
        def update_project(project: dict[str, Any]) -> None:
            remaining = [
                pointcloud
                for pointcloud in existing_clouds
                if pointcloud is not target
            ]
            _replace_multi_project_pointclouds(project, remaining)
            append_project_history(project, timestamp, f"Punktwolke '{target_name}' wurde entfernt.")

        if not update_project_in_index(index_data, project_id, update_project):
            raise RuntimeError("Projekt konnte im Index nicht gefunden werden.")
        if not save_index(index_data):
            raise RuntimeError("Projekt-Index konnte nicht gespeichert werden.")
    except Exception:
        _restore_index(index_data, snapshot)
        raise

    if target_keys:
        try:
            delete_keys(target_keys)
        except Exception as error:
            return ProjectOperationResult(
                status="partial",
                project_id=project_id,
                orphaned_keys=target_keys,
                warnings=(f"Punktwolken-Dateien konnten nicht vollstaendig geloescht werden: {error}",),
                message="Index wurde aktualisiert; entfernte Punktwolke benoetigt Cleanup.",
            )

    return ProjectOperationResult(
        status="success",
        project_id=project_id,
        deleted_keys=target_keys,
        message="Punktwolke wurde entfernt.",
    )


def replace_project_pointclouds(
    *,
    s3_client,
    index_data: dict[str, Any],
    project_id: str,
    base_viewer_path: str,
    s3_prefix: str,
    prepared_clouds: tuple[PreparedCloudUpload, ...],
    existing_keys: tuple[str, ...] | list[str],
    save_index: Callable[[dict[str, Any]], bool],
    delete_keys: Callable[[tuple[str, ...]], None],
    on_progress: ProgressCallback | None = None,
    bucket_name: str = BUCKET_NAME,
    timestamp: str = "",
) -> ProjectOperationResult:
    """Upload replacement clouds, save index, then clean obsolete old keys.

    Failure contract:
    - Before index save: delete successfully uploaded new keys and restore index.
    - After index save during old-key cleanup: keep new index and report warning.
    """

    snapshot = copy.deepcopy(index_data)
    ledger = UploadedKeyLedger()
    files_to_upload = [
        file_to_upload
        for cloud in prepared_clouds
        for file_to_upload in cloud.files_to_upload
    ]
    replacement_keys = collect_upload_file_keys(prepared_clouds)

    try:
        upload_files_to_s3(
            s3_client,
            files_to_upload,
            bucket_name=bucket_name,
            on_progress=on_progress,
            ledger=ledger,
        )
        pointcloud_entries = [cloud.index_entry for cloud in prepared_clouds]

        def update_project(project: dict[str, Any]) -> None:
            original_project = snapshot_project(project, snapshot)
            project.clear()
            project.update(
                build_multi_project_metadata(
                    project=original_project,
                    base_viewer_path=base_viewer_path,
                    s3_prefix=s3_prefix,
                    pointcloud_entries=pointcloud_entries,
                )
            )
            append_project_history(project, timestamp, "Alle Punktwolken wurden ausgetauscht.")

        if not update_project_in_index(index_data, project_id, update_project):
            raise RuntimeError("Projekt konnte im Index nicht gefunden werden.")
        if not save_index(index_data):
            raise RuntimeError("Projekt-Index konnte nicht gespeichert werden.")
    except Exception:
        if ledger.uploaded_keys:
            delete_keys(ledger.as_tuple())
        _restore_index(index_data, snapshot)
        raise

    orphaned_keys = compute_orphaned_keys(
        exclude_model_object_keys(existing_keys, s3_prefix),
        replacement_keys,
    )
    if orphaned_keys:
        try:
            delete_keys(orphaned_keys)
        except Exception as error:
            return ProjectOperationResult(
                status="partial",
                project_id=project_id,
                uploaded_keys=ledger.as_tuple(),
                orphaned_keys=orphaned_keys,
                warnings=(f"Alte S3-Keys konnten nicht vollständig gelöscht werden: {error}",),
                message="Index wurde aktualisiert; alte Dateien benötigen Cleanup.",
            )

    return ProjectOperationResult(
        status="success",
        project_id=project_id,
        uploaded_keys=ledger.as_tuple(),
        deleted_keys=orphaned_keys,
        message="Punktwolkendaten wurden ersetzt.",
    )


def replace_single_project_pointcloud(
    *,
    s3_client,
    index_data: dict[str, Any],
    project_id: str,
    base_viewer_path: str,
    s3_prefix: str,
    prepared_cloud: PreparedCloudUpload,
    target_pointcloud_s3_path: str,
    existing_target_keys: tuple[str, ...] | list[str],
    save_index: Callable[[dict[str, Any]], bool],
    delete_keys: Callable[[tuple[str, ...]], None],
    on_progress: ProgressCallback | None = None,
    bucket_name: str = BUCKET_NAME,
    timestamp: str = "",
) -> ProjectOperationResult:
    """Replace one child pointcloud while preserving the other children."""

    snapshot = copy.deepcopy(index_data)
    original_snapshot_project = _project_from_snapshot(snapshot, project_id)
    snapshot_pointclouds = original_snapshot_project.get("pointclouds")
    is_legacy_single = not isinstance(snapshot_pointclouds, list) or not snapshot_pointclouds
    legacy_display_name = (
        str(original_snapshot_project.get("name", "")).strip()
        or str(original_snapshot_project.get("projekt", "")).strip()
    )
    ledger = UploadedKeyLedger()
    replacement_keys = collect_upload_file_keys((prepared_cloud,))

    try:
        upload_files_to_s3(
            s3_client,
            prepared_cloud.files_to_upload,
            bucket_name=bucket_name,
            on_progress=on_progress,
            ledger=ledger,
        )

        def update_project(project: dict[str, Any]) -> None:
            original_project = snapshot_project(project, snapshot)
            pointclouds = original_project.get("pointclouds")
            if not isinstance(pointclouds, list) or not pointclouds:
                if not _project_matches_s3_path(original_project, target_pointcloud_s3_path):
                    raise ValueError(f"Punktwolke mit S3-Pfad '{target_pointcloud_s3_path}' wurde nicht gefunden.")
                pointcloud_name = str(original_project.get("name", "")).strip() or str(
                    original_project.get("projekt", "Punktwolke")
                )
                if isinstance(original_project.get("models"), list):
                    pointcloud_entry = prepared_cloud.index_entry
                    pointcloud_entry["name"] = pointcloud_name
                    project.clear()
                    project.update(
                        build_multi_project_metadata(
                            project=original_project,
                            base_viewer_path=base_viewer_path,
                            s3_prefix=s3_prefix,
                            pointcloud_entries=[pointcloud_entry],
                        )
                    )
                    project.pop("name", None)
                    append_project_history(
                        project,
                        timestamp,
                        f"Punktwolke '{pointcloud_name}' wurde ausgetauscht.",
                    )
                    return
                disabled_at = original_project.get("disabled_at")
                project.clear()
                project.update(
                    build_single_project_metadata(
                        timestamp=str(original_project.get("datum", "")),
                        kunde=str(original_project.get("kunde", "")),
                        projekt=str(original_project.get("projekt", "")),
                        project_id=str(original_project.get("id", "")),
                        project_url=str(original_project.get("link", "")),
                        prepared_cloud=prepared_cloud,
                    )
                )
                if disabled_at is not None:
                    project["disabled_at"] = disabled_at
                for key in ("visible", "history", "name"):
                    if key in original_project:
                        project[key] = original_project[key]
                append_project_history(
                    project,
                    timestamp,
                    f"Punktwolke '{pointcloud_name}' wurde ausgetauscht.",
                )
                return

            replaced = False
            pointcloud_entries: list[dict[str, Any]] = []
            for pointcloud in pointclouds:
                if not isinstance(pointcloud, dict):
                    continue
                if _pointcloud_matches_s3_path(pointcloud, target_pointcloud_s3_path):
                    updated_entry = prepared_cloud.index_entry
                    if "visible" in pointcloud:
                        updated_entry["visible"] = pointcloud["visible"]
                    pointcloud_entries.append(updated_entry)
                    replaced = True
                else:
                    pointcloud_entries.append(copy.deepcopy(pointcloud))

            if not replaced:
                raise ValueError(f"Punktwolke mit S3-Pfad '{target_pointcloud_s3_path}' wurde nicht gefunden.")

            project.clear()
            project.update(
                build_multi_project_metadata(
                    project=original_project,
                    base_viewer_path=base_viewer_path,
                    s3_prefix=s3_prefix,
                    pointcloud_entries=pointcloud_entries,
                )
            )
            replaced_name = next(
                (
                    str(pointcloud.get("name", "Punktwolke"))
                    for pointcloud in pointclouds
                    if isinstance(pointcloud, dict)
                    and _pointcloud_matches_s3_path(pointcloud, target_pointcloud_s3_path)
                ),
                "Punktwolke",
            )
            append_project_history(project, timestamp, f"Punktwolke '{replaced_name}' wurde ausgetauscht.")

        if not update_project_in_index(index_data, project_id, update_project):
            raise RuntimeError("Projekt konnte im Index nicht gefunden werden.")
        if is_legacy_single and legacy_display_name:
            _overwrite_uploaded_potree_name(
                s3_client,
                prepared_cloud,
                legacy_display_name,
                bucket_name=bucket_name,
            )
        if not save_index(index_data):
            raise RuntimeError("Projekt-Index konnte nicht gespeichert werden.")
    except Exception:
        if ledger.uploaded_keys:
            delete_keys(ledger.as_tuple())
        _restore_index(index_data, snapshot)
        raise

    orphaned_keys = compute_orphaned_keys(
        exclude_model_object_keys(existing_target_keys, s3_prefix),
        replacement_keys,
    )
    if orphaned_keys:
        try:
            delete_keys(orphaned_keys)
        except Exception as error:
            return ProjectOperationResult(
                status="partial",
                project_id=project_id,
                uploaded_keys=ledger.as_tuple(),
                orphaned_keys=orphaned_keys,
                warnings=(f"Alte S3-Keys konnten nicht vollstaendig geloescht werden: {error}",),
                message="Index wurde aktualisiert; alte Dateien benoetigen Cleanup.",
            )

    return ProjectOperationResult(
        status="success",
        project_id=project_id,
        uploaded_keys=ledger.as_tuple(),
        deleted_keys=orphaned_keys,
        message="Punktwolke wurde ersetzt.",
    )


def _overwrite_uploaded_potree_name(
    s3_client,
    prepared_cloud: PreparedCloudUpload,
    display_name: str,
    *,
    bucket_name: str,
) -> None:
    """Keep a legacy cloud's display name when its Potree data is replaced."""

    if prepared_cloud.input_format != "potree":
        return
    metadata_upload = next(
        (
            (local_path, s3_key)
            for local_path, s3_key in prepared_cloud.files_to_upload
            if s3_key.casefold().endswith("/metadata.json")
        ),
        None,
    )
    if metadata_upload is None:
        return
    local_path, metadata_key = metadata_upload
    with open(local_path, "r", encoding="utf-8") as handle:
        metadata = json.load(handle)
    if not isinstance(metadata, dict):
        raise ValueError(f"Potree-Metadaten sind ungültig: {metadata_key}")
    metadata["name"] = display_name
    payload = (json.dumps(metadata, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    s3_client.put_object(
        Bucket=bucket_name,
        Key=metadata_key,
        Body=payload,
        ContentType="application/json",
        CacheControl=S3_INDEX_CACHE_CONTROL,
    )


def replace_single_project_model(
    *,
    s3_client,
    index_data: dict[str, Any],
    project_id: str,
    prepared_model: PreparedModelUpload,
    target_model_s3_path: str,
    existing_target_keys: tuple[str, ...] | list[str],
    save_index: Callable[[dict[str, Any]], bool],
    delete_keys: Callable[[tuple[str, ...]], None],
    on_progress: ProgressCallback | None = None,
    bucket_name: str = BUCKET_NAME,
    timestamp: str = "",
) -> ProjectOperationResult:
    """Replace one immutable GLB package, then switch its models[] entry."""

    snapshot = copy.deepcopy(index_data)
    original_project = _project_from_snapshot(snapshot, project_id)
    models = original_project.get("models")
    if not isinstance(models, list):
        raise ValueError("Projekt enthält keine austauschbaren GLB-Modelle.")
    target_path = _normalize_s3_path(target_model_s3_path)
    matches = [
        model
        for model in models
        if isinstance(model, dict) and _normalize_s3_path(str(model.get("s3_path", ""))) == target_path
    ]
    if len(matches) != 1:
        raise ValueError("Das ausgewählte GLB konnte nicht eindeutig gefunden werden.")
    target_model = matches[0]
    replacement = prepared_model.index_entry
    if replacement is None:
        raise ValueError("Das vorbereitete Ersatz-GLB hat keinen Zielpfad.")
    if replacement.id != str(target_model.get("id", "")).strip():
        raise ValueError("Die Modell-ID des Ersatz-GLB stimmt nicht mit dem ausgewählten Modell überein.")

    replacement_entry = replacement.as_dict()
    if "visible" in target_model:
        replacement_entry["visible"] = target_model["visible"]
    replacement_prefix = _normalize_s3_path(replacement.s3_path)
    if replacement_prefix == target_path:
        return ProjectOperationResult(
            status="success",
            project_id=project_id,
            message="Das ausgewählte GLB ist bereits in diesem Datenstand vorhanden.",
        )

    files_to_upload = (
        (prepared_model.scene_path, f"{replacement_prefix}/scene.glb"),
        (prepared_model.manifest_path, f"{replacement_prefix}/model.json"),
    )
    ledger = UploadedKeyLedger()
    try:
        upload_files_to_s3(
            s3_client,
            files_to_upload,
            bucket_name=bucket_name,
            on_progress=on_progress,
            ledger=ledger,
            checksum_sha256_keys=tuple(key for _path, key in files_to_upload),
        )
        verify_uploaded_model_files(s3_client, files_to_upload, bucket_name=bucket_name)

        def update_project(project: dict[str, Any]) -> None:
            current_models = project.get("models")
            if not isinstance(current_models, list):
                raise ValueError("Projekt enthält keine austauschbaren GLB-Modelle.")
            replaced = False
            updated_models = []
            for model in current_models:
                if isinstance(model, dict) and _normalize_s3_path(str(model.get("s3_path", ""))) == target_path:
                    if replaced:
                        raise ValueError("Das ausgewählte GLB ist im Projekt mehrfach vorhanden.")
                    updated_models.append(copy.deepcopy(replacement_entry))
                    replaced = True
                else:
                    updated_models.append(copy.deepcopy(model))
            if not replaced:
                raise ValueError("Das ausgewählte GLB wurde im Projekt nicht gefunden.")
            project["models"] = updated_models
            append_project_history(
                project,
                timestamp,
                f"3D-Modell '{target_model.get('name', replacement.name)}' wurde ausgetauscht.",
            )

        if not update_project_in_index(index_data, project_id, update_project):
            raise RuntimeError("Projekt konnte im Index nicht gefunden werden.")
        if not save_index(index_data):
            raise RuntimeError("Projekt-Index konnte nicht gespeichert werden.")
    except Exception:
        rollback_keys = _rollback_keys_preserving_indexed_models(ledger.as_tuple(), snapshot)
        if rollback_keys:
            delete_keys(rollback_keys)
        _restore_index(index_data, snapshot)
        raise

    old_prefix = f"{target_path}/"
    old_candidates = tuple(
        key
        for key in existing_target_keys
        if _normalize_s3_path(key).startswith(old_prefix)
        and _normalize_s3_path(key) not in {uploaded_key for uploaded_key in ledger.as_tuple()}
    )
    old_keys = _rollback_keys_preserving_indexed_models(old_candidates, index_data)
    if old_keys:
        try:
            delete_keys(old_keys)
        except Exception as error:
            return ProjectOperationResult(
                status="partial",
                project_id=project_id,
                uploaded_keys=ledger.as_tuple(),
                orphaned_keys=old_keys,
                warnings=(f"Alte GLB-Dateien konnten nicht vollständig gelöscht werden: {error}",),
                message="GLB wurde ausgetauscht; alte Dateien benötigen Cleanup.",
            )

    return ProjectOperationResult(
        status="success",
        project_id=project_id,
        uploaded_keys=ledger.as_tuple(),
        deleted_keys=old_keys,
        message="GLB wurde ausgetauscht.",
    )


def add_project_models(
    *,
    s3_client,
    index_data: dict[str, Any],
    project_id: str,
    project_viewer_root: str,
    project_s3_prefix: str,
    prepared_models: tuple[PreparedModelUpload, ...] | list[PreparedModelUpload],
    save_index: Callable[[dict[str, Any]], bool],
    delete_keys: Callable[[tuple[str, ...]], None],
    on_progress: ProgressCallback | None = None,
    bucket_name: str = BUCKET_NAME,
    timestamp: str = "",
) -> ProjectOperationResult:
    """Upload new immutable GLB packages, then append their models[] entries."""

    additions = tuple(prepared_models)
    if not additions:
        raise ValueError("Mindestens ein vorbereitetes GLB-Modell ist erforderlich.")
    snapshot = copy.deepcopy(index_data)
    original_project = _project_from_snapshot(snapshot, project_id)
    existing_models = original_project.get("models", [])
    if not isinstance(existing_models, list):
        raise ValueError("Projekt enthält ungültige models[]-Metadaten.")
    model_ids = {
        str(model.get("id", "")).strip()
        for model in existing_models
        if isinstance(model, dict) and str(model.get("id", "")).strip()
    }
    model_entries: list[dict[str, Any]] = []
    files_to_upload: list[UploadFile] = []
    from .glb_optimization_service import build_model_index_entry

    for prepared_model in additions:
        entry = prepared_model.index_entry
        expected_entry = build_model_index_entry(
            prepared_model,
            project_viewer_root=project_viewer_root,
            project_s3_prefix=project_s3_prefix,
        )
        if entry is None or entry != expected_entry:
            raise ValueError("GLB-Upload abgebrochen: Modellpfad und data_version stimmen nicht überein.")
        if not entry.id or entry.id in model_ids:
            raise ValueError(f"Doppelte Modell-ID: {entry.id}")
        model_ids.add(entry.id)
        model_entries.append(entry.as_dict())
        prefix = entry.s3_path.rstrip("/")
        files_to_upload.extend(
            (
                (prepared_model.scene_path, f"{prefix}/scene.glb"),
                (prepared_model.manifest_path, f"{prefix}/model.json"),
            )
        )

    upload_plan = PreparedProjectUpload(
        project_metadata={
            "viewer_path": project_viewer_root,
            "s3_path": project_s3_prefix,
            "models": model_entries,
        },
        files_to_upload=tuple(files_to_upload),
    )
    _validate_prepared_project_model_paths(upload_plan)
    model_files = _model_files_to_verify(upload_plan)
    ledger = UploadedKeyLedger()
    try:
        upload_files_to_s3(
            s3_client,
            model_files,
            bucket_name=bucket_name,
            on_progress=on_progress,
            ledger=ledger,
            checksum_sha256_keys=tuple(key for _path, key in model_files),
        )
        verify_uploaded_model_files(s3_client, model_files, bucket_name=bucket_name)

        def update_project(project: dict[str, Any]) -> None:
            current_models = project.get("models", [])
            if not isinstance(current_models, list):
                raise ValueError("Projekt enthält ungültige models[]-Metadaten.")
            current_ids = {
                str(model.get("id", "")).strip()
                for model in current_models
                if isinstance(model, dict) and str(model.get("id", "")).strip()
            }
            if current_ids.intersection(entry["id"] for entry in model_entries):
                raise ValueError("Mindestens eine Modell-ID ist im Projekt bereits vorhanden.")
            project["models"] = [*copy.deepcopy(current_models), *copy.deepcopy(model_entries)]
            names = [entry["name"] for entry in model_entries]
            message = (
                f"3D-Modell '{names[0]}' wurde hinzugefügt."
                if len(names) == 1
                else f"{len(names)} 3D-Modelle wurden hinzugefügt: {', '.join(names)}."
            )
            append_project_history(project, timestamp, message)

        if not update_project_in_index(index_data, project_id, update_project):
            raise RuntimeError("Projekt konnte im Index nicht gefunden werden.")
        if not save_index(index_data):
            raise RuntimeError("Projekt-Index konnte nicht gespeichert werden.")
    except Exception:
        rollback_keys = _rollback_keys_preserving_indexed_models(ledger.as_tuple(), snapshot)
        if rollback_keys:
            delete_keys(rollback_keys)
        _restore_index(index_data, snapshot)
        raise

    return ProjectOperationResult(
        status="success",
        project_id=project_id,
        uploaded_keys=ledger.as_tuple(),
        message="3D-Modell wurde hinzugefügt." if len(model_entries) == 1 else "3D-Modelle wurden hinzugefügt.",
    )


def remove_project_model(
    *,
    index_data: dict[str, Any],
    project_id: str,
    target_model_s3_path: str,
    existing_target_keys: tuple[str, ...] | list[str],
    save_index: Callable[[dict[str, Any]], bool],
    delete_keys: Callable[[tuple[str, ...]], None],
    timestamp: str = "",
) -> ProjectOperationResult:
    """Remove one models[] entry before deleting its unreferenced immutable package."""

    snapshot = copy.deepcopy(index_data)
    original_project = _project_from_snapshot(snapshot, project_id)
    models = original_project.get("models")
    if not isinstance(models, list):
        raise ValueError("Projekt enthält keine entfernbaren GLB-Modelle.")
    target_path = _normalize_s3_path(target_model_s3_path)
    matches = [
        model
        for model in models
        if isinstance(model, dict) and _normalize_s3_path(str(model.get("s3_path", ""))) == target_path
    ]
    if len(matches) != 1:
        raise ValueError("Das ausgewählte GLB konnte nicht eindeutig gefunden werden.")
    target_model = matches[0]
    target_name = str(target_model.get("name", "3D-Modell")).strip() or "3D-Modell"

    try:
        def update_project(project: dict[str, Any]) -> None:
            current_models = project.get("models")
            if not isinstance(current_models, list):
                raise ValueError("Projekt enthält keine entfernbaren GLB-Modelle.")
            remaining = [
                copy.deepcopy(model)
                for model in current_models
                if not (
                    isinstance(model, dict)
                    and _normalize_s3_path(str(model.get("s3_path", ""))) == target_path
                )
            ]
            if len(remaining) != len(current_models) - 1:
                raise ValueError("Das ausgewählte GLB wurde nicht eindeutig im Projekt gefunden.")
            project["models"] = remaining
            append_project_history(project, timestamp, f"3D-Modell '{target_name}' wurde entfernt.")

        if not update_project_in_index(index_data, project_id, update_project):
            raise RuntimeError("Projekt konnte im Index nicht gefunden werden.")
        if not save_index(index_data):
            raise RuntimeError("Projekt-Index konnte nicht gespeichert werden.")
    except Exception:
        _restore_index(index_data, snapshot)
        raise

    target_prefix = f"{target_path}/"
    candidate_keys = tuple(
        key
        for key in existing_target_keys
        if _normalize_s3_path(key) == target_path or _normalize_s3_path(key).startswith(target_prefix)
    )
    target_keys = _rollback_keys_preserving_indexed_models(candidate_keys, index_data)
    if target_keys:
        try:
            delete_keys(target_keys)
        except Exception as error:
            return ProjectOperationResult(
                status="partial",
                project_id=project_id,
                orphaned_keys=target_keys,
                warnings=(f"GLB-Dateien konnten nicht vollständig gelöscht werden: {error}",),
                message="Modell wurde aus dem Projekt entfernt; S3-Dateien benötigen Cleanup.",
            )

    return ProjectOperationResult(
        status="success",
        project_id=project_id,
        deleted_keys=target_keys,
        message="3D-Modell wurde entfernt.",
    )


def upload_new_project(
    *,
    s3_client,
    index_data: dict[str, Any],
    prepared_upload: PreparedProjectUpload,
    save_index: Callable[[dict[str, Any]], bool],
    delete_keys: Callable[[tuple[str, ...]], None],
    on_progress: ProgressCallback | None = None,
    bucket_name: str = BUCKET_NAME,
    cancel_requested: CancelCallback | None = None,
) -> UploadResult:
    """Upload a new project and insert it into projects_index.json.

    Failure or cancellation before index save restores the input index snapshot
    and deletes newly uploaded keys, except immutable model versions already
    referenced by that snapshot.
    """

    _validate_prepared_project_model_paths(prepared_upload)
    snapshot = copy.deepcopy(index_data)
    ledger = UploadedKeyLedger()
    model_files = _model_files_to_verify(prepared_upload)
    try:
        upload_files_to_s3(
            s3_client,
            prepared_upload.files_to_upload,
            bucket_name=bucket_name,
            on_progress=on_progress,
            ledger=ledger,
            cancel_requested=cancel_requested,
            checksum_sha256_keys=tuple(s3_key for _local_path, s3_key in model_files),
        )
        if model_files:
            verify_uploaded_model_files(
                s3_client,
                model_files,
                bucket_name=bucket_name,
                cancel_requested=cancel_requested,
            )
        projects = index_data.get("projects")
        if not isinstance(projects, list):
            projects = []
            index_data["projects"] = projects
        projects.insert(0, dict(prepared_upload.project_metadata))
        _emit(on_progress, ProgressEvent(kind="progress", percent=0.0, message="Projekt wird gespeichert...", phase="index"))
        if not save_index(index_data):
            raise RuntimeError("Projekt-Index konnte nicht gespeichert werden.")
        _emit(on_progress, ProgressEvent(kind="progress", percent=1.0, message="Projekt wurde gespeichert.", phase="index"))
    except OperationCancelledError:
        if ledger.uploaded_keys:
            _emit(on_progress, ProgressEvent(kind="log", message="[ABBRUCH] Entferne bereits hochgeladene Dateien..."))
            rollback_keys = _rollback_keys_preserving_indexed_models(ledger.as_tuple(), snapshot)
            if rollback_keys:
                delete_keys(rollback_keys)
        _restore_index(index_data, snapshot)
        return UploadResult(
            status="cancelled",
            project_id=str(prepared_upload.project_metadata.get("id", "")),
            message="Upload abgebrochen. Bereits hochgeladene Dateien wurden wieder entfernt.",
        )
    except Exception:
        if ledger.uploaded_keys:
            rollback_keys = _rollback_keys_preserving_indexed_models(ledger.as_tuple(), snapshot)
            if rollback_keys:
                delete_keys(rollback_keys)
        _restore_index(index_data, snapshot)
        raise

    return UploadResult(
        status="success",
        project_id=str(prepared_upload.project_metadata.get("id", "")),
        project_url=str(prepared_upload.project_metadata.get("link", "")),
        s3_prefix=str(prepared_upload.project_metadata.get("s3_path", "")),
        uploaded_keys=ledger.as_tuple(),
        message="Upload erfolgreich.",
    )


def _model_files_to_verify(prepared_upload: PreparedProjectUpload) -> tuple[UploadFile, ...]:
    models = prepared_upload.project_metadata.get("models")
    if not isinstance(models, list):
        return ()
    expected_keys = tuple(
        f"{str(model.get('s3_path') or '').rstrip('/')}/{filename}"
        for model in models
        if isinstance(model, dict) and str(model.get("s3_path") or "").strip()
        for filename in ("scene.glb", "model.json")
    )
    if len(set(expected_keys)) != len(expected_keys):
        raise ValueError("GLB-Upload abgebrochen: models[] enthält doppelte Paketpfade.")
    expected_key_set = set(expected_keys)
    files_by_key: dict[str, UploadFile] = {}
    for file_to_upload in prepared_upload.files_to_upload:
        s3_key = file_to_upload[1]
        if s3_key not in expected_key_set:
            continue
        if s3_key in files_by_key:
            raise ValueError(f"GLB-Upload abgebrochen: Paketdatei doppelt vorbereitet: {s3_key}")
        files_by_key[s3_key] = file_to_upload
    missing_keys = [s3_key for s3_key in expected_keys if s3_key not in files_by_key]
    if missing_keys:
        raise ValueError(f"GLB-Upload abgebrochen: Paketdatei fehlt: {missing_keys[0]}")
    return tuple(files_by_key[s3_key] for s3_key in expected_keys)


def _validate_prepared_project_model_paths(prepared_upload: PreparedProjectUpload) -> None:
    """Reject malformed content-addressed model plans before the first S3 call."""

    models = prepared_upload.project_metadata.get("models")
    if models is None:
        return
    if not isinstance(models, list):
        raise ValueError("GLB-Upload abgebrochen: models muss eine Liste sein.")
    viewer_root = _normalize_s3_path(str(prepared_upload.project_metadata.get("viewer_path", "")))
    s3_root = _normalize_s3_path(str(prepared_upload.project_metadata.get("s3_path", "")))
    for model in models:
        if not isinstance(model, dict):
            raise ValueError("GLB-Upload abgebrochen: Ungueltiger models[]-Eintrag.")
        model_id = _normalize_s3_path(str(model.get("id", "")))
        s3_path = _normalize_s3_path(str(model.get("s3_path", "")))
        match = re.fullmatch(
            rf"{re.escape(s3_root)}/models/{re.escape(model_id)}/versions/([0-9a-f]{{64}})",
            s3_path,
        )
        data_version = match.group(1) if match else ""
        expected_viewer_path = f"{viewer_root}/models/{model_id}/versions/{data_version}/model.json"
        if (
            not model_id
            or not data_version
            or model.get("format") != "glb"
            or _normalize_s3_path(str(model.get("viewer_path", ""))) != expected_viewer_path
        ):
            raise ValueError(
                "GLB-Upload abgebrochen: models[] enthaelt keinen gueltigen "
                "data_version-Pfad. Es wurden keine S3-Daten geaendert."
            )


def _rollback_keys_preserving_indexed_models(
    uploaded_keys: tuple[str, ...],
    index_snapshot: dict[str, Any],
) -> tuple[str, ...]:
    """Never delete immutable model versions already referenced by the old index."""

    protected_prefixes: list[str] = []
    for section in ("projects", "disabled_projects"):
        for project in index_snapshot.get(section, ()):
            if not isinstance(project, dict):
                continue
            models = project.get("models", ())
            if not isinstance(models, list):
                continue
            for model in models:
                if isinstance(model, dict):
                    prefix = _normalize_s3_path(str(model.get("s3_path", "")))
                    if prefix:
                        protected_prefixes.append(prefix)
    return tuple(
        key
        for key in uploaded_keys
        if not any(
            _normalize_s3_path(key) == prefix or _normalize_s3_path(key).startswith(f"{prefix}/")
            for prefix in protected_prefixes
        )
    )


def remap_project_path(value: str, old_prefix: str, new_prefix: str) -> str:
    text = str(value or "")
    old = str(old_prefix or "").rstrip("/")
    new = str(new_prefix or "").rstrip("/")
    if old and (text == old or text.startswith(f"{old}/")):
        return f"{new}{text[len(old):]}"
    return text


def _remap_model_project_paths(
    value: Any,
    old_viewer_root: str,
    new_viewer_root: str,
    old_s3_prefix: str,
    new_s3_prefix: str,
) -> Any:
    """Copy model metadata while rebasing every persisted project path."""

    if isinstance(value, dict):
        return {
            key: _remap_model_project_paths(
                item,
                old_viewer_root,
                new_viewer_root,
                old_s3_prefix,
                new_s3_prefix,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _remap_model_project_paths(
                item,
                old_viewer_root,
                new_viewer_root,
                old_s3_prefix,
                new_s3_prefix,
            )
            for item in value
        ]
    if isinstance(value, str):
        return remap_project_path(
            remap_project_path(value, old_viewer_root, new_viewer_root),
            old_s3_prefix,
            new_s3_prefix,
        )
    return copy.deepcopy(value)


def build_duplicate_project_metadata(
    *,
    source_project: dict[str, Any],
    timestamp: str,
    new_kunde: str,
    new_projekt: str,
    new_project_id: str,
    new_project_url: str,
    new_viewer_root: str,
    new_s3_prefix: str,
) -> dict[str, Any]:
    """Build an active cloned project entry while preserving multi-cloud metadata."""

    old_s3_prefix = str(source_project.get("s3_path", "")).rstrip("/")
    old_viewer_path = str(source_project.get("viewer_path", "")).rstrip("/")
    if source_project.get("format") == "copc" and old_viewer_path.endswith(f"/{COPC_OBJECT_NAME}"):
        old_viewer_root = old_viewer_path[: -len(f"/{COPC_OBJECT_NAME}")]
    else:
        old_viewer_root = old_viewer_path

    duplicated = copy.deepcopy(source_project)
    duplicated.update(
        {
            "datum": timestamp,
            "kunde": new_kunde,
            "id": new_project_id,
            "projekt": new_projekt,
            "link": new_project_url,
            "viewer_path": remap_project_path(
                str(source_project.get("viewer_path", "")),
                old_viewer_root,
                new_viewer_root,
            ),
            "s3_path": remap_project_path(
                str(source_project.get("s3_path", "")),
                old_s3_prefix,
                new_s3_prefix,
            ),
        }
    )
    duplicated.pop("disabled_at", None)

    pointclouds = duplicated.get("pointclouds")
    if isinstance(pointclouds, list):
        remapped_pointclouds = []
        for pointcloud in pointclouds:
            if not isinstance(pointcloud, dict):
                remapped_pointclouds.append(pointcloud)
                continue
            updated_cloud = copy.deepcopy(pointcloud)
            if "viewer_path" in updated_cloud:
                updated_cloud["viewer_path"] = remap_project_path(
                    updated_cloud["viewer_path"],
                    old_viewer_root,
                    new_viewer_root,
                )
            if "s3_path" in updated_cloud:
                updated_cloud["s3_path"] = remap_project_path(
                    updated_cloud["s3_path"],
                    old_s3_prefix,
                    new_s3_prefix,
                )
            remapped_pointclouds.append(updated_cloud)
        duplicated["pointclouds"] = remapped_pointclouds
        duplicated["pointcloud_count"] = len([cloud for cloud in remapped_pointclouds if isinstance(cloud, dict)])

    models = duplicated.get("models")
    if isinstance(models, list):
        duplicated["models"] = _remap_model_project_paths(
            models,
            old_viewer_root,
            new_viewer_root,
            old_s3_prefix,
            new_s3_prefix,
        )

    return duplicated


def apply_project_rename_metadata(
    project: dict[str, Any],
    new_kunde: str,
    new_projekt: str,
    pointcloud_names: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Rename project and optional pointcloud names without changing S3 paths."""

    updated = copy.deepcopy(project)
    updated["kunde"] = new_kunde
    updated["projekt"] = new_projekt
    pointclouds = updated.get("pointclouds")
    if isinstance(pointclouds, list) and pointclouds:
        for index, name in enumerate(pointcloud_names):
            if index < len(pointclouds) and isinstance(pointclouds[index], dict):
                pointclouds[index]["name"] = name
    elif pointcloud_names:
        pointcloud_name = str(pointcloud_names[0] or "").strip()
        if pointcloud_name and pointcloud_name != str(new_projekt or "").strip():
            updated["name"] = pointcloud_name
        else:
            updated.pop("name", None)
    return updated


def build_deleted_project_entry(
    project_info: dict[str, Any],
    s3_path: str,
    deleted_at: str,
) -> dict[str, Any]:
    return {
        "id": project_info.get("id", ""),
        "kunde": project_info.get("kunde", ""),
        "projekt": project_info.get("projekt", ""),
        "s3_path": s3_path,
        "deleted_at": deleted_at,
        "original_link": project_info.get("link", ""),
    }


def upsert_deleted_project(
    deleted_data: dict[str, Any],
    deleted_entry: dict[str, Any],
) -> dict[str, Any]:
    deleted_projects = deleted_data.get("deleted_projects", [])
    filtered_projects = [
        project
        for project in deleted_projects
        if project.get("s3_path") != deleted_entry["s3_path"]
        and project.get("id") != deleted_entry["id"]
    ]
    filtered_projects.insert(0, deleted_entry)
    deleted_data["deleted_projects"] = filtered_projects
    return deleted_data


def duplicate_project(
    *,
    s3_client,
    index_data: dict[str, Any],
    source_project: dict[str, Any],
    timestamp: str,
    new_kunde: str,
    new_projekt: str,
    new_project_id: str,
    new_project_url: str,
    new_viewer_root: str,
    new_s3_prefix: str,
    save_index: Callable[[dict[str, Any]], bool],
    delete_keys: Callable[[tuple[str, ...]], None],
    bucket_name: str = BUCKET_NAME,
    on_progress: ProgressCallback | None = None,
) -> ProjectOperationResult:
    source_s3_path = str(source_project.get("s3_path", "")).strip()
    if not source_s3_path:
        raise ValueError("Quellprojekt hat keinen S3-Pfad.")

    snapshot = copy.deepcopy(index_data)
    copied_keys: tuple[str, ...] = ()
    try:
        _emit(on_progress, ProgressEvent(kind="step", step=1, total_steps=3, message="Ermittle Projektdateien..."))
        source_entries = collect_project_object_entries(s3_client, source_s3_path, bucket_name=bucket_name)
        source_keys = [str(entry["Key"]) for entry in source_entries]
        if not source_keys:
            raise ValueError("Keine Dateien im Quellprojekt gefunden.")
        _emit(
            on_progress,
            ProgressEvent(kind="step", step=2, total_steps=3, message=f"Kopiere {len(source_keys)} Dateien..."),
        )
        copied_keys = copy_project_objects(
            s3_client,
            source_keys,
            source_s3_path,
            new_s3_prefix,
            bucket_name=bucket_name,
            on_progress=on_progress,
            source_sizes={str(entry["Key"]): int(entry.get("Size", 0) or 0) for entry in source_entries},
        )
        _emit(on_progress, ProgressEvent(kind="step", step=3, total_steps=3, message="Speichere Projekt-Index..."))
        new_project = build_duplicate_project_metadata(
            source_project=source_project,
            timestamp=timestamp,
            new_kunde=new_kunde,
            new_projekt=new_projekt,
            new_project_id=new_project_id,
            new_project_url=new_project_url,
            new_viewer_root=new_viewer_root,
            new_s3_prefix=new_s3_prefix,
        )
        projects = index_data.get("projects")
        if not isinstance(projects, list):
            projects = []
            index_data["projects"] = projects
        projects.insert(0, new_project)
        if not save_index(index_data):
            raise RuntimeError("Projekt-Index konnte nicht gespeichert werden.")
    except Exception:
        if copied_keys:
            delete_keys(copied_keys)
        _restore_index(index_data, snapshot)
        raise

    return ProjectOperationResult(
        status="success",
        project_id=new_project_id,
        uploaded_keys=copied_keys,
        message="Projekt wurde dupliziert.",
    )


def delete_project(
    *,
    s3_client,
    index_data: dict[str, Any],
    deleted_data: dict[str, Any],
    project_info: dict[str, Any],
    deleted_at: str,
    save_index: Callable[[dict[str, Any]], bool],
    save_deleted: Callable[[dict[str, Any]], bool],
    bucket_name: str = BUCKET_NAME,
) -> ProjectOperationResult:
    s3_path = str(project_info.get("s3_path", "")).strip()
    project_id = str(project_info.get("id", "")).strip()
    if not s3_path:
        return ProjectOperationResult(status="failed", project_id=project_id, message="S3-Pfad nicht gefunden.")

    object_keys = tuple(collect_project_objects(s3_client, s3_path, bucket_name=bucket_name))
    deleted_count = delete_s3_objects(s3_client, object_keys, bucket_name=bucket_name) if object_keys else 0

    metadata_errors: list[str] = []
    deleted_entry = build_deleted_project_entry(project_info, s3_path, deleted_at)
    upsert_deleted_project(deleted_data, deleted_entry)
    if not save_deleted(deleted_data):
        metadata_errors.append("deleted_projects.json")

    removed = remove_project_from_index(index_data, project_id)
    if removed:
        if not save_index(index_data):
            metadata_errors.append("projects_index.json")

    if metadata_errors:
        return ProjectOperationResult(
            status="partial",
            project_id=project_id,
            deleted_keys=object_keys,
            warnings=(f"Metadaten konnten nicht vollständig aktualisiert werden: {', '.join(metadata_errors)}",),
            message="Projektdaten wurden in S3 gelöscht, aber Metadaten sind unvollständig.",
        )

    return ProjectOperationResult(
        status="success",
        project_id=project_id,
        deleted_keys=object_keys,
        message=f"Projekt wurde gelöscht ({deleted_count} S3-Objekte).",
    )


def build_download_folder_name(project_info: dict[str, Any], sanitize_func) -> str:
    folder_parts = [
        sanitize_func(project_info.get("kunde", "")),
        sanitize_func(project_info.get("projekt", "")),
        str(project_info.get("id", "")).strip(),
    ]
    return "_".join(part for part in folder_parts if part) or "punktwolke"


def download_project(
    *,
    s3_client,
    project_info: dict[str, Any],
    target_dir: str,
    sanitize_func,
    on_progress: ProgressCallback | None = None,
    cancel_requested: CancelCallback | None = None,
    bucket_name: str = BUCKET_NAME,
) -> tuple[str, tuple[str, ...]]:
    import os

    source_s3_path = str(project_info.get("s3_path", "")).strip()
    if not source_s3_path:
        raise ValueError("Projekt hat keinen S3-Pfad.")
    if not target_dir:
        raise ValueError("Kein Zielordner ausgewaehlt.")

    download_dir = os.path.join(target_dir, build_download_folder_name(project_info, sanitize_func))
    os.makedirs(download_dir, exist_ok=True)
    object_entries = [
        entry
        for entry in collect_project_object_entries(s3_client, source_s3_path, bucket_name=bucket_name)
        if not str(entry["Key"]).endswith("/")
    ]
    if not object_entries:
        raise ValueError("Keine Dateien im Projekt gefunden.")
    try:
        downloaded = download_project_objects(
            s3_client,
            object_entries,
            source_s3_path,
            download_dir,
            bucket_name=bucket_name,
            on_progress=on_progress,
            cancel_requested=cancel_requested,
        )
    except DownloadCancelledError as exc:
        raise ProjectDownloadCancelledError(download_dir, exc.downloaded_paths) from exc
    return download_dir, downloaded


def snapshot_project(project: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    project_id = str(project.get("id", "")).strip()
    for key in ("projects", "disabled_projects"):
        for candidate in snapshot.get(key, []):
            if isinstance(candidate, dict) and str(candidate.get("id", "")).strip() == project_id:
                return copy.deepcopy(candidate)
    return copy.deepcopy(project)


__all__ = [
    "PreparedCloudUpload",
    "PreparedProjectUpload",
    "ProjectDownloadCancelledError",
    "add_project_pointclouds",
    "build_multi_project_metadata",
    "build_new_project_upload",
    "build_duplicate_project_metadata",
    "build_single_project_metadata",
    "collect_upload_file_keys",
    "compute_orphaned_keys",
    "exclude_model_object_keys",
    "filter_pointcloud_object_keys",
    "apply_project_rename_metadata",
    "build_deleted_project_entry",
    "build_download_folder_name",
    "prepare_cloud_uploads",
    "prepare_single_project_upload",
    "pointcloud_object_list_prefix",
    "rebase_prepared_cloud_upload",
    "delete_project",
    "download_project",
    "duplicate_project",
    "remap_project_path",
    "replace_project_pointclouds",
    "replace_single_project_pointcloud",
    "replace_single_project_model",
    "add_project_models",
    "remove_project_model",
    "remove_project_pointcloud",
    "resolve_unique_multi_project_child",
    "upload_new_project",
    "upsert_deleted_project",
    "validate_explicit_multi_project",
]
