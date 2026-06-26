"""UI-free dialog state helpers for the upload workflow."""

from __future__ import annotations

from dataclasses import dataclass

from dronautix_uploader.core.upload_workflow_service import NewProjectUploadWorkflowRequest


@dataclass(frozen=True)
class UploadDialogState:
    customer: str
    project: str
    source_paths: tuple[str, ...]
    converter_path: str = ""
    output_base_dir: str = ""
    overwrite: bool = False
    horizontal_crs: str = ""
    vertical_crs: str = ""


def validate_upload_dialog_state(state: UploadDialogState) -> NewProjectUploadWorkflowRequest:
    customer = state.customer.strip()
    project = state.project.strip()
    source_paths = tuple(path.strip() for path in state.source_paths if path.strip())
    converter_path = state.converter_path.strip()
    output_base_dir = state.output_base_dir.strip()
    horizontal_crs = state.horizontal_crs.strip()
    vertical_crs = state.vertical_crs.strip()

    if not customer:
        raise ValueError("Kunde darf nicht leer sein.")
    if not project:
        raise ValueError("Projektname darf nicht leer sein.")
    if not source_paths:
        raise ValueError("Mindestens eine Punktwolkenquelle auswählen.")
    if any(_source_needs_conversion(path) for path in source_paths):
        if not converter_path:
            raise ValueError("Potree Converter ist für LAS/LAZ-Quellen erforderlich.")
        if not output_base_dir:
            raise ValueError("Ausgabeordner ist für LAS/LAZ-Quellen erforderlich.")

    crs_info_by_source_path = _build_crs_info_by_source_path(source_paths, horizontal_crs, vertical_crs)
    return NewProjectUploadWorkflowRequest(
        source_paths=source_paths,
        kunde=customer,
        projekt=project,
        converter_path=converter_path,
        output_base_dir=output_base_dir,
        overwrite=state.overwrite,
        crs_info_by_source_path=crs_info_by_source_path,
    )


def _build_crs_info_by_source_path(
    source_paths: tuple[str, ...],
    horizontal_crs: str,
    vertical_crs: str,
) -> dict[str, dict[str, str]] | None:
    if not horizontal_crs and not vertical_crs:
        return None
    crs_info: dict[str, str] = {}
    if horizontal_crs:
        crs_info["value"] = horizontal_crs
        crs_info["projection"] = horizontal_crs
    if vertical_crs:
        crs_info["vertical_crs"] = vertical_crs
        crs_info["vertical_epsg"] = vertical_crs
        crs_info["vertical_projection"] = vertical_crs
    return {source_path: dict(crs_info) for source_path in source_paths}


def _source_needs_conversion(source_path: str) -> bool:
    lower_path = source_path.lower()
    return (lower_path.endswith(".las") or lower_path.endswith(".laz")) and not lower_path.endswith(".copc.laz")


__all__ = [
    "UploadDialogState",
    "validate_upload_dialog_state",
]
