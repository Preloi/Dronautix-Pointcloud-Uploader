"""Prepare pointcloud inputs for upload and replacement workflows."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .contracts import PointcloudSource, ProgressCallback, ProgressEvent
from .local_conversion_service import (
    ConverterRunner,
    LocalConversionRequest,
    build_local_output_dir,
    run_local_conversion,
)
from .naming_service import get_pointcloud_display_name, make_unique_slug


@dataclass(frozen=True)
class PointcloudPreparationRequest:
    sources: tuple[str, ...]
    converter_path: str = ""
    output_base_dir: str = ""
    overwrite: bool = False


def prepare_pointcloud_sources(
    request: PointcloudPreparationRequest,
    on_progress: ProgressCallback | None = None,
    converter_runner: ConverterRunner | None = None,
) -> tuple[PointcloudSource, ...]:
    """Convert/classify raw inputs into upload-ready COPC or Potree sources."""

    if not request.sources:
        raise ValueError("Bitte mindestens eine Punktwolkenquelle auswaehlen.")

    prepared: list[PointcloudSource] = []
    used_slugs: set[str] = set()
    total = len(request.sources)
    for index, source_path in enumerate(request.sources, start=1):
        source = str(source_path or "").strip()
        if not source:
            raise ValueError("Leerer Punktwolkenpfad ist ungueltig.")
        _emit(on_progress, ProgressEvent(kind="step", step=index, total_steps=total, message="Bereite Punktwolke vor..."))
        _emit(on_progress, ProgressEvent(kind="detail", detail=source))

        input_format = classify_pointcloud_source(source)
        name = get_pointcloud_display_name(source)
        slug = make_unique_slug(name, used_slugs)

        if input_format == "copc":
            prepared.append(
                PointcloudSource(
                    source_path=source,
                    name=name,
                    slug=slug,
                    input_format="copc",
                    source_type="raw_file",
                )
            )
            continue

        if input_format == "potree":
            prepared.append(
                PointcloudSource(
                    source_path=source,
                    name=name,
                    slug=slug,
                    input_format="potree",
                    source_type="potree_dir",
                )
            )
            continue

        if input_format == "raw":
            if not request.converter_path:
                raise ValueError("Kein Potree Converter fuer LAS/LAZ-Vorbereitung angegeben.")
            if not request.output_base_dir:
                raise ValueError("Kein Ausgabeordner fuer LAS/LAZ-Vorbereitung angegeben.")
            output_dir = build_local_output_dir(source, request.output_base_dir)
            result = run_local_conversion(
                LocalConversionRequest(
                    source_file=source,
                    output_dir=output_dir,
                    converter_path=request.converter_path,
                    overwrite=request.overwrite,
                ),
                on_progress=on_progress,
                converter_runner=converter_runner or _default_converter_runner,
            )
            prepared.append(
                PointcloudSource(
                    source_path=result.output_dir,
                    name=name,
                    slug=slug,
                    input_format="potree",
                    source_type="potree_dir",
                )
            )
            continue

        raise ValueError(f"Nicht unterstuetzte Punktwolkenquelle: {source}")

    _emit(on_progress, ProgressEvent(kind="progress", percent=1.0))
    return tuple(prepared)


def classify_pointcloud_source(source_path: str) -> str:
    source = str(source_path or "").strip()
    if os.path.isdir(source):
        if _is_potree_dir(source):
            return "potree"
        raise ValueError(f"Ordner ist kein Potree-Projekt: {source}")
    if not os.path.isfile(source):
        raise ValueError(f"Punktwolkenquelle nicht gefunden: {source}")

    lower_name = os.path.basename(source).lower()
    extension = os.path.splitext(lower_name)[1]
    if lower_name.endswith(".copc.laz"):
        return "copc"
    if extension in {".las", ".laz"}:
        return "raw"
    raise ValueError(f"Nicht unterstuetztes Punktwolkenformat: {source}")


def _is_potree_dir(source_dir: str) -> bool:
    return os.path.isfile(os.path.join(source_dir, "metadata.json")) or os.path.isfile(
        os.path.join(source_dir, "cloud.js")
    )


def _default_converter_runner(source_file, converter_path, output_dir, on_progress):
    from .converter_service import run_potree_conversion

    run_potree_conversion(source_file, converter_path, output_dir, on_progress)


def _emit(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    if callback:
        callback(event)


__all__ = [
    "PointcloudPreparationRequest",
    "classify_pointcloud_source",
    "prepare_pointcloud_sources",
]
