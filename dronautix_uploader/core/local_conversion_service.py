"""UI-free local LAS/LAZ to Potree conversion workflow."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Callable

from .contracts import ProgressCallback, ProgressEvent
from .converter_service import run_potree_conversion
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


def build_local_output_dir(source_path: str, output_base_dir: str) -> str:
    source_name = os.path.splitext(os.path.basename(source_path))[0]
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
    if os.path.commonpath([output_parent_dir, output_dir]) != output_parent_dir:
        raise ValueError("Der Zielordner fuer die lokale Konvertierung ist ungueltig.")

    if os.path.isdir(output_dir) and not request.overwrite:
        raise FileExistsError("Der Ausgabeordner existiert bereits.")


def _emit(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    if callback:
        callback(event)


def run_local_conversion(
    request: LocalConversionRequest,
    on_progress: ProgressCallback | None = None,
    converter_runner: ConverterRunner = run_potree_conversion,
) -> LocalConversionResult:
    """Run local Potree conversion without UI dependencies."""

    validate_local_conversion_request(request)
    output_dir = os.path.abspath(request.output_dir)
    output_parent_dir = os.path.dirname(output_dir)

    _emit(on_progress, ProgressEvent(kind="log", message="[KONVERTIERUNG] Starte lokale Potree-Konvertierung"))
    _emit(on_progress, ProgressEvent(kind="step", step=1, total_steps=5, message="Bereite Zielordner vor..."))
    _emit(on_progress, ProgressEvent(kind="detail", detail="Der lokale Potree-Projektordner wird vorbereitet"))
    _emit(on_progress, ProgressEvent(kind="progress", percent=0.05))

    if output_parent_dir:
        os.makedirs(output_parent_dir, exist_ok=True)
    if os.path.isdir(output_dir):
        _emit(on_progress, ProgressEvent(kind="log", message="[CLEANUP] Vorhandenen Ausgabeordner entfernen"))
        shutil.rmtree(output_dir)

    _emit(on_progress, ProgressEvent(kind="step", step=2, total_steps=5, message="Konvertiere mit Potree..."))
    _emit(on_progress, ProgressEvent(kind="detail", detail="Die Punktwolke wird lokal in das Potree-Format umgewandelt"))
    converter_runner(request.source_file, request.converter_path, output_dir, on_progress)

    _emit(on_progress, ProgressEvent(kind="step", step=3, total_steps=5, message="Pruefe Ergebnis..."))
    _emit(on_progress, ProgressEvent(kind="detail", detail="Die konvertierten Daten werden lokal bereitgestellt"))
    if not os.path.isdir(output_dir):
        raise RuntimeError("Der Ausgabeordner wurde nicht erzeugt.")

    _emit(on_progress, ProgressEvent(kind="log", message="[ERFOLG] Potree-Projekt lokal gespeichert"))
    _emit(on_progress, ProgressEvent(kind="step", step=5, total_steps=5, message="Fertig"))
    _emit(on_progress, ProgressEvent(kind="detail", detail=output_dir))
    _emit(on_progress, ProgressEvent(kind="progress", percent=1.0))
    return LocalConversionResult(output_dir=output_dir, message="Lokale Konvertierung abgeschlossen.")


__all__ = [
    "LocalConversionRequest",
    "LocalConversionResult",
    "build_local_output_dir",
    "run_local_conversion",
    "validate_local_conversion_request",
]
