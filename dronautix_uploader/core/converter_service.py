"""UI-free PotreeConverter boundary using Brotli output encoding."""

from __future__ import annotations

import os
import json
import math
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import queue
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterable

from .contracts import CancelCallback, OperationCancelledError, ProgressCallback, ProgressEvent

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
    validate_potree_output(output_dir)
    metadata_path = os.path.join(output_dir, "metadata.json")
    try:
        with open(metadata_path, encoding="utf-8") as metadata_file:
            metadata = json.load(metadata_file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Potree-Konvertierung hat keine gueltige metadata.json erzeugt.") from error

    if str(metadata.get("encoding", "")).upper() != "BROTLI":
        raise RuntimeError("PotreeConverter hat das angeforderte BROTLI-Encoding nicht erzeugt.")


def validate_potree_output(output_dir: str) -> None:
    """Require a complete Potree 2.x directory that the viewer can load."""

    metadata_path = os.path.join(output_dir, "metadata.json")
    if os.path.isfile(metadata_path):
        try:
            with open(metadata_path, encoding="utf-8") as metadata_file:
                metadata = json.load(metadata_file)
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("Potree-Projekt enthaelt keine gueltige metadata.json.") from error
        if not isinstance(metadata, dict) or not metadata:
            raise RuntimeError("Potree-Projekt enthaelt leere oder ungueltige Metadaten.")
        hierarchy = metadata.get("hierarchy")
        attributes = metadata.get("attributes")
        position = next((item for item in attributes or () if isinstance(item, dict) and item.get("name") == "position"), None)
        bounding_box = metadata.get("boundingBox")
        points = _integer(metadata.get("points"), minimum=1)
        first_chunk_size = _integer(hierarchy.get("firstChunkSize") if isinstance(hierarchy, dict) else None, minimum=1)
        step_size = _integer(hierarchy.get("stepSize") if isinstance(hierarchy, dict) else None, minimum=1)
        depth = _integer(hierarchy.get("depth") if isinstance(hierarchy, dict) else None, minimum=0)
        hierarchy_path = os.path.join(output_dir, "hierarchy.bin")
        octree_path = os.path.join(output_dir, "octree.bin")
        if (
            not str(metadata.get("version", "")).startswith("2.")
            or str(metadata.get("encoding", "")).upper() not in {"BROTLI", "DEFAULT", "UNCOMPRESSED"}
            or not isinstance(hierarchy, dict)
            or not isinstance(attributes, list)
            or points is None
            or not _valid_xyz(metadata.get("offset"))
            or not _valid_xyz(metadata.get("scale"), positive=True)
            or not _valid_bounds(bounding_box)
            or not isinstance(position, dict)
            or position.get("type") != "int32"
            or _integer(position.get("numElements")) != 3
            or _integer(position.get("elementSize")) != 4
            or _integer(position.get("size")) != 12
            or first_chunk_size is None
            or step_size is None
            or depth is None
        ):
            raise RuntimeError("Potree-2-Metadaten enthalten nicht alle erforderlichen Strukturangaben.")
        if (
            not os.path.isfile(hierarchy_path)
            or os.path.getsize(hierarchy_path) < first_chunk_size
            or first_chunk_size % 22 != 0
            or not os.path.isfile(octree_path)
            or os.path.getsize(octree_path) <= 0
        ):
            raise RuntimeError("Potree-2-Projekt benoetigt hierarchy.bin und octree.bin mit Daten.")
        _validate_potree2_ranges(hierarchy_path, octree_path, first_chunk_size)
        return

    if os.path.isfile(os.path.join(output_dir, "cloud.js")):
        raise RuntimeError(
            "Potree 1 (cloud.js) wird nicht unterstuetzt. "
            "Bitte mit PotreeConverter 2.x neu konvertieren; der Viewer benoetigt metadata.json."
        )
    raise RuntimeError("Ordner ist kein vollstaendiges Potree-Projekt.")


def _valid_xyz(value, *, positive: bool = False) -> bool:
    if not isinstance(value, list) or len(value) != 3:
        return False
    try:
        numbers = [float(item) for item in value]
    except (TypeError, ValueError):
        return False
    return all(math.isfinite(item) and (item > 0 if positive else True) for item in numbers)


def _integer(value, *, minimum: int | None = None) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return number if minimum is None or number >= minimum else None


def _validate_potree2_ranges(hierarchy_path: str, octree_path: str, first_chunk_size: int) -> None:
    hierarchy_size = os.path.getsize(hierarchy_path)
    octree_size = os.path.getsize(octree_path)
    pending = [(0, first_chunk_size)]
    visited = set()
    with open(hierarchy_path, "rb") as hierarchy_file:
        while pending:
            offset, size = pending.pop()
            if (offset, size) in visited:
                raise RuntimeError("Potree-2-Hierarchie enthaelt einen zyklischen Proxy-Verweis.")
            visited.add((offset, size))
            if size <= 0 or size % 22 or offset < 0 or offset + size > hierarchy_size:
                raise RuntimeError("Potree-2-Hierarchie verweist ausserhalb von hierarchy.bin.")
            hierarchy_file.seek(offset)
            for _record_index in range(size // 22):
                record = hierarchy_file.read(22)
                if len(record) != 22:
                    raise RuntimeError("Potree-2-Hierarchie ist unvollstaendig.")
                node_type, _child_mask, points, byte_offset, byte_size = struct.unpack("<BBIQQ", record)
                if node_type == 2:
                    pending.append((byte_offset, byte_size))
                elif node_type not in {0, 1} or (
                    points > 0 and (byte_size <= 0 or byte_offset + byte_size > octree_size)
                ) or (points == 0 and (byte_offset != 0 or byte_size != 0)):
                    raise RuntimeError("Potree-2-Hierarchie verweist ausserhalb von octree.bin.")


def _valid_bounds(value) -> bool:
    return (
        isinstance(value, dict)
        and _valid_xyz(value.get("min"))
        and _valid_xyz(value.get("max"))
        and all(float(lower) <= float(upper) for lower, upper in zip(value["min"], value["max"]))
    )


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
    cancel_requested: CancelCallback | None = None,
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
        output_queue: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            stdout: Iterable[str] = process.stdout or ()
            try:
                for raw_line in stdout:
                    output_queue.put(raw_line)
            finally:
                output_queue.put(None)

        threading.Thread(target=read_output, daemon=True).start()
        reader_done = False
        try:
            poll = getattr(process, "poll", lambda: getattr(process, "returncode", None))
            while not reader_done or poll() is None:
                if cancel_requested is not None and cancel_requested():
                    raise OperationCancelledError("Konvertierung wurde abgebrochen.")
                try:
                    raw_line = output_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if raw_line is None:
                    reader_done = True
                    continue
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
            stop = getattr(process, "terminate", None) or getattr(process, "kill", None)
            if callable(stop):
                stop()
            try:
                process.wait(timeout=5)
            except TypeError:
                process.wait()
            except subprocess.TimeoutExpired:
                kill = getattr(process, "kill", None)
                if callable(kill):
                    kill()
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


__all__ = [
    "PotreeCommand",
    "build_potree_command",
    "parse_potree_percent",
    "run_potree_conversion",
    "validate_brotli_output",
    "validate_potree_output",
]
