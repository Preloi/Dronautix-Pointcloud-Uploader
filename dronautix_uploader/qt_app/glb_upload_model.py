"""Small UI-side helpers for optional native-GLB selection."""

from __future__ import annotations

import os
from pathlib import Path


def append_unique_glb_paths(existing_paths, new_paths) -> tuple[str, ...]:
    """Append supported paths once, comparing Windows paths case-insensitively."""

    result: list[str] = []
    seen: set[str] = set()
    for raw_path in tuple(existing_paths) + tuple(new_paths):
        path = str(raw_path or "").strip()
        if not path or not path.lower().endswith(".glb"):
            continue
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return tuple(result)


def explicit_glb_model_json_pair(paths) -> tuple[str, str] | None:
    """Return only an intentionally supplied ``GLB + model.json`` pair.

    A JSON file is never associated by name, location, or discovery. It must
    arrive together with exactly one GLB, or the caller gets a clear error.
    """

    cleaned = tuple(str(path or "").strip() for path in paths if str(path or "").strip())
    sidecars = tuple(path for path in cleaned if Path(path).name.casefold() == "model.json")
    if not sidecars:
        return None
    glbs = tuple(path for path in cleaned if path.casefold().endswith(".glb"))
    if len(cleaned) != 2 or len(glbs) != 1 or len(sidecars) != 1:
        raise ValueError("model.json nur zusammen mit genau einem GLB zuordnen.")
    return glbs[0], sidecars[0]


def format_file_size(path: str) -> str:
    """Return a compact display size without making a missing selection invalid."""

    try:
        size = Path(path).stat().st_size
    except OSError:
        return "Größe unbekannt"
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return "Größe unbekannt"


__all__ = [
    "append_unique_glb_paths",
    "explicit_glb_model_json_pair",
    "format_file_size",
]
