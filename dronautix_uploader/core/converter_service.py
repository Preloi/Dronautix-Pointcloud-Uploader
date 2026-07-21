"""UI-free PotreeConverter boundary using Brotli output encoding."""

from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterable

from .contracts import ProgressCallback, ProgressEvent

POTREE_CONVERTER_FLAGS = ("-o", "--overwrite", "--encoding", "BROTLI")


@dataclass(frozen=True)
class PotreeCommand:
    args: tuple[str, ...]
    cwd: str


def build_potree_command(source_file: str, converter_path: str, output_dir: str) -> PotreeCommand:
    """Build the PotreeConverter command used by the V2 app."""

    return PotreeCommand(
        args=(converter_path, source_file, "-o", output_dir, "--overwrite", "--encoding", "BROTLI"),
        cwd=os.path.dirname(converter_path),
    )


def _emit(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    if callback:
        callback(event)


def parse_potree_percent(line: str) -> float | None:
    match = re.search(r"(\d+)%", line)
    if not match:
        return None
    return min(max(int(match.group(1)) / 100.0, 0.0), 1.0)


def validate_brotli_output(output_dir: str) -> None:
    metadata_path = os.path.join(output_dir, "metadata.json")
    try:
        with open(metadata_path, encoding="utf-8") as metadata_file:
            metadata = json.load(metadata_file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Potree-Konvertierung hat keine gueltige metadata.json erzeugt.") from error

    if str(metadata.get("encoding", "")).upper() != "BROTLI":
        raise RuntimeError("PotreeConverter hat das angeforderte BROTLI-Encoding nicht erzeugt.")


def _hidden_window_options() -> dict[str, object]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


def _windows_short_path(path: str) -> str:
    if os.name != "nt" or path.isascii():
        return path
    import ctypes

    kernel32 = ctypes.windll.kernel32
    length = kernel32.GetShortPathNameW(path, None, 0)
    if not length:
        return ""
    buffer = ctypes.create_unicode_buffer(length)
    if not kernel32.GetShortPathNameW(path, buffer, length):
        return ""
    return buffer.value if buffer.value.isascii() else ""


@contextmanager
def _converter_safe_source_path(source_file: str, output_dir: str):
    safe_path = _windows_short_path(source_file)
    if safe_path:
        yield safe_path
        return

    staging_dir = tempfile.mkdtemp(
        prefix="dronautix_potree_source_",
    )
    alias = os.path.join(staging_dir, f"source{os.path.splitext(source_file)[1].lower()}")
    try:
        try:
            os.link(source_file, alias)
        except OSError as error:
            raise RuntimeError(
                f"PotreeConverter kann den Unicode-Pfad '{source_file}' nicht direkt lesen und "
                "ein platzsparender temporärer Dateialias konnte nicht erstellt werden. "
                "Bitte Datei vorübergehend ohne Umlaute benennen."
            ) from error
        yield _windows_short_path(alias) or alias
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def _potree_failure_hint(output_lines: list[str]) -> str:
    output = "\n".join(output_lines).casefold()
    if "#points: 0" in output and "invalid bounding box" in output:
        return (
            "PotreeConverter konnte keine lesbaren Punkte aus der LAS/LAZ-Datei laden. "
            "Prüfen Sie, ob die Datei Punktdaten enthält und gültige XYZ-Grenzen besitzt."
        )
    if "bad allocation" in output or "bad_alloc" in output or "out of memory" in output:
        return "PotreeConverter hatte nicht genügend Arbeitsspeicher für diese Punktwolke."
    return "PotreeConverter hat die Verarbeitung unerwartet beendet."


def run_potree_conversion(
    source_file: str,
    converter_path: str,
    output_dir: str,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Run PotreeConverter without importing or touching any UI toolkit."""

    os.makedirs(output_dir, exist_ok=True)
    print(f"[SOURCE] {source_file}", file=sys.stderr, flush=True)
    print(f"[OUTPUT] {output_dir}", file=sys.stderr, flush=True)
    _emit(on_progress, ProgressEvent(kind="log", message="[KONVERTIERUNG] Starte Potree Converter...", phase="conversion"))
    _emit(on_progress, ProgressEvent(kind="log", message=f"[CONVERTER] {converter_path}", phase="conversion"))
    _emit(on_progress, ProgressEvent(kind="log", message=f"[OUTPUT] {output_dir}", phase="conversion"))

    with _converter_safe_source_path(source_file, output_dir) as converter_source:
        command = build_potree_command(converter_source, converter_path, output_dir)
        try:
            process = subprocess.Popen(
                list(command.args),
                cwd=command.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
                **_hidden_window_options(),
            )
        except OSError as error:
            raise RuntimeError(
                f"PotreeConverter konnte nicht gestartet werden.\n"
                f"Datei: {source_file}\nConverter: {converter_path}\nSystemmeldung: {error}"
            ) from error
        output_tail: list[str] = []

        try:
            stdout: Iterable[str] = process.stdout or ()
            for raw_line in stdout:
                line = raw_line.strip()
                if not line:
                    continue
                print(f"[POTREE] {line}", file=sys.stderr, flush=True)
                output_tail.append(line)
                del output_tail[:-20]
                _emit(on_progress, ProgressEvent(kind="log", message=f"[POTREE] {line}", phase="conversion"))
                percent = parse_potree_percent(line)
                if percent is not None:
                    _emit(on_progress, ProgressEvent(kind="progress", percent=percent, phase="conversion"))
        except BaseException:
            # Ein Abbruch aus dem Progress-Callback darf keinen verwaisten
            # PotreeConverter-Prozess zuruecklassen.
            process.kill()
            process.wait()
            raise

        process.wait()
        if process.returncode != 0:
            detail = "\n".join(output_tail[-8:])
            suffix = f"\nLetzte Potree-Ausgabe:\n{detail}" if detail else ""
            raise RuntimeError(
                f"Potree Konvertierung von '{source_file}' fehlgeschlagen "
                f"(Exit Code: {process.returncode}).\n"
                f"Ursache: {_potree_failure_hint(output_tail)}{suffix}"
            )

    try:
        validate_brotli_output(output_dir)
    except RuntimeError as error:
        raise RuntimeError(
            f"PotreeConverter wurde für '{source_file}' beendet, hat aber kein verwendbares "
            f"BROTLI-Ergebnis erzeugt. Ausgabeordner: {output_dir}. Ursache: {error}"
        ) from error

    _emit(on_progress, ProgressEvent(kind="log", message="[KONVERTIERUNG] Potree Konvertierung mit BROTLI abgeschlossen", phase="conversion"))
    _emit(on_progress, ProgressEvent(kind="progress", percent=1.0, phase="conversion"))
