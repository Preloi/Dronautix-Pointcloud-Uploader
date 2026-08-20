"""Dataclass contracts for UI-free project operations.

These contracts are intentionally small and serializable. QtWidgets, the old
CustomTkinter UI, tests, and future automation can all consume the same core
surface without importing Tk globals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal


ProgressKind = Literal["log", "step", "progress", "detail", "warning", "error"]
OperationStatus = Literal["success", "partial", "failed", "cancelled"]


@dataclass(frozen=True)
class ProgressEvent:
    kind: ProgressKind
    message: str = ""
    step: int | None = None
    total_steps: int | None = None
    percent: float | None = None
    detail: str = ""
    phase: str = ""


ProgressCallback = Callable[[ProgressEvent], None]
CancelCallback = Callable[[], bool]


class OperationCancelledError(RuntimeError):
    """Raised when a running operation is cancelled by the caller."""

    def __init__(self, message: str = "Vorgang wurde abgebrochen.") -> None:
        super().__init__(message)


def make_cancel_guarded_progress(
    on_progress: ProgressCallback | None,
    cancel_requested: CancelCallback | None,
) -> ProgressCallback | None:
    """Wrap a progress callback so every emitted event checks for cancellation.

    Long-running phases (e.g. PotreeConverter) emit progress continuously, so
    raising from the callback stops them promptly without changing their API.
    """

    if cancel_requested is None:
        return on_progress

    def guarded(event: ProgressEvent) -> None:
        if cancel_requested():
            raise OperationCancelledError()
        if on_progress is not None:
            on_progress(event)

    return guarded


@dataclass(frozen=True)
class PointcloudSource:
    source_path: str
    name: str = ""
    slug: str = ""
    input_format: Literal["potree", "copc", "raw", "potree_dir", ""] = ""
    source_type: Literal["raw_file", "potree_dir", ""] = ""
    crs_info: dict[str, Any] | None = None


@dataclass(frozen=True)
class ModelUploadInput:
    """One selected, natively georeferenced GLB model."""

    source_path: str
    name: str = ""
    slug: str = ""
    model_json_path: str = ""


@dataclass(frozen=True)
class ModelIndexEntry:
    """The additive ``models[]`` entry written to ``projects_index.json``."""

    id: str
    name: str
    viewer_path: str
    s3_path: str
    crs: str
    vertical_crs: str
    crs_name: str = ""
    vertical_name: str = ""
    vertical_datum: str = ""
    format: Literal["glb"] = "glb"

    def as_dict(self) -> dict[str, str]:
        result = {
            "id": self.id,
            "name": self.name,
            "format": self.format,
            "viewer_path": self.viewer_path,
            "s3_path": self.s3_path,
            "crs": self.crs,
            "vertical_crs": self.vertical_crs,
        }
        for key in ("crs_name", "vertical_name", "vertical_datum"):
            value = str(getattr(self, key, "") or "").strip()
            if value:
                result[key] = value
        return result


@dataclass(frozen=True)
class GLBOptimizationResult:
    """Outcome of validation and the selected GLB optimization candidate."""

    selected_candidate: str = "original"
    source_size: int = 0
    output_size: int = 0
    original_sha256: str = ""
    output_sha256: str = ""
    primitive_count: int = 0
    triangle_count: int = 0
    texture_count: int = 0
    used_fallback: bool = True
    fallback_reason: str = ""
    toolchain_versions: dict[str, str] = field(default_factory=dict)
    control_points: tuple[tuple[float, float, float], ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreparedModelUpload:
    """Staged, validated GLB plus its immutable model manifest."""

    model_input: ModelUploadInput
    name: str
    slug: str
    staging_dir: str
    scene_path: str
    manifest_path: str
    original_sha256: str
    model_to_project: tuple[float, ...]
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]
    crs_info: dict[str, Any]
    optimization: GLBOptimizationResult
    data_version: str = ""
    index_entry: ModelIndexEntry | None = None

    @property
    def output_sha256(self) -> str:
        """Return the selected GLB content hash in canonical path form."""

        return str(self.optimization.output_sha256 or "").lower()

    @property
    def package_sha256(self) -> str:
        """Return the immutable GLB-plus-manifest package hash."""

        return str(self.data_version or "").lower()


@dataclass(frozen=True)
class UploadRequest:
    sources: tuple[PointcloudSource, ...]
    kunde: str
    projekt: str
    aws_access: str
    aws_secret: str
    converter_path: str = ""
    output_base_dir: str = ""
    crs_input: str = ""
    vertical_input: str = ""
    overwrite: bool = False
    model_inputs: tuple[ModelUploadInput, ...] = ()


@dataclass(frozen=True)
class ReplacementRequest:
    project: dict[str, Any]
    replacement: PointcloudSource
    aws_access: str
    aws_secret: str
    target_pointcloud: dict[str, Any] | None = None
    converter_path: str = ""
    output_base_dir: str = ""
    crs_input: str = ""
    vertical_input: str = ""
    overwrite: bool = False


@dataclass(frozen=True)
class MultiReplacementRequest:
    project: dict[str, Any]
    replacements: tuple[PointcloudSource, ...]
    aws_access: str
    aws_secret: str
    converter_path: str = ""
    output_base_dir: str = ""
    crs_input: str = ""
    vertical_input: str = ""
    overwrite: bool = False


@dataclass(frozen=True)
class PointcloudAddRequest:
    """Add prepared sources to an existing explicit multi-cloud project."""

    project: dict[str, Any]
    additions: tuple[PointcloudSource, ...]
    aws_access: str
    aws_secret: str
    converter_path: str = ""
    output_base_dir: str = ""
    crs_input: str = ""
    vertical_input: str = ""
    overwrite: bool = False


@dataclass(frozen=True)
class PointcloudRemoveRequest:
    """Remove one explicit child pointcloud from a multi-cloud project."""

    project: dict[str, Any]
    target_pointcloud: dict[str, Any]
    aws_access: str
    aws_secret: str


@dataclass(frozen=True)
class ProjectMetadataUpdate:
    project_id: str
    kunde: str
    projekt: str
    pointcloud_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectDeleteRequest:
    project_id: str
    aws_access: str = ""
    aws_secret: str = ""


@dataclass(frozen=True)
class ProjectLinkStateUpdate:
    project_id: str
    disabled: bool


@dataclass(frozen=True)
class DownloadRequest:
    project: dict[str, Any]
    target_dir: str
    aws_access: str
    aws_secret: str


@dataclass(frozen=True)
class UploadResult:
    status: OperationStatus
    project_id: str = ""
    project_url: str = ""
    s3_prefix: str = ""
    uploaded_keys: tuple[str, ...] = ()
    cleanup_warnings: tuple[str, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class ProjectOperationResult:
    status: OperationStatus
    project_id: str = ""
    uploaded_keys: tuple[str, ...] = ()
    deleted_keys: tuple[str, ...] = ()
    orphaned_keys: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class DownloadResult:
    status: OperationStatus
    download_dir: str = ""
    downloaded_files: tuple[str, ...] = ()
    message: str = ""


@dataclass
class UploadedKeyLedger:
    """Tracks S3 keys only after their upload call completed successfully."""

    uploaded_keys: list[str] = field(default_factory=list)

    def record(self, key: str) -> None:
        if key:
            self.uploaded_keys.append(key)

    def as_tuple(self) -> tuple[str, ...]:
        return tuple(self.uploaded_keys)
