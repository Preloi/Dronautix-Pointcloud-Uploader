"""UI-free helpers for projects_index.json management."""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from typing import Any

from .constants import PROJECT_LINK_DISABLED_UI_KEY, S3_DISABLED_PROJECTS_KEY

PROJECTS_KEY = "projects"

PROJECT_CRS_METADATA_KEYS = (
    "crs",
    "projection",
    "epsg",
    "vertical_crs",
    "vertical_epsg",
    "vertical_projection",
    "vertical_datum",
    "vertical_name",
    "crs_info",
)


def append_project_history(project: MutableMapping[str, Any], timestamp: str, message: str) -> None:
    """Persist one successful project or pointcloud change."""

    timestamp = str(timestamp or "").strip()
    message = str(message or "").strip()
    if not timestamp or not message:
        return
    history = project.get("history")
    if not isinstance(history, list):
        history = []
        project["history"] = history
    history.append({"timestamp": timestamp, "message": message})


def get_index_project_list(index_data: MutableMapping[str, Any], key: str = PROJECTS_KEY) -> list[Any]:
    """Return a project list from an index, initializing missing or invalid lists."""

    if not isinstance(index_data, MutableMapping):
        return []

    projects = index_data.get(key)
    if not isinstance(projects, list):
        projects = []
        index_data[key] = projects
    return projects


def get_disabled_projects(index_data: MutableMapping[str, Any]) -> list[Any]:
    """Return the disabled projects list from an index."""

    return get_index_project_list(index_data, S3_DISABLED_PROJECTS_KEY)


def get_all_projects_for_management(index_data: MutableMapping[str, Any]) -> list[tuple[dict[str, Any], bool]]:
    """Return active and disabled projects with their disabled state."""

    active_projects = [
        (project, False)
        for project in get_index_project_list(index_data, PROJECTS_KEY)
        if isinstance(project, dict)
    ]
    disabled_projects = [
        (project, True)
        for project in get_disabled_projects(index_data)
        if isinstance(project, dict)
    ]
    return active_projects + disabled_projects


def update_project_in_index(
    index_data: MutableMapping[str, Any],
    project_id: str,
    update_func: Callable[[dict[str, Any]], None],
) -> bool:
    """Update a project in either the active or disabled project list."""

    normalized_project_id = str(project_id).strip()
    if not normalized_project_id:
        return False

    for project_list_key in (PROJECTS_KEY, S3_DISABLED_PROJECTS_KEY):
        projects = get_index_project_list(index_data, project_list_key)
        for idx, project in enumerate(projects):
            if not isinstance(project, dict):
                continue
            if str(project.get("id", "")).strip() != normalized_project_id:
                continue

            updated_project = dict(project)
            update_func(updated_project)
            projects[idx] = updated_project
            return True

    return False


def remove_project_from_index(index_data: MutableMapping[str, Any], project_id: str) -> bool:
    """Remove a project from both active and disabled project lists."""

    normalized_project_id = str(project_id).strip()
    if not normalized_project_id:
        return False

    changed = False
    for key in (PROJECTS_KEY, S3_DISABLED_PROJECTS_KEY):
        projects = get_index_project_list(index_data, key)
        original_count = len(projects)
        index_data[key] = [
            project
            for project in projects
            if not isinstance(project, dict)
            or str(project.get("id", "")).strip() != normalized_project_id
        ]
        changed = changed or len(index_data[key]) != original_count

    return changed


def update_project_link_disabled_state(
    index_data: MutableMapping[str, Any],
    project_ids: list[str] | tuple[str, ...] | set[str],
    disabled: bool,
    *,
    timestamp: str,
) -> int:
    """Move projects between active and disabled lists using the legacy ordering."""

    project_id_set = {str(project_id).strip() for project_id in project_ids if str(project_id).strip()}
    if not project_id_set:
        return 0

    active_projects = get_index_project_list(index_data, PROJECTS_KEY)
    disabled_projects = get_disabled_projects(index_data)
    source_projects = active_projects if disabled else disabled_projects
    target_projects = disabled_projects if disabled else active_projects

    moved_projects = []
    remaining_projects = []
    for project in source_projects:
        project_id = str(project.get("id", "")).strip() if isinstance(project, dict) else ""
        if project_id not in project_id_set:
            remaining_projects.append(project)
            continue

        updated_project = strip_project_ui_state(project)
        if disabled:
            updated_project["disabled_at"] = timestamp
        else:
            updated_project.pop("disabled_at", None)
        moved_projects.append(updated_project)

    if disabled:
        index_data[PROJECTS_KEY] = remaining_projects
    else:
        index_data[S3_DISABLED_PROJECTS_KEY] = remaining_projects

    moved_ids = {str(project.get("id", "")).strip() for project in moved_projects}
    target_projects[:] = [
        project
        for project in target_projects
        if not isinstance(project, dict) or str(project.get("id", "")).strip() not in moved_ids
    ]
    target_projects[0:0] = moved_projects
    return len(moved_projects)


def clear_project_crs_metadata(project: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Remove stale top-level project CRS fields without touching pointcloud metadata."""

    if not isinstance(project, MutableMapping):
        return project

    for key in PROJECT_CRS_METADATA_KEYS:
        project.pop(key, None)
    return project


def apply_common_crs_or_clear(
    project: MutableMapping[str, Any],
    common_crs: dict[str, Any] | None,
    apply_func: Callable[[MutableMapping[str, Any], dict[str, Any]], None],
) -> MutableMapping[str, Any]:
    """Apply common CRS metadata to a project, or clear stale project-level CRS fields."""

    if common_crs:
        apply_func(project, common_crs)
    else:
        clear_project_crs_metadata(project)
    return project


def strip_project_ui_state(project: dict[str, Any]) -> dict[str, Any]:
    """Return a project copy without non-persistable link-disabled UI flags."""

    cleaned_project = dict(project)
    cleaned_project.pop(PROJECT_LINK_DISABLED_UI_KEY, None)
    cleaned_project.pop("link_disabled", None)
    return cleaned_project


__all__ = [
    "PROJECT_CRS_METADATA_KEYS",
    "PROJECTS_KEY",
    "append_project_history",
    "apply_common_crs_or_clear",
    "clear_project_crs_metadata",
    "get_all_projects_for_management",
    "get_disabled_projects",
    "get_index_project_list",
    "remove_project_from_index",
    "strip_project_ui_state",
    "update_project_link_disabled_state",
    "update_project_in_index",
]
