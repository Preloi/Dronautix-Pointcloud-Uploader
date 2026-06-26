"""UI-free PotreeConverter boundary.

The command shape is intentionally frozen from the current CustomTkinter app:
``[converter_path, source_file, "-o", output_dir, "--overwrite"]`` with the
process working directory set to the converter executable's directory.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Iterable

from .contracts import ProgressCallback, ProgressEvent

POTREE_CONVERTER_FLAGS = ("-o", "--overwrite")


@dataclass(frozen=True)
class PotreeCommand:
    args: tuple[str, ...]
    cwd: str


def build_potree_command(source_file: str, converter_path: str, output_dir: str) -> PotreeCommand:
    """Build the exact PotreeConverter command used by the legacy app."""

    return PotreeCommand(
        args=(converter_path, source_file, "-o", output_dir, "--overwrite"),
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

    stdout: Iterable[str] = process.stdout or ()
    for raw_line in stdout:
        line = raw_line.strip()
        if not line:
            continue
        _emit(on_progress, ProgressEvent(kind="log", message=f"[POTREE] {line}"))
        percent = parse_potree_percent(line)
        if percent is not None:
            _emit(on_progress, ProgressEvent(kind="progress", percent=percent))

    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"Potree Konvertierung fehlgeschlagen (Exit Code: {process.returncode})")

    _emit(on_progress, ProgressEvent(kind="log", message="[KONVERTIERUNG] Potree Konvertierung abgeschlossen"))
    _emit(on_progress, ProgressEvent(kind="progress", percent=1.0))
