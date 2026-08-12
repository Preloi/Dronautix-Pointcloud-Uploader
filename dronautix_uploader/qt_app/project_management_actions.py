"""UI-free handoff models for Qt project management actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .project_management import PointcloudPreview, ProjectPreview


ACTION_DUPLICATE = "duplicate"
ACTION_DELETE = "delete"
ACTION_DOWNLOAD = "download"
ACTION_DISABLE_LINK = "disable_link"
ACTION_ENABLE_LINK = "enable_link"
ACTION_OPEN_LINK = "open_link"
ACTION_COPY_LINK = "copy_link"
ACTION_RENAME = "rename"
ACTION_REPLACE_ALL_POINTCLOUDS = "replace_all_pointclouds"
ACTION_REPLACE_SINGLE_POINTCLOUD = "replace_single_pointcloud"
ACTION_ADD_POINTCLOUDS = "add_pointclouds"
ACTION_REMOVE_POINTCLOUD = "remove_pointcloud"

PROJECT_MANAGEMENT_SECTION = "Projektverwaltung"
POINTCLOUD_REPLACEMENT_SECTION = "Punktwolkendaten austauschen"
POINTCLOUD_MANAGEMENT_SECTION = "Punktwolken verwalten"

SUCCESS_STATUS = "success"
PARTIAL_STATUS = "partial"
FAILED_STATUS = "failed"
CANCELLED_STATUS = "cancelled"


@dataclass(frozen=True)
class ProjectManagementAction:
    action_id: str
    label: str
    section: str


@dataclass(frozen=True)
class ActionAvailability:
    action: ProjectManagementAction
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class ProjectOperationSummary:
    status: str
    message: str
    warnings: tuple[str, ...] = ()
    uploaded_keys: tuple[str, ...] = ()
    deleted_keys: tuple[str, ...] = ()
    downloaded_files: tuple[str, ...] = ()
    download_dir: str = ""

    @property
    def statusbar_text(self) -> str:
        return self.message

    @property
    def activity_lines(self) -> tuple[str, ...]:
        lines = [f"{status_label(self.status)}: {self.message}"]
        if self.uploaded_keys:
            lines.append(_count_line("Hochgeladen", self.uploaded_keys))
        if self.deleted_keys:
            lines.append(_count_line("Gelöscht", self.deleted_keys))
        if self.downloaded_files:
            lines.append(_count_line("Heruntergeladen", self.downloaded_files, plural_unit="Dateien"))
        if self.download_dir:
            lines.append(f"Ziel: {self.download_dir}")
        lines.extend(f"Warnung: {warning}" for warning in self.warnings)
        return tuple(lines)


PROJECT_MANAGEMENT_ACTIONS = (
    ProjectManagementAction(ACTION_OPEN_LINK, "Im Browser öffnen", PROJECT_MANAGEMENT_SECTION),
    ProjectManagementAction(ACTION_COPY_LINK, "Link kopieren", PROJECT_MANAGEMENT_SECTION),
    ProjectManagementAction(ACTION_RENAME, "Projekt umbenennen", PROJECT_MANAGEMENT_SECTION),
    ProjectManagementAction(ACTION_DUPLICATE, "Projekt duplizieren", PROJECT_MANAGEMENT_SECTION),
    ProjectManagementAction(ACTION_DOWNLOAD, "Projekt herunterladen", PROJECT_MANAGEMENT_SECTION),
    ProjectManagementAction(ACTION_DISABLE_LINK, "Link deaktivieren", PROJECT_MANAGEMENT_SECTION),
    ProjectManagementAction(ACTION_ENABLE_LINK, "Link aktivieren", PROJECT_MANAGEMENT_SECTION),
    ProjectManagementAction(ACTION_DELETE, "Projekt löschen", PROJECT_MANAGEMENT_SECTION),
    ProjectManagementAction(
        ACTION_REPLACE_ALL_POINTCLOUDS,
        "Alle Punktwolken austauschen",
        POINTCLOUD_REPLACEMENT_SECTION,
    ),
    ProjectManagementAction(
        ACTION_REPLACE_SINGLE_POINTCLOUD,
        "Ausgewählte Punktwolke austauschen",
        POINTCLOUD_REPLACEMENT_SECTION,
    ),
    ProjectManagementAction(
        ACTION_ADD_POINTCLOUDS,
        "Punktwolke hinzufügen",
        POINTCLOUD_MANAGEMENT_SECTION,
    ),
    ProjectManagementAction(
        ACTION_REMOVE_POINTCLOUD,
        "Ausgewählte Punktwolke entfernen",
        POINTCLOUD_MANAGEMENT_SECTION,
    ),
)
PROJECT_MANAGEMENT_ACTION_BY_ID = {action.action_id: action for action in PROJECT_MANAGEMENT_ACTIONS}


def action_by_id(action_id: str) -> ProjectManagementAction:
    return PROJECT_MANAGEMENT_ACTION_BY_ID[action_id]


def is_action_available(
    action_id: str,
    project: ProjectPreview | None,
    pointcloud: PointcloudPreview | None = None,
) -> bool:
    return action_availability(action_id, project, pointcloud).available


def action_availability(
    action_id: str,
    project: ProjectPreview | None,
    pointcloud: PointcloudPreview | None = None,
) -> ActionAvailability:
    action = action_by_id(action_id)

    if project is None:
        return ActionAvailability(action, False, "Kein Projekt ausgewählt.")
    if action_id == ACTION_OPEN_LINK and not project.link:
        return ActionAvailability(action, False, "Projekt hat keinen Link.")
    if action_id == ACTION_OPEN_LINK and project.disabled:
        return ActionAvailability(action, False, "Projekt-Link ist deaktiviert.")
    if action_id == ACTION_COPY_LINK and not project.link:
        return ActionAvailability(action, False, "Projekt hat keinen Link.")
    if action_id == ACTION_DOWNLOAD and not project.s3_path:
        return ActionAvailability(action, False, "Projekt hat keinen S3-Pfad.")
    if action_id == ACTION_DISABLE_LINK and project.disabled:
        return ActionAvailability(action, False, "Projekt-Link ist bereits deaktiviert.")
    if action_id == ACTION_ENABLE_LINK and not project.disabled:
        return ActionAvailability(action, False, "Projekt-Link ist bereits aktiv.")
    if action_id == ACTION_REPLACE_SINGLE_POINTCLOUD and pointcloud is None:
        return ActionAvailability(action, False, "Keine konkrete Punktwolke ausgewählt.")
    if action_id in {ACTION_ADD_POINTCLOUDS, ACTION_REMOVE_POINTCLOUD} and not project.has_explicit_pointclouds:
        return ActionAvailability(action, False, "Nur Projekte mit expliziter pointclouds[]-Liste können Punktwolken verwalten.")
    if action_id == ACTION_REMOVE_POINTCLOUD:
        if pointcloud is None:
            return ActionAvailability(action, False, "Keine konkrete Punktwolke ausgewählt.")
        if not pointcloud.s3_path:
            return ActionAvailability(action, False, "Ausgewählte Punktwolke hat keinen S3-Pfad.")
        if len(project.pointclouds) <= 1:
            return ActionAvailability(action, False, "Die letzte Punktwolke eines Projekts kann nicht entfernt werden.")
    return ActionAvailability(action, True)


def available_actions(
    project: ProjectPreview | None,
    pointcloud: PointcloudPreview | None = None,
) -> tuple[ProjectManagementAction, ...]:
    return tuple(
        availability.action
        for availability in (
            action_availability(action.action_id, project, pointcloud) for action in PROJECT_MANAGEMENT_ACTIONS
        )
        if availability.available
    )


def summarize_project_operation_result(result: Any) -> ProjectOperationSummary:
    status = str(getattr(result, "status", "") or FAILED_STATUS)
    message = str(getattr(result, "message", "") or _default_message(status))
    warnings = _compact_tuple(getattr(result, "warnings", ())) + _compact_tuple(
        getattr(result, "cleanup_warnings", ())
    )
    uploaded_keys = _compact_tuple(getattr(result, "uploaded_keys", ()))
    deleted_keys = _compact_tuple(getattr(result, "deleted_keys", ()))
    downloaded_files = _compact_tuple(getattr(result, "downloaded_files", ()))
    download_dir = str(getattr(result, "download_dir", "") or "")

    details = []
    if uploaded_keys:
        details.append(_count_line("hochgeladen", uploaded_keys))
    if deleted_keys:
        details.append(_count_line("gelöscht", deleted_keys))
    if downloaded_files:
        details.append(_count_line("heruntergeladen", downloaded_files, plural_unit="Dateien"))
    if download_dir:
        details.append(f"Ziel: {download_dir}")
    if warnings:
        details.append(_count_line("Warnung", warnings))
    if details:
        message = f"{message} ({'; '.join(details)})"

    return ProjectOperationSummary(
        status=status,
        message=message,
        warnings=warnings,
        uploaded_keys=uploaded_keys,
        deleted_keys=deleted_keys,
        downloaded_files=downloaded_files,
        download_dir=download_dir,
    )


def status_label(status: str) -> str:
    if status == SUCCESS_STATUS:
        return "Erfolgreich"
    if status == PARTIAL_STATUS:
        return "Teilweise erfolgreich"
    if status == FAILED_STATUS:
        return "Fehlgeschlagen"
    if status == CANCELLED_STATUS:
        return "Abgebrochen"
    return status or "Unbekannt"


def _compact_tuple(values: Any) -> tuple[str, ...]:
    if not values:
        return ()
    if isinstance(values, str):
        return (values,)
    return tuple(str(value) for value in values if str(value))


def _default_message(status: str) -> str:
    if status == SUCCESS_STATUS:
        return "Aktion abgeschlossen."
    if status == PARTIAL_STATUS:
        return "Aktion teilweise abgeschlossen."
    if status == FAILED_STATUS:
        return "Aktion fehlgeschlagen."
    if status == CANCELLED_STATUS:
        return "Aktion abgebrochen."
    return "Aktion beendet."


def _count_line(label: str, values: tuple[str, ...], *, plural_unit: str = "Keys") -> str:
    if len(values) == 1:
        return f"{label}: {values[0]}"
    return f"{label}: {len(values)} {plural_unit}"


__all__ = [
    "ACTION_DELETE",
    "ACTION_DISABLE_LINK",
    "ACTION_DOWNLOAD",
    "ACTION_ENABLE_LINK",
    "ACTION_COPY_LINK",
    "ACTION_OPEN_LINK",
    "ACTION_DUPLICATE",
    "ACTION_RENAME",
    "ACTION_REPLACE_ALL_POINTCLOUDS",
    "ACTION_REPLACE_SINGLE_POINTCLOUD",
    "ACTION_ADD_POINTCLOUDS",
    "ACTION_REMOVE_POINTCLOUD",
    "CANCELLED_STATUS",
    "FAILED_STATUS",
    "PARTIAL_STATUS",
    "POINTCLOUD_REPLACEMENT_SECTION",
    "POINTCLOUD_MANAGEMENT_SECTION",
    "PROJECT_MANAGEMENT_ACTIONS",
    "PROJECT_MANAGEMENT_ACTION_BY_ID",
    "PROJECT_MANAGEMENT_SECTION",
    "SUCCESS_STATUS",
    "ActionAvailability",
    "ProjectManagementAction",
    "ProjectOperationSummary",
    "action_availability",
    "action_by_id",
    "available_actions",
    "is_action_available",
    "status_label",
    "summarize_project_operation_result",
]
