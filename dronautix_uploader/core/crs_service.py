"""UI-free CRS metadata normalization and comparison helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
from typing import Any


_EPSG_PATTERN = re.compile(r"^\s*(?:EPSG\s*:?\s*)?(\d+)\s*$", re.IGNORECASE)
_OGC_URN_PATTERN = re.compile(r"^urn:ogc:def:crs:([^:]+):([^:]*):(.+)$", re.IGNORECASE)
_OGC_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?opengis\.net/def/crs/([^/]+)/([^/]+)/([^/?#]+)/?$",
    re.IGNORECASE,
)
_WKT_PATTERN = re.compile(
    r"^(?:BOUNDCRS|COMPOUNDCRS|COMPD_CS|PROJCRS|PROJCS|GEOGCRS|GEOGCS|GEODCRS|VERTCRS|VERT_CS|ENGCRS|LOCAL_CS)\s*\[",
    re.IGNORECASE,
)
_WKT_AUTHORITY_PATTERN = re.compile(
    r'(?:AUTHORITY|ID)\s*\[\s*"([^"]+)"\s*,\s*"?([^"\],]+)"?\s*\]',
    re.IGNORECASE,
)


class CrsValidationError(ValueError):
    """A supplied CRS value has no stable unambiguous technical reference."""


@dataclass(frozen=True)
class CrsSummary:
    """Stable, comparable summary of horizontal and vertical CRS metadata."""

    horizontal: str = ""
    vertical: str = ""

    @property
    def text(self) -> str:
        parts = []
        if self.horizontal:
            parts.append(self.horizontal)
        if self.vertical:
            parts.append(f"Vertikal: {self.vertical}")
        return " | ".join(parts)

    @property
    def has_value(self) -> bool:
        return bool(self.horizontal or self.vertical)


@dataclass(frozen=True)
class ProjectCrsDecision:
    """Result of resolving one project-level CRS from pointcloud metadata."""

    common_crs: dict[str, Any] | None
    summary: CrsSummary
    active_count: int
    has_mismatch: bool = False

    @property
    def should_set_project_crs(self) -> bool:
        return self.common_crs is not None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _canonical_reference(value: Any, label: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    match = _EPSG_PATTERN.match(text)
    if match:
        return f"EPSG:{match.group(1)}"
    urn = _OGC_URN_PATTERN.match(text)
    url = _OGC_URL_PATTERN.match(text)
    if urn or url:
        authority, version, code = (urn or url).groups()
        authority = authority.upper()
        code = code.strip()
        if authority == "EPSG" and code.isdigit():
            return f"EPSG:{code}"
        return f"urn:ogc:def:crs:{authority}:{version}:{code}"
    if _WKT_PATTERN.match(text):
        authorities = _WKT_AUTHORITY_PATTERN.findall(text)
        if not authorities:
            raise CrsValidationError(f"{label} benötigt eine WKT-Authority/ID.")
        epsg_codes = {
            code.strip()
            for authority, code in authorities
            if authority.upper() == "EPSG" and code.strip().isdigit()
        }
        if len(epsg_codes) == 1:
            return f"EPSG:{epsg_codes.pop()}"
        # A compound WKT may legitimately contain multiple component EPSG IDs;
        # collapsing it to either component would change the technical CRS.
        return " ".join(text.split())
    raise CrsValidationError(
        f"{label} ist keine eindeutige technische CRS-Referenz. "
        "Erwartet wird EPSG, eine OGC-URN/-URL oder WKT mit Authority/ID."
    )


def _technical_reference(
    metadata: Mapping[str, Any],
    keys: tuple[str, ...],
    label: str,
    fallback_keys: tuple[str, ...] = (),
) -> str:
    invalid: list[CrsValidationError] = []
    for candidate_keys in (keys, fallback_keys):
        references: set[str] = set()
        for key in candidate_keys:
            value = metadata.get(key)
            if not _clean_text(value):
                continue
            try:
                references.add(_canonical_reference(value, label))
            except CrsValidationError as error:
                invalid.append(error)
        if len(references) > 1:
            raise CrsValidationError(f"{label} enthält widersprüchliche technische Referenzen.")
        if references:
            return references.pop()
    if invalid:
        raise invalid[0]
    return ""


def _first_non_empty(metadata: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _clean_text(metadata.get(key))
        if value:
            return value
    return ""


def normalize_crs_metadata(crs_info: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a small canonical CRS dict, or None when no CRS value is present."""

    if not crs_info:
        return None

    horizontal = _technical_reference(
        crs_info,
        ("epsg", "value", "projection", "crs"),
        "Horizontales CRS",
        ("wkt",),
    )
    name = _first_non_empty(crs_info, ("crs_name", "name"))
    wkt = _first_non_empty(crs_info, ("wkt",))
    vertical_value = _technical_reference(
        crs_info,
        ("vertical_epsg", "vertical_crs", "vertical_projection"),
        "Vertikales CRS",
        ("vertical_wkt",),
    )
    vertical_name = _first_non_empty(crs_info, ("vertical_name", "vertical_datum"))

    if not any((horizontal, name, wkt, vertical_value, vertical_name)):
        return None

    normalized: dict[str, Any] = {}
    if horizontal:
        normalized["value"] = horizontal
        normalized["projection"] = horizontal
        if horizontal.upper().startswith("EPSG:"):
            normalized["epsg"] = horizontal
            normalized["code"] = horizontal.split(":", 1)[1]
    if name:
        normalized["name"] = name
        normalized["crs_name"] = name
    if wkt:
        normalized["wkt"] = wkt

    if vertical_value:
        normalized["vertical_crs"] = vertical_value
        normalized["vertical_epsg"] = vertical_value
        normalized["vertical_projection"] = vertical_value
    if vertical_name:
        normalized["vertical_name"] = vertical_name
        normalized["vertical_datum"] = vertical_name

    source = _first_non_empty(crs_info, ("source",))
    if source:
        normalized["source"] = source

    return normalized or None


def get_crs_display_value(crs_info: Mapping[str, Any] | None) -> str:
    normalized = normalize_crs_metadata(crs_info)
    if not normalized:
        return ""
    return _clean_text(normalized.get("epsg") or normalized.get("value") or normalized.get("name"))


def get_crs_technical_value(crs_info: Mapping[str, Any] | None) -> str:
    normalized = normalize_crs_metadata(crs_info)
    return _clean_text((normalized or {}).get("epsg") or (normalized or {}).get("value"))


def get_vertical_crs_technical_value(crs_info: Mapping[str, Any] | None) -> str:
    normalized = normalize_crs_metadata(crs_info)
    return _clean_text((normalized or {}).get("vertical_crs") or (normalized or {}).get("vertical_epsg"))


def get_vertical_crs_display_value(crs_info: Mapping[str, Any] | None) -> str:
    normalized = normalize_crs_metadata(crs_info)
    if not normalized:
        return ""

    vertical_value = _clean_text(normalized.get("vertical_crs") or normalized.get("vertical_epsg"))
    vertical_name = _clean_text(normalized.get("vertical_name") or normalized.get("vertical_datum"))
    if vertical_value and vertical_name:
        return f"{vertical_value} ({vertical_name})"
    return vertical_value or vertical_name


def summarize_crs_metadata(crs_info: Mapping[str, Any] | None) -> CrsSummary:
    return CrsSummary(
        horizontal=get_crs_display_value(crs_info),
        vertical=get_vertical_crs_display_value(crs_info),
    )


def get_crs_summary_text(crs_info: Mapping[str, Any] | None) -> str:
    return summarize_crs_metadata(crs_info).text


def crs_metadata_matches(
    left: Mapping[str, Any] | None,
    right: Mapping[str, Any] | None,
) -> bool:
    left_summary = _technical_crs_summary(left)
    right_summary = _technical_crs_summary(right)
    return bool(left_summary.horizontal) and left_summary == right_summary


def get_common_crs_metadata(
    crs_infos: Iterable[Mapping[str, Any] | None],
) -> dict[str, Any] | None:
    """Return normalized CRS only when every provided CRS summary matches."""

    normalized_infos = [normalize_crs_metadata(crs_info) for crs_info in crs_infos]
    if not normalized_infos or any(crs_info is None for crs_info in normalized_infos):
        return None

    first = normalized_infos[0]
    first_summary = _technical_crs_summary(first)
    if not first_summary.horizontal:
        return None

    for crs_info in normalized_infos[1:]:
        if _technical_crs_summary(crs_info) != first_summary:
            return None
    return dict(first)


def _technical_crs_summary(crs_info: Mapping[str, Any] | None) -> CrsSummary:
    return CrsSummary(
        horizontal=get_crs_technical_value(crs_info),
        vertical=get_vertical_crs_technical_value(crs_info),
    )


def is_active_pointcloud(pointcloud: Mapping[str, Any]) -> bool:
    """Treat pointclouds as active unless they are explicitly hidden/disabled."""

    return pointcloud.get("visible") is not False and pointcloud.get("disabled") is not True


def extract_pointcloud_crs_metadata(pointcloud: Mapping[str, Any]) -> dict[str, Any] | None:
    if not pointcloud:
        return None
    crs_info = pointcloud.get("crs_info")
    if isinstance(crs_info, Mapping):
        return normalize_crs_metadata(crs_info)
    top_level_crs = {
        key: pointcloud.get(key)
        for key in (
            "crs",
            "epsg",
            "projection",
            "value",
            "crs_name",
            "vertical_crs",
            "vertical_epsg",
            "vertical_projection",
            "vertical_name",
            "vertical_datum",
        )
    }
    return normalize_crs_metadata(top_level_crs)


def get_common_active_pointcloud_crs(
    pointclouds: Iterable[Mapping[str, Any]],
) -> ProjectCrsDecision:
    """Resolve project CRS from active pointclouds without modifying entries."""

    active_crs = [
        extract_pointcloud_crs_metadata(pointcloud)
        for pointcloud in pointclouds
        if is_active_pointcloud(pointcloud)
    ]
    common_crs = get_common_crs_metadata(active_crs)
    active_count = len(active_crs)
    has_mismatch = active_count > 0 and common_crs is None
    return ProjectCrsDecision(
        common_crs=common_crs,
        summary=summarize_crs_metadata(common_crs),
        active_count=active_count,
        has_mismatch=has_mismatch,
    )


__all__ = [
    "CrsSummary",
    "CrsValidationError",
    "ProjectCrsDecision",
    "crs_metadata_matches",
    "extract_pointcloud_crs_metadata",
    "get_common_active_pointcloud_crs",
    "get_common_crs_metadata",
    "get_crs_display_value",
    "get_crs_technical_value",
    "get_crs_summary_text",
    "get_vertical_crs_display_value",
    "get_vertical_crs_technical_value",
    "is_active_pointcloud",
    "normalize_crs_metadata",
    "summarize_crs_metadata",
]
