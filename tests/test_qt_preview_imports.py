import ast
import importlib
import inspect

from dronautix_uploader.core.config_service import get_config_locations, load_config_file, save_config_file
from dronautix_uploader.qt_app.activity_model import ActivityLogEntry, ActivityPreview
from dronautix_uploader.qt_app.app import (
    RUNTIME_MODE_ENV,
    prepare_preview_config,
    prepare_runtime_config,
    qt_argv_without_runtime_mode,
    resolve_runtime_identity,
)
from dronautix_uploader.qt_app.app_identity import resolve_app_identity
from dronautix_uploader.qt_app.dashboard_settings_model import example_settings_preview
from dronautix_uploader.qt_app.pages import (
    _dispatch_project_action,
    _resolve_activity_preview,
    _resolve_project_previews,
    _resolve_settings_preview,
)
from dronautix_uploader.qt_app.project_management import example_project_previews


def test_qt_preview_modules_import_without_pyside6():
    """The preview package must stay import-safe on machines without PySide6."""

    for module_name in (
        "Dronautix_Pointcloud_Uploader_v2",
        "Dronautix_Pointcloud_Uploader_v2_final",
        "dronautix_uploader.qt_app",
        "dronautix_uploader.qt_app.app",
        "dronautix_uploader.qt_app.app_identity",
        "dronautix_uploader.qt_app.cutover_readiness_controller",
        "dronautix_uploader.qt_app.main_window",
        "dronautix_uploader.qt_app.pages",
        "dronautix_uploader.qt_app.local_conversion_controller",
        "dronautix_uploader.qt_app.local_conversion_dialog_models",
        "dronautix_uploader.qt_app.path_drop",
        "dronautix_uploader.qt_app.project_management_actions",
        "dronautix_uploader.qt_app.project_management_controller",
        "dronautix_uploader.qt_app.project_management_dialog_models",
        "dronautix_uploader.qt_app.project_management_dialogs",
        "dronautix_uploader.qt_app.runtime_services",
        "dronautix_uploader.qt_app.settings_controller",
        "dronautix_uploader.qt_app.settings_dialogs",
        "dronautix_uploader.qt_app.task_worker",
        "dronautix_uploader.qt_app.style",
        "dronautix_uploader.qt_app.upload_dialog_models",
        "dronautix_uploader.qt_app.upload_workflow_controller",
        "dronautix_uploader.qt_app.upload_wizard_model",
    ):
        importlib.import_module(module_name)


def test_qt_app_identity_defaults_to_isolated_preview_and_requires_explicit_final():
    preview = resolve_app_identity()
    final = resolve_app_identity("final")

    assert preview.mode == "preview"
    assert preview.uses_preview_config is True
    assert "Preview" in preview.application_name
    assert final.mode == "final"
    assert final.uses_preview_config is False
    assert final.application_name == "Dronautix Pointcloud Uploader"
    assert "Preview" not in final.application_name


def test_runtime_identity_resolves_preview_for_source_entrypoint_and_final_for_production_exe():
    preview = resolve_runtime_identity(["Dronautix_Pointcloud_Uploader_v2.py"], environ={})
    final = resolve_runtime_identity(["C:/Program Files/Dronautix/Dronautix_Pointcloud_Uploader.exe"], environ={})

    assert preview.mode == "preview"
    assert final.mode == "final"


def test_runtime_identity_cli_overrides_environment_and_executable_name():
    preview = resolve_runtime_identity(
        ["C:/Program Files/Dronautix/Dronautix_Pointcloud_Uploader.exe", "--preview"],
        environ={RUNTIME_MODE_ENV: "final"},
    )
    final = resolve_runtime_identity(
        ["Dronautix_Pointcloud_Uploader_v2.py", "--final"],
        environ={RUNTIME_MODE_ENV: "preview"},
    )

    assert preview.mode == "preview"
    assert final.mode == "final"
    assert qt_argv_without_runtime_mode(["app.py", "--final", "--other"]) == ["app.py", "--other"]


def test_runtime_identity_uses_environment_when_no_cli_override_is_present():
    final = resolve_runtime_identity(["Dronautix_Pointcloud_Uploader_v2.py"], environ={RUNTIME_MODE_ENV: "final"})

    assert final.mode == "final"


def test_prepare_preview_config_migrates_legacy_config_before_runtime_load(tmp_path):
    locations = get_config_locations(preview=True, environ={"APPDATA": str(tmp_path)})
    save_config_file(
        locations.legacy_config,
        {
            "converter_path": "legacy-converter.exe",
            "output_base_dir": "C:/LegacyOut",
        },
    )

    assert prepare_preview_config(preview=True, environ={"APPDATA": str(tmp_path)})
    assert load_config_file(locations.current_config) == {
        "converter_path": "legacy-converter.exe",
        "output_base_dir": "C:/LegacyOut",
    }
    assert not prepare_preview_config(preview=True, environ={"APPDATA": str(tmp_path)})


def test_prepare_runtime_config_keeps_final_on_legacy_config_path(tmp_path):
    locations = get_config_locations(preview=False, environ={"APPDATA": str(tmp_path)})
    save_config_file(locations.current_config, {"converter_path": "prod-converter.exe"})

    assert not prepare_runtime_config(resolve_app_identity("final"), environ={"APPDATA": str(tmp_path)})
    assert load_config_file(locations.current_config) == {"converter_path": "prod-converter.exe"}


def test_projects_page_preview_source_can_be_injected_without_qt():
    injected = example_project_previews()[:1]

    class Provider:
        def list_projects_for_management(self):
            return [({"id": "provided", "projekt": "Provided", "kunde": "Kunde"}, False)]

    assert _resolve_project_previews(project_previews=injected) == injected
    assert _resolve_project_previews(project_provider=lambda: injected) == injected
    assert _resolve_project_previews(project_provider=Provider())[0].project_id == "provided"
    # Ohne Provider bleibt die Liste leer; es werden keine Beispieldaten angezeigt.
    assert _resolve_project_previews() == ()


def test_projects_page_provider_errors_do_not_break_preview_resolution():
    def failing_provider():
        raise RuntimeError("S3 unavailable")

    assert _resolve_project_previews(project_provider=failing_provider) == ()


def test_activity_page_preview_source_can_be_injected_without_qt():
    entry = ActivityLogEntry(
        timestamp="21.06.2026 16:40",
        action="Upload",
        status="Erfolgreich",
        severity="Erfolg",
        project="Projekt",
        customer="Kunde",
        actor="Test",
        summary="Fertig",
        detail="Detail",
        source_path="",
        target_path="",
        duration="-",
    )
    preview = ActivityPreview(entries=(entry,))

    assert _resolve_activity_preview(activity_preview=preview) is preview
    assert _resolve_activity_preview(activity_provider=lambda: (entry,)).entries == (entry,)
    assert _resolve_activity_preview(activity_provider=lambda: preview) is preview
    # Ohne Provider startet das Protokoll leer statt mit Beispieldaten.
    assert _resolve_activity_preview().entries == ()


def test_settings_page_preview_source_can_be_injected_without_qt():
    preview = example_settings_preview()

    assert _resolve_settings_preview(settings_preview=preview) is preview
    assert _resolve_settings_preview(settings_provider=lambda: preview) is preview
    assert _resolve_settings_preview().settings_status


def test_runtime_project_rows_accept_callable_or_service_provider_without_qt():
    from dronautix_uploader.qt_app.main_window import resolve_runtime_project_rows

    rows = [({"id": "project"}, False)]
    fallback = example_project_previews()[:1]

    class Provider:
        def list_projects_for_management(self):
            return rows

    assert resolve_runtime_project_rows(None) == ()
    assert resolve_runtime_project_rows(None, fallback_rows=fallback) == fallback
    assert resolve_runtime_project_rows(lambda: rows) == rows
    assert resolve_runtime_project_rows(Provider()) == rows


def test_project_action_dispatch_supports_legacy_and_project_aware_callbacks_without_qt():
    project = example_project_previews()[0]
    pointcloud = project.pointclouds[0]
    legacy_calls = []
    project_calls = []
    pointcloud_calls = []

    _dispatch_project_action(legacy_calls.append, "delete", project, pointcloud)
    _dispatch_project_action(lambda action, selected_project: project_calls.append((action, selected_project)), "rename", project)
    _dispatch_project_action(
        lambda action, selected_project, selected_pointcloud: pointcloud_calls.append(
            (action, selected_project, selected_pointcloud)
        ),
        "replace_single_pointcloud",
        project,
        pointcloud,
    )

    assert legacy_calls == ["delete"]
    assert project_calls == [("rename", project)]
    assert pointcloud_calls == [("replace_single_pointcloud", project, pointcloud)]


def test_qss_defines_click_feedback_and_project_table_item_colors_without_qt():
    from dronautix_uploader.qt_app.style import APP_STYLE

    assert "QPushButton#ActionButton:hover" in APP_STYLE
    assert "QPushButton#ActionButton:pressed" in APP_STYLE
    assert "QPushButton#SidebarButton:pressed" in APP_STYLE
    assert "QPushButton:pressed" in APP_STYLE
    assert "QTableView#ProjectsTable::item" in APP_STYLE
    assert "QTableView#ProjectsTable::item:selected" in APP_STYLE


def test_projects_page_defers_reload_until_project_action_result_without_qt():
    from dronautix_uploader.qt_app.pages import create_projects_page

    tree = ast.parse(inspect.getsource(create_projects_page))
    click_handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_handle_project_action_click"
    )
    called_names = {
        node.func.id
        for node in ast.walk(click_handler)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "_dispatch_project_action" in called_names
    assert "reload_projects" not in called_names


def test_main_window_project_actions_check_busy_state_before_starting_worker_without_qt():
    from dronautix_uploader.qt_app.main_window import create_main_window

    tree = ast.parse(inspect.getsource(create_main_window))
    action_handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_handle_project_action"
    )
    called_attrs = {
        node.func.attr
        for node in ast.walk(action_handler)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "_handle_project_link_action" in called_attrs
    assert "_has_active_background_tasks" in called_attrs
    assert "_start_background_task" in called_attrs


def test_main_window_records_detail_progress_events_but_not_high_frequency_progress_without_qt():
    from dronautix_uploader.qt_app.main_window import create_main_window

    tree = ast.parse(inspect.getsource(create_main_window))
    progress_handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "receive_progress"
    )
    literal_sets = [
        {element.value for element in node.elts if isinstance(element, ast.Constant)}
        for node in ast.walk(progress_handler)
        if isinstance(node, ast.Set)
    ]

    assert {"log", "step", "detail", "warning", "error"} in literal_sets
    assert all("progress" not in literal_set for literal_set in literal_sets)


def test_runtime_dialogs_accept_settings_defaults_without_qt():
    from dronautix_uploader.qt_app.project_management_dialogs import (
        prompt_replace_all_pointclouds,
        prompt_replace_single_pointcloud,
    )

    assert "defaults" in inspect.signature(prompt_replace_all_pointclouds).parameters
    assert "defaults" in inspect.signature(prompt_replace_single_pointcloud).parameters
