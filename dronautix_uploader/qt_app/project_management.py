"""UI-free project management preview data helpers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dronautix_uploader.core.metadata_service import get_crs_summary_text


DATUM_PLACEHOLDER = "Noch nicht geladen"
_DATUM_FALLBACK_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y")


def format_project_datum(raw: Any) -> str:
    """Render the stored project ``datum`` as a readable ``DD.MM.YYYY HH:MM`` date.

    The legacy app stores ``datetime.now().isoformat()`` (e.g.
    ``2026-06-25T14:30:45.123456``). Unknown or empty values fall back to a
    placeholder so the column never shows a raw ISO timestamp.
    """

    text = str(raw or "").strip()
    if not text:
        return DATUM_PLACEHOLDER
    iso_candidate = text[:-1] if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(iso_candidate).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        pass
    for fmt in _DATUM_FALLBACK_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            continue
    return text


def project_datum_sort_key(raw: Any) -> str:
    """Return a lexicographically sortable ``YYYY-MM-DD HH:MM`` key for ``datum``.

    Used so the "Aktualisiert" column sorts chronologically even though it is
    displayed in German ``DD.MM.YYYY`` order. Unparseable/empty values yield an
    empty key, which sorts before any real date.
    """

    text = str(raw or "").strip()
    if not text:
        return ""
    iso_candidate = text[:-1] if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(iso_candidate).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        pass
    for fmt in _DATUM_FALLBACK_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return ""


STATUS_ALL = "Alle Status"
STATUS_ACTIVE = "Aktiv"
STATUS_DISABLED = "Deaktiviert"
STATUS_FILTERS = (STATUS_ALL, STATUS_ACTIVE, STATUS_DISABLED)


@dataclass(frozen=True)
class PointcloudPreview:
    name: str
    format: str
    points: str
    crs: str
    s3_path: str = ""
    viewer_path: str = ""
    visible: bool = True


@dataclass(frozen=True)
class ProjectPreview:
    project_id: str
    project: str
    customer: str
    format: str
    updated: str
    link: str
    disabled: bool
    pointclouds: tuple[PointcloudPreview, ...]
    s3_path: str = ""
    viewer_path: str = ""
    updated_sort: str = ""

    @property
    def status(self) -> str:
        return project_status(self.disabled)


def project_status(disabled: bool) -> str:
    """Return the management status label used by the V2 UI."""

    return STATUS_DISABLED if disabled else STATUS_ACTIVE


def status_filter_accepts(disabled: bool, selected_status: str) -> bool:
    """Match V2 active/disabled filtering from the project-list membership."""

    if selected_status == STATUS_ACTIVE:
        return not disabled
    if selected_status == STATUS_DISABLED:
        return disabled
    return True


def make_project_preview(project: dict[str, Any], disabled: bool) -> ProjectPreview:
    """Create a preview row from project-index-like data."""

    return ProjectPreview(
        project_id=str(project.get("id", "")).strip(),
        project=str(project.get("projekt", "")).strip() or "Unbenanntes Projekt",
        customer=str(project.get("kunde", "")).strip() or "Ohne Kunde",
        format=str(project.get("format", "")).strip() or str(project.get("pointcloud_format", "")).strip() or "Multi",
        updated=format_project_datum(project.get("datum", "")),
        updated_sort=project_datum_sort_key(project.get("datum", "")),
        link=str(project.get("link", "")).strip(),
        disabled=bool(disabled),
        pointclouds=tuple(_make_pointcloud_previews(project)),
        s3_path=str(project.get("s3_path", "")).strip(),
        viewer_path=str(project.get("viewer_path", "")).strip(),
    )


def make_project_previews(projects: Iterable[tuple[dict[str, Any], bool]]) -> tuple[ProjectPreview, ...]:
    """Create preview rows from ``ProjectManagementService`` list output."""

    previews: list[ProjectPreview] = []
    for row in projects:
        if isinstance(row, ProjectPreview):
            previews.append(row)
            continue
        try:
            project, disabled = row
        except (TypeError, ValueError):
            continue
        if isinstance(project, dict):
            previews.append(make_project_preview(project, disabled))
    return tuple(previews)


def load_project_previews(project_provider) -> tuple[ProjectPreview, ...]:
    """Load project previews from a service-like provider.

    The provider can either be a callable returning ``(project, disabled)``
    tuples or an object exposing ``list_projects_for_management``.
    """

    if project_provider is None:
        return example_project_previews()
    if callable(project_provider):
        return make_project_previews(project_provider())
    return make_project_previews(project_provider.list_projects_for_management())


def example_project_previews() -> tuple[ProjectPreview, ...]:
    """Return representative project rows for the disconnected Qt preview."""

    active_projects = (
        {
            "id": "example-north",
            "projekt": "Beispielprojekt Nord",
            "kunde": "Dronautix",
            "format": "Multi",
            "datum": "Noch nicht geladen",
            "link": "viewer/projekte/beispielprojekt-nord",
            "s3_path": "pointclouds/dronautix/example-north/beispielprojekt-nord",
            "viewer_path": "dronautix/example-north/beispielprojekt-nord",
            "pointclouds": [
                {
                    "name": "Bestand EG",
                    "format": "Potree",
                    "points": "18.2 Mio.",
                    "crs": "EPSG:25832 + DHHN2016",
                    "s3_path": "pointclouds/dronautix/example-north/beispielprojekt-nord/bestand_eg",
                },
                {
                    "name": "Fassade Nord",
                    "format": "COPC",
                    "points": "7.4 Mio.",
                    "crs": "EPSG:25832",
                    "s3_path": "pointclouds/dronautix/example-north/beispielprojekt-nord/fassade_nord",
                },
                {
                    "name": "Dachaufmass",
                    "format": "Potree",
                    "points": "3.1 Mio.",
                    "crs": "EPSG:25832 + DHHN2016",
                    "s3_path": "pointclouds/dronautix/example-north/beispielprojekt-nord/dachaufmass",
                },
            ],
        },
        {
            "id": "copc-demo",
            "projekt": "COPC Demo",
            "kunde": "Interner Test",
            "format": "COPC",
            "datum": "Noch nicht geladen",
            "link": "viewer/projekte/copc-demo",
            "s3_path": "pointclouds/interner_test/copc-demo/copc_demo",
            "viewer_path": "interner_test/copc-demo/copc_demo",
            "pointclouds": [
                {
                    "name": "Direktupload",
                    "format": "COPC",
                    "points": "12.6 Mio.",
                    "crs": "EPSG:3857",
                    "s3_path": "pointclouds/interner_test/copc-demo/copc_demo/source.copc.laz",
                },
            ],
        },
    )
    disabled_projects = (
        {
            "id": "disabled-upload",
            "projekt": "Deaktivierter Upload",
            "kunde": "Kunde",
            "format": "Multi",
            "datum": "Noch nicht geladen",
            "link": "viewer/projekte/deaktivierter-upload",
            "s3_path": "pointclouds/kunde/disabled-upload/deaktivierter_upload",
            "viewer_path": "kunde/disabled-upload/deaktivierter_upload",
            "pointclouds": [
                {
                    "name": "Archiv Laserscan",
                    "format": "Potree",
                    "points": "22.0 Mio.",
                    "crs": "EPSG:25832",
                    "s3_path": "pointclouds/kunde/disabled-upload/deaktivierter_upload/archiv_laserscan",
                },
                {
                    "name": "Altbestand",
                    "format": "Potree",
                    "points": "9.8 Mio.",
                    "crs": "Unbekannt",
                    "s3_path": "pointclouds/kunde/disabled-upload/deaktivierter_upload/altbestand",
                },
            ],
        },
    )

    return tuple(make_project_preview(project, False) for project in active_projects) + tuple(
        make_project_preview(project, True) for project in disabled_projects
    )


def _make_pointcloud_previews(project: dict[str, Any]) -> list[PointcloudPreview]:
    pointclouds = project.get("pointclouds")
    if isinstance(pointclouds, list):
        previews = []
        for index, pointcloud in enumerate(pointclouds, start=1):
            if not isinstance(pointcloud, dict):
                continue
            previews.append(
                PointcloudPreview(
                    name=str(pointcloud.get("name", "")).strip() or f"Punktwolke {index}",
                    format=str(pointcloud.get("format", "")).strip() or "Unbekannt",
                    points=str(pointcloud.get("points", "")).strip() or "-",
                    crs=_get_pointcloud_crs_label(pointcloud),
                    s3_path=str(pointcloud.get("s3_path", "")).strip(),
                    viewer_path=str(pointcloud.get("viewer_path", "")).strip(),
                    visible=pointcloud.get("visible") is not False,
                )
            )
        if previews:
            return previews

    return [
        PointcloudPreview(
            name=str(project.get("projekt", "")).strip() or "Punktwolke 1",
            format=str(project.get("format", "")).strip() or "Unbekannt",
            points="-",
            crs=_get_pointcloud_crs_label(project),
            s3_path=str(project.get("s3_path", "")).strip(),
            viewer_path=str(project.get("viewer_path", "")).strip(),
        )
    ]


def _get_pointcloud_crs_label(pointcloud: dict[str, Any]) -> str:
    top_level = str(pointcloud.get("crs", "")).strip()
    if top_level:
        return top_level
    summary = get_crs_summary_text(pointcloud.get("crs_info") if isinstance(pointcloud.get("crs_info"), dict) else None)
    return summary or "Unbekannt"


__all__ = [
    "PointcloudPreview",
    "ProjectPreview",
    "STATUS_ACTIVE",
    "STATUS_ALL",
    "STATUS_DISABLED",
    "STATUS_FILTERS",
    "example_project_previews",
    "load_project_previews",
    "make_project_preview",
    "make_project_previews",
    "project_status",
    "status_filter_accepts",
]
