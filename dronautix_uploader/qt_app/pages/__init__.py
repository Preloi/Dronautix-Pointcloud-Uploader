"""Page factories for the QtWidgets app (one module per page)."""

from __future__ import annotations

from .widgets import DROP_LIST_MAX_VISIBLE_ROWS, _STATUS_PILL_BY_LEVEL, _clear_layout_widgets, _create_settings_status_item, _create_settings_status_panel, _create_source_drop_list, _fit_drop_list_height
from .settings_page import COMMON_AWS_REGIONS, _resolve_settings_preview, _resolve_settings_state, create_settings_page
from .upload_page import UPLOAD_MODE_CONVERT, UPLOAD_MODE_UPLOAD, UploadFormInputs, create_upload_page
from .projects_page import PROJECT_SEARCH_DEBOUNCE_MS, ProjectActionCallback, ProjectProvider, _create_projects_model, _dispatch_project_action, _format_project_search_text, _populate_projects_model, _resolve_project_previews, create_projects_page
from .activity_page import ActivityProvider, _create_activity_model, _create_activity_stat_card, _populate_activity_model, _resolve_activity_preview, _update_activity_summary_labels, create_activity_page

__all__ = [
    "ActivityProvider",
    "COMMON_AWS_REGIONS",
    "DROP_LIST_MAX_VISIBLE_ROWS",
    "PROJECT_SEARCH_DEBOUNCE_MS",
    "ProjectActionCallback",
    "ProjectProvider",
    "UPLOAD_MODE_CONVERT",
    "UPLOAD_MODE_UPLOAD",
    "UploadFormInputs",
    "_STATUS_PILL_BY_LEVEL",
    "_clear_layout_widgets",
    "_create_activity_model",
    "_create_activity_stat_card",
    "_create_projects_model",
    "_create_settings_status_item",
    "_create_settings_status_panel",
    "_create_source_drop_list",
    "_dispatch_project_action",
    "_fit_drop_list_height",
    "_format_project_search_text",
    "_populate_activity_model",
    "_populate_projects_model",
    "_resolve_activity_preview",
    "_resolve_project_previews",
    "_resolve_settings_preview",
    "_resolve_settings_state",
    "_update_activity_summary_labels",
    "create_activity_page",
    "create_projects_page",
    "create_settings_page",
    "create_upload_page",
]
