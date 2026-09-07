"""UI-free local LAS/LAZ to Potree conversion workflow."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Callable

from .contracts import CancelCallback, ProgressCallback, ProgressEvent
from .converter_service import run_potree_conversion, validate_potree_output
from .naming_service import sanitize_folder_name


@dataclass(frozen=True)
class LocalConversionRequest:
    source_file: str
    output_dir: str
    converter_path: str
    overwrite: bool = False


@dataclass(frozen=True)
class LocalConversionResult:
    output_dir: str
    message: str


ConverterRunner = Callable[[str, str, str, ProgressCallback | None], None]


def build_local_output_dir(source_path: str, output_base_dir: str, unique_name: str = "") -> str:
    source_name = unique_name or os.path.splitext(os.path.basename(source_path))[0]
    folder_name = sanitize_folder_name(source_name) or "potree_export"
    return os.path.abspath(os.path.join(output_base_dir, f"{folder_name}_potree"))


def validate_local_conversion_request(request: LocalConversionRequest) -> None:
    if not request.source_file or not os.path.isfile(request.source_file):
        raise ValueError("Bitte eine gueltige LAS/LAZ Datei auswaehlen.")

    file_name = os.path.basename(request.source_file).lower()
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_name.endswith(".copc.laz") or file_ext not in {".las", ".laz"}:
        raise ValueError("Es koennen nur .las oder .laz Dateien lokal konvertiert werden.")

    if not request.output_dir:
        raise ValueError("Bitte einen lokalen Zielordner auswaehlen.")

    if not request.converter_path or not os.path.exists(request.converter_path):
        raise ValueError("Kein Potree Converter verfuegbar.")

    output_dir = os.path.abspath(request.output_dir)
    output_parent_dir = os.path.dirname(output_dir) or output_dir
    for protected_path, label in ((request.source_file, "Quelldatei"), (request.converter_path, "Potree Converter")):
        protected = os.path.realpath(os.path.abspath(protected_path))
        target = os.path.realpath(output_dir)
        try:
            inside_target = os.path.commonpath([protected, target]) == target
        except ValueError:
            inside_target = False
        if inside_target:
            raise ValueError(f"Der Zielordner darf {label} nicht enthalten.")

    if os.path.isdir(output_dir) and not request.overwrite:
        raise FileExistsError("Der Ausgabeordner existiert bereits.")


def _emit(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    if callback:
        callback(event)


def run_local_conversion(
    request: LocalConversionRequest,
    on_progress: ProgressCallback | None = None,
    converter_runner: ConverterRunner = run_potree_conversion,
    cancel_requested: CancelCallback | None = None,
) -> LocalConversionResult:
    """Run local Potree conversion without UI dependencies."""

    validate_local_conversion_request(request)
    output_dir = os.path.abspath(request.output_dir)
    output_parent_dir = os.path.dirname(output_dir)

    _emit(on_progress, ProgressEvent(kind="log", message="[KONVERTIERUNG] Starte lokale Potree-Konvertierung", phase="conversion"))
    _emit(on_progress, ProgressEvent(kind="step", step=1, total_steps=5, message="Bereite Zielordner vor...", phase="conversion"))
    _emit(on_progress, ProgressEvent(kind="detail", detail="Der lokale Potree-Projektordner wird vorbereitet", phase="conversion"))
    _emit(on_progress, ProgressEvent(kind="progress", percent=0.05, phase="conversion"))

    if output_parent_dir:
        os.makedirs(output_parent_dir, exist_ok=True)
    staging_dir = tempfile.mkdtemp(prefix=f".{os.path.basename(output_dir)}.tmp-", dir=output_parent_dir or None)
    shutil.rmtree(staging_dir)

    _emit(on_progress, ProgressEvent(kind="step", step=2, total_steps=5, message="Konvertiere mit Potree...", phase="conversion"))
    _emit(on_progress, ProgressEvent(kind="detail", detail="Die Punktwolke wird lokal in das Potree-Format umgewandelt", phase="conversion"))
    try:
        if converter_runner is run_potree_conversion:
            converter_runner(
                request.source_file,
                request.converter_path,
                staging_dir,
                on_progress,
                cancel_requested=cancel_requested,
            )
        else:
            converter_runner(request.source_file, request.converter_path, staging_dir, on_progress)

        _emit(on_progress, ProgressEvent(kind="step", step=3, total_steps=5, message="Pruefe Ergebnis...", phase="conversion"))
        _emit(on_progress, ProgressEvent(kind="detail", detail="Die konvertierten Daten werden lokal bereitgestellt", phase="conversion"))
        validate_potree_output(staging_dir)

        backup_dir = ""
        if os.path.isdir(output_dir):
            backup_dir = tempfile.mkdtemp(prefix=f".{os.path.basename(output_dir)}.old-", dir=output_parent_dir or None)
            shutil.rmtree(backup_dir)
            os.replace(output_dir, backup_dir)
        try:
            os.replace(staging_dir, output_dir)
        except BaseException:
            if backup_dir and not os.path.exists(output_dir):
                os.replace(backup_dir, output_dir)
            raise
        if backup_dir:
            shutil.rmtree(backup_dir)
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    _emit(on_progress, ProgressEvent(kind="log", message="[ERFOLG] Potree-Projekt lokal gespeichert", phase="conversion"))
    _emit(on_progress, ProgressEvent(kind="step", step=5, total_steps=5, message="Fertig", phase="conversion"))
    _emit(on_progress, ProgressEvent(kind="detail", detail=output_dir, phase="conversion"))
    _emit(on_progress, ProgressEvent(kind="progress", percent=1.0, phase="conversion"))
    return LocalConversionResult(output_dir=output_dir, message="Lokale Konvertierung abgeschlossen.")


__all__ = [
    "LocalConversionRequest",
    "LocalConversionResult",
    "build_local_output_dir",
    "run_local_conversion",
    "validate_local_conversion_request",
]
