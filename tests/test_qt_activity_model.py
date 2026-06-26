from datetime import datetime

from dronautix_uploader.core.contracts import ProgressEvent
from dronautix_uploader.qt_app.activity_model import (
    ACTION_DELETE,
    ACTION_DOWNLOAD,
    ACTION_REPLACE,
    ACTION_UPDATE,
    ACTION_UPLOAD,
    ActivityLogStore,
    SEVERITY_INFO,
    SEVERITY_ERROR,
    SEVERITY_SUCCESS,
    SEVERITY_WARNING,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    STATUS_WARNING,
    action_filter_accepts,
    example_activity_preview,
    filter_activity_entries,
    format_activity_detail,
    operation_summary_to_activity_entry,
    progress_event_to_activity_entry,
    severity_filter_accepts,
    status_filter_accepts,
    summarize_activity_entries,
)
from dronautix_uploader.qt_app.project_management_actions import ProjectOperationSummary


def test_example_activity_preview_covers_core_log_actions():
    preview = example_activity_preview()

    actions = {entry.action for entry in preview.entries}

    assert {ACTION_UPLOAD, ACTION_REPLACE, ACTION_DELETE, ACTION_DOWNLOAD, ACTION_UPDATE}.issubset(actions)
    assert any(entry.status == STATUS_RUNNING for entry in preview.entries)
    assert any(entry.status == STATUS_SUCCESS for entry in preview.entries)
    assert any(entry.severity == SEVERITY_ERROR for entry in preview.entries)


def test_activity_filter_predicates_accept_selected_values_only():
    assert action_filter_accepts(ACTION_UPLOAD, "Alle Aktionen")
    assert action_filter_accepts(ACTION_DELETE, ACTION_DELETE)
    assert action_filter_accepts(ACTION_DOWNLOAD, ACTION_DOWNLOAD)
    assert not action_filter_accepts(ACTION_REPLACE, ACTION_UPLOAD)

    assert status_filter_accepts(STATUS_FAILED, "Alle Status")
    assert status_filter_accepts(STATUS_RUNNING, STATUS_RUNNING)
    assert not status_filter_accepts(STATUS_SUCCESS, STATUS_FAILED)

    assert severity_filter_accepts(SEVERITY_WARNING, "Alle Schweregrade")
    assert severity_filter_accepts(SEVERITY_ERROR, SEVERITY_ERROR)
    assert not severity_filter_accepts(SEVERITY_WARNING, SEVERITY_ERROR)


def test_activity_entries_filter_by_action_status_severity_and_query():
    entries = example_activity_preview().entries

    failed_deletes = filter_activity_entries(
        entries,
        action=ACTION_DELETE,
        status=STATUS_FAILED,
        severity=SEVERITY_ERROR,
    )
    north_uploads = filter_activity_entries(entries, action=ACTION_UPLOAD, query="nord")

    assert len(failed_deletes) == 1
    assert failed_deletes[0].summary == "Löschen abgebrochen"
    assert len(north_uploads) == 1
    assert north_uploads[0].project == "Beispielprojekt Nord"


def test_activity_summary_and_detail_are_qt_free_and_stable():
    entries = example_activity_preview().entries
    summary = summarize_activity_entries(entries)
    detail = format_activity_detail(entries[0])

    assert summary.total == len(entries)
    assert summary.running == 1
    assert summary.failed == 1
    assert summary.completed == 2
    assert "Aktion: Upload" in detail
    assert "Ziel:" in detail


def test_progress_event_mapping_sets_status_severity_and_detail():
    entry = progress_event_to_activity_entry(
        ProgressEvent(kind="step", message="Konvertierung gestartet", step=1, total_steps=3, percent=25, detail="scan.laz"),
        timestamp="21.06.2026 16:10",
        action=ACTION_UPLOAD,
        project="Projekt",
        customer="Kunde",
    )
    warning = progress_event_to_activity_entry(
        ProgressEvent(kind="warning", message="Cleanup offen"),
        timestamp="21.06.2026 16:11",
        action=ACTION_REPLACE,
    )
    failed = progress_event_to_activity_entry(
        ProgressEvent(kind="error", message="Upload fehlgeschlagen"),
        timestamp="21.06.2026 16:12",
    )

    assert entry.status == STATUS_RUNNING
    assert entry.severity == SEVERITY_INFO
    assert "Schritt 1/3" in entry.detail
    assert "25%" in entry.detail
    assert warning.status == STATUS_WARNING
    assert warning.severity == SEVERITY_WARNING
    assert failed.status == STATUS_FAILED
    assert failed.severity == SEVERITY_ERROR


def test_progress_event_mapping_accepts_fractional_and_percent_scales():
    fractional = progress_event_to_activity_entry(
        ProgressEvent(kind="progress", percent=1.0),
        timestamp="21.06.2026 16:13",
    )
    percent = progress_event_to_activity_entry(
        ProgressEvent(kind="progress", percent=25),
        timestamp="21.06.2026 16:14",
    )

    assert "100%" in fractional.detail
    assert "25%" in percent.detail


def test_detail_progress_event_mapping_keeps_detail_text_visible():
    entry = progress_event_to_activity_entry(
        ProgressEvent(kind="detail", detail="C:/Daten/scan.laz"),
        timestamp="21.06.2026 16:15",
        action=ACTION_UPLOAD,
    )

    assert entry.status == STATUS_RUNNING
    assert entry.summary == "C:/Daten/scan.laz"
    assert entry.detail == "C:/Daten/scan.laz"


def test_operation_summary_mapping_sets_success_partial_and_failed_status():
    success = operation_summary_to_activity_entry(
        ProjectOperationSummary(status="success", message="Upload abgeschlossen."),
        timestamp="21.06.2026 16:20",
        action=ACTION_UPLOAD,
    )
    partial = operation_summary_to_activity_entry(
        ProjectOperationSummary(status="partial", message="Replace mit Warnung.", warnings=("Orphan cleanup offen.",)),
        timestamp="21.06.2026 16:21",
        action=ACTION_REPLACE,
    )
    failed = operation_summary_to_activity_entry(
        ProjectOperationSummary(status="failed", message="Löschen fehlgeschlagen."),
        timestamp="21.06.2026 16:22",
        action=ACTION_DELETE,
    )
    cancelled = operation_summary_to_activity_entry(
        ProjectOperationSummary(status="cancelled", message="Download abgebrochen."),
        timestamp="21.06.2026 16:23",
        action=ACTION_DOWNLOAD,
    )

    assert success.status == STATUS_SUCCESS
    assert success.severity == SEVERITY_SUCCESS
    assert partial.status == STATUS_WARNING
    assert partial.severity == SEVERITY_WARNING
    assert "Orphan cleanup offen." in partial.detail
    assert failed.status == STATUS_FAILED
    assert failed.severity == SEVERITY_ERROR
    assert cancelled.status == STATUS_WARNING
    assert cancelled.severity == SEVERITY_WARNING


def test_activity_log_store_prepends_progress_and_summary_entries():
    store = ActivityLogStore(clock=lambda: datetime(2026, 6, 21, 16, 30))

    store.record_progress_event(ProgressEvent(kind="log", message="Vorbereitung"), action=ACTION_UPLOAD)
    store.record_operation_summary(
        ProjectOperationSummary(status="success", message="Projekt hochgeladen."),
        action=ACTION_UPLOAD,
        project="Projekt",
        customer="Kunde",
    )

    preview = store.preview()
    assert [entry.summary for entry in preview.entries] == ["Projekt hochgeladen.", "Vorbereitung"]
    assert preview.entries[0].timestamp == "21.06.2026 16:30"
    assert preview.entries[0].project == "Projekt"


def test_activity_log_store_records_errors_as_failed_entries():
    store = ActivityLogStore(clock=lambda: datetime(2026, 6, 21, 16, 35))

    store.record_error(
        RuntimeError("S3 nicht erreichbar"),
        action=ACTION_UPDATE,
        project="Projekt",
        customer="Kunde",
        actor="Einstellungen",
        target_path="projects_index.json",
    )

    entry = store.preview().entries[0]
    assert entry.timestamp == "21.06.2026 16:35"
    assert entry.status == STATUS_FAILED
    assert entry.severity == SEVERITY_ERROR
    assert entry.summary == "S3 nicht erreichbar"
    assert entry.detail == "Fehler: S3 nicht erreichbar"
    assert entry.actor == "Einstellungen"
