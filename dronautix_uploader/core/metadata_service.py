"""Viewer/index metadata helpers shared by upload and replace workflows."""

from __future__ import annotations

import json
from pathlib import Path
from collections.abc import MutableMapping
from typing import Any

from .crs_service import (
    get_crs_display_value as _get_crs_display_value,
    get_crs_summary_text as _get_crs_summary_text,
    get_vertical_crs_display_value as _get_vertical_crs_display_value,
    summarize_crs_metadata,
)


def apply_crs_metadata(
    target: MutableMapping[str, Any],
    crs_info: dict[str, Any] | None,
    include_projection: bool = True,
) -> None:
    """Write viewer-compatible CRS fields into a project or pointcloud entry."""

    if not isinstance(target, MutableMapping) or not crs_info:
        return

    value = crs_info.get("value")
    projection = crs_info.get("projection") or value
    if value:
        target["crs"] = value
    if include_projection and projection:
        target["projection"] = projection
    if crs_info.get("epsg"):
        target["epsg"] = crs_info["epsg"]

    vertical_value = crs_info.get("vertical_crs") or crs_info.get("vertical_epsg")
    vertical_name = crs_info.get("vertical_name") or crs_info.get("vertical_datum")
    if vertical_value:
        target["vertical_crs"] = vertical_value
        target["vertical_epsg"] = vertical_value
        target["vertical_projection"] = vertical_value
    if vertical_name:
        target["vertical_datum"] = vertical_name
    target["crs_info"] = dict(crs_info)


def create_pointcloud_index_entry(
    name: str,
    input_format: str,
    viewer_path: str,
    s3_path: str,
    crs_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a viewer-compatible pointcloud entry for projects_index.json."""

    entry: dict[str, Any] = {
        "name": name,
        "format": input_format,
        "viewer_path": viewer_path,
        "s3_path": s3_path,
        "visible": True,
    }
    apply_crs_metadata(entry, crs_info)
    return entry


def get_crs_display_value(crs_info: dict[str, Any] | None) -> str:
    return _get_crs_display_value(crs_info)


def get_vertical_crs_display_value(crs_info: dict[str, Any] | None) -> str:
    return _get_vertical_crs_display_value(crs_info)


def get_crs_summary_text(crs_info: dict[str, Any] | None) -> str:
    return _get_crs_summary_text(crs_info)


def get_common_crs_info(crs_infos) -> dict[str, Any] | None:
    """Return a common CRS only when every cloud has the same CRS summary."""

    crs_info_list = list(crs_infos)
    if not crs_info_list or any(not crs_info for crs_info in crs_info_list):
        return None

    first = crs_info_list[0]
    first_summary = summarize_crs_metadata(first)
    if not first_summary.has_value:
        return None

    for crs_info in crs_info_list[1:]:
        if summarize_crs_metadata(crs_info) != first_summary:
            return None
    return dict(first)


def write_potree_metadata_crs(output_dir: str | Path, crs_info: dict[str, Any] | None) -> tuple[Path, ...]:
    """Write viewer-readable CRS fields into Potree metadata.json/cloud.js files."""

    if not crs_info:
        return ()

    directory = Path(output_dir)
    updated_files: list[Path] = []
    metadata_path = directory / "metadata.json"
    if metadata_path.is_file() and _write_potree_metadata_json(metadata_path, crs_info):
        updated_files.append(metadata_path)

    cloudjs_path = directory / "cloud.js"
    if cloudjs_path.is_file() and _write_potree_cloudjs(cloudjs_path, crs_info):
        updated_files.append(cloudjs_path)

    return tuple(updated_files)


def write_potree_metadata_crs_for_sources(sources) -> tuple[Path, ...]:
    """Apply CRS metadata to prepared Potree sources before upload."""

    updated_files: list[Path] = []
    for source in sources or ():
        if getattr(source, "input_format", "") != "potree":
            continue
        crs_info = getattr(source, "crs_info", None)
        if not isinstance(crs_info, dict) or not crs_info:
            continue
        updated_files.extend(write_potree_metadata_crs(getattr(source, "source_path", ""), crs_info))
    return tuple(updated_files)


def _write_potree_metadata_json(path: Path, crs_info: dict[str, Any]) -> bool:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(metadata, dict):
        return False

    projection = crs_info.get("projection") or crs_info.get("value")
    if projection:
        metadata["projection"] = projection
    apply_crs_metadata(metadata, crs_info, include_projection=False)
    srs = metadata.get("srs") if isinstance(metadata.get("srs"), dict) else {}
    epsg_code = _epsg_code(crs_info)
    if epsg_code:
        srs["authority"] = "EPSG"
        srs["horizontal"] = epsg_code
    if crs_info.get("wkt"):
        srs["wkt"] = crs_info.get("wkt")
    vertical_code = _epsg_code(crs_info, key="vertical_epsg")
    if vertical_code:
        srs["vertical"] = vertical_code
    vertical_name = crs_info.get("vertical_name") or crs_info.get("vertical_datum")
    if vertical_name:
        srs["vertical_name"] = vertical_name
    if srs:
        metadata["srs"] = srs

    path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )
    return True


def _write_potree_cloudjs(path: Path, crs_info: dict[str, Any]) -> bool:
    try:
        cloudjs = _read_cloudjs_json(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(cloudjs, dict):
        return False

    projection = crs_info.get("projection") or crs_info.get("value")
    if projection:
        cloudjs["projection"] = projection
    apply_crs_metadata(cloudjs, crs_info, include_projection=False)
    path.write_text(
        "cloud.js = " + json.dumps(cloudjs, indent=2, ensure_ascii=False) + ";",
        encoding="utf-8",
        newline="\n",
    )
    return True


def _read_cloudjs_json(text: str) -> Any:
    payload = text.strip()
    if payload.startswith("cloud.js"):
        payload = payload[len("cloud.js") :].strip()
    if payload.startswith("="):
        payload = payload[1:].strip()
    return json.loads(payload.rstrip(";").strip())


def _epsg_code(crs_info: dict[str, Any], *, key: str = "epsg") -> str:
    value = crs_info.get(key)
    if value is None and key == "epsg":
        value = crs_info.get("code")
    if value is None:
        return ""
    text = str(value).strip()
    if text.upper().startswith("EPSG:"):
        return text.split(":", 1)[1].strip()
    return text


__all__ = [
    "apply_crs_metadata",
    "create_pointcloud_index_entry",
    "get_common_crs_info",
    "get_crs_display_value",
    "get_crs_summary_text",
    "get_vertical_crs_display_value",
    "write_potree_metadata_crs",
    "write_potree_metadata_crs_for_sources",
]
