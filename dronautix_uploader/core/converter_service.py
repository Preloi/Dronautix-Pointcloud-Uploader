"""UI-free PotreeConverter boundary using Brotli output encoding."""

from __future__ import annotations

import os
import json
import re
import subprocess
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


def run_potree_conversion(
    source_file: str,
    converter_path: str,
    output_dir: str,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Run PotreeConverter without importing or touching any UI toolkit."""

    os.makedirs(output_dir, exist_ok=True)
    command = build_potree_command(source_file, converter_path, output_dir)
    _emit(on_progress, ProgressEvent(kind="log", message="[KONVERTIERUNG] Starte Potree Converter..."))
    _emit(on_progress, ProgressEvent(kind="log", message=f"[CONVERTER] {converter_path}"))
    _emit(on_progress, ProgressEvent(kind="log", message=f"[OUTPUT] {output_dir}"))

    process = subprocess.Popen(
        list(command.args),
        cwd=command.cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1,
    )

    try:
        stdout: Iterable[str] = process.stdout or ()
        for raw_line in stdout:
            line = raw_line.strip()
            if not line:
                continue
            _emit(on_progress, ProgressEvent(kind="log", message=f"[POTREE] {line}"))
            percent = parse_potree_percent(line)
            if percent is not None:
                _emit(on_progress, ProgressEvent(kind="progress", percent=percent))
    except BaseException:
        # Ein Abbruch aus dem Progress-Callback darf keinen verwaisten
        # PotreeConverter-Prozess zuruecklassen.
        process.kill()
        process.wait()
        raise

    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"Potree Konvertierung fehlgeschlagen (Exit Code: {process.returncode})")

    validate_brotli_output(output_dir)

    _emit(on_progress, ProgressEvent(kind="log", message="[KONVERTIERUNG] Potree Konvertierung mit BROTLI abgeschlossen"))
    _emit(on_progress, ProgressEvent(kind="progress", percent=1.0))
