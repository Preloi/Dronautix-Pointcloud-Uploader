"""Dependency-free CRS auto-detection from LAS/LAZ/COPC headers and Potree metadata.

This is a UI-free port of the legacy detection logic. It parses LAS/LAZ/COPC VLR
and EVLR records directly (no laspy/pyproj), so it works on compressed files
without decompressing point data, and reads CRS from existing Potree projects.
"""

from __future__ import annotations

import json
import os
import re
import struct
from typing import Any

__all__ = [
    "detect_pointcloud_crs",
    "detect_las_crs",
    "detect_potree_crs",
    "read_las_projection_records",
    "extract_epsg_from_wkt",
]


def clean_las_text(raw_value: Any) -> str:
    """Decode null-terminated LAS/VLR text fields robustly."""

    if raw_value is None:
        return ""
    if isinstance(raw_value, bytes):
        text = raw_value.decode("utf-8", errors="ignore")
    else:
        text = str(raw_value)
    return text.replace("\x00", "").strip().strip("|").strip()


def normalize_crs_value(crs_value: Any, source: str = "auto", name: Any = None, wkt: Any = None) -> dict | None:
    """Normalize a CRS input into viewer/JSON-compatible metadata."""

    raw_value = clean_las_text(crs_value)
    if not raw_value:
        return None

    epsg_match = re.fullmatch(r"(?:EPSG[:\s-]*)?(\d{3,6})", raw_value, re.IGNORECASE)
    crs_info: dict[str, Any] = {"value": raw_value, "source": source}
    if epsg_match:
        code = epsg_match.group(1)
        crs_info.update(
            {"value": f"EPSG:{code}", "projection": f"EPSG:{code}", "auth": "EPSG", "code": code, "epsg": f"EPSG:{code}"}
        )
    else:
        crs_info["projection"] = raw_value
        authority_match = re.search(r"\bEPSG[:\s-]*(\d{3,6})\b", raw_value, re.IGNORECASE)
        if authority_match:
            code = authority_match.group(1)
            crs_info.update({"projection": f"EPSG:{code}", "auth": "EPSG", "code": code, "epsg": f"EPSG:{code}"})
    if name:
        crs_info["name"] = clean_las_text(name)
    if wkt:
        crs_info["wkt"] = clean_las_text(wkt)
    return crs_info


# --- WKT helpers -----------------------------------------------------------


def _extract_wkt_block(wkt: str, keyword: str) -> str:
    if not wkt or not keyword:
        return ""
    match = re.search(rf"\b{re.escape(keyword)}\s*\[", wkt, re.IGNORECASE)
    if not match:
        return ""
    start = match.start()
    bracket_start = wkt.find("[", match.start())
    depth = 0
    in_quote = False
    index = bracket_start
    while index < len(wkt):
        char = wkt[index]
        if char == '"':
            in_quote = not in_quote
        elif not in_quote:
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    return wkt[start : index + 1]
        index += 1
    return wkt[start:]


def _extract_wkt_epsg_codes(wkt: str) -> list[str]:
    if not wkt:
        return []
    return re.findall(r'(?:AUTHORITY|ID)\s*\[\s*"EPSG"\s*,\s*"?(\d{3,6})"?', wkt, re.IGNORECASE)


def _extract_wkt_name_for_keywords(wkt: str, keywords: tuple[str, ...]) -> str:
    for keyword in keywords:
        block = _extract_wkt_block(wkt, keyword)
        if not block:
            continue
        name_match = re.search(rf"^\s*{re.escape(keyword)}\s*\[\s*\"([^\"]+)\"", block, re.IGNORECASE)
        if name_match:
            return clean_las_text(name_match.group(1))
    return ""


def extract_epsg_from_wkt(wkt: str) -> str:
    if not wkt:
        return ""
    for keyword in ("PROJCRS", "PROJCS", "GEOGCRS", "GEOGCS"):
        epsg_codes = _extract_wkt_epsg_codes(_extract_wkt_block(wkt, keyword))
        if epsg_codes:
            return epsg_codes[-1]
    if re.search(r"\b(?:VERTCRS|VERT_CS)\s*\[", wkt, re.IGNORECASE):
        return ""
    matches = _extract_wkt_epsg_codes(wkt)
    return matches[-1] if matches else ""


def _extract_vertical_epsg_from_wkt(wkt: str) -> str:
    for keyword in ("VERTCRS", "VERT_CS"):
        epsg_codes = _extract_wkt_epsg_codes(_extract_wkt_block(wkt, keyword))
        if epsg_codes:
            return epsg_codes[-1]
    return ""


def _extract_vertical_name_from_wkt(wkt: str) -> str:
    return _extract_wkt_name_for_keywords(wkt, ("VERTCRS", "VERT_CS"))


def _extract_name_from_wkt(wkt: str) -> str:
    if not wkt:
        return ""
    horizontal_name = _extract_wkt_name_for_keywords(wkt, ("PROJCRS", "PROJCS", "GEOGCRS", "GEOGCS"))
    if horizontal_name:
        return horizontal_name
    return _extract_wkt_name_for_keywords(wkt, ("COMPOUNDCRS", "COMPD_CS"))


# --- LAS VLR/EVLR reading --------------------------------------------------


def read_las_projection_records(filepath: str, max_record_bytes: int = 1024 * 1024) -> list[dict]:
    """Read CRS-relevant LAS/LAZ VLR and EVLR records without external dependencies."""

    records: list[dict] = []
    try:
        with open(filepath, "rb") as file:
            header = file.read(375)
            if len(header) < 104 or header[:4] != b"LASF":
                return records

            version_major = header[24]
            version_minor = header[25]
            header_size = struct.unpack_from("<H", header, 94)[0]
            point_data_offset = struct.unpack_from("<I", header, 96)[0]
            vlr_count = struct.unpack_from("<I", header, 100)[0]

            file.seek(header_size)
            for _ in range(vlr_count):
                vlr_header = file.read(54)
                if len(vlr_header) < 54:
                    break
                user_id = clean_las_text(vlr_header[2:18])
                record_id = struct.unpack_from("<H", vlr_header, 18)[0]
                record_length = struct.unpack_from("<H", vlr_header, 20)[0]
                data = file.read(min(record_length, max_record_bytes))
                if record_length > max_record_bytes:
                    file.seek(record_length - max_record_bytes, os.SEEK_CUR)
                if user_id == "LASF_Projection":
                    records.append({"user_id": user_id, "record_id": record_id, "data": data})
                if point_data_offset and file.tell() >= point_data_offset:
                    break

            if len(header) >= 247 and header_size >= 247 and version_major == 1 and version_minor >= 4:
                evlr_start = struct.unpack_from("<Q", header, 235)[0]
                evlr_count = struct.unpack_from("<I", header, 243)[0]
                if evlr_start and evlr_count:
                    file.seek(evlr_start)
                    for _ in range(evlr_count):
                        evlr_header = file.read(60)
                        if len(evlr_header) < 60:
                            break
                        user_id = clean_las_text(evlr_header[2:18])
                        record_id = struct.unpack_from("<H", evlr_header, 18)[0]
                        record_length = struct.unpack_from("<Q", evlr_header, 20)[0]
                        data = file.read(min(record_length, max_record_bytes))
                        if record_length > max_record_bytes:
                            file.seek(record_length - max_record_bytes, os.SEEK_CUR)
                        if user_id == "LASF_Projection":
                            records.append({"user_id": user_id, "record_id": record_id, "data": data})
    except Exception:
        return records
    return records


def _decode_geo_key_value(entry, ascii_params: str, double_params: list):
    key_id, tiff_tag_location, count, value_offset = entry
    if tiff_tag_location == 0:
        return value_offset
    if tiff_tag_location == 34737:
        return clean_las_text(ascii_params[value_offset : value_offset + count])
    if tiff_tag_location == 34736:
        return double_params[value_offset : value_offset + count]
    return None


def detect_las_crs(source_path: str) -> dict | None:
    """Detect EPSG/WKT CRS from a LAS/LAZ/COPC header."""

    records = read_las_projection_records(source_path)
    if not records:
        return None

    wkt = ""
    geo_key_entries: list = []
    ascii_params = ""
    double_params: list = []
    for record in records:
        record_id = record["record_id"]
        data = record["data"]
        if record_id in (2111, 2112):
            candidate = clean_las_text(data)
            if candidate:
                wkt = candidate
        elif record_id == 34735 and len(data) >= 8:
            value_count = len(data) // 2
            values = struct.unpack("<" + "H" * value_count, data[: value_count * 2])
            entry_count = values[3] if len(values) >= 4 else 0
            for index in range(entry_count):
                start = 4 + index * 4
                if start + 4 <= len(values):
                    geo_key_entries.append(values[start : start + 4])
        elif record_id == 34736 and len(data) >= 8:
            double_count = len(data) // 8
            double_params = list(struct.unpack("<" + "d" * double_count, data[: double_count * 8]))
        elif record_id == 34737:
            ascii_params = data.decode("utf-8", errors="ignore").replace("\x00", "")

    if wkt:
        epsg_code = extract_epsg_from_wkt(wkt)
        vertical_epsg_code = _extract_vertical_epsg_from_wkt(wkt)
        vertical_name = _extract_vertical_name_from_wkt(wkt)
        wkt_name = _extract_name_from_wkt(wkt)
        if epsg_code:
            crs_info = normalize_crs_value(f"EPSG:{epsg_code}", source="auto", name=wkt_name, wkt=wkt)
        elif wkt_name:
            crs_info = normalize_crs_value(wkt_name, source="auto", name=wkt_name, wkt=wkt)
        else:
            crs_info = None
        if crs_info:
            if vertical_epsg_code:
                crs_info["vertical_epsg"] = f"EPSG:{vertical_epsg_code}"
                crs_info["vertical_crs"] = f"EPSG:{vertical_epsg_code}"
            if vertical_name:
                crs_info["vertical_name"] = vertical_name
                crs_info["vertical_datum"] = vertical_name
        return crs_info

    geo_keys = {entry[0]: _decode_geo_key_value(entry, ascii_params, double_params) for entry in geo_key_entries}

    crs_name = ""
    for name_key in (3073, 2049):
        value = geo_keys.get(name_key)
        if isinstance(value, str) and value:
            crs_name = value
            break

    vertical_name = ""
    vertical_value = geo_keys.get(4097)
    if isinstance(vertical_value, str) and vertical_value:
        vertical_name = vertical_value
    vertical_epsg_code = geo_keys.get(4096)

    def attach_vertical_crs(crs_info):
        if not crs_info:
            return crs_info
        if isinstance(vertical_epsg_code, int) and 0 < vertical_epsg_code < 32767:
            crs_info["vertical_epsg"] = f"EPSG:{vertical_epsg_code}"
            crs_info["vertical_crs"] = f"EPSG:{vertical_epsg_code}"
        if vertical_name:
            crs_info["vertical_name"] = vertical_name
            crs_info["vertical_datum"] = vertical_name
        return crs_info

    for epsg_key in (3072, 2048):
        epsg_code = geo_keys.get(epsg_key)
        if isinstance(epsg_code, int) and 0 < epsg_code < 32767:
            return attach_vertical_crs(normalize_crs_value(f"EPSG:{epsg_code}", source="auto", name=crs_name))

    return attach_vertical_crs(normalize_crs_value(crs_name, source="auto")) if crs_name else None


# --- Potree metadata -------------------------------------------------------


def _read_cloudjs_json(cloudjs_path: str) -> dict:
    with open(cloudjs_path, "r", encoding="utf-8") as file:
        cloudjs_text = file.read().strip()
    if cloudjs_text.startswith("cloud.js"):
        cloudjs_text = cloudjs_text[len("cloud.js") :].strip()
    if cloudjs_text.startswith("="):
        cloudjs_text = cloudjs_text[1:].strip()
    return json.loads(cloudjs_text.rstrip(";").strip())


def _attach_vertical_metadata(crs_info, metadata_source):
    if not isinstance(metadata_source, dict):
        return crs_info
    merged = dict(crs_info or {"source": "auto"})
    srs = metadata_source.get("srs") if isinstance(metadata_source.get("srs"), dict) else {}
    vertical_value = (
        metadata_source.get("vertical_crs")
        or metadata_source.get("vertical_epsg")
        or metadata_source.get("vertical_projection")
        or srs.get("vertical")
    )
    vertical_name = (
        metadata_source.get("vertical_name") or metadata_source.get("vertical_datum") or srs.get("vertical_name")
    )
    if vertical_value:
        vertical_text = clean_las_text(vertical_value)
        if vertical_text and vertical_text.isdigit():
            vertical_text = f"EPSG:{vertical_text}"
        if vertical_text:
            merged["vertical_crs"] = vertical_text
            merged["vertical_epsg"] = vertical_text
            merged["vertical_projection"] = vertical_text
    if vertical_name:
        merged["vertical_name"] = clean_las_text(vertical_name)
        merged["vertical_datum"] = clean_las_text(vertical_name)
    return merged if len(merged) > 1 else crs_info


def detect_crs_from_metadata_dict(metadata) -> dict | None:
    if not isinstance(metadata, dict):
        return None
    srs = metadata.get("srs") if isinstance(metadata.get("srs"), dict) else {}
    horizontal = (
        metadata.get("projection")
        or metadata.get("crs")
        or metadata.get("epsg")
        or srs.get("projection")
        or srs.get("horizontal")
        or srs.get("wkt")
    )
    if horizontal and str(horizontal).isdigit():
        horizontal = f"EPSG:{horizontal}"
    if srs.get("authority") == "EPSG" and srs.get("horizontal"):
        horizontal = f"EPSG:{srs.get('horizontal')}"
    crs_info = normalize_crs_value(horizontal, source="auto") if horizontal else None
    if not crs_info and srs.get("wkt"):
        crs_info = normalize_crs_value(srs.get("wkt"), source="auto", wkt=srs.get("wkt"))
    return _attach_vertical_metadata(crs_info, metadata)


def detect_potree_crs(directory_path: str) -> dict | None:
    """Detect CRS from an existing Potree project's metadata.json/cloud.js."""

    if not directory_path or not os.path.isdir(directory_path):
        return None
    metadata_path = os.path.join(directory_path, "metadata.json")
    if os.path.isfile(metadata_path):
        try:
            with open(metadata_path, "r", encoding="utf-8") as file:
                crs_info = detect_crs_from_metadata_dict(json.load(file))
            if crs_info:
                return crs_info
        except Exception:
            pass
    cloudjs_path = os.path.join(directory_path, "cloud.js")
    if os.path.isfile(cloudjs_path):
        try:
            return detect_crs_from_metadata_dict(_read_cloudjs_json(cloudjs_path))
        except Exception:
            return None
    return None


def detect_pointcloud_crs(source_path: str) -> dict | None:
    """Detect CRS from a LAS/LAZ/COPC file or an existing Potree directory."""

    if source_path and os.path.isdir(source_path):
        return detect_potree_crs(source_path)
    if not source_path or not os.path.isfile(source_path):
        return None
    return detect_las_crs(source_path)
