"""UI-free dialog state helpers for project management."""

from __future__ import annotations

from dataclasses import dataclass

from .project_management import ProjectPreview
from .project_management_controller import (
    DownloadProjectInput,
    DuplicateProjectInput,
    RenameProjectInput,
    ReplaceAllPointcloudsInput,
    ReplaceSinglePointcloudInput,
)


@dataclass(frozen=True)
class ProjectRenameDialogState:
    customer: str
    project: str
    pointcloud_names: tuple[str, ...] = ()
    fallback_pointcloud_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectDuplicateDialogState:
    customer: str
    project: str


@dataclass(frozen=True)
class ProjectDownloadDialogState:
    target_dir: str = ""
    project_label: str = ""
    detail_text: str = ""


@dataclass(frozen=True)
class ProjectDeleteDialogState:
    project_label: str
    detail_text: str
    confirmation_label: str = "Projekt und S3-Daten löschen"
    requires_confirmation: bool = True


@dataclass(frozen=True)
class ProjectLinkStateDialogState:
    project_label: str
    detail_text: str
    confirmation_label: str
    disabled: bool


@dataclass(frozen=True)
class ProjectReplaceDialogState:
    source_paths: tuple[str, ...] = ()
    converter_path: str = ""
    output_base_dir: str = ""
    overwrite: bool = False
    horizontal_crs: str = ""
    vertical_crs: str = ""


def build_rename_dialog_state(project: ProjectPreview) -> ProjectRenameDialogState:
    pointcloud_names = tuple(pointcloud.name for pointcloud in project.pointclouds)
    return ProjectRenameDialogState(
        customer=project.customer,
        project=project.project,
        pointcloud_names=pointcloud_names,
        fallback_pointcloud_names=pointcloud_names,
    )


def build_duplicate_dialog_state(project: ProjectPreview) -> ProjectDuplicateDialogState:
    return ProjectDuplicateDialogState(
        customer=project.customer,
        project=f"{project.project} Kopie",
    )


def build_delete_dialog_state(project: ProjectPreview) -> ProjectDeleteDialogState:
    project_label = f"{project.customer} / {project.project}".strip(" /")
    detail = (
        f"Projekt-ID: {project.project_id}\n"
        f"Status: {project.status}\n"
        f"S3-Pfad: {project.s3_path or '-'}\n"
        "Die zugehörigen S3-Daten werden gelöscht und das Projekt wird aus der Projektverwaltung entfernt."
    )
    return ProjectDeleteDialogState(project_label=project_label, detail_text=detail)


def build_download_dialog_state(project: ProjectPreview) -> ProjectDownloadDialogState:
    project_label = f"{project.customer} / {project.project}".strip(" /")
    detail = (
        f"Projekt-ID: {project.project_id}\n"
        f"S3-Pfad: {project.s3_path or '-'}\n"
        "Alle Dateien unterhalb des Projekt-S3-Pfads werden in einen lokalen Unterordner geladen."
    )
    return ProjectDownloadDialogState(project_label=project_label, detail_text=detail)


def build_link_state_dialog_state(project: ProjectPreview, disabled: bool) -> ProjectLinkStateDialogState:
    project_label = f"{project.customer} / {project.project}".strip(" /")
    action_text = "deaktiviert" if disabled else "aktiviert"
    confirmation = "Link deaktivieren" if disabled else "Link aktivieren"
    detail = (
        f"Projekt-ID: {project.project_id}\n"
        f"Aktueller Status: {project.status}\n"
        f"Link: {project.link or '-'}\n"
        f"Der Projekt-Link wird {action_text}; S3-Daten und Metadatenpfade bleiben unverändert."
    )
    return ProjectLinkStateDialogState(
        project_label=project_label,
        detail_text=detail,
        confirmation_label=confirmation,
        disabled=disabled,
    )


def validate_rename_dialog_state(state: ProjectRenameDialogState) -> RenameProjectInput:
    customer = state.customer.strip()
    project = state.project.strip()
    if not customer:
        raise ValueError("Kunde darf nicht leer sein.")
    if not project:
        raise ValueError("Projektname darf nicht leer sein.")
    return RenameProjectInput(
        customer=customer,
        project=project,
        pointcloud_names=_normalize_pointcloud_names(state.pointcloud_names, state.fallback_pointcloud_names),
    )


def validate_duplicate_dialog_state(state: ProjectDuplicateDialogState) -> DuplicateProjectInput:
    customer = state.customer.strip()
    project = state.project.strip()
    if not customer:
        raise ValueError("Kunde darf nicht leer sein.")
    if not project:
        raise ValueError("Projektname darf nicht leer sein.")
    return DuplicateProjectInput(customer=customer, project=project)


def validate_download_dialog_state(state: ProjectDownloadDialogState) -> DownloadProjectInput:
    target_dir = state.target_dir.strip()
    if not target_dir:
        raise ValueError("Zielordner darf nicht leer sein.")
    return DownloadProjectInput(target_dir=target_dir)


def validate_replace_all_dialog_state(state: ProjectReplaceDialogState) -> ReplaceAllPointcloudsInput:
    source_paths = _normalize_source_paths(state.source_paths)
    if not source_paths:
        raise ValueError("Mindestens eine Ersatz-Punktwolke auswählen.")
    _validate_conversion_settings(source_paths, state.converter_path, state.output_base_dir)
    crs_info = _build_crs_info(state.horizontal_crs, state.vertical_crs)
    return ReplaceAllPointcloudsInput(
        source_paths=source_paths,
        converter_path=state.converter_path.strip(),
        output_base_dir=state.output_base_dir.strip(),
        overwrite=state.overwrite,
        crs_info_by_source_path={source_path: dict(crs_info) for source_path in source_paths} if crs_info else None,
    )


def validate_replace_single_dialog_state(state: ProjectReplaceDialogState) -> ReplaceSinglePointcloudInput:
    source_paths = _normalize_source_paths(state.source_paths)
    if len(source_paths) != 1:
        raise ValueError("Bitte genau eine Ersatz-Punktwolke auswählen.")
    _validate_conversion_settings(source_paths, state.converter_path, state.output_base_dir)
    crs_info = _build_crs_info(state.horizontal_crs, state.vertical_crs)
    return ReplaceSinglePointcloudInput(
        source_path=source_paths[0],
        converter_path=state.converter_path.strip(),
        output_base_dir=state.output_base_dir.strip(),
        overwrite=state.overwrite,
        crs_info=crs_info or None,
    )


def _normalize_pointcloud_names(
    pointcloud_names: tuple[str, ...],
    fallback_pointcloud_names: tuple[str, ...],
) -> tuple[str, ...]:
    normalized = []
    max_count = max(len(pointcloud_names), len(fallback_pointcloud_names))
    for index in range(max_count):
        value = pointcloud_names[index].strip() if index < len(pointcloud_names) else ""
        fallback = fallback_pointcloud_names[index].strip() if index < len(fallback_pointcloud_names) else ""
        normalized.append(value or fallback or f"Punktwolke {index + 1}")
    return tuple(normalized)


def _normalize_source_paths(source_paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(path.strip() for path in source_paths if path.strip())


def _validate_conversion_settings(source_paths: tuple[str, ...], converter_path: str, output_base_dir: str) -> None:
    if not any(_source_needs_conversion(path) for path in source_paths):
        return
    if not converter_path.strip():
        raise ValueError("Potree Converter ist für LAS/LAZ-Quellen erforderlich.")
    if not output_base_dir.strip():
        raise ValueError("Ausgabeordner ist für LAS/LAZ-Quellen erforderlich.")


def _build_crs_info(horizontal_crs: str, vertical_crs: str) -> dict[str, str]:
    horizontal = str(horizontal_crs or "").strip()
    vertical = str(vertical_crs or "").strip()
    crs_info: dict[str, str] = {}
    if horizontal:
        crs_info["value"] = horizontal
        crs_info["projection"] = horizontal
    if vertical:
        crs_info["vertical_crs"] = vertical
        crs_info["vertical_epsg"] = vertical
        crs_info["vertical_projection"] = vertical
    return crs_info


def _source_needs_conversion(source_path: str) -> bool:
    lower_path = source_path.lower()
    return (lower_path.endswith(".las") or lower_path.endswith(".laz")) and not lower_path.endswith(".copc.laz")


__all__ = [
    "ProjectDeleteDialogState",
    "ProjectDuplicateDialogState",
    "ProjectDownloadDialogState",
    "ProjectLinkStateDialogState",
    "ProjectReplaceDialogState",
    "ProjectRenameDialogState",
    "build_delete_dialog_state",
    "build_download_dialog_state",
    "build_link_state_dialog_state",
    "build_duplicate_dialog_state",
    "build_rename_dialog_state",
    "validate_download_dialog_state",
    "validate_duplicate_dialog_state",
    "validate_rename_dialog_state",
    "validate_replace_all_dialog_state",
    "validate_replace_single_dialog_state",
]
