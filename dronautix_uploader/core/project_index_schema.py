"""Per-project index schema: version guard and the compact schema-2 serializer.

Projects created by this uploader carry ``index_schema_version: 2``. Their
``projects_index.json`` entry keeps the viewer's shape but drops known CRS
duplicates. Everything here is pure: it works on copies, does no I/O and only
touches the one project a save explicitly targets. Proof that a detailed CRS
value (WKT, source) survives in the dataset's own metadata documents is
gathered by the caller and handed in as ``CrsDetailEvidence``.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
from typing import Any

from .crs_service import CrsValidationError, extract_pointcloud_crs_metadata, is_active_pointcloud, normalize_crs_metadata

INDEX_SCHEMA_VERSION_KEY = "index_schema_version"
COMPACT_INDEX_SCHEMA_VERSION = 2

_HORIZONTAL_KEYS = ("value", "projection", "crs", "epsg", "horizontal")
_VERTICAL_KEYS = ("vertical_crs", "vertical_epsg", "vertical_projection")
_CRS_NAME_KEYS = ("name", "crs_name")
_VERTICAL_NAME_KEYS = ("vertical_name", "vertical_datum")
CRS_DETAIL_EVIDENCE_KEYS = ("wkt", "vertical_wkt", "source")
# crs_info keys a recomputed project summary restores from its pointclouds.
_RECONSTRUCTIBLE_KEYS = frozenset(
    (*_HORIZONTAL_KEYS, "code", "auth", *_CRS_NAME_KEYS, *_VERTICAL_KEYS, *_VERTICAL_NAME_KEYS)
)
# Only for these keys an empty value carries no meaning. An unknown key counts
# by its mere presence, whatever its value.
_KNOWN_CRS_INFO_KEYS = _RECONSTRUCTIBLE_KEYS | frozenset(CRS_DETAIL_EVIDENCE_KEYS)
_WKT_LIKE = re.compile(r"^\s*[A-Za-z_]+\s*\[")


class UnsupportedIndexSchemaError(ValueError):
    """A project entry carries an index schema this uploader cannot write."""


@dataclass(frozen=True)
class CrsDetailEvidence:
    """CRS detail values proven by the metadata documents under one dataset path."""

    s3_path: str
    details: frozenset[tuple[str, str]]


@dataclass(frozen=True)
class IndexSaveContext:
    """Explicit target of one index write. Passed per call, never persisted."""

    project_id: str
    evidence: tuple[CrsDetailEvidence, ...] = ()


def is_compact_index_project(project: Any) -> bool:
    if not isinstance(project, Mapping):
        return False
    version = project.get(INDEX_SCHEMA_VERSION_KEY)
    return type(version) is int and version == COMPACT_INDEX_SCHEMA_VERSION


def ensure_writable_index_schema(project: Any) -> None:
    """Reject writes to entries with an index schema other than legacy or 2."""

    if not isinstance(project, Mapping) or INDEX_SCHEMA_VERSION_KEY not in project:
        return
    if is_compact_index_project(project):
        return
    label = str(project.get("id", "") or "").strip() or "unbekannt"
    raise UnsupportedIndexSchemaError(
        f"Projekt '{label}' verwendet eine unbekannte Indexschema-Version "
        f"({project[INDEX_SCHEMA_VERSION_KEY]!r}). Änderungen sind mit dieser Uploader-Version "
        "nicht möglich; Anzeigen und Herunterladen bleiben möglich. Es wurden keine Daten geändert."
    )


def mark_compact_index_schema(project: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of a new project entry that leads with the schema-2 marker."""

    return {INDEX_SCHEMA_VERSION_KEY: COMPACT_INDEX_SCHEMA_VERSION, **project}


def crs_detail_evidence_from_documents(s3_path: Any, documents: Iterable[Any]) -> CrsDetailEvidence | None:
    """Collect stored raw CRS details of one dataset's metadata.json/cloud.js.

    Only the documents' own ``crs_info`` and ``srs.wkt`` count. A top-level
    ``source`` is Potree's input file name, not a CRS origin.
    """

    path = _normalized_path(s3_path)
    details: set[tuple[str, str]] = set()
    for document in documents:
        if not isinstance(document, Mapping):
            continue
        crs_info = document.get("crs_info")
        if isinstance(crs_info, Mapping):
            for key in CRS_DETAIL_EVIDENCE_KEYS:
                value = crs_info.get(key)
                if isinstance(value, str) and value.strip():
                    details.add((key, value))
        srs = document.get("srs")
        if isinstance(srs, Mapping) and isinstance(srs.get("wkt"), str) and srs["wkt"].strip():
            details.add(("wkt", srs["wkt"]))
    if not path or not details:
        return None
    return CrsDetailEvidence(path, frozenset(details))


def needs_crs_detail_evidence(entry: Any) -> bool:
    """Whether removing the entry's crs_info would require document evidence."""

    crs_info = entry.get("crs_info") if isinstance(entry, Mapping) else None
    if not isinstance(crs_info, Mapping):
        return False
    return any(isinstance(crs_info.get(key), str) and crs_info[key].strip() for key in CRS_DETAIL_EVIDENCE_KEYS)


def crs_summary_source_path(project: Mapping[str, Any]) -> str:
    """Return the dataset path a project's CRS summary is taken from.

    A single-cloud entry is its own dataset; a multi project copies its summary
    from the first active pointcloud (``get_common_crs_info``).
    """

    pointclouds = project.get("pointclouds")
    if not isinstance(pointclouds, list) or not pointclouds:
        return _normalized_path(project.get("s3_path"))
    for pointcloud in pointclouds:
        if isinstance(pointcloud, dict) and is_active_pointcloud(pointcloud):
            return _normalized_path(pointcloud.get("s3_path"))
    return ""


def compact_project_entry(
    project: Mapping[str, Any],
    evidence: Iterable[CrsDetailEvidence] = (),
) -> dict[str, Any]:
    """Return the persisted form of one project entry.

    Only an explicit schema-2 entry is reduced; any other entry comes back as
    an unchanged copy. The result is idempotent and the input is not modified.
    """

    compacted = copy.deepcopy(dict(project))
    if not is_compact_index_project(compacted):
        return compacted
    details_by_path: dict[str, set[tuple[str, str]]] = {}
    for item in evidence:
        details_by_path.setdefault(_normalized_path(item.s3_path), set()).update(item.details)

    def details_for(path: Any) -> frozenset[tuple[str, str]]:
        normalized = _normalized_path(path)
        return frozenset(details_by_path.get(normalized, ())) if normalized else frozenset()

    _compact_crs_entry(compacted, details_for(crs_summary_source_path(compacted)))
    pointclouds = compacted.get("pointclouds")
    if isinstance(pointclouds, list):
        for pointcloud in pointclouds:
            if isinstance(pointcloud, dict):
                _compact_crs_entry(pointcloud, details_for(pointcloud.get("s3_path")))
    models = compacted.get("models")
    if isinstance(models, list):
        for model in models:
            if isinstance(model, dict):
                # A GLB manifest is no evidence for pointcloud CRS details.
                _compact_crs_entry(model, frozenset())
    return compacted


def unsecured_project_crs_details(
    project: Mapping[str, Any],
    datasets: Iterable[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Name project-summary CRS details that no listed dataset entry carries.

    A recomputed summary only restores the reconstructible references and
    names. Any other detail of the summary block survives only if one of the
    datasets (kept, removed or replaced by the action) holds the same value.
    """

    crs_info = project.get("crs_info")
    if not isinstance(crs_info, Mapping):
        return ()
    dataset_blocks = [
        dataset["crs_info"]
        for dataset in datasets
        if isinstance(dataset, Mapping) and isinstance(dataset.get("crs_info"), Mapping)
    ]
    unsecured = []
    for key, value in crs_info.items():
        if key in _RECONSTRUCTIBLE_KEYS or (key in _KNOWN_CRS_INFO_KEYS and _is_empty(value)):
            continue
        if not any(key in block and block[key] == value for block in dataset_blocks):
            unsecured.append(str(key))
    return tuple(sorted(unsecured))


def _compact_crs_entry(entry: dict[str, Any], details: frozenset[tuple[str, str]]) -> None:
    _drop_equivalent_aliases(entry)
    crs_info = entry.get("crs_info")
    if not isinstance(crs_info, Mapping):
        return
    promoted_names = _crs_info_removal(entry, crs_info, details)
    if promoted_names is None:
        return
    entry.update(promoted_names)
    entry.pop("crs_info")


def _drop_equivalent_aliases(entry: dict[str, Any]) -> None:
    horizontal = entry.get("crs")
    vertical = entry.get("vertical_crs")
    for key in ("projection", "epsg"):
        if key in entry and _same_reference(entry[key], horizontal, "value"):
            del entry[key]
    for key in ("vertical_epsg", "vertical_projection"):
        if key in entry and _same_reference(entry[key], vertical, "vertical_crs"):
            del entry[key]
    vertical_name = _text(entry.get("vertical_name"))
    if "vertical_datum" in entry and vertical_name and _text(entry["vertical_datum"]) == vertical_name:
        del entry["vertical_datum"]


def _crs_info_removal(
    entry: Mapping[str, Any],
    crs_info: Mapping[str, Any],
    details: frozenset[tuple[str, str]],
) -> dict[str, str] | None:
    """Return names to lift into flat fields, or None to keep the whole block."""

    horizontal = entry.get("crs")
    vertical = entry.get("vertical_crs")
    epsg_code = _epsg_code(horizontal)
    names: dict[str, set[str]] = {"crs_name": set(), "vertical_name": set()}
    for key, value in crs_info.items():
        if key not in _KNOWN_CRS_INFO_KEYS:
            return None
        if _is_empty(value):
            continue
        if not isinstance(value, str):
            return None
        if key in _HORIZONTAL_KEYS:
            if not _same_reference(value, horizontal, "value"):
                return None
        elif key in _VERTICAL_KEYS:
            if not _same_reference(value, vertical, "vertical_crs"):
                return None
        elif key == "code":
            if not epsg_code or value.strip() != epsg_code:
                return None
        elif key == "auth":
            if not epsg_code or value.strip() != "EPSG":
                return None
        elif key in _CRS_NAME_KEYS:
            names["crs_name"].add(value.strip())
        elif key in _VERTICAL_NAME_KEYS:
            names["vertical_name"].add(value.strip())
        elif (key, value) not in details:
            # The remaining known keys are the CRS_DETAIL_EVIDENCE_KEYS.
            return None

    promoted: dict[str, str] = {}
    for field, found in names.items():
        if not found:
            continue
        if len(found) != 1:
            return None
        name = next(iter(found))
        if field not in entry:
            promoted[field] = name
        elif not isinstance(entry[field], str) or entry[field].strip() != name:
            return None

    # Readers prefer crs_info over flat fields: removing the block must not
    # change what they see.
    flat_entry = {key: value for key, value in entry.items() if key != "crs_info"}
    flat_entry.update(promoted)
    try:
        if _reader_view(normalize_crs_metadata(crs_info)) != _reader_view(extract_pointcloud_crs_metadata(flat_entry)):
            return None
    except CrsValidationError:
        return None
    return promoted


def _reader_view(normalized: Mapping[str, Any] | None) -> tuple[str, str, str, str]:
    normalized = normalized or {}
    return tuple(_text(normalized.get(key)) for key in ("value", "name", "vertical_crs", "vertical_name"))


def _same_reference(value: Any, reference: Any, field: str) -> bool:
    """Whether ``value`` denotes the canonical ``reference`` without losing text."""

    if not isinstance(value, str) or not value.strip() or not isinstance(reference, str) or not reference.strip():
        return False
    if _WKT_LIKE.match(value) or _WKT_LIKE.match(reference):
        # A WKT collapses to its EPSG code on interpretation; only the
        # identical text is a lossless duplicate.
        return value == reference
    try:
        left = normalize_crs_metadata({field: value})
        right = normalize_crs_metadata({field: reference})
    except CrsValidationError:
        return False
    return bool(left and right and left.get(field) and left.get(field) == right.get(field))


def _epsg_code(reference: Any) -> str:
    if not isinstance(reference, str):
        return ""
    match = re.fullmatch(r"EPSG:(\d+)", reference.strip())
    return match.group(1) if match else ""


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalized_path(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().replace("\\", "/").strip("/")


__all__ = [
    "COMPACT_INDEX_SCHEMA_VERSION",
    "CRS_DETAIL_EVIDENCE_KEYS",
    "CrsDetailEvidence",
    "INDEX_SCHEMA_VERSION_KEY",
    "IndexSaveContext",
    "UnsupportedIndexSchemaError",
    "compact_project_entry",
    "crs_detail_evidence_from_documents",
    "crs_summary_source_path",
    "ensure_writable_index_schema",
    "is_compact_index_project",
    "mark_compact_index_schema",
    "needs_crs_detail_evidence",
    "unsecured_project_crs_details",
]
