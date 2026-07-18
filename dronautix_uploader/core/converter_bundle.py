"""Resolve the bundled PotreeConverter without importing any UI code."""

from __future__ import annotations

from pathlib import Path
import sys

from .constants import BUNDLED_CONVERTER_DIR, BUNDLED_CONVERTER_DLL, BUNDLED_CONVERTER_EXE


def bundled_resource_root() -> Path:
    """Return the root that contains bundled data in source and PyInstaller runs."""

    frozen_root = getattr(sys, "_MEIPASS", "")
    if frozen_root:
        return Path(str(frozen_root))
    return Path(__file__).resolve().parents[2]


def get_bundled_converter_dir(resource_root: str | Path | None = None) -> Path:
    root = Path(resource_root) if resource_root is not None else bundled_resource_root()
    return root.joinpath(*BUNDLED_CONVERTER_DIR)


def get_bundled_converter_path(resource_root: str | Path | None = None) -> Path:
    return get_bundled_converter_dir(resource_root) / BUNDLED_CONVERTER_EXE


def is_converter_bundle_available(resource_root: str | Path | None = None) -> bool:
    converter_dir = get_bundled_converter_dir(resource_root)
    return (converter_dir / BUNDLED_CONVERTER_EXE).is_file() and (converter_dir / BUNDLED_CONVERTER_DLL).is_file()


def resolve_converter_path(configured_path: str = "", resource_root: str | Path | None = None) -> str:
    """Return the bundled converter path if available."""

    del configured_path  # Retained for compatibility with older callers/config files.
    if is_converter_bundle_available(resource_root):
        return str(get_bundled_converter_path(resource_root))
    return ""


__all__ = [
    "bundled_resource_root",
    "get_bundled_converter_dir",
    "get_bundled_converter_path",
    "is_converter_bundle_available",
    "resolve_converter_path",
]
