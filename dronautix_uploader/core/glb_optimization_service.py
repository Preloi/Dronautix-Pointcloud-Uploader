"""Safe, UI-free preparation of natively georeferenced GLB-2.0 uploads.

Only a sealed bundled toolchain may decode or optimize Draco, Meshopt, and
KTX2 resources. Until that self-test passes, this service preserves a valid
uncompressed original and fail-closes compressed input; it never invokes a
globally installed tool.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
import base64
import binascii
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import tempfile
from typing import Any, Callable, Protocol
from urllib.parse import unquote_to_bytes
import zlib

from .contracts import (
    CancelCallback,
    GLBOptimizationResult,
    ModelIndexEntry,
    ModelUploadInput,
    OperationCancelledError,
    PreparedModelUpload,
    ProgressCallback,
    ProgressEvent,
)
from .crs_service import (
    CrsValidationError,
    get_crs_technical_value,
    get_vertical_crs_technical_value,
    normalize_crs_metadata,
)
from .glb_toolchain import (
    GLBToolchainStatus,
    get_bundled_runner_path,
    get_bundled_tool_path,
    get_bundled_toolchain_environment,
    get_glb_toolchain_status,
    load_viewer_capabilities,
)
from .naming_service import make_unique_slug, sanitize_folder_name


_GLB_HEADER = struct.Struct("<4sII")
_CHUNK_HEADER = struct.Struct("<II")
_JSON_CHUNK = 0x4E4F534A
_BIN_CHUNK = 0x004E4942
_MATRIX_EPSILON = 1e-12
_BOUNDS_TOLERANCE_METRES = 0.001
_IDENTITY_MATRIX = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


class GLBValidationError(ValueError):
    """A GLB, companion manifest, capability, or placement is invalid."""


class GLBOptimizationToolchain(Protocol):
    """Sealed bundled optimizer boundary; global Node installations are excluded.

    The service calls this boundary only after :mod:`glb_toolchain` verified
    the local runtime, all hashes, and every bundled runner self-test.
    """

    def optimize_candidates(
        self,
        source_path: Path,
        output_dir: Path,
        cancel_requested: CancelCallback | None = None,
    ) -> Iterable[tuple[str, Path]]: ...


class GLBCompressedAssetDecoder(Protocol):
    """Bundled decoder boundary for Draco, Meshopt and KTX2 GLBs.

    The decoder must return a new, self-contained uncompressed GLB.  The
    service validates that result exactly like any other candidate; no global
    command or implicit browser decoder is ever used.
    """

    def decode(
        self,
        source_path: Path,
        extensions: tuple[str, ...],
        output_dir: Path,
        cancel_requested: CancelCallback | None = None,
    ) -> Path: ...


@dataclass
class BundledGLBOptimizationToolchain:
    """Runs only a sealed local GLB bundle, never PATH or a global npm tool."""

    resource_root: str | Path | None = None
    last_warnings: list[str] = field(default_factory=list, init=False, repr=False)

    def optimize_candidates(
        self,
        source_path: Path,
        output_dir: Path,
        cancel_requested: CancelCallback | None = None,
    ) -> Iterable[tuple[str, Path]]:
        self.last_warnings.clear()
        status = get_glb_toolchain_status(self.resource_root)
        if not (status.toolchain_available and status.viewer_supports_compressed_output):
            return ()
        source_path = source_path.resolve()
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        candidates: list[tuple[str, Path]] = []
        conservative_source = source_path
        for codec in ("conservative", "visura-safe", "meshopt", "draco", "ktx2"):
            _raise_if_cancelled(cancel_requested)
            target = (output_dir / f"optimized-{codec}.glb").resolve()
            candidate_source = source_path if codec in {"conservative", "visura-safe"} else conservative_source
            try:
                _run_bundled_runner(
                    self.resource_root,
                    "optimizer",
                    (codec, str(candidate_source), str(target)),
                    cancel_requested,
                )
            except OperationCancelledError:
                if target.exists():
                    target.unlink()
                raise
            except GLBValidationError as error:
                self.last_warnings.append(f"{codec}: {error}")
                target.unlink(missing_ok=True)
                continue
            if target.is_file():
                candidates.append((codec, target))
                if codec == "conservative":
                    conservative_source = target
        return tuple(candidates)


@dataclass(frozen=True)
class BundledGLBCompressedAssetDecoder:
    """Explicit decoder bridge. A sealed bundle must ship a decoder runner."""

    resource_root: str | Path | None = None

    def decode(
        self,
        source_path: Path,
        extensions: tuple[str, ...],
        output_dir: Path,
        cancel_requested: CancelCallback | None = None,
    ) -> Path:
        source_path = source_path.resolve()
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        status = get_glb_toolchain_status(self.resource_root)
        if not (status.toolchain_available and status.viewer_supports_compressed_output):
            raise GLBValidationError("Für komprimierte GLB-Eingaben ist kein versiegelter gebündelter Decoder verfügbar.")
        runner = get_bundled_runner_path("decoder", self.resource_root)
        if not runner.is_file():
            raise GLBValidationError("Der versiegelte GLB-Bundle enthält keinen Decoder-Runner für Draco, Meshopt oder KTX2.")
        target = (output_dir / "decoded-uncompressed.glb").resolve()
        _run_bundled_runner(
            self.resource_root,
            "decoder",
            ("decode", ",".join(extensions), str(source_path), str(target)),
            cancel_requested,
        )
        if not target.is_file():
            raise GLBValidationError("Der gebündelte GLB-Decoder hat keine unkomprimierte Ausgabe erzeugt.")
        return target


@dataclass(frozen=True)
class _GLBInspection:
    path: Path
    document: dict[str, Any]
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]
    primitive_count: int
    triangle_count: int
    texture_count: int
    control_points: tuple[tuple[float, float, float], ...]


@dataclass
class GLBOptimizationService:
    """Validate, stage, and manifest generic GLB-2.0 models without UI code."""

    capabilities_path: str | Path | None = None
    toolchain: GLBOptimizationToolchain | None = None
    compressed_decoder: GLBCompressedAssetDecoder | None = None
    resource_root: str | Path | None = None
    _toolchain_status: GLBToolchainStatus | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        # Defaults are concrete sealed-bundle adapters. If verification ever
        # fails they preserve an uncompressed original and reject compressed
        # input rather than consulting a machine-wide tool installation.
        if self.toolchain is None:
            self.toolchain = BundledGLBOptimizationToolchain(self.resource_root)
        if self.compressed_decoder is None:
            self.compressed_decoder = BundledGLBCompressedAssetDecoder(self.resource_root)

    def toolchain_self_test(self) -> GLBToolchainStatus:
        if self._toolchain_status is None:
            self._toolchain_status = get_glb_toolchain_status(self.resource_root)
        return self._toolchain_status

    def validate_model_upload_input(
        self,
        model_input: ModelUploadInput,
        *,
        project_crs_info: Mapping[str, Any] | None,
    ) -> None:
        source_path = _source_path(model_input)
        inspection = self._inspect_glb(source_path)
        self._resolve_georeferencing(model_input, source_path, inspection, project_crs_info)

    def prepare(
        self,
        model_input: ModelUploadInput,
        *,
        project_crs_info: Mapping[str, Any] | None,
        staging_root: str | Path,
        used_slugs: set[str] | None = None,
        project_viewer_root: str = "",
        project_s3_prefix: str = "",
        on_progress: ProgressCallback | None = None,
        cancel_requested: CancelCallback | None = None,
    ) -> PreparedModelUpload:
        """Stage a selected GLB and write a viewer-compatible ``model.json``.

        The caller owns the resulting staging directory on success and must call
        :func:`cleanup_prepared_model_uploads` after upload or rollback.
        """

        _raise_if_cancelled(cancel_requested)
        source_path = _source_path(model_input)
        root = Path(staging_root)
        root.mkdir(parents=True, exist_ok=True)
        stage_dir = Path(tempfile.mkdtemp(prefix=".glb-upload-", dir=root))
        try:
            _emit(on_progress, ProgressEvent(kind="detail", detail=str(source_path), phase="optimization"))
            staged_original = stage_dir / "original.glb"
            _copy_with_cancel(source_path, staged_original, cancel_requested)
            _raise_if_cancelled(cancel_requested)

            inspection = self._inspect_glb(staged_original, stage_dir, cancel_requested=cancel_requested)
            matrix, bounds_min, bounds_max, crs_info = self._resolve_georeferencing(
                model_input,
                source_path,
                inspection,
                project_crs_info,
            )
            status = self.toolchain_self_test()
            selected_path, selected_name, warnings, output_inspection = self._select_candidate(
                inspection.path,
                inspection,
                matrix,
                stage_dir,
                status,
                cancel_requested,
            )
            scene_path = stage_dir / "scene.glb"
            _copy_with_cancel(selected_path, scene_path, cancel_requested)
            _raise_if_cancelled(cancel_requested)

            original_sha256 = _sha256(staged_original, cancel_requested)
            output_sha256 = _sha256(scene_path, cancel_requested)
            name = _model_display_name(model_input, source_path)
            slug = _model_slug(model_input, name, used_slugs)
            result = GLBOptimizationResult(
                selected_candidate=selected_name,
                source_size=staged_original.stat().st_size,
                output_size=scene_path.stat().st_size,
                original_sha256=original_sha256,
                output_sha256=output_sha256,
                primitive_count=output_inspection.primitive_count,
                triangle_count=output_inspection.triangle_count,
                texture_count=output_inspection.texture_count,
                used_fallback=selected_name == "original",
                fallback_reason=status.fallback_reason if selected_name == "original" else "",
                toolchain_versions={"glb_toolchain": status.toolchain_version} if status.toolchain_version else {},
                control_points=tuple(_transform_point(point, matrix) for point in output_inspection.control_points),
                warnings=tuple(warnings),
            )
            manifest = _build_model_manifest(
                matrix=matrix,
                bounds_min=bounds_min,
                bounds_max=bounds_max,
                crs_info=crs_info,
                original_sha256=original_sha256,
                optimization=result,
                toolchain_status=status,
            )
            data_version = _model_package_sha256(output_sha256, manifest)
            manifest_path = stage_dir / "model.json"
            manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            prepared = PreparedModelUpload(
                model_input=model_input,
                name=name,
                slug=slug,
                staging_dir=str(stage_dir),
                scene_path=str(scene_path),
                manifest_path=str(manifest_path),
                original_sha256=original_sha256,
                model_to_project=matrix,
                bounds_min=bounds_min,
                bounds_max=bounds_max,
                crs_info=dict(crs_info),
                optimization=result,
                data_version=data_version,
            )
            if project_viewer_root or project_s3_prefix:
                prepared = replace(
                    prepared,
                    index_entry=build_model_index_entry(
                        prepared,
                        project_viewer_root=project_viewer_root,
                        project_s3_prefix=project_s3_prefix,
                    ),
                )
            _emit(
                on_progress,
                ProgressEvent(
                    kind="detail",
                    message=f"[MODELL] {name}: {selected_name}, {result.output_size} Bytes",
                    detail=json.dumps(
                        {
                            "model_path": str(source_path),
                            "optimization_status": selected_name,
                            "output_size": result.output_size,
                        },
                        ensure_ascii=False,
                    ),
                    phase="optimization",
                ),
            )
            _emit(on_progress, ProgressEvent(kind="progress", percent=1.0, phase="optimization"))
            return prepared
        except BaseException:
            _cleanup_stage_dir(stage_dir)
            raise

    def prepare_many(
        self,
        model_inputs: Iterable[ModelUploadInput],
        *,
        project_crs_info: Mapping[str, Any] | None,
        staging_root: str | Path,
        project_viewer_root: str = "",
        project_s3_prefix: str = "",
        on_progress: ProgressCallback | None = None,
        cancel_requested: CancelCallback | None = None,
    ) -> tuple[PreparedModelUpload, ...]:
        inputs = tuple(model_inputs)
        _assert_unique_model_inputs(inputs)
        prepared: list[PreparedModelUpload] = []
        used_slugs: set[str] = set()
        try:
            total = len(inputs)
            for index, model_input in enumerate(inputs, start=1):
                _raise_if_cancelled(cancel_requested)
                _emit(
                    on_progress,
                    ProgressEvent(
                        kind="step",
                        step=index,
                        total_steps=total,
                        percent=(index - 1) / total if total else 1.0,
                        message="Bereite 3D-Modell vor...",
                        phase="optimization",
                    ),
                )
                prepared.append(
                    self.prepare(
                        model_input,
                        project_crs_info=project_crs_info,
                        staging_root=staging_root,
                        used_slugs=used_slugs,
                        project_viewer_root=project_viewer_root,
                        project_s3_prefix=project_s3_prefix,
                        on_progress=on_progress,
                        cancel_requested=cancel_requested,
                    )
                )
            return tuple(prepared)
        except BaseException:
            cleanup_prepared_model_uploads(prepared)
            raise

    def _capabilities(self) -> dict[str, Any]:
        if self.capabilities_path:
            try:
                capabilities = json.loads(Path(self.capabilities_path).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise GLBValidationError(f"Viewer-Capability-Datei ist ungültig: {error}") from error
        else:
            capabilities = load_viewer_capabilities(self.resource_root)
        if not isinstance(capabilities, dict) or capabilities.get("schema_version") != 1:
            raise GLBValidationError("Viewer-Capability-Datei hat eine nicht unterstützte Version.")
        decoders = capabilities.get("decoders")
        supported_extensions = capabilities.get("supported_extensions")
        if not isinstance(decoders, Mapping) or not isinstance(supported_extensions, list) or not all(
            isinstance(value, str) and value.strip() for value in supported_extensions
        ):
            raise GLBValidationError("Viewer-Capability-Datei enthält ungültige Decoder- oder Erweiterungsdaten.")
        blocked_by_decoder = {
            "draco": "KHR_draco_mesh_compression",
            "meshopt": "EXT_meshopt_compression",
            "ktx2_basisu": "KHR_texture_basisu",
            "webp": "EXT_texture_webp",
        }
        return {
            "blocked_extensions": [
                extension for decoder, extension in blocked_by_decoder.items() if decoders.get(decoder) is not True
            ] + (["KHR_meshopt_compression"] if decoders.get("meshopt") is not True else []),
            "supported_required_extensions": list(supported_extensions),
        }

    def _inspect_glb(
        self,
        path: Path,
        decode_root: Path | None = None,
        *,
        allow_decode: bool = True,
        cancel_requested: CancelCallback | None = None,
    ) -> _GLBInspection:
        document = _read_glb_document(path)
        _raise_if_cancelled(cancel_requested)
        capabilities = self._capabilities()
        _validate_glb_document(document, capabilities)
        compressed_extensions = _compressed_extensions(document)
        if compressed_extensions:
            if not allow_decode or self.compressed_decoder is None:
                raise GLBValidationError(
                    "GLB verwendet komprimierte Draco-, Meshopt- oder KTX2-Ressourcen, "
                    "für die kein gebündelter Decoder verfügbar ist: " + ", ".join(compressed_extensions)
                )
            temporary_root: tempfile.TemporaryDirectory[str] | None = None
            try:
                if decode_root is None:
                    temporary_root = tempfile.TemporaryDirectory(prefix="glb-decoder-")
                    output_dir = Path(temporary_root.name)
                else:
                    output_dir = decode_root / ".decoded"
                    output_dir.mkdir(parents=True, exist_ok=True)
                decoded_path = Path(
                    self.compressed_decoder.decode(path, compressed_extensions, output_dir, cancel_requested)
                )
                _raise_if_cancelled(cancel_requested)
                if not decoded_path.is_file() or decoded_path.resolve() == path.resolve():
                    raise GLBValidationError("Der gebündelte GLB-Decoder hat keine neue GLB-Ausgabe erzeugt.")
                return self._inspect_glb(decoded_path, output_dir, allow_decode=False, cancel_requested=cancel_requested)
            finally:
                if temporary_root is not None:
                    temporary_root.cleanup()
        bounds_min, bounds_max, primitive_count, triangle_count = _document_bounds(path, document)
        return _GLBInspection(
            path=path,
            document=document,
            bounds_min=bounds_min,
            bounds_max=bounds_max,
            primitive_count=primitive_count,
            triangle_count=triangle_count,
            texture_count=len(document.get("images", [])),
            control_points=_document_control_points(path, document),
        )

    def _resolve_georeferencing(
        self,
        model_input: ModelUploadInput,
        source_path: Path,
        inspection: _GLBInspection,
        project_crs_info: Mapping[str, Any] | None,
    ) -> tuple[tuple[float, ...], tuple[float, float, float], tuple[float, float, float], dict[str, Any]]:
        project_crs = _require_project_crs(project_crs_info)
        embedded = _read_embedded_georeferencing(inspection.document)
        manifest = _read_input_model_manifest(model_input, source_path)
        embedded_matrix, embedded_crs = embedded if embedded is not None else (None, None)
        manifest_matrix = _validate_native_georeferencing_matrix(manifest.get("model_to_project")) if manifest else None
        if embedded_matrix is not None:
            matrix = embedded_matrix
            if manifest_matrix is not None and matrix != manifest_matrix:
                raise GLBValidationError("model.json model_to_project weicht von der eingebetteten GLB-Georeferenzierung ab.")
        else:
            matrix = manifest_matrix or _IDENTITY_MATRIX

        _assert_optional_crs_matches_project(
            embedded_crs,
            project_crs,
            "Das eingebettete GLB-CRS oder der Höhenbezug passt nicht zur Punktwolke.",
        )
        bounds_min, bounds_max = _transform_bounds(inspection.bounds_min, inspection.bounds_max, matrix)
        if manifest is not None:
            manifest_bounds_min, manifest_bounds_max = _validate_manifest_bounds(manifest.get("bounds"))
            _assert_manifest_bounds_match(manifest_bounds_min, manifest_bounds_max, inspection, matrix)
            _assert_optional_crs_matches_project(
                _optional_manifest_crs(manifest),
                project_crs,
                "model.json-CRS oder Höhenbezug passt nicht zur Punktwolke.",
            )
        return matrix, bounds_min, bounds_max, project_crs

    def _select_candidate(
        self,
        original_path: Path,
        original_inspection: _GLBInspection,
        model_to_project: tuple[float, ...],
        stage_dir: Path,
        status: GLBToolchainStatus,
        cancel_requested: CancelCallback | None,
    ) -> tuple[Path, str, list[str], _GLBInspection]:
        if not (
            status.toolchain_available
            and status.viewer_supports_compressed_output
            and self.toolchain is not None
        ):
            return original_path, "original", [], original_inspection
        warnings: list[str] = []
        try:
            candidates = self.toolchain.optimize_candidates(original_path, stage_dir, cancel_requested)
            runner_warnings = getattr(self.toolchain, "last_warnings", ())
            for warning in runner_warnings:
                text = str(warning)
                if "E_NOT_STATIC" not in text:
                    warnings.append(f"GLB-Optimierung ausgelassen ({text})")
            available_candidates: list[tuple[str, Path]] = []
            for candidate_name, candidate_path in candidates:
                candidate = Path(candidate_path)
                if candidate.is_file():
                    available_candidates.append((str(candidate_name or "optimized"), candidate))
                else:
                    warnings.append(f"Optimierungskandidat fehlt: {candidate_name}.")
            original_size = original_path.stat().st_size
            original_signature: dict[str, Any] | None = None
            for candidate_name, candidate in sorted(
                available_candidates,
                key=lambda item: item[1].stat().st_size,
            ):
                _raise_if_cancelled(cancel_requested)
                if candidate.stat().st_size >= original_size:
                    continue
                if candidate_name.casefold() == "ktx2" and original_inspection.texture_count == 0:
                    continue
                try:
                    candidate_inspection = self._inspect_glb(candidate, stage_dir, cancel_requested=cancel_requested)
                    _assert_bounds_match(original_inspection, candidate_inspection)
                    _assert_control_points_match(original_inspection, candidate_inspection, model_to_project)
                    audited_transcoding = (
                        candidate_name.casefold() == "visura-safe"
                        and isinstance(self.toolchain, BundledGLBOptimizationToolchain)
                    )
                    if original_signature is None and not audited_transcoding:
                        original_signature = _preservation_signature(original_inspection)
                    _assert_preserved_model_features(
                        original_inspection,
                        candidate_inspection,
                        original_signature=original_signature if not audited_transcoding else None,
                        audited_transcoding=audited_transcoding,
                    )
                except GLBValidationError as error:
                    warnings.append(f"Optimierungskandidat verworfen ({candidate_name}): {error}")
                    continue
                return candidate, candidate_name, warnings, candidate_inspection
        except OperationCancelledError:
            raise
        except Exception as error:
            warnings.append(f"GLB-Optimierung fehlgeschlagen: {error}")
        return original_path, "original", warnings, original_inspection


def prepare_model_uploads(
    model_inputs: Iterable[ModelUploadInput],
    *,
    project_crs_info: Mapping[str, Any] | None,
    staging_root: str | Path,
    project_viewer_root: str = "",
    project_s3_prefix: str = "",
    on_progress: ProgressCallback | None = None,
    cancel_requested: CancelCallback | None = None,
    service: GLBOptimizationService | None = None,
) -> tuple[PreparedModelUpload, ...]:
    """Convenience entry point for upload workflows without a UI dependency."""

    return (service or GLBOptimizationService()).prepare_many(
        model_inputs,
        project_crs_info=project_crs_info,
        staging_root=staging_root,
        project_viewer_root=project_viewer_root,
        project_s3_prefix=project_s3_prefix,
        on_progress=on_progress,
        cancel_requested=cancel_requested,
    )


def validate_model_upload_input(
    model_input: ModelUploadInput,
    *,
    project_crs_info: Mapping[str, Any] | None,
    service: GLBOptimizationService | None = None,
) -> None:
    (service or GLBOptimizationService()).validate_model_upload_input(
        model_input,
        project_crs_info=project_crs_info,
    )


def build_model_index_entry(
    prepared: PreparedModelUpload,
    *,
    project_viewer_root: str,
    project_s3_prefix: str,
) -> ModelIndexEntry:
    """Create the viewer's ``models[]`` entry at its immutable content path."""

    viewer_root = _normalized_relative_root(project_viewer_root, "Viewer-Projektpfad")
    s3_root = _normalized_relative_root(project_s3_prefix, "S3-Projektpfad")
    data_version = prepared.package_sha256
    if re.fullmatch(r"[0-9a-f]{64}", data_version) is None:
        raise ValueError(
            "GLB-Upload abgebrochen: data_version fehlt oder ist kein gueltiger "
            "Paket-SHA-256 mit 64 Hex-Zeichen. Es wurden keine S3-Daten geaendert."
        )
    relative = f"models/{prepared.slug}/versions/{data_version}"
    return ModelIndexEntry(
        id=prepared.slug,
        name=prepared.name,
        viewer_path=f"{viewer_root}/{relative}/model.json",
        s3_path=f"{s3_root}/{relative}",
        crs=get_crs_technical_value(prepared.crs_info),
        vertical_crs=get_vertical_crs_technical_value(prepared.crs_info),
        crs_name=str(prepared.crs_info.get("crs_name") or prepared.crs_info.get("name") or "").strip(),
        vertical_name=str(prepared.crs_info.get("vertical_name") or "").strip(),
        vertical_datum=str(prepared.crs_info.get("vertical_datum") or "").strip(),
    )


def cleanup_prepared_model_uploads(prepared_uploads: Iterable[PreparedModelUpload]) -> None:
    """Remove only service-created stage directories after upload or rollback."""

    for prepared in prepared_uploads:
        _cleanup_stage_dir(Path(prepared.staging_dir))


def _read_glb_document(path: Path) -> dict[str, Any]:
    try:
        file_size = path.stat().st_size
    except OSError as error:
        raise GLBValidationError(f"GLB-Datei nicht lesbar: {path}") from error
    if file_size < 20:
        raise GLBValidationError("Die Modellressource ist keine GLB-2.0-Datei.")
    try:
        with path.open("rb") as stream:
            header = stream.read(_GLB_HEADER.size)
            if len(header) != _GLB_HEADER.size:
                raise GLBValidationError("GLB-Kopf ist unvollständig.")
            magic, version, declared_length = _GLB_HEADER.unpack(header)
            if magic != b"glTF" or version != 2:
                raise GLBValidationError("Die Modellressource ist keine GLB-2.0-Datei.")
            if declared_length != file_size:
                raise GLBValidationError("Die GLB-Dateilänge ist ungültig.")
            document: dict[str, Any] | None = None
            binary_chunk_length: int | None = None
            binary_chunk_offset: int | None = None
            chunk_index = 0
            while stream.tell() < declared_length:
                chunk_header = stream.read(_CHUNK_HEADER.size)
                if len(chunk_header) != _CHUNK_HEADER.size:
                    raise GLBValidationError("GLB-Chunk-Kopf ist unvollständig.")
                chunk_length, chunk_type = _CHUNK_HEADER.unpack(chunk_header)
                if chunk_length % 4 != 0 or stream.tell() + chunk_length > declared_length:
                    raise GLBValidationError("GLB-Chunk-Länge ist ungültig.")
                if chunk_index == 0 and chunk_type != _JSON_CHUNK:
                    raise GLBValidationError("Die GLB-Datei enthält keinen gültigen JSON-Chunk.")
                if chunk_type == _JSON_CHUNK:
                    if document is not None:
                        raise GLBValidationError("Die GLB-Datei enthält mehrere JSON-Chunks.")
                    raw_json = stream.read(chunk_length)
                    try:
                        parsed = json.loads(raw_json.decode("utf-8").rstrip(" \t\r\n\x00"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise GLBValidationError(f"GLB-JSON ist ungültig: {error}") from error
                    if not isinstance(parsed, dict):
                        raise GLBValidationError("GLB-JSON muss ein Objekt sein.")
                    document = parsed
                elif chunk_type == _BIN_CHUNK:
                    if binary_chunk_length is not None:
                        raise GLBValidationError("Die GLB-Datei enthält mehrere BIN-Chunks.")
                    binary_chunk_length = chunk_length
                    binary_chunk_offset = stream.tell()
                    stream.seek(chunk_length, os.SEEK_CUR)
                else:
                    raise GLBValidationError("Die GLB-Datei enthält einen nicht unterstützten Chunk.")
                chunk_index += 1
            if stream.tell() != declared_length or document is None:
                raise GLBValidationError("Die GLB-Datei enthält keinen gültigen JSON-Chunk.")
            _validate_glb_binary_layout(document, binary_chunk_length)
            # Private parser metadata.  It is never serialized and lets the
            # control-point check read only the sampled binary vertices.
            document["_dronautix_bin_chunk_offset"] = binary_chunk_offset
            document["_dronautix_bin_chunk_length"] = binary_chunk_length
            return document
    except OSError as error:
        raise GLBValidationError(f"GLB-Datei nicht lesbar: {path}") from error


def _validate_glb_document(document: dict[str, Any], capabilities: Mapping[str, Any]) -> None:
    asset = document.get("asset")
    if not isinstance(asset, dict) or str(asset.get("version", "")).strip() != "2.0":
        raise GLBValidationError("GLB enthält keine glTF-2.0-Asset-Angabe.")
    for key in ("buffers", "images", "meshes", "nodes", "scenes", "accessors", "bufferViews"):
        value = document.get(key)
        if value is not None and not isinstance(value, list):
            raise GLBValidationError(f"GLB-Feld {key} muss eine Liste sein.")
    extensions_used = _extension_list(document, "extensionsUsed")
    extensions_required = _extension_list(document, "extensionsRequired")
    if not set(extensions_required).issubset(extensions_used):
        raise GLBValidationError("extensionsRequired muss in extensionsUsed enthalten sein.")
    blocked = set(capabilities["blocked_extensions"])
    blocked_used = sorted(set(extensions_used) & blocked)
    if blocked_used:
        raise GLBValidationError(
            "Komprimierte oder transcodierte GLB-Ressourcen werden vom produktiven Viewer nicht unterstützt: "
            + ", ".join(blocked_used)
        )
    supported_required = set(capabilities["supported_required_extensions"])
    unsupported_required = sorted(set(extensions_required) - supported_required)
    if unsupported_required:
        raise GLBValidationError(
            "GLB benötigt eine im produktiven Viewer nicht bestätigte Erweiterung: "
            + ", ".join(unsupported_required)
        )
    for resource_type in ("buffers", "images"):
        for resource in document.get(resource_type, []) or []:
            if not isinstance(resource, dict):
                raise GLBValidationError(f"GLB-{resource_type} enthält keinen Objekt-Eintrag.")
            uri = resource.get("uri")
            if uri is not None and (not isinstance(uri, str) or not uri.startswith("data:")):
                raise GLBValidationError("GLB-Modelle dürfen keine externen Ressourcen nachladen.")


def _validate_glb_binary_layout(document: Mapping[str, Any], binary_chunk_length: int | None) -> None:
    """Validate every declared buffer range before trusting accessor bounds."""

    buffers = document.get("buffers", []) or []
    buffer_lengths: list[int] = []
    meshopt_virtual_buffers: set[int] = set()
    for index, buffer in enumerate(buffers):
        if not isinstance(buffer, Mapping):
            raise GLBValidationError("GLB-buffers enthält keinen Objekt-Eintrag.")
        declared_length = _nonnegative_int(buffer.get("byteLength"), "Buffer-byteLength")
        uri = buffer.get("uri")
        if uri is None:
            if index == 0 and binary_chunk_length is not None:
                if binary_chunk_length < declared_length or binary_chunk_length - declared_length > 3:
                    raise GLBValidationError("GLB-BIN-Chunk passt nicht zur deklarierten Bufferlänge.")
                available_length = declared_length
            elif _is_meshopt_virtual_buffer(document, buffer):
                # EXT_meshopt_compression represents decoded fallback bytes in
                # a second, virtual buffer. Their compressed source ranges are
                # checked below; the decoder consumes them before we read any
                # accessor from this conceptual buffer.
                meshopt_virtual_buffers.add(index)
                available_length = declared_length
            else:
                raise GLBValidationError("GLB deklariert Bufferdaten ohne zugehörigen BIN-Chunk.")
        else:
            available_length = _data_uri_length(uri)
            if available_length < declared_length:
                raise GLBValidationError("GLB-Daten-URI ist kürzer als die deklarierte Bufferlänge.")
        buffer_lengths.append(available_length)

    buffer_views = document.get("bufferViews", []) or []
    view_lengths: list[int] = []
    for view in buffer_views:
        if not isinstance(view, Mapping):
            raise GLBValidationError("GLB-bufferViews enthält keinen Objekt-Eintrag.")
        buffer_index = _nonnegative_int(view.get("buffer"), "bufferView-buffer")
        if buffer_index >= len(buffer_lengths):
            raise GLBValidationError("GLB-bufferView verweist auf einen ungültigen Buffer.")
        offset = _nonnegative_int(view.get("byteOffset", 0), "bufferView-byteOffset")
        length = _nonnegative_int(view.get("byteLength"), "bufferView-byteLength")
        if offset + length > buffer_lengths[buffer_index]:
            raise GLBValidationError("GLB-bufferView liegt außerhalb des zugehörigen Buffers.")
        stride = view.get("byteStride")
        if stride is not None:
            stride_value = _nonnegative_int(stride, "bufferView-byteStride")
            if stride_value < 4 or stride_value > 252 or stride_value % 4:
                raise GLBValidationError("GLB-bufferView-byteStride ist ungültig.")
        if buffer_index in meshopt_virtual_buffers:
            _validate_meshopt_virtual_buffer_view(view, buffer_lengths, meshopt_virtual_buffers)
        view_lengths.append(length)

    accessors = document.get("accessors", []) or []
    for accessor in accessors:
        if not isinstance(accessor, Mapping):
            raise GLBValidationError("GLB-accessors enthält keinen Objekt-Eintrag.")
        count = _nonnegative_int(accessor.get("count"), "Accessor-count")
        element_size = _accessor_element_size(accessor)
        view_index = accessor.get("bufferView")
        if view_index is not None:
            view_index = _nonnegative_int(view_index, "Accessor-bufferView")
            if view_index >= len(buffer_views):
                raise GLBValidationError("GLB-Accessor verweist auf eine ungültige bufferView.")
            accessor_offset = _nonnegative_int(accessor.get("byteOffset", 0), "Accessor-byteOffset")
            stride = buffer_views[view_index].get("byteStride", element_size)
            stride = _nonnegative_int(stride, "Accessor-byteStride")
            if stride < element_size:
                raise GLBValidationError("GLB-Accessor-byteStride ist kleiner als ein Element.")
            required = accessor_offset + ((count - 1) * stride + element_size if count else 0)
            if required > view_lengths[view_index]:
                raise GLBValidationError("GLB-Accessor liegt außerhalb seiner bufferView.")
        sparse = accessor.get("sparse")
        if sparse is not None:
            _validate_sparse_accessor(sparse, count, element_size, buffer_views, view_lengths)

    for image in document.get("images", []) or []:
        if not isinstance(image, Mapping):
            continue
        has_view = "bufferView" in image
        has_uri = "uri" in image
        if has_view == has_uri:
            raise GLBValidationError("GLB-Bild benötigt genau eine bufferView oder Daten-URI.")
        if has_view:
            view_index = _nonnegative_int(image.get("bufferView"), "Image-bufferView")
            if view_index >= len(buffer_views):
                raise GLBValidationError("GLB-Bild verweist auf eine ungültige bufferView.")
        else:
            _data_uri_length(image.get("uri"))


def _is_meshopt_virtual_buffer(document: Mapping[str, Any], buffer: Mapping[str, Any]) -> bool:
    """Return whether an absent URI is the EXT_meshopt fallback contract.

    A GLB normally has a single physical BIN-backed buffer.  glTF-Transform's
    valid Meshopt output adds a second conceptual buffer whose bytes are
    supplied by ``EXT_meshopt_compression`` bufferViews.  Never treat another
    absent URI as valid merely because Meshopt is listed somewhere.
    """

    extensions = buffer.get("extensions")
    meshopt = extensions.get("EXT_meshopt_compression") if isinstance(extensions, Mapping) else None
    if not isinstance(meshopt, Mapping) or meshopt.get("fallback") is not True:
        return False
    used = document.get("extensionsUsed", []) or []
    required = document.get("extensionsRequired", []) or []
    return "EXT_meshopt_compression" in used or "EXT_meshopt_compression" in required


def _validate_meshopt_virtual_buffer_view(
    view: Mapping[str, Any],
    buffer_lengths: list[int],
    virtual_buffers: set[int],
) -> None:
    extensions = view.get("extensions")
    compressed = extensions.get("EXT_meshopt_compression") if isinstance(extensions, Mapping) else None
    if not isinstance(compressed, Mapping):
        raise GLBValidationError("Virtueller Meshopt-bufferView hat keine EXT_meshopt_compression-Daten.")
    source_buffer = _nonnegative_int(compressed.get("buffer"), "Meshopt-buffer")
    if source_buffer >= len(buffer_lengths) or source_buffer in virtual_buffers:
        raise GLBValidationError("Meshopt-bufferView verweist auf keinen physischen Buffer.")
    source_offset = _nonnegative_int(compressed.get("byteOffset", 0), "Meshopt-byteOffset")
    source_length = _nonnegative_int(compressed.get("byteLength"), "Meshopt-byteLength")
    if source_offset + source_length > buffer_lengths[source_buffer]:
        raise GLBValidationError("Meshopt-bufferView liegt außerhalb des komprimierten Buffers.")
    stride = _nonnegative_int(compressed.get("byteStride"), "Meshopt-byteStride")
    count = _nonnegative_int(compressed.get("count"), "Meshopt-count")
    if stride <= 0 or count * stride != _nonnegative_int(view.get("byteLength"), "bufferView-byteLength"):
        raise GLBValidationError("Meshopt-bufferView hat keine passende dekodierte Länge.")
    if compressed.get("mode") not in {"ATTRIBUTES", "TRIANGLES", "INDICES"}:
        raise GLBValidationError("Meshopt-bufferView verwendet einen ungültigen Modus.")


def _validate_sparse_accessor(
    sparse: Any,
    accessor_count: int,
    element_size: int,
    buffer_views: list[Any],
    view_lengths: list[int],
) -> None:
    if not isinstance(sparse, Mapping):
        raise GLBValidationError("GLB-Accessor-sparse ist ungültig.")
    sparse_count = _nonnegative_int(sparse.get("count"), "Sparse-count")
    if sparse_count > accessor_count:
        raise GLBValidationError("GLB-Sparse-count überschreitet den Accessor-count.")
    indices = sparse.get("indices")
    values = sparse.get("values")
    if not isinstance(indices, Mapping) or not isinstance(values, Mapping):
        raise GLBValidationError("GLB-Sparse-Accessor benötigt indices und values.")
    index_component_size = {5121: 1, 5123: 2, 5125: 4}.get(indices.get("componentType"))
    if index_component_size is None:
        raise GLBValidationError("GLB-Sparse-indices verwendet einen ungültigen Komponententyp.")
    _validate_sparse_view_range(indices, sparse_count * index_component_size, buffer_views, view_lengths, "indices")
    _validate_sparse_view_range(values, sparse_count * element_size, buffer_views, view_lengths, "values")


def _validate_sparse_view_range(
    definition: Mapping[str, Any],
    required_bytes: int,
    buffer_views: list[Any],
    view_lengths: list[int],
    label: str,
) -> None:
    view_index = _nonnegative_int(definition.get("bufferView"), f"Sparse-{label}-bufferView")
    if view_index >= len(buffer_views):
        raise GLBValidationError(f"GLB-Sparse-{label} verweist auf eine ungültige bufferView.")
    offset = _nonnegative_int(definition.get("byteOffset", 0), f"Sparse-{label}-byteOffset")
    if offset + required_bytes > view_lengths[view_index]:
        raise GLBValidationError(f"GLB-Sparse-{label} liegt außerhalb seiner bufferView.")


def _accessor_element_size(accessor: Mapping[str, Any]) -> int:
    component_size = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}.get(
        accessor.get("componentType")
    )
    accessor_type = accessor.get("type")
    component_count = {
        "SCALAR": 1,
        "VEC2": 2,
        "VEC3": 3,
        "VEC4": 4,
        "MAT2": 4,
        "MAT3": 9,
        "MAT4": 16,
    }.get(accessor_type)
    if component_size is None or component_count is None:
        raise GLBValidationError("GLB-Accessor verwendet einen ungültigen Typ oder Komponententyp.")
    if accessor_type in {"MAT2", "MAT3", "MAT4"}:
        columns = int(accessor_type[-1])
        rows = columns
        column_size = rows * component_size
        aligned_column_size = ((column_size + 3) // 4) * 4
        return columns * aligned_column_size
    return component_size * component_count


def _data_uri_length(uri: Any) -> int:
    if not isinstance(uri, str) or not uri.startswith("data:") or "," not in uri:
        raise GLBValidationError("GLB enthält keine gültige selbstständige Daten-URI.")
    metadata, payload = uri.split(",", 1)
    try:
        if metadata.casefold().endswith(";base64"):
            return len(base64.b64decode(payload, validate=True))
        return len(unquote_to_bytes(payload))
    except (ValueError, binascii.Error) as error:
        raise GLBValidationError("GLB-Daten-URI ist ungültig.") from error


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GLBValidationError(f"{label} muss eine nichtnegative Ganzzahl sein.")
    return value


def _extension_list(document: Mapping[str, Any], key: str) -> tuple[str, ...]:
    values = document.get(key, [])
    if not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values):
        raise GLBValidationError(f"{key} muss eine Liste nichtleerer Erweiterungsnamen sein.")
    return tuple(value.strip() for value in values)


def _compressed_extensions(document: Mapping[str, Any]) -> tuple[str, ...]:
    """Return compressed resource extensions that require actual decoding."""

    known = {
        "KHR_draco_mesh_compression",
        "EXT_meshopt_compression",
        "KHR_meshopt_compression",
        "KHR_texture_basisu",
    }
    used = _extension_list(document, "extensionsUsed")
    return tuple(extension for extension in used if extension in known)


def _document_bounds(path: Path, document: dict[str, Any]) -> tuple[tuple[float, float, float], tuple[float, float, float], int, int]:
    nodes = document.get("nodes", []) or []
    meshes = document.get("meshes", []) or []
    accessors = document.get("accessors", []) or []
    if not nodes or not meshes:
        raise GLBValidationError("GLB enthält keine darstellbare Mesh-Szene.")
    if not all(isinstance(node, dict) for node in nodes) or not all(isinstance(mesh, dict) for mesh in meshes):
        raise GLBValidationError("GLB-Nodes oder Meshes sind ungültig.")
    if not all(isinstance(accessor, dict) for accessor in accessors):
        raise GLBValidationError("GLB-Accessors sind ungültig.")

    mesh_bounds: dict[int, tuple[tuple[float, float, float], tuple[float, float, float], int, int]] = {}
    for mesh_index, mesh in enumerate(meshes):
        primitives = mesh.get("primitives") if isinstance(mesh, dict) else None
        if not isinstance(primitives, list) or not primitives:
            raise GLBValidationError("GLB-Mesh enthält keine Primitives.")
        bounds_min: tuple[float, float, float] | None = None
        bounds_max: tuple[float, float, float] | None = None
        primitive_count = 0
        triangle_count = 0
        for primitive in primitives:
            if not isinstance(primitive, dict) or not isinstance(primitive.get("attributes"), dict):
                raise GLBValidationError("GLB-Primitive ist ungültig.")
            position_index = primitive["attributes"].get("POSITION")
            if not isinstance(position_index, int) or not 0 <= position_index < len(accessors):
                raise GLBValidationError("GLB-Primitive benötigt einen gültigen POSITION-Accessor.")
            local_min, local_max, count = _position_accessor_bounds(accessors[position_index], document)
            bounds_min = local_min if bounds_min is None else _min3(bounds_min, local_min)
            bounds_max = local_max if bounds_max is None else _max3(bounds_max, local_max)
            primitive_count += 1
            triangle_count += _primitive_triangle_count(primitive, accessors, count)
        assert bounds_min is not None and bounds_max is not None
        mesh_bounds[mesh_index] = bounds_min, bounds_max, primitive_count, triangle_count

    roots = _scene_roots(document, nodes)
    final_min: tuple[float, float, float] | None = None
    final_max: tuple[float, float, float] | None = None
    primitive_count = 0
    triangle_count = 0

    def visit(node_index: int, parent_matrix: tuple[float, ...], active: set[int]) -> None:
        nonlocal final_min, final_max, primitive_count, triangle_count
        if not isinstance(node_index, int) or not 0 <= node_index < len(nodes):
            raise GLBValidationError("GLB-Szene verweist auf einen ungültigen Node.")
        if node_index in active:
            raise GLBValidationError("GLB-Node-Hierarchie enthält einen Zyklus.")
        node = nodes[node_index]
        world = _matrix_multiply(parent_matrix, _node_matrix(node))
        mesh_index = node.get("mesh")
        if mesh_index is not None:
            if not isinstance(mesh_index, int) or mesh_index not in mesh_bounds:
                raise GLBValidationError("GLB-Node verweist auf ein ungültiges Mesh.")
            local_min, local_max, mesh_primitives, mesh_triangles = mesh_bounds[mesh_index]
            world_min, world_max = _transform_bounds(local_min, local_max, world)
            final_min = world_min if final_min is None else _min3(final_min, world_min)
            final_max = world_max if final_max is None else _max3(final_max, world_max)
            primitive_count += mesh_primitives
            triangle_count += mesh_triangles
        children = node.get("children", [])
        if not isinstance(children, list):
            raise GLBValidationError("GLB-Node-children muss eine Liste sein.")
        next_active = set(active)
        next_active.add(node_index)
        for child_index in children:
            visit(child_index, world, next_active)

    identity = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    for root in roots:
        visit(root, identity, set())
    if final_min is None or final_max is None:
        raise GLBValidationError("GLB-Szene enthält keine darstellbare Geometrie.")
    # Bounds must be taken from actual POSITION values, not trusted accessor
    # metadata. First reject stale declarations, then calculate the displayed
    # world-space extrema including every node transform.
    unique_positions = {accessor for accessor, _world in _visible_position_contexts(document)}
    for accessor_index in unique_positions:
        declared_min, declared_max, _count = _position_accessor_bounds(accessors[accessor_index], document)
        values = tuple(_iter_position_values(path, document, accessor_index))
        actual_min = tuple(min(point[index] for point in values) for index in range(3))
        actual_max = tuple(max(point[index] for point in values) for index in range(3))
        if max(abs(left - right) for left, right in zip((*declared_min, *declared_max), (*actual_min, *actual_max), strict=True)) > _BOUNDS_TOLERANCE_METRES:
            raise GLBValidationError("POSITION-Accessor-Bounds weichen mehr als 1 mm von den echten Vertexdaten ab.")
    actual_world_points = (
        _transform_point(local_point, world)
        for accessor_index, world in _visible_position_contexts(document)
        for local_point in _iter_position_values(path, document, accessor_index)
    )
    try:
        first = next(actual_world_points)
    except StopIteration as error:
        raise GLBValidationError("GLB-Szene enthält keine echte darstellbare Geometrie.") from error
    actual_min, actual_max = first, first
    for point in actual_world_points:
        actual_min = _min3(actual_min, point)
        actual_max = _max3(actual_max, point)
    return actual_min, actual_max, primitive_count, triangle_count


def _document_control_points(path: Path, document: dict[str, Any]) -> tuple[tuple[float, float, float], ...]:
    """Derive reordering-invariant landmarks from real displayed vertices."""

    contexts = _visible_position_contexts(document)

    def points() -> Iterable[tuple[float, float, float]]:
        for accessor_index, world in contexts:
            for local_point in _iter_position_values(path, document, accessor_index):
                yield _transform_point(local_point, world)

    try:
        first = min(points())
    except ValueError as error:
        raise GLBValidationError("GLB-Szene enthält keine echten POSITION-Kontrollpunkte.") from error
    second = max(points(), key=lambda point: (_squared_distance(point, first), point))
    if _squared_distance(first, second) <= _MATRIX_EPSILON * _MATRIX_EPSILON:
        raise GLBValidationError("GLB-Geometrie benötigt mindestens drei unterschiedliche echte Kontrollpunkte.")
    third = max(points(), key=lambda point: (min(_squared_distance(point, first), _squared_distance(point, second)), point))
    if min(_squared_distance(third, first), _squared_distance(third, second)) <= _MATRIX_EPSILON * _MATRIX_EPSILON:
        raise GLBValidationError("GLB-Geometrie benötigt mindestens drei unterschiedliche echte Kontrollpunkte.")
    return first, second, third


def _visible_position_contexts(document: Mapping[str, Any]) -> tuple[tuple[int, tuple[float, ...]], ...]:
    nodes = document.get("nodes", []) or []
    meshes = document.get("meshes", []) or []
    accessors = document.get("accessors", []) or []
    if not (
        all(isinstance(value, dict) for value in nodes)
        and all(isinstance(value, dict) for value in meshes)
        and all(isinstance(value, dict) for value in accessors)
    ):
        raise GLBValidationError("GLB-Geometrie enthält ungültige Nodes, Meshes oder Accessors.")
    contexts: list[tuple[int, tuple[float, ...]]] = []

    def visit(node_index: int, parent: tuple[float, ...], active: set[int]) -> None:
        if not isinstance(node_index, int) or not 0 <= node_index < len(nodes) or node_index in active:
            raise GLBValidationError("GLB-Node-Hierarchie ist ungültig oder zyklisch.")
        node = nodes[node_index]
        world = _matrix_multiply(parent, _node_matrix(node))
        mesh_index = node.get("mesh")
        if mesh_index is not None:
            if not isinstance(mesh_index, int) or not 0 <= mesh_index < len(meshes):
                raise GLBValidationError("GLB-Node verweist auf ein ungültiges Mesh.")
            for primitive in meshes[mesh_index].get("primitives", []) or []:
                attributes = primitive.get("attributes") if isinstance(primitive, Mapping) else None
                position = attributes.get("POSITION") if isinstance(attributes, Mapping) else None
                if not isinstance(position, int) or not 0 <= position < len(accessors):
                    raise GLBValidationError("GLB-Primitive benötigt einen gültigen POSITION-Accessor.")
                _position_accessor_bounds(accessors[position], document)
                contexts.append((position, world))
        children = node.get("children", [])
        if not isinstance(children, list):
            raise GLBValidationError("GLB-Node-children muss eine Liste sein.")
        for child in children:
            visit(child, world, {*active, node_index})

    for root in _scene_roots(dict(document), nodes):
        visit(root, _IDENTITY_MATRIX, set())
    return tuple(contexts)


def _iter_position_values(path: Path, document: Mapping[str, Any], accessor_index: int) -> Iterable[tuple[float, float, float]]:
    accessors = document.get("accessors", []) or []
    if not isinstance(accessor_index, int) or not 0 <= accessor_index < len(accessors):
        raise GLBValidationError("GLB-POSITION-Accessor ist ungültig.")
    accessor = accessors[accessor_index]
    if not isinstance(accessor, Mapping):
        raise GLBValidationError("GLB-POSITION-Accessor ist ungültig.")
    count = _position_accessor_bounds(accessor, document)[2]
    reader_count, value_at = _accessor_reader(path, document, accessor_index)
    if reader_count != count:
        raise GLBValidationError("GLB-POSITION-Accessor hat eine inkonsistente Anzahl.")
    for vertex_index in range(count):
        value = value_at(vertex_index)
        if len(value) != 3 or not all(math.isfinite(float(component)) for component in value):
            raise GLBValidationError("GLB-POSITION enthält ungültige Zahlen.")
        yield tuple(float(component) for component in value)


def _read_buffer_view(path: Path, document: Mapping[str, Any], view_index: int) -> bytes:
    views = document.get("bufferViews", []) or []
    buffers = document.get("buffers", []) or []
    if not isinstance(view_index, int) or not 0 <= view_index < len(views) or not isinstance(views[view_index], Mapping):
        raise GLBValidationError("GLB-bufferView ist ungültig.")
    view = views[view_index]
    buffer_index = _nonnegative_int(view.get("buffer"), "bufferView-buffer")
    if buffer_index >= len(buffers) or not isinstance(buffers[buffer_index], Mapping):
        raise GLBValidationError("GLB-bufferView verweist auf einen ungültigen Buffer.")
    length = _nonnegative_int(view.get("byteLength"), "bufferView-byteLength")
    offset = _nonnegative_int(view.get("byteOffset", 0), "bufferView-byteOffset")
    buffer = buffers[buffer_index]
    uri = buffer.get("uri")
    if uri is not None:
        data = _data_uri_bytes(uri)
        return data[offset : offset + length]
    bin_offset = document.get("_dronautix_bin_chunk_offset")
    if buffer_index != 0 or not isinstance(bin_offset, int):
        raise GLBValidationError("GLB-bufferView hat keinen lesbaren BIN-Chunk.")
    try:
        with path.open("rb") as stream:
            stream.seek(bin_offset + offset)
            data = stream.read(length)
    except OSError as error:
        raise GLBValidationError(f"GLB-Datei nicht lesbar: {path}") from error
    if len(data) != length:
        raise GLBValidationError("GLB-bufferView liegt außerhalb des BIN-Chunks.")
    return data


def _data_uri_bytes(uri: Any) -> bytes:
    if not isinstance(uri, str) or not uri.startswith("data:") or "," not in uri:
        raise GLBValidationError("GLB enthält keine gültige selbstständige Daten-URI.")
    metadata, payload = uri.split(",", 1)
    try:
        return base64.b64decode(payload, validate=True) if metadata.casefold().endswith(";base64") else unquote_to_bytes(payload)
    except (ValueError, binascii.Error) as error:
        raise GLBValidationError("GLB-Daten-URI ist ungültig.") from error


def _scene_roots(document: dict[str, Any], nodes: list[dict[str, Any]]) -> tuple[int, ...]:
    scenes = document.get("scenes", []) or []
    if scenes:
        scene_index = document.get("scene", 0)
        if not isinstance(scene_index, int) or not 0 <= scene_index < len(scenes):
            raise GLBValidationError("GLB-Standardszene ist ungültig.")
        scene = scenes[scene_index]
        if not isinstance(scene, dict) or not isinstance(scene.get("nodes", []), list):
            raise GLBValidationError("GLB-Szene enthält keine gültige Node-Liste.")
        return tuple(scene.get("nodes", []))
    children: set[int] = set()
    for node in nodes:
        node_children = node.get("children", [])
        if not isinstance(node_children, list):
            raise GLBValidationError("GLB-Node-children muss eine Liste sein.")
        for child in node_children:
            if not isinstance(child, int) or not 0 <= child < len(nodes):
                raise GLBValidationError("GLB-Node verweist auf ein ungültiges Kind.")
            children.add(child)
    return tuple(index for index in range(len(nodes)) if index not in children)


def _position_accessor_bounds(
    accessor: Mapping[str, Any], document: Mapping[str, Any] | None = None,
) -> tuple[tuple[float, float, float], tuple[float, float, float], int]:
    """Return decoded POSITION bounds, including KHR_mesh_quantization rules."""

    component_type = accessor.get("componentType")
    if accessor.get("type") != "VEC3":
        raise GLBValidationError("POSITION-Accessor muss VEC3 sein.")
    if component_type == 5126:
        if accessor.get("normalized") is not None and accessor.get("normalized") is not False:
            raise GLBValidationError("Float32-POSITION darf nicht normalized sein.")
        quantized = False
    elif component_type in {5120, 5121, 5122, 5123}:
        if document is None or "KHR_mesh_quantization" not in _extension_list(document, "extensionsUsed"):
            raise GLBValidationError(
                "Integer-POSITION benötigt die deklarierte Erweiterung KHR_mesh_quantization."
            )
        normalized = accessor.get("normalized", False)
        if not isinstance(normalized, bool):
            raise GLBValidationError("Integer-POSITION normalized muss ein boolescher Wert sein.")
        quantized = True
    else:
        raise GLBValidationError("POSITION-Accessor muss Float32 oder KHR_mesh_quantization-Integer VEC3 sein.")
    count = accessor.get("count")
    if not isinstance(count, int) or count <= 0:
        raise GLBValidationError("POSITION-Accessor hat keine gültige Anzahl.")
    # glTF 2.0 initializes an accessor without bufferView to zero. Its JSON
    # min/max may intentionally describe data supplied by an optional extension,
    # which this viewer profile does not consume.
    if "bufferView" not in accessor and "sparse" not in accessor:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), count
    raw_minimum = _finite_vector(accessor.get("min"), "POSITION-min")
    raw_maximum = _finite_vector(accessor.get("max"), "POSITION-max")
    if any(raw_minimum[index] > raw_maximum[index] for index in range(3)):
        raise GLBValidationError("POSITION-Accessor-Bounds sind ungültig.")
    if not quantized:
        return raw_minimum, raw_maximum, count
    limits = {
        5120: (-128, 127),
        5121: (0, 255),
        5122: (-32768, 32767),
        5123: (0, 65535),
    }[int(component_type)]
    if any(
        not value.is_integer() or not limits[0] <= value <= limits[1]
        for value in (*raw_minimum, *raw_maximum)
    ):
        raise GLBValidationError("Integer-POSITION-Bounds liegen außerhalb des Komponententyps.")
    raw_minimum_int = tuple(int(value) for value in raw_minimum)
    raw_maximum_int = tuple(int(value) for value in raw_maximum)
    if accessor.get("normalized", False):
        return (
            _normalized_accessor_components(raw_minimum_int, int(component_type)),
            _normalized_accessor_components(raw_maximum_int, int(component_type)),
            count,
        )
    minimum = tuple(float(value) for value in raw_minimum_int)
    maximum = tuple(float(value) for value in raw_maximum_int)
    return minimum, maximum, count


def _primitive_triangle_count(primitive: dict[str, Any], accessors: list[dict[str, Any]], position_count: int) -> int:
    mode = primitive.get("mode", 4)
    if not isinstance(mode, int):
        raise GLBValidationError("GLB-Primitive-Modus ist ungültig.")
    count = position_count
    index_accessor = primitive.get("indices")
    if index_accessor is not None:
        if not isinstance(index_accessor, int) or not 0 <= index_accessor < len(accessors):
            raise GLBValidationError("GLB-Primitive-Index-Accessor ist ungültig.")
        count = accessors[index_accessor].get("count")
        if not isinstance(count, int) or count < 0:
            raise GLBValidationError("GLB-Primitive-Indexanzahl ist ungültig.")
    if mode == 4:
        return count // 3
    if mode in {5, 6}:
        return max(0, count - 2)
    return 0


def _node_matrix(node: dict[str, Any]) -> tuple[float, ...]:
    matrix = node.get("matrix")
    if matrix is not None:
        return _validate_model_matrix(matrix)
    translation = _finite_vector(node.get("translation", (0.0, 0.0, 0.0)), "Node-Translation")
    scale = _finite_vector(node.get("scale", (1.0, 1.0, 1.0)), "Node-Skalierung")
    rotation_values = node.get("rotation", (0.0, 0.0, 0.0, 1.0))
    if not isinstance(rotation_values, (list, tuple)) or len(rotation_values) != 4:
        raise GLBValidationError("Node-Rotation ist ungültig.")
    try:
        rotation = tuple(float(value) for value in rotation_values)
    except (TypeError, ValueError) as error:
        raise GLBValidationError("Node-Rotation ist ungültig.") from error
    if not all(math.isfinite(value) for value in rotation):
        raise GLBValidationError("Node-Rotation enthält ungültige Zahlen.")
    x, y, z, w = rotation
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length <= _MATRIX_EPSILON:
        raise GLBValidationError("Node-Rotation darf nicht null sein.")
    x, y, z, w = x / length, y / length, z / length, w / length
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz, wx, wy, wz = x * y, x * z, y * z, w * x, w * y, w * z
    sx, sy, sz = scale
    return (
        (1 - 2 * (yy + zz)) * sx,
        (2 * (xy + wz)) * sx,
        (2 * (xz - wy)) * sx,
        0.0,
        (2 * (xy - wz)) * sy,
        (1 - 2 * (xx + zz)) * sy,
        (2 * (yz + wx)) * sy,
        0.0,
        (2 * (xz + wy)) * sz,
        (2 * (yz - wx)) * sz,
        (1 - 2 * (xx + yy)) * sz,
        0.0,
        translation[0],
        translation[1],
        translation[2],
        1.0,
    )


def _validate_model_matrix(values: Any) -> tuple[float, ...]:
    if not isinstance(values, (list, tuple)) or len(values) != 16:
        raise GLBValidationError("model_to_project muss aus 16 Matrixwerten bestehen.")
    try:
        matrix = tuple(float(value) for value in values)
    except (TypeError, ValueError) as error:
        raise GLBValidationError("model_to_project enthält keine Zahlen.") from error
    if not all(math.isfinite(value) for value in matrix):
        raise GLBValidationError("model_to_project enthält ungültige Zahlen.")
    if any(abs(matrix[index]) > _MATRIX_EPSILON for index in (3, 7, 11)) or abs(matrix[15] - 1.0) > _MATRIX_EPSILON:
        raise GLBValidationError("model_to_project muss eine affine Matrix sein.")
    return matrix[:3] + (0.0,) + matrix[4:7] + (0.0,) + matrix[8:11] + (0.0,) + matrix[12:15] + (1.0,)


def _validate_native_georeferencing_matrix(values: Any) -> tuple[float, ...]:
    """Validate a native Float64 matrix without normalizing its stored values."""

    if not isinstance(values, (list, tuple)) or len(values) != 16:
        raise GLBValidationError("Die eingebettete model_to_project-Matrix muss 16 Werte enthalten.")
    try:
        matrix = tuple(float(value) for value in values)
    except (TypeError, ValueError) as error:
        raise GLBValidationError("Die eingebettete model_to_project-Matrix enthält keine Zahlen.") from error
    if not all(math.isfinite(value) for value in matrix):
        raise GLBValidationError("Die eingebettete model_to_project-Matrix enthält ungültige Zahlen.")
    if any(abs(matrix[index]) > _MATRIX_EPSILON for index in (3, 7, 11)) or abs(matrix[15] - 1.0) > _MATRIX_EPSILON:
        raise GLBValidationError("Die eingebettete model_to_project-Matrix muss affin sein.")
    # Native coordinates are metres.  An orthonormal basis excludes a hidden
    # scale/shear conversion while preserving the publisher's Float64 values.
    columns = tuple((matrix[index], matrix[index + 1], matrix[index + 2]) for index in (0, 4, 8))
    if any(abs(sum(value * value for value in column) - 1.0) > 1e-9 for column in columns):
        raise GLBValidationError("Die eingebettete model_to_project-Matrix darf keine Skalierung enthalten.")
    if any(abs(sum(left[index] * right[index] for index in range(3))) > 1e-9 for left, right in ((columns[0], columns[1]), (columns[0], columns[2]), (columns[1], columns[2]))):
        raise GLBValidationError("Die eingebettete model_to_project-Matrix darf keinen Scheranteil enthalten.")
    return matrix


def _optional_native_georeferencing_matrix(values: Any) -> tuple[float, ...] | None:
    return None if values is None else _validate_native_georeferencing_matrix(values)


def _read_embedded_georeferencing(
    document: Mapping[str, Any],
) -> tuple[tuple[float, ...] | None, dict[str, Any] | None] | None:
    asset = document.get("asset")
    extras = asset.get("extras") if isinstance(asset, Mapping) else None
    georeferencing = extras.get("dronautix_georeferencing") if isinstance(extras, Mapping) else None
    if georeferencing is None:
        return None
    if not isinstance(georeferencing, Mapping):
        raise GLBValidationError("asset.extras.dronautix_georeferencing ist ungültig.")
    unit = str(georeferencing.get("unit", "")).strip().casefold()
    if unit and unit not in {"m", "metre", "meter", "metres", "meters"}:
        raise GLBValidationError("Die eingebettete GLB-Georeferenzierung muss die Einheit Meter angeben.")
    matrix_value = georeferencing.get("model_to_project_column_major", georeferencing.get("model_to_project"))
    matrix = _optional_native_georeferencing_matrix(matrix_value)
    _validate_precision_localization(georeferencing.get("precision_localization"), matrix)
    crs = georeferencing.get("crs")
    horizontal = crs.get("horizontal") if isinstance(crs, Mapping) else None
    vertical = crs.get("vertical") if isinstance(crs, Mapping) else None
    if crs is not None and not isinstance(crs, Mapping):
        raise GLBValidationError("Das eingebettete GLB-CRS ist ungültig.")
    if horizontal is not None and not isinstance(horizontal, Mapping):
        raise GLBValidationError("Das eingebettete horizontale CRS ist ungültig.")
    if vertical is not None and not isinstance(vertical, Mapping):
        raise GLBValidationError("Das eingebettete vertikale CRS ist ungültig.")
    crs_info = _normalize_embedded_crs(horizontal, vertical)
    return matrix, crs_info


def _normalize_embedded_crs(
    horizontal: Mapping[str, Any] | None,
    vertical: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Normalize embedded EPSG, OGC or authoritative WKT references."""

    def reference(value: Mapping[str, Any] | None) -> str:
        if not value:
            return ""
        if value.get("epsg") not in (None, ""):
            return _epsg_value(value.get("epsg"))
        for key in ("value", "crs", "urn", "uri", "url", "wkt"):
            candidate = str(value.get(key, "") or "").strip()
            if candidate:
                return candidate
        return ""

    return normalize_crs_metadata({
        "value": reference(horizontal),
        "crs_name": str((horizontal or {}).get("name", "") or "").strip(),
        "vertical_crs": reference(vertical),
        "vertical_name": str((vertical or {}).get("name", "") or "").strip(),
        "vertical_datum": str((vertical or {}).get("datum", "") or "").strip(),
    })


def _validate_precision_localization(value: Any, model_to_project: tuple[float, ...] | None) -> None:
    """Permit localization only when it exactly proves the selected placement."""

    if value is None:
        return
    if model_to_project is None:
        raise GLBValidationError("precision_localization benötigt eine eingebettete model_to_project-Matrix.")
    if not isinstance(value, Mapping):
        raise GLBValidationError("precision_localization muss ein Objekt mit Gegenmatrix sein.")
    forward = _validate_native_georeferencing_matrix(value.get("local_to_native_column_major"))
    inverse = _validate_native_georeferencing_matrix(value.get("native_to_local_column_major"))
    if forward != model_to_project:
        raise GLBValidationError("precision_localization local_to_native muss exakt model_to_project entsprechen.")
    identity = _matrix_multiply(forward, inverse)
    reverse_identity = _matrix_multiply(inverse, forward)
    if not (_matrix_matches(identity, _IDENTITY_MATRIX) and _matrix_matches(reverse_identity, _IDENTITY_MATRIX)):
        raise GLBValidationError("precision_localization benötigt eine exakte Gegenmatrix.")


def _epsg_value(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return text if text.casefold().startswith("epsg:") else f"EPSG:{text}"


def _matrix_matches(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    return max(abs(first - second) for first, second in zip(left, right, strict=True)) <= _MATRIX_EPSILON


def _read_input_model_manifest(model_input: ModelUploadInput, source_path: Path) -> dict[str, Any] | None:
    explicit_path = str(model_input.model_json_path or "").strip()
    if not explicit_path:
        return None
    manifest_path = Path(explicit_path)
    if not manifest_path.is_file():
        raise GLBValidationError("Angegebene model.json wurde nicht gefunden.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GLBValidationError(f"model.json ist ungültig: {error}") from error
    if not isinstance(manifest, dict):
        raise GLBValidationError("model.json muss ein JSON-Objekt sein.")
    if manifest.get("schema_version") != 1:
        raise GLBValidationError("model.json hat eine nicht unterstützte Schema-Version.")
    if str(manifest.get("format", "")).casefold() != "glb":
        raise GLBValidationError("model.json beschreibt kein GLB-Modell.")
    if manifest.get("coordinate_space") != "project_local":
        raise GLBValidationError("model.json muss project_local verwenden.")
    entrypoint = str(manifest.get("entrypoint", "") or "").strip()
    if not _safe_relative_path(entrypoint) or not entrypoint.casefold().endswith(".glb"):
        raise GLBValidationError("model.json enthält keinen sicheren relativen GLB-Einstiegspfad.")
    if Path(entrypoint).name.casefold() != source_path.name.casefold():
        raise GLBValidationError("model.json-Einstiegspunkt passt nicht zur ausgewählten GLB-Datei.")
    return manifest


def _optional_manifest_crs(manifest: Mapping[str, Any]) -> dict[str, Any] | None:
    return normalize_crs_metadata(
        {"value": manifest.get("crs"), "vertical_crs": manifest.get("vertical_crs")}
    )


def _validate_manifest_bounds(value: Any) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    if not isinstance(value, Mapping):
        raise GLBValidationError("model.json enthält keine gültigen Bounds.")
    minimum = _finite_vector(value.get("min"), "Bounds-Minimum")
    maximum = _finite_vector(value.get("max"), "Bounds-Maximum")
    if any(minimum[index] > maximum[index] for index in range(3)):
        raise GLBValidationError("model.json-Bounds sind ungültig.")
    return minimum, maximum


def _assert_manifest_bounds_match(
    bounds_min: tuple[float, float, float],
    bounds_max: tuple[float, float, float],
    inspection: _GLBInspection,
    matrix: tuple[float, ...],
) -> None:
    actual_min, actual_max = _transform_bounds(inspection.bounds_min, inspection.bounds_max, matrix)
    actual = (*actual_min, *actual_max)
    expected = (*bounds_min, *bounds_max)
    if max(abs(left - right) for left, right in zip(actual, expected, strict=True)) > _BOUNDS_TOLERANCE_METRES:
        raise GLBValidationError("model.json-Bounds weichen mehr als 1 mm von der GLB-Geometrie ab.")


def _assert_bounds_match(original: _GLBInspection, candidate: _GLBInspection) -> None:
    actual = (*original.bounds_min, *original.bounds_max)
    candidate_values = (*candidate.bounds_min, *candidate.bounds_max)
    if max(abs(left - right) for left, right in zip(actual, candidate_values, strict=True)) > _BOUNDS_TOLERANCE_METRES:
        raise GLBValidationError("Optimierte GLB-Bounds weichen mehr als 1 mm vom Original ab.")


def _assert_control_points_match(
    original: _GLBInspection,
    candidate: _GLBInspection,
    model_to_project: tuple[float, ...],
) -> None:
    if len(original.control_points) < 3 or len(candidate.control_points) < 3:
        raise GLBValidationError("GLB-Kontrollpunkte sind unvollständig.")
    source_points = tuple(_transform_point(point, model_to_project) for point in original.control_points)
    candidate_points = tuple(_transform_point(point, model_to_project) for point in candidate.control_points)
    tolerance_squared = _BOUNDS_TOLERANCE_METRES * _BOUNDS_TOLERANCE_METRES
    for index, point in enumerate(source_points, start=1):
        if min(_squared_distance(point, candidate_point) for candidate_point in candidate_points) > tolerance_squared:
            raise GLBValidationError(f"Optimierter GLB-Kontrollpunkt {index} weicht mehr als 1 mm vom Original ab.")
    for index, point in enumerate(candidate_points, start=1):
        if min(_squared_distance(point, source_point) for source_point in source_points) > tolerance_squared:
            raise GLBValidationError(f"Optimierter GLB-Kontrollpunkt {index} weicht mehr als 1 mm vom Original ab.")


def _assert_preserved_model_features(
    original: _GLBInspection,
    candidate: _GLBInspection,
    *,
    original_signature: dict[str, Any] | None = None,
    audited_transcoding: bool = False,
) -> None:
    """Reject candidates that silently lose viewer-visible glTF semantics."""

    expected_signature = original_signature if original_signature is not None else _preservation_signature(
        original,
        audited_transcoding=audited_transcoding,
    )
    if expected_signature != _preservation_signature(candidate, audited_transcoding=audited_transcoding):
        raise GLBValidationError(
            "Optimierungskandidat verändert Materialien, Texturen, Animationen, Skins, Morph Targets, "
            "Vertex-Attribute, Knotennamen, Georeferenzierung oder Szenenstruktur."
        )


def _preservation_signature(
    inspection: _GLBInspection,
    *,
    audited_transcoding: bool = False,
) -> dict[str, Any]:
    document, path = inspection.document, inspection.path
    node_ids = _node_identities(document)
    scenes = document.get("scenes", []) or []
    scene_index = document.get("scene", 0)
    if scenes:
        if not isinstance(scene_index, int) or not 0 <= scene_index < len(scenes) or not isinstance(scenes[scene_index], Mapping):
            raise GLBValidationError("GLB-Standardszene ist ungültig.")
        roots = scenes[scene_index].get("nodes", []) or []
    else:
        roots = _scene_roots(dict(document), document.get("nodes", []) or [])
    representation_extensions = {"EXT_texture_webp", "KHR_mesh_quantization"} if audited_transcoding else set()
    return {
        "scene": [
            _node_signature(
                document,
                path,
                node_ids,
                root,
                set(),
                _IDENTITY_MATRIX,
                audited_transcoding=audited_transcoding,
            )
            for root in roots
        ],
        "animations": sorted(_animation_signature(document, path, node_ids, animation) for animation in document.get("animations", []) or []),
        "extensions_used": sorted(
            extension for extension in document.get("extensionsUsed", []) or [] if extension not in representation_extensions
        ),
        "extensions_required": sorted(
            extension for extension in document.get("extensionsRequired", []) or [] if extension not in representation_extensions
        ),
        "georeferencing": _georeferencing_signature(document),
    }


def _node_identities(document: Mapping[str, Any]) -> tuple[str, ...]:
    nodes = document.get("nodes", []) or []
    if not all(isinstance(node, Mapping) for node in nodes):
        raise GLBValidationError("GLB-Nodes sind ungültig.")
    names = [str(node.get("name", "")).strip() for node in nodes]
    # Named nodes are stable across harmless array renumbering. For unnamed or
    # duplicate nodes, retain the original slot and conservatively reject an
    # ambiguous candidate instead of guessing its animation/skin target.
    return tuple(name if name and names.count(name) == 1 else f"@slot:{index}" for index, name in enumerate(names))


def _node_signature(
    document: Mapping[str, Any],
    path: Path,
    node_ids: tuple[str, ...],
    node_index: Any,
    active: set[int],
    parent_matrix: tuple[float, ...],
    *,
    audited_transcoding: bool = False,
) -> dict[str, Any]:
    nodes = document.get("nodes", []) or []
    if not isinstance(node_index, int) or not 0 <= node_index < len(nodes) or node_index in active:
        raise GLBValidationError("GLB-Node-Hierarchie ist ungültig oder zyklisch.")
    node = nodes[node_index]
    if not isinstance(node, Mapping):
        raise GLBValidationError("GLB-Node ist ungültig.")
    world = _matrix_multiply(parent_matrix, _node_matrix(dict(node)))
    children = node.get("children", []) or []
    if not isinstance(children, list):
        raise GLBValidationError("GLB-Node-children muss eine Liste sein.")
    return {
        "id": node_ids[node_index],
        "transform": "audited" if audited_transcoding else list(_node_matrix(dict(node))),
        "mesh": _mesh_signature(
            document,
            path,
            node.get("mesh"),
            world,
            audited_transcoding=audited_transcoding,
        ),
        "skin": _skin_signature(document, path, node_ids, node.get("skin")),
        "children": [
            _node_signature(
                document,
                path,
                node_ids,
                child,
                {*active, node_index},
                world,
                audited_transcoding=audited_transcoding,
            )
            for child in children
        ],
    }


def _mesh_signature(
    document: Mapping[str, Any],
    path: Path,
    mesh_index: Any,
    world_matrix: tuple[float, ...],
    *,
    audited_transcoding: bool = False,
) -> Any:
    if mesh_index is None:
        return None
    meshes = document.get("meshes", []) or []
    if not isinstance(mesh_index, int) or not 0 <= mesh_index < len(meshes) or not isinstance(meshes[mesh_index], Mapping):
        raise GLBValidationError("GLB-Node verweist auf ein ungültiges Mesh.")
    mesh = meshes[mesh_index]
    primitives = mesh.get("primitives", []) or []
    if not isinstance(primitives, list):
        raise GLBValidationError("GLB-Mesh-Primitives sind ungültig.")
    return {
        "name": mesh.get("name"),
        "weights": _canonical_value(mesh.get("weights")),
        "primitives": [
            _primitive_signature(
                document,
                path,
                primitive,
                world_matrix,
                audited_transcoding=audited_transcoding,
            )
            for primitive in primitives
        ],
    }


def _primitive_signature(
    document: Mapping[str, Any],
    path: Path,
    primitive: Any,
    world_matrix: tuple[float, ...],
    *,
    audited_transcoding: bool = False,
) -> dict[str, Any]:
    if not isinstance(primitive, Mapping) or not isinstance(primitive.get("attributes"), Mapping):
        raise GLBValidationError("GLB-Primitive ist ungültig.")
    attributes = primitive["attributes"]
    targets = primitive.get("targets", []) or []
    if not isinstance(targets, list):
        raise GLBValidationError("GLB-Morph-Targets sind ungültig.")
    ignored_attributes = {"NORMAL", "TANGENT"} if _unlit_without_normal_mapping(document, primitive.get("material")) else set()
    mode = primitive.get("mode", 4)
    if audited_transcoding:
        return {
            "mode": mode,
            "attributes": {
                name: _accessor_shape(document, index)
                for name, index in sorted(attributes.items())
                if name not in ignored_attributes
            },
            "indices": _accessor_shape(document, primitive.get("indices")) if primitive.get("indices") is not None else None,
            "material": _material_signature(
                document,
                path,
                primitive.get("material"),
                audited_transcoding=True,
            ),
            "targets": [
                {
                    name: _accessor_shape(document, index)
                    for name, index in sorted(target.items())
                    if name not in ignored_attributes
                }
                for target in targets
                if isinstance(target, Mapping)
            ],
        }
    if mode == 4:
        return {
            "mode": mode,
            "triangles": _triangle_signature(document, path, primitive, world_matrix, ignored_attributes),
            "material": _material_signature(document, path, primitive.get("material")),
        }
    return {
        "mode": mode,
        "attributes": {
            name: _accessor_digest(path, document, index)
            for name, index in sorted(attributes.items())
            if name not in ignored_attributes
        },
        # Only TRIANGLES has a complete order-independent canonicalization.
        # For every other mode a changed vertex/index buffer is unsafe, so
        # candidates fail closed while the original remains uploadable.
        "indices": _accessor_digest(path, document, primitive.get("indices")) if primitive.get("indices") is not None else None,
        "material": _material_signature(document, path, primitive.get("material")),
        "targets": [
            {
                name: _accessor_digest(path, document, index)
                for name, index in sorted(target.items())
                if name not in ignored_attributes
            }
            for target in targets
            if isinstance(target, Mapping)
        ],
    }


def _accessor_shape(document: Mapping[str, Any], accessor_index: Any) -> dict[str, Any]:
    accessors = document.get("accessors", []) or []
    if not isinstance(accessor_index, int) or not 0 <= accessor_index < len(accessors) or not isinstance(
        accessors[accessor_index], Mapping
    ):
        raise GLBValidationError("GLB verweist auf einen ungültigen Accessor.")
    accessor = accessors[accessor_index]
    return {
        "nonempty": _nonnegative_int(accessor.get("count"), "Accessor-count") > 0,
        "type": accessor.get("type"),
    }


def _unlit_without_normal_mapping(document: Mapping[str, Any], material_index: Any) -> bool:
    materials = document.get("materials", []) or []
    if not isinstance(material_index, int) or not 0 <= material_index < len(materials) or not isinstance(materials[material_index], Mapping):
        return False
    material = materials[material_index]
    extensions = material.get("extensions", {})
    return (
        isinstance(extensions, Mapping)
        and "KHR_materials_unlit" in extensions
        and "EXT_materials_bump" not in extensions
        and "normalTexture" not in material
    )


def _triangle_signature(
    document: Mapping[str, Any],
    path: Path,
    primitive: Mapping[str, Any],
    world_matrix: tuple[float, ...],
    ignored_attributes: set[str],
) -> tuple[int, str]:
    """Hash the complete displayed TRIANGLES geometry independent of order.

    glTF-Transform's reorder pass may reorder both vertices and triangles. We
    canonicalize each rendered triangle from decoded POSITION and all relevant
    per-vertex attributes, then sort its cryptographic digests. This is much
    stronger than the three control points while avoiding raw accessor-index
    coupling.
    """

    attributes = primitive.get("attributes")
    if not isinstance(attributes, Mapping) or not isinstance(attributes.get("POSITION"), int):
        raise GLBValidationError("GLB-Primitive benötigt einen gültigen POSITION-Accessor.")
    if _texcoords_are_irrelevant(document, path, primitive.get("material")):
        ignored_attributes = {*ignored_attributes, *(name for name in attributes if str(name).startswith("TEXCOORD_"))}
    readers = {
        str(name): _accessor_reader(path, document, index)
        for name, index in sorted(attributes.items())
        if name not in ignored_attributes
    }
    position_reader = readers.get("POSITION")
    if position_reader is None:
        raise GLBValidationError("GLB-Primitive hat keinen prüfbaren POSITION-Accessor.")
    position_count, _position_value = position_reader
    target_readers: list[dict[str, tuple[int, Callable[[int], tuple[float | int, ...]]]]] = []
    targets = primitive.get("targets", []) or []
    if not isinstance(targets, list):
        raise GLBValidationError("GLB-Morph-Targets sind ungültig.")
    for target in targets:
        if not isinstance(target, Mapping):
            raise GLBValidationError("GLB-Morph-Target ist ungültig.")
        target_readers.append(
            {
                str(name): _accessor_reader(path, document, index)
                for name, index in sorted(target.items())
                if name not in ignored_attributes
            }
        )
    indices_accessor = primitive.get("indices")
    if indices_accessor is None:
        indices = range(position_count)
    else:
        index_count, index_value = _accessor_reader(path, document, indices_accessor)
        decoded_indices = [index_value(index)[0] for index in range(index_count)]
        if not all(isinstance(value, int) and 0 <= value < position_count for value in decoded_indices):
            raise GLBValidationError("GLB-Primitive-Indices sind ungültig.")
        indices = decoded_indices
    indices = tuple(indices)
    if len(indices) % 3:
        raise GLBValidationError("GLB-TRIANGLES-Primitive hat keine vollständigen Dreiecke.")

    vertex_digests: dict[int, bytes] = {}

    def vertex_digest(vertex_index: int) -> bytes:
        cached = vertex_digests.get(vertex_index)
        if cached is not None:
            return cached
        digest = hashlib.sha256()
        for name, (count, value_at) in readers.items():
            if vertex_index >= count:
                raise GLBValidationError("GLB-Vertexattribut hat eine abweichende Anzahl.")
            value = value_at(vertex_index)
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            if name == "POSITION":
                if len(value) != 3:
                    raise GLBValidationError("GLB-POSITION-Accessor ist ungültig.")
                point = _transform_point((float(value[0]), float(value[1]), float(value[2])), world_matrix)
                digest.update(struct.pack("<qqq", *(round(component / _BOUNDS_TOLERANCE_METRES) for component in point)))
            else:
                digest.update(json.dumps(value, separators=(",", ":")).encode("ascii"))
        for target_index, target in enumerate(target_readers):
            digest.update(f"morph:{target_index}".encode("ascii"))
            for name, (count, value_at) in target.items():
                if vertex_index >= count:
                    raise GLBValidationError("GLB-Morph-Accessor hat eine abweichende Anzahl.")
                digest.update(name.encode("utf-8"))
                digest.update(json.dumps(value_at(vertex_index), separators=(",", ":")).encode("ascii"))
        result = digest.digest()
        vertex_digests[vertex_index] = result
        return result

    triangle_digests: list[bytes] = []
    for offset in range(0, len(indices), 3):
        vertices = tuple(vertex_digest(int(indices[offset + step])) for step in range(3))
        cycle = min(vertices, vertices[1:] + vertices[:1], vertices[2:] + vertices[:2])
        triangle_digests.append(hashlib.sha256(b"".join(cycle)).digest())
    triangle_digests.sort()
    digest = hashlib.sha256()
    for triangle in triangle_digests:
        digest.update(triangle)
    return len(triangle_digests), digest.hexdigest()


def _accessor_reader(
    path: Path, document: Mapping[str, Any], accessor_index: Any
) -> tuple[int, Callable[[int], tuple[float | int, ...]]]:
    accessors = document.get("accessors", []) or []
    if not isinstance(accessor_index, int) or not 0 <= accessor_index < len(accessors) or not isinstance(accessors[accessor_index], Mapping):
        raise GLBValidationError("GLB verweist auf einen ungültigen Accessor.")
    accessor = accessors[accessor_index]
    count = _nonnegative_int(accessor.get("count"), "Accessor-count")
    component_type = accessor.get("componentType")
    component_format = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}.get(component_type)
    component_count = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}.get(accessor.get("type"))
    if component_format is None or component_count is None:
        raise GLBValidationError("GLB-Accessor verwendet einen für Dreiecksprüfung ungültigen Typ.")
    component_size = struct.calcsize("<" + component_format)
    element_size = component_size * component_count
    view_index = accessor.get("bufferView")
    data: bytes | None = None
    offset, stride = 0, element_size
    if view_index is not None:
        views = document.get("bufferViews", []) or []
        if not isinstance(view_index, int) or not 0 <= view_index < len(views) or not isinstance(views[view_index], Mapping):
            raise GLBValidationError("GLB-Accessor verweist auf eine ungültige bufferView.")
        data = _read_buffer_view(path, document, view_index)
        offset = _nonnegative_int(accessor.get("byteOffset", 0), "Accessor-byteOffset")
        stride = _nonnegative_int(views[view_index].get("byteStride", element_size), "Accessor-byteStride")
    sparse = _sparse_accessor_values(path, document, accessor, component_format, component_count)

    def value_at(index: int) -> tuple[float | int, ...]:
        if not 0 <= index < count:
            raise GLBValidationError("GLB-Accessorwert liegt außerhalb des Bereichs.")
        if index in sparse:
            values = sparse[index]
        elif data is None:
            values: tuple[float | int, ...] = (0.0,) * component_count
        else:
            try:
                values = struct.unpack_from("<" + component_format * component_count, data, offset + index * stride)
            except struct.error as error:
                raise GLBValidationError("GLB-Accessor liegt außerhalb der Binärdaten.") from error
        if accessor.get("normalized") is True and component_type != 5126:
            return _normalized_accessor_components(values, int(component_type))
        return tuple(values)

    return count, value_at


def _sparse_accessor_values(
    path: Path,
    document: Mapping[str, Any],
    accessor: Mapping[str, Any],
    component_format: str,
    component_count: int,
) -> dict[int, tuple[float | int, ...]]:
    sparse = accessor.get("sparse")
    if sparse is None:
        return {}
    if not isinstance(sparse, Mapping):
        raise GLBValidationError("GLB-Sparse-Accessor ist ungültig.")
    indices, values = sparse.get("indices"), sparse.get("values")
    if not isinstance(indices, Mapping) or not isinstance(values, Mapping):
        raise GLBValidationError("GLB-Sparse-Accessor benötigt indices und values.")
    index_format = {5121: "B", 5123: "H", 5125: "I"}.get(indices.get("componentType"))
    if index_format is None:
        raise GLBValidationError("GLB-Sparse-indices verwendet einen ungültigen Komponententyp.")
    index_data = _read_buffer_view(path, document, _nonnegative_int(indices.get("bufferView"), "Sparse-indices-bufferView"))
    value_data = _read_buffer_view(path, document, _nonnegative_int(values.get("bufferView"), "Sparse-values-bufferView"))
    index_offset = _nonnegative_int(indices.get("byteOffset", 0), "Sparse-indices-byteOffset")
    value_offset = _nonnegative_int(values.get("byteOffset", 0), "Sparse-values-byteOffset")
    index_size = struct.calcsize("<" + index_format)
    value_size = struct.calcsize("<" + component_format) * component_count
    result: dict[int, tuple[float | int, ...]] = {}
    for sparse_index in range(_nonnegative_int(sparse.get("count"), "Sparse-count")):
        try:
            index = struct.unpack_from("<" + index_format, index_data, index_offset + sparse_index * index_size)[0]
            result[index] = struct.unpack_from("<" + component_format * component_count, value_data, value_offset + sparse_index * value_size)
        except struct.error as error:
            raise GLBValidationError("GLB-Sparse-Accessor liegt außerhalb der Binärdaten.") from error
    return result


def _normalized_accessor_components(values: tuple[float | int, ...], component_type: int) -> tuple[float, ...]:
    if component_type in {5120, 5122}:
        maximum = {5120: 127, 5122: 32767}[component_type]
        return tuple(max(-1.0, int(value) / maximum) for value in values)
    maximum = {5121: 255, 5123: 65535, 5125: 4294967295}[component_type]
    return tuple(int(value) / maximum for value in values)


def _only_uniform_base_color_texture(document: Mapping[str, Any], path: Path, material_index: Any) -> bool:
    materials = document.get("materials", []) or []
    if not isinstance(material_index, int) or not 0 <= material_index < len(materials) or not isinstance(materials[material_index], Mapping):
        return False
    material = materials[material_index]
    pbr = material.get("pbrMetallicRoughness")
    if not isinstance(pbr, Mapping) or _uniform_base_color_texture(document, path, pbr.get("baseColorTexture")) is None:
        return False
    if any(key in material for key in ("normalTexture", "occlusionTexture", "emissiveTexture")) or "metallicRoughnessTexture" in pbr:
        return False
    extensions = material.get("extensions", {})
    return isinstance(extensions, Mapping) and set(extensions).issubset({"KHR_materials_unlit"})


def _texcoords_are_irrelevant(document: Mapping[str, Any], path: Path, material_index: Any) -> bool:
    if _only_uniform_base_color_texture(document, path, material_index):
        return True
    materials = document.get("materials", []) or []
    if not isinstance(material_index, int) or not 0 <= material_index < len(materials) or not isinstance(materials[material_index], Mapping):
        return False
    # Texture-coordinate attributes contribute to every standard material
    # texture slot and to known extension slots alike.  Never discard them
    # merely because a slot happens to live below ``extensions``.
    return not _material_has_texture_reference(materials[material_index])


def _material_has_texture_reference(value: Any, key: str = "") -> bool:
    if isinstance(value, Mapping):
        if "texture" in key.casefold() and isinstance(value.get("index"), int):
            return True
        return any(_material_has_texture_reference(item, str(name)) for name, item in value.items())
    if isinstance(value, list):
        return any(_material_has_texture_reference(item, key) for item in value)
    return False


def _material_signature(
    document: Mapping[str, Any],
    path: Path,
    material_index: Any,
    *,
    audited_transcoding: bool = False,
) -> Any:
    if material_index is None:
        return None
    materials = document.get("materials", []) or []
    if not isinstance(material_index, int) or not 0 <= material_index < len(materials) or not isinstance(materials[material_index], Mapping):
        raise GLBValidationError("GLB-Primitive verweist auf ein ungültiges Material.")

    material = dict(materials[material_index])
    if audited_transcoding:
        material = _without_default_material_values(material)
    pbr = material.get("pbrMetallicRoughness")
    if isinstance(pbr, Mapping):
        pbr = dict(pbr)
        uniform_color = _uniform_base_color_texture(document, path, pbr.get("baseColorTexture"))
        if uniform_color is not None:
            factor = _base_color_factor(pbr.get("baseColorFactor"))
            if factor is not None:
                # A 1x1 PNG is coordinate-independent. Replacing it with the
                # exact multiplied factor is viewer-equivalent and explains
                # glTF-Transform's safe prune of colour swatches.
                pbr["baseColorFactor"] = [factor[index] * uniform_color[index] for index in range(4)]
                pbr.pop("baseColorTexture", None)
        material["pbrMetallicRoughness"] = pbr

    def canonical(value: Any, key: str = "") -> Any:
        if isinstance(value, Mapping):
            if "index" in value and "texture" in key.casefold():
                return {
                    "texture": _texture_signature(
                        document,
                        path,
                        value.get("index"),
                        audited_transcoding=audited_transcoding,
                    ),
                    **{name: canonical(item, name) for name, item in value.items() if name != "index"},
                }
            return {name: canonical(item, name) for name, item in sorted(value.items())}
        if isinstance(value, list):
            return [canonical(item, key) for item in value]
        return _canonical_value(value)

    return canonical(material)


def _without_default_material_values(material: Mapping[str, Any]) -> dict[str, Any]:
    """Remove explicit glTF defaults emitted by the sealed transcoder."""

    result = copy.deepcopy(dict(material))

    def remove_default(container: dict[str, Any], key: str, default: Any) -> None:
        if container.get(key) == default:
            container.pop(key, None)

    remove_default(result, "emissiveFactor", [0, 0, 0])
    remove_default(result, "alphaMode", "OPAQUE")
    remove_default(result, "alphaCutoff", 0.5)
    remove_default(result, "doubleSided", False)

    pbr = result.get("pbrMetallicRoughness")
    if isinstance(pbr, Mapping):
        normalized_pbr = dict(pbr)
        remove_default(normalized_pbr, "baseColorFactor", [1, 1, 1, 1])
        remove_default(normalized_pbr, "metallicFactor", 1)
        remove_default(normalized_pbr, "roughnessFactor", 1)
        if normalized_pbr:
            result["pbrMetallicRoughness"] = normalized_pbr
        else:
            result.pop("pbrMetallicRoughness", None)

    for texture_key, scalar_key in (("normalTexture", "scale"), ("occlusionTexture", "strength")):
        texture_info = result.get(texture_key)
        if isinstance(texture_info, Mapping):
            normalized_texture = dict(texture_info)
            remove_default(normalized_texture, scalar_key, 1)
            result[texture_key] = normalized_texture

    def normalize_texture_coordinates(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            if "texture" in key.casefold() and isinstance(value.get("index"), int):
                remove_default(value, "texCoord", 0)
            for child_key, child in value.items():
                normalize_texture_coordinates(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                normalize_texture_coordinates(child, key)

    normalize_texture_coordinates(result)
    return result


def _base_color_factor(value: Any) -> tuple[float, float, float, float] | None:
    if value is None:
        return 1.0, 1.0, 1.0, 1.0
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        factor = tuple(float(component) for component in value)
    except (TypeError, ValueError):
        return None
    return factor if all(math.isfinite(component) for component in factor) else None  # type: ignore[return-value]


def _uniform_base_color_texture(
    document: Mapping[str, Any], path: Path, texture_info: Any
) -> tuple[float, float, float, float] | None:
    if not isinstance(texture_info, Mapping) or not isinstance(texture_info.get("index"), int):
        return None
    textures = document.get("textures", []) or []
    texture_index = texture_info["index"]
    if not 0 <= texture_index < len(textures) or not isinstance(textures[texture_index], Mapping):
        return None
    texture = textures[texture_index]
    if texture.get("extensions") or not isinstance(texture.get("source"), int):
        return None
    images = document.get("images", []) or []
    source = texture["source"]
    if not 0 <= source < len(images) or not isinstance(images[source], Mapping):
        return None
    image = images[source]
    try:
        data = _data_uri_bytes(image.get("uri")) if "uri" in image else _read_buffer_view(
            path, document, _nonnegative_int(image.get("bufferView"), "Image-bufferView")
        )
    except GLBValidationError:
        return None
    return _single_pixel_png_rgba(data) if image.get("mimeType") in {None, "image/png"} else None


def _single_pixel_png_rgba(data: bytes) -> tuple[float, float, float, float] | None:
    """Decode the intentionally narrow, lossless 1x1 PNG colour-swatch case."""

    decoded = _decode_png_rgba(data)
    if decoded is None:
        return None
    width, height, rgba, _profile = decoded
    if (width, height) != (1, 1):
        return None
    return tuple(value / 255 for value in rgba[:4])  # type: ignore[return-value]


def _png_pixel_signature(data: bytes) -> dict[str, Any] | None:
    decoded = _decode_png_rgba(data)
    if decoded is None:
        return None
    width, height, rgba, profile = decoded
    return {
        "width": width,
        "height": height,
        "channels": "rgba8",
        "pixels_sha256": hashlib.sha256(rgba).hexdigest(),
        "color_profile_sha256": hashlib.sha256(profile).hexdigest(),
    }


def _decode_png_rgba(data: bytes) -> tuple[int, int, bytes, bytes] | None:
    """Decode non-interlaced 8-bit PNG pixels with the Python stdlib only."""

    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    offset, width, height, bit_depth, color_type, interlace = 8, 0, 0, 0, 0, 1
    compressed = bytearray()
    profile = bytearray()
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4 : offset + 8]
        payload_end = offset + 8 + length
        if payload_end + 4 > len(data):
            return None
        payload = data[offset + 8 : payload_end]
        if kind == b"IHDR" and len(payload) == 13:
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            if compression != 0 or filtering != 0:
                return None
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind in {b"sRGB", b"gAMA", b"cHRM", b"iCCP"}:
            profile.extend(kind + payload)
        elif kind == b"IEND":
            break
        offset = payload_end + 4
    if width <= 0 or height <= 0 or bit_depth != 8 or interlace != 0 or color_type not in {0, 2, 4, 6}:
        return None
    components = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    stride = width * components
    try:
        raw = zlib.decompress(compressed)
    except zlib.error:
        return None
    if len(raw) != height * (stride + 1):
        return None
    previous = bytearray(stride)
    rgba = bytearray(width * height * 4)
    source_offset = target_offset = 0
    for _row in range(height):
        filter_type = raw[source_offset]
        source_offset += 1
        filtered = raw[source_offset : source_offset + stride]
        source_offset += stride
        reconstructed = bytearray(stride)
        for index, value in enumerate(filtered):
            left = reconstructed[index - components] if index >= components else 0
            above = previous[index]
            upper_left = previous[index - components] if index >= components else 0
            if filter_type == 0:
                reconstructed[index] = value
            elif filter_type == 1:
                reconstructed[index] = (value + left) & 0xFF
            elif filter_type == 2:
                reconstructed[index] = (value + above) & 0xFF
            elif filter_type == 3:
                reconstructed[index] = (value + ((left + above) // 2)) & 0xFF
            elif filter_type == 4:
                pa = abs(above - upper_left)
                pb = abs(left - upper_left)
                pc = abs(left + above - 2 * upper_left)
                predictor = left if pa <= pb and pa <= pc else above if pb <= pc else upper_left
                reconstructed[index] = (value + predictor) & 0xFF
            else:
                return None
        for pixel in range(width):
            values = reconstructed[pixel * components : (pixel + 1) * components]
            if color_type == 0:
                rgba[target_offset : target_offset + 4] = bytes((values[0], values[0], values[0], 255))
            elif color_type == 2:
                rgba[target_offset : target_offset + 4] = bytes((*values, 255))
            elif color_type == 4:
                rgba[target_offset : target_offset + 4] = bytes((values[0], values[0], values[0], values[1]))
            else:
                rgba[target_offset : target_offset + 4] = values
            target_offset += 4
        previous = reconstructed
    return width, height, bytes(rgba), bytes(profile)


def _texture_signature(
    document: Mapping[str, Any],
    path: Path,
    texture_index: Any,
    *,
    audited_transcoding: bool = False,
) -> Any:
    textures = document.get("textures", []) or []
    if not isinstance(texture_index, int) or not 0 <= texture_index < len(textures) or not isinstance(textures[texture_index], Mapping):
        raise GLBValidationError("GLB-Material verweist auf eine ungültige Textur.")
    texture = textures[texture_index]
    extensions = texture.get("extensions", {})
    if not isinstance(extensions, Mapping):
        raise GLBValidationError("GLB-Textur-Extensions sind ungültig.")
    source = texture.get("source")
    alternative_sources = [
        extension.get("source")
        for name in ("KHR_texture_basisu", "EXT_texture_webp", "EXT_texture_avif")
        if isinstance((extension := extensions.get(name)), Mapping) and isinstance(extension.get("source"), int)
    ]
    distinct_alternatives = set(alternative_sources)
    if len(distinct_alternatives) > 1:
        raise GLBValidationError("GLB-Textur hat mehrere widersprüchliche alternative Bildquellen.")
    if alternative_sources:
        source = alternative_sources[0]
    if not isinstance(source, int):
        raise GLBValidationError("GLB-Textur hat keine prüfbare Bildquelle.")
    samplers = document.get("samplers", []) or []
    sampler_index = texture.get("sampler")
    if sampler_index is not None and (
        not isinstance(sampler_index, int) or not 0 <= sampler_index < len(samplers)
    ):
        raise GLBValidationError("GLB-Textur verweist auf einen ungültigen Sampler.")
    semantic_extensions = dict(extensions)
    # A bundled decoder may materialize KTX2 as a regular image source.  The
    # image signature below is the proof of equivalence; retaining the
    # container-only source field would wrongly reject that safe conversion.
    semantic_extensions.pop("KHR_texture_basisu", None)
    if audited_transcoding:
        semantic_extensions.pop("EXT_texture_webp", None)
        semantic_extensions.pop("EXT_texture_avif", None)
    sampler = samplers[sampler_index] if isinstance(sampler_index, int) else {}
    sampler_defaults = {"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}
    sampler_signature = {
        key: value
        for key, value in dict(sampler).items()
        if key not in sampler_defaults or value != sampler_defaults[key]
    }
    return {
        "sampler": _canonical_value(sampler_signature),
        "source": _image_signature(
            document,
            path,
            source,
            audited_transcoding=audited_transcoding,
        ),
        "extensions": _canonical_value(semantic_extensions),
    }


def _image_signature(
    document: Mapping[str, Any],
    path: Path,
    image_index: Any,
    *,
    audited_transcoding: bool = False,
) -> Any:
    images = document.get("images", []) or []
    if not isinstance(image_index, int) or not 0 <= image_index < len(images) or not isinstance(images[image_index], Mapping):
        raise GLBValidationError("GLB-Textur verweist auf ein ungültiges Bild.")
    image = images[image_index]
    if "uri" in image:
        data = _data_uri_bytes(image.get("uri"))
    else:
        data = _read_buffer_view(path, document, _nonnegative_int(image.get("bufferView"), "Image-bufferView"))
    if audited_transcoding:
        if not data:
            raise GLBValidationError("GLB-Texturbild ist leer.")
        return {"quality": "sealed-runner-audited"}
    pixels = _png_pixel_signature(data)
    if pixels is not None:
        return {"decoded_png": pixels}
    # JPEG/KTX2 cannot be decoded by this stdlib-only verifier. A candidate is
    # therefore accepted only after the bundled decoder has materialized a
    # supported PNG, otherwise the source bytes must be exactly preserved.
    return {"mimeType": image.get("mimeType"), "sha256": hashlib.sha256(data).hexdigest()}


def _skin_signature(document: Mapping[str, Any], path: Path, node_ids: tuple[str, ...], skin_index: Any) -> Any:
    if skin_index is None:
        return None
    skins = document.get("skins", []) or []
    if not isinstance(skin_index, int) or not 0 <= skin_index < len(skins) or not isinstance(skins[skin_index], Mapping):
        raise GLBValidationError("GLB-Node verweist auf einen ungültigen Skin.")
    skin = skins[skin_index]
    joints = skin.get("joints", [])
    if not isinstance(joints, list) or not all(isinstance(joint, int) and 0 <= joint < len(node_ids) for joint in joints):
        raise GLBValidationError("GLB-Skin-Joints sind ungültig.")
    return {
        "joints": [node_ids[joint] for joint in joints],
        "skeleton": node_ids[skin["skeleton"]] if isinstance(skin.get("skeleton"), int) and 0 <= skin["skeleton"] < len(node_ids) else None,
        "inverse_bind": _accessor_digest(path, document, skin.get("inverseBindMatrices")) if skin.get("inverseBindMatrices") is not None else None,
    }


def _animation_signature(document: Mapping[str, Any], path: Path, node_ids: tuple[str, ...], animation: Any) -> str:
    if not isinstance(animation, Mapping):
        raise GLBValidationError("GLB-Animation ist ungültig.")
    samplers = animation.get("samplers", []) or []
    channels = animation.get("channels", []) or []
    if not isinstance(samplers, list) or not isinstance(channels, list):
        raise GLBValidationError("GLB-Animation enthält ungültige Sampler oder Kanäle.")
    semantic_channels = []
    for channel in channels:
        if not isinstance(channel, Mapping) or not isinstance(channel.get("sampler"), int) or not 0 <= channel["sampler"] < len(samplers):
            raise GLBValidationError("GLB-Animationskanal ist ungültig.")
        sampler = samplers[channel["sampler"]]
        target = channel.get("target")
        if not isinstance(sampler, Mapping) or not isinstance(target, Mapping):
            raise GLBValidationError("GLB-Animationskanal ist ungültig.")
        node = target.get("node")
        if not isinstance(node, int) or not 0 <= node < len(node_ids):
            raise GLBValidationError("GLB-Animationskanal hat kein stabiles Node-Ziel.")
        semantic_channels.append({
            "target": {"node": node_ids[node], "path": target.get("path")},
            "interpolation": sampler.get("interpolation", "LINEAR"),
            "input": _accessor_digest(path, document, sampler.get("input")),
            "output": _accessor_digest(path, document, sampler.get("output")),
        })
    return json.dumps(sorted(semantic_channels, key=lambda value: json.dumps(value, sort_keys=True)), sort_keys=True, separators=(",", ":"))


def _accessor_digest(path: Path, document: Mapping[str, Any], accessor_index: Any) -> str:
    accessors = document.get("accessors", []) or []
    if not isinstance(accessor_index, int) or not 0 <= accessor_index < len(accessors) or not isinstance(accessors[accessor_index], Mapping):
        raise GLBValidationError("GLB verweist auf einen ungültigen Accessor.")
    accessor = accessors[accessor_index]
    digest = hashlib.sha256(json.dumps({key: accessor.get(key) for key in ("componentType", "count", "type", "normalized")}, sort_keys=True).encode()).digest()
    hasher = hashlib.sha256(digest)
    view_index = accessor.get("bufferView")
    if view_index is not None:
        views = document.get("bufferViews", []) or []
        if not isinstance(view_index, int) or not 0 <= view_index < len(views) or not isinstance(views[view_index], Mapping):
            raise GLBValidationError("GLB-Accessor verweist auf eine ungültige bufferView.")
        data = _read_buffer_view(path, document, view_index)
        element_size = _accessor_element_size(accessor)
        offset = _nonnegative_int(accessor.get("byteOffset", 0), "Accessor-byteOffset")
        stride = _nonnegative_int(views[view_index].get("byteStride", element_size), "Accessor-byteStride")
        for index in range(_nonnegative_int(accessor.get("count"), "Accessor-count")):
            hasher.update(data[offset + index * stride : offset + index * stride + element_size])
    sparse = accessor.get("sparse")
    if sparse is not None:
        hasher.update(json.dumps(_canonical_value(sparse), sort_keys=True).encode())
    return hasher.hexdigest()


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _georeferencing_signature(document: Mapping[str, Any]) -> Any:
    asset = document.get("asset")
    extras = asset.get("extras") if isinstance(asset, Mapping) else None
    if not isinstance(extras, Mapping):
        return None
    return extras.get("dronautix_georeferencing")


def _require_project_crs(crs_info: Mapping[str, Any] | None) -> dict[str, Any]:
    try:
        normalized = normalize_crs_metadata(crs_info)
        horizontal = get_crs_technical_value(normalized)
        vertical = get_vertical_crs_technical_value(normalized)
    except CrsValidationError as error:
        raise GLBValidationError(f"Projekt-CRS ist nicht eindeutig: {error}") from error
    if not normalized or not horizontal:
        raise GLBValidationError("Das technische horizontale Projekt-CRS fehlt oder ist ungültig.")
    if not vertical:
        raise GLBValidationError("Das technische vertikale Projekt-CRS fehlt oder ist ungültig.")
    return normalized


def _assert_optional_crs_matches_project(
    declared_crs: Mapping[str, Any] | None,
    project_crs: Mapping[str, Any],
    error_message: str,
) -> None:
    """A supplied horizontal or vertical reference must agree with the project.

    Missing metadata deliberately inherits the project reference.  Values are
    compared per component so an embedded EPSG still agrees with project
    metadata that additionally records a vertical datum name.
    """

    try:
        declared = normalize_crs_metadata(declared_crs)
    except CrsValidationError as error:
        raise GLBValidationError(f"{error_message} {error}") from error
    if not declared:
        return
    declared_horizontal = get_crs_technical_value(declared)
    declared_vertical = get_vertical_crs_technical_value(declared)
    project_horizontal = get_crs_technical_value(project_crs)
    project_vertical = get_vertical_crs_technical_value(project_crs)
    if declared_horizontal and declared_horizontal != project_horizontal:
        raise GLBValidationError(error_message)
    if declared_vertical and declared_vertical != project_vertical:
        raise GLBValidationError(error_message)


def _build_model_manifest(
    *,
    matrix: tuple[float, ...],
    bounds_min: tuple[float, float, float],
    bounds_max: tuple[float, float, float],
    crs_info: Mapping[str, Any],
    original_sha256: str,
    optimization: GLBOptimizationResult,
    toolchain_status: GLBToolchainStatus,
) -> dict[str, Any]:
    normalized_crs = normalize_crs_metadata(crs_info) or {}
    manifest = {
        "schema_version": 1,
        "format": "glb",
        "coordinate_space": "project_local",
        "entrypoint": "scene.glb",
        "model_to_project": list(matrix),
        "bounds": {"min": list(bounds_min), "max": list(bounds_max)},
        "crs": get_crs_technical_value(normalized_crs),
        "vertical_crs": get_vertical_crs_technical_value(normalized_crs),
        "original_sha256": original_sha256,
        "toolchain": {
            "mode": toolchain_status.mode,
            "available": toolchain_status.toolchain_available,
            "versions": dict(optimization.toolchain_versions),
            "viewer_capability_version": str(toolchain_status.viewer_capabilities.get("capability_version", "")),
        },
        "optimization": {
            "selected_candidate": optimization.selected_candidate,
            "source_size": optimization.source_size,
            "output_size": optimization.output_size,
            "output_sha256": optimization.output_sha256,
            "primitive_count": optimization.primitive_count,
            "triangle_count": optimization.triangle_count,
            "texture_count": optimization.texture_count,
            "control_points": [list(point) for point in optimization.control_points],
            "used_fallback": optimization.used_fallback,
            "fallback_reason": optimization.fallback_reason,
            "warnings": list(optimization.warnings),
        },
    }
    for key, value in (
        ("crs_name", normalized_crs.get("crs_name") or normalized_crs.get("name")),
        ("vertical_name", normalized_crs.get("vertical_name")),
        ("vertical_datum", normalized_crs.get("vertical_datum")),
    ):
        text = str(value or "").strip()
        if text:
            manifest[key] = text
    return manifest


def _model_package_sha256(scene_sha256: str, manifest: Mapping[str, Any]) -> str:
    """Hash the immutable GLB bytes identity and canonical manifest content."""

    scene_hash = str(scene_sha256 or "").lower()
    if re.fullmatch(r"[0-9a-f]{64}", scene_hash) is None:
        raise GLBValidationError("scene.glb besitzt keinen gueltigen SHA-256-Hash.")
    canonical_manifest = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(bytes.fromhex(scene_hash))
    digest.update(b"\0")
    digest.update(canonical_manifest)
    return digest.hexdigest()


def _source_path(model_input: ModelUploadInput) -> Path:
    source = Path(str(model_input.source_path or "").strip())
    if not str(source) or source.suffix.casefold() != ".glb":
        raise GLBValidationError("Nur .glb-Modelle werden unterstützt.")
    if not source.is_file():
        raise GLBValidationError(f"GLB-Datei wurde nicht gefunden: {source}")
    return source


def _model_display_name(model_input: ModelUploadInput, source_path: Path) -> str:
    return str(model_input.name or "").strip() or source_path.stem or "Modell"


def _model_slug(model_input: ModelUploadInput, name: str, used_slugs: set[str] | None) -> str:
    desired = sanitize_folder_name(str(model_input.slug or "")) or sanitize_folder_name(name) or "modell"
    if used_slugs is None:
        return desired
    return make_unique_slug(desired, used_slugs)


def _assert_unique_model_inputs(model_inputs: tuple[ModelUploadInput, ...]) -> None:
    seen: set[str] = set()
    for model_input in model_inputs:
        source = _source_path(model_input)
        normalized = os.path.normcase(os.path.abspath(source))
        if normalized in seen:
            raise GLBValidationError("Dasselbe GLB-Modell wurde mehrfach ausgewählt.")
        seen.add(normalized)


def _normalized_relative_root(value: str, label: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/").strip("/")
    if not _safe_relative_path(normalized):
        raise GLBValidationError(f"{label} ist nicht sicher.")
    return normalized


def _safe_relative_path(value: str) -> bool:
    path = str(value or "").strip().replace("\\", "/")
    if not path or path.startswith("/") or ":" in path or "?" in path or "#" in path:
        return False
    return all(part not in {"", ".", ".."} for part in path.split("/"))


def _finite_vector(value: Any, label: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise GLBValidationError(f"{label} muss drei Zahlen enthalten.")
    try:
        vector = tuple(float(item) for item in value)
    except (TypeError, ValueError) as error:
        raise GLBValidationError(f"{label} enthält keine Zahlen.") from error
    if not all(math.isfinite(item) for item in vector):
        raise GLBValidationError(f"{label} enthält ungültige Zahlen.")
    return vector  # type: ignore[return-value]


def _matrix_multiply(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(
        sum(left[row + inner * 4] * right[inner + column * 4] for inner in range(4))
        for column in range(4)
        for row in range(4)
    )


def _transform_bounds(
    minimum: tuple[float, float, float], maximum: tuple[float, float, float], matrix: tuple[float, ...]
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    points = [
        _transform_point((x, y, z), matrix)
        for x in (minimum[0], maximum[0])
        for y in (minimum[1], maximum[1])
        for z in (minimum[2], maximum[2])
    ]
    return (
        tuple(min(point[index] for point in points) for index in range(3)),  # type: ignore[return-value]
        tuple(max(point[index] for point in points) for index in range(3)),  # type: ignore[return-value]
    )


def _transform_point(point: tuple[float, float, float], matrix: tuple[float, ...]) -> tuple[float, float, float]:
    x, y, z = point
    transformed = (
        matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
        matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
        matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14],
    )
    if not all(math.isfinite(value) for value in transformed):
        raise GLBValidationError("GLB-Node-Transformation erzeugt ungültige Bounds.")
    return transformed


def _squared_distance(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return sum((first - second) ** 2 for first, second in zip(left, right, strict=True))


def _min3(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(min(left[index], right[index]) for index in range(3))  # type: ignore[return-value]


def _max3(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(max(left[index], right[index]) for index in range(3))  # type: ignore[return-value]


def _copy_with_cancel(source: Path, target: Path, cancel_requested: CancelCallback | None) -> None:
    try:
        with source.open("rb") as reader, target.open("wb") as writer:
            while chunk := reader.read(1024 * 1024):
                _raise_if_cancelled(cancel_requested)
                writer.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    shutil.copystat(source, target, follow_symlinks=True)


def _run_bundled_runner(
    resource_root: str | Path | None,
    runner_id: str,
    arguments: tuple[str, ...],
    cancel_requested: CancelCallback | None,
) -> None:
    """Invoke a declared runner with the declared node executable only."""

    node = get_bundled_tool_path("node", resource_root)
    runner = get_bundled_runner_path(runner_id, resource_root)
    if not node.is_file() or not runner.is_file():
        raise GLBValidationError(f"Gebündelter GLB-{runner_id}-Runner fehlt.")
    process_group_options: dict[str, Any]
    if os.name == "nt":
        process_group_options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        process_group_options = {"start_new_session": True}
    try:
        process = subprocess.Popen(
            [str(node), str(runner), *arguments],
            cwd=runner.parent.parent,
            env=get_bundled_toolchain_environment(resource_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **process_group_options,
        )
    except OSError as error:
        raise GLBValidationError(f"Gebündelter GLB-{runner_id}-Runner startet nicht: {error}") from error
    try:
        while True:
            _raise_if_cancelled(cancel_requested)
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode != 0:
            detail = (stderr or stdout).strip().replace("\r", " ").replace("\n", " ")
            raise GLBValidationError(f"Gebündelter GLB-{runner_id}-Runner fehlgeschlagen: {detail[:240]}")
    except BaseException:
        if process.poll() is None:
            _terminate_process_tree(process)
        raise


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Stop the bundled runner and every codec process it started."""

    if os.name == "nt":
        _terminate_windows_process_tree(process.pid)
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _terminate_windows_process_tree(root_pid: int) -> None:
    """Terminate an exact Windows descendant tree without invoking a shell."""

    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    descendants: dict[int, list[int]] = {}
    if snapshot != invalid_handle:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                descendants.setdefault(int(entry.th32ParentProcessID), []).append(int(entry.th32ProcessID))
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        kernel32.CloseHandle(snapshot)

    ordered: list[int] = []

    def collect(pid: int) -> None:
        for child_pid in descendants.get(pid, ()):
            collect(child_pid)
            ordered.append(child_pid)

    collect(root_pid)
    ordered.append(root_pid)
    for pid in ordered:
        handle = kernel32.OpenProcess(0x0001 | 0x00100000, False, pid)
        if not handle:
            continue
        try:
            kernel32.TerminateProcess(handle, 1)
            kernel32.WaitForSingleObject(handle, 5000)
        finally:
            kernel32.CloseHandle(handle)


def _sha256(path: Path, cancel_requested: CancelCallback | None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            _raise_if_cancelled(cancel_requested)
            digest.update(chunk)
    return digest.hexdigest()


def _raise_if_cancelled(cancel_requested: CancelCallback | None) -> None:
    if cancel_requested is not None and cancel_requested():
        raise OperationCancelledError("GLB-Optimierung wurde abgebrochen.")


def _cleanup_stage_dir(stage_dir: Path) -> None:
    if stage_dir.name.startswith(".glb-upload-"):
        shutil.rmtree(stage_dir, ignore_errors=True)


def _emit(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    if callback is not None:
        callback(event)


__all__ = [
    "GLBOptimizationService",
    "GLBCompressedAssetDecoder",
    "GLBOptimizationToolchain",
    "GLBToolchainStatus",
    "GLBValidationError",
    "build_model_index_entry",
    "cleanup_prepared_model_uploads",
    "prepare_model_uploads",
    "validate_model_upload_input",
]
