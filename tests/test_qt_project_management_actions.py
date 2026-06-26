from dataclasses import dataclass

from dronautix_uploader.qt_app.project_management import PointcloudPreview, ProjectPreview
from dronautix_uploader.qt_app.project_management_actions import (
    ACTION_DELETE,
    ACTION_DISABLE_LINK,
    ACTION_COPY_LINK,
    ACTION_DOWNLOAD,
    ACTION_ENABLE_LINK,
    ACTION_DUPLICATE,
    ACTION_OPEN_LINK,
    ACTION_RENAME,
    ACTION_REPLACE_ALL_POINTCLOUDS,
    ACTION_REPLACE_SINGLE_POINTCLOUD,
    POINTCLOUD_REPLACEMENT_SECTION,
    PROJECT_MANAGEMENT_ACTIONS,
    PROJECT_MANAGEMENT_SECTION,
    CANCELLED_STATUS,
    available_actions,
    is_action_available,
    summarize_project_operation_result,
)


@dataclass(frozen=True)
class OperationResult:
    status: str
    message: str
    warnings: tuple[str, ...] = ()
    uploaded_keys: tuple[str, ...] = ()
    deleted_keys: tuple[str, ...] = ()
    downloaded_files: tuple[str, ...] = ()
    download_dir: str = ""


def test_project_management_action_ids_labels_and_sections_are_stable():
    actions = {action.action_id: action for action in PROJECT_MANAGEMENT_ACTIONS}

    assert tuple(actions) == (
        ACTION_OPEN_LINK,
        ACTION_COPY_LINK,
        ACTION_RENAME,
        ACTION_DUPLICATE,
        ACTION_DOWNLOAD,
        ACTION_DISABLE_LINK,
        ACTION_ENABLE_LINK,
        ACTION_DELETE,
        ACTION_REPLACE_ALL_POINTCLOUDS,
        ACTION_REPLACE_SINGLE_POINTCLOUD,
    )
    assert actions[ACTION_OPEN_LINK].label == "Im Browser öffnen"
    assert actions[ACTION_COPY_LINK].label == "Link kopieren"
    assert actions[ACTION_RENAME].label == "Projekt umbenennen"
    assert actions[ACTION_DUPLICATE].label == "Projekt duplizieren"
    assert actions[ACTION_DOWNLOAD].label == "Projekt herunterladen"
    assert actions[ACTION_DISABLE_LINK].label == "Link deaktivieren"
    assert actions[ACTION_ENABLE_LINK].label == "Link aktivieren"
    assert actions[ACTION_DELETE].label == "Projekt löschen"
    assert actions[ACTION_REPLACE_ALL_POINTCLOUDS].label == "Alle Punktwolken austauschen"
    assert actions[ACTION_REPLACE_SINGLE_POINTCLOUD].label == "Ausgewählte Punktwolke austauschen"
    assert actions[ACTION_RENAME].section == PROJECT_MANAGEMENT_SECTION
    assert actions[ACTION_REPLACE_SINGLE_POINTCLOUD].section == POINTCLOUD_REPLACEMENT_SECTION
    assert PROJECT_MANAGEMENT_SECTION == "Projektverwaltung"
    assert POINTCLOUD_REPLACEMENT_SECTION == "Punktwolkendaten austauschen"


def test_actions_are_unavailable_without_selected_project():
    assert available_actions(None) == ()

    for action in PROJECT_MANAGEMENT_ACTIONS:
        assert not is_action_available(action.action_id, None)


def test_project_actions_and_replace_all_are_available_for_single_project():
    project = _project(_pointcloud("Direktupload"))

    assert {action.action_id for action in available_actions(project)} == {
        ACTION_RENAME,
        ACTION_OPEN_LINK,
        ACTION_COPY_LINK,
        ACTION_DUPLICATE,
        ACTION_DOWNLOAD,
        ACTION_DISABLE_LINK,
        ACTION_DELETE,
        ACTION_REPLACE_ALL_POINTCLOUDS,
    }
    assert is_action_available(ACTION_REPLACE_ALL_POINTCLOUDS, project)
    assert not is_action_available(ACTION_REPLACE_SINGLE_POINTCLOUD, project)
    assert is_action_available(ACTION_REPLACE_SINGLE_POINTCLOUD, project, project.pointclouds[0])


def test_single_replace_requires_concrete_pointcloud_context_for_multi_project():
    project = _project(_pointcloud("Scan A"), _pointcloud("Scan B"))

    assert not is_action_available(ACTION_REPLACE_SINGLE_POINTCLOUD, project)
    assert is_action_available(ACTION_REPLACE_SINGLE_POINTCLOUD, project, project.pointclouds[1])
    assert {action.action_id for action in available_actions(project, project.pointclouds[0])} == {
        ACTION_RENAME,
        ACTION_OPEN_LINK,
        ACTION_COPY_LINK,
        ACTION_DUPLICATE,
        ACTION_DOWNLOAD,
        ACTION_DISABLE_LINK,
        ACTION_DELETE,
        ACTION_REPLACE_ALL_POINTCLOUDS,
        ACTION_REPLACE_SINGLE_POINTCLOUD,
    }


def test_success_result_summary_is_compact_for_statusbar_and_activity_log():
    summary = summarize_project_operation_result(
        OperationResult(
            status="success",
            message="Projekt dupliziert.",
            uploaded_keys=("projects/demo/cloud.js", "projects/demo/metadata.json"),
        )
    )

    assert summary.status == "success"
    assert summary.statusbar_text == "Projekt dupliziert. (hochgeladen: 2 Keys)"
    assert summary.activity_lines == (
        "Erfolgreich: Projekt dupliziert. (hochgeladen: 2 Keys)",
        "Hochgeladen: 2 Keys",
    )


def test_partial_result_summary_keeps_warnings_and_deleted_key_count():
    summary = summarize_project_operation_result(
        OperationResult(
            status="partial",
            message="Punktwolkendaten teilweise ausgetauscht.",
            warnings=("Index konnte nicht aktualisiert werden.",),
            deleted_keys=("old/cloud.js", "old/metadata.json"),
        )
    )

    assert summary.statusbar_text == (
        "Punktwolkendaten teilweise ausgetauscht. (gelöscht: 2 Keys; Warnung: "
        "Index konnte nicht aktualisiert werden.)"
    )
    assert summary.activity_lines == (
        "Teilweise erfolgreich: Punktwolkendaten teilweise ausgetauscht. "
        "(gelöscht: 2 Keys; Warnung: Index konnte nicht aktualisiert werden.)",
        "Gelöscht: 2 Keys",
        "Warnung: Index konnte nicht aktualisiert werden.",
    )


def test_failed_result_summary_uses_default_message_when_service_message_is_empty():
    summary = summarize_project_operation_result(OperationResult(status="failed", message=""))

    assert summary.status == "failed"
    assert summary.statusbar_text == "Aktion fehlgeschlagen."
    assert summary.activity_lines == ("Fehlgeschlagen: Aktion fehlgeschlagen.",)


def test_cancelled_result_summary_uses_readable_status_label_and_default_message():
    summary = summarize_project_operation_result(OperationResult(status=CANCELLED_STATUS, message=""))

    assert summary.status == "cancelled"
    assert summary.statusbar_text == "Aktion abgebrochen."
    assert summary.activity_lines == ("Abgebrochen: Aktion abgebrochen.",)


def test_download_result_summary_includes_file_count_and_target_dir():
    summary = summarize_project_operation_result(
        OperationResult(
            status="success",
            message="Projekt heruntergeladen.",
            downloaded_files=("C:/Downloads/cloud.js", "C:/Downloads/metadata.json"),
            download_dir="C:/Downloads/projekt",
        )
    )

    assert summary.statusbar_text == "Projekt heruntergeladen. (heruntergeladen: 2 Dateien; Ziel: C:/Downloads/projekt)"
    assert summary.activity_lines == (
        "Erfolgreich: Projekt heruntergeladen. (heruntergeladen: 2 Dateien; Ziel: C:/Downloads/projekt)",
        "Heruntergeladen: 2 Dateien",
        "Ziel: C:/Downloads/projekt",
    )


def test_download_action_requires_project_s3_path():
    project = _project(_pointcloud("Scan"))
    project_without_s3 = ProjectPreview(
        project_id=project.project_id,
        project=project.project,
        customer=project.customer,
        format=project.format,
        updated=project.updated,
        link=project.link,
        disabled=project.disabled,
        pointclouds=project.pointclouds,
    )

    assert is_action_available(ACTION_DOWNLOAD, project)
    assert not is_action_available(ACTION_DOWNLOAD, project_without_s3)


def test_link_open_and_copy_actions_require_link_and_respect_disabled_status():
    project = _project(_pointcloud("Scan"))
    project_without_link = ProjectPreview(
        project_id=project.project_id,
        project=project.project,
        customer=project.customer,
        format=project.format,
        updated=project.updated,
        link="",
        disabled=False,
        pointclouds=project.pointclouds,
        s3_path=project.s3_path,
    )
    disabled_project = ProjectPreview(
        project_id=project.project_id,
        project=project.project,
        customer=project.customer,
        format=project.format,
        updated=project.updated,
        link=project.link,
        disabled=True,
        pointclouds=project.pointclouds,
        s3_path=project.s3_path,
    )

    assert is_action_available(ACTION_OPEN_LINK, project)
    assert is_action_available(ACTION_COPY_LINK, project)
    assert not is_action_available(ACTION_OPEN_LINK, project_without_link)
    assert not is_action_available(ACTION_COPY_LINK, project_without_link)
    assert not is_action_available(ACTION_OPEN_LINK, disabled_project)
    assert is_action_available(ACTION_COPY_LINK, disabled_project)


def test_link_state_actions_follow_current_project_status():
    active_project = _project(_pointcloud("Scan"))
    disabled_project = ProjectPreview(
        project_id=active_project.project_id,
        project=active_project.project,
        customer=active_project.customer,
        format=active_project.format,
        updated=active_project.updated,
        link=active_project.link,
        disabled=True,
        pointclouds=active_project.pointclouds,
        s3_path=active_project.s3_path,
    )

    assert is_action_available(ACTION_DISABLE_LINK, active_project)
    assert not is_action_available(ACTION_ENABLE_LINK, active_project)
    assert not is_action_available(ACTION_DISABLE_LINK, disabled_project)
    assert is_action_available(ACTION_ENABLE_LINK, disabled_project)


def _project(*pointclouds: PointcloudPreview) -> ProjectPreview:
    return ProjectPreview(
        project_id="project-1",
        project="Projekt 1",
        customer="Kunde",
        format="Multi",
        updated="Noch nicht geladen",
        link="viewer/projekte/project-1",
        disabled=False,
        pointclouds=pointclouds,
        s3_path="projects/project-1",
    )


def _pointcloud(name: str) -> PointcloudPreview:
    return PointcloudPreview(name=name, format="Potree", points="-", crs="EPSG:25832")
