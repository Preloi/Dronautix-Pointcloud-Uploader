"""UI-free activity log preview data helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dronautix_uploader.core.contracts import ProgressEvent


ACTION_ALL = "Alle Aktionen"
ACTION_UPLOAD = "Upload"
ACTION_REPLACE = "Replace"
ACTION_DELETE = "Delete"
ACTION_DOWNLOAD = "Download"
ACTION_CONVERT = "Konvertierung"
ACTION_UPDATE = "Update"
ACTION_FILTERS = (ACTION_ALL, ACTION_UPLOAD, ACTION_REPLACE, ACTION_DELETE, ACTION_DOWNLOAD, ACTION_CONVERT, ACTION_UPDATE)

STATUS_ALL = "Alle Status"
STATUS_QUEUED = "Geplant"
STATUS_RUNNING = "Läuft"
STATUS_SUCCESS = "Erfolgreich"
STATUS_WARNING = "Mit Warnung"
STATUS_FAILED = "Fehlgeschlagen"
STATUS_FILTERS = (STATUS_ALL, STATUS_QUEUED, STATUS_RUNNING, STATUS_SUCCESS, STATUS_WARNING, STATUS_FAILED)

SEVERITY_ALL = "Alle Schweregrade"
SEVERITY_INFO = "Info"
SEVERITY_SUCCESS = "Erfolg"
SEVERITY_WARNING = "Warnung"
SEVERITY_ERROR = "Fehler"
SEVERITY_FILTERS = (SEVERITY_ALL, SEVERITY_INFO, SEVERITY_SUCCESS, SEVERITY_WARNING, SEVERITY_ERROR)


@dataclass(frozen=True)
class ActivityLogEntry:
    timestamp: str
    action: str
    status: str
    severity: str
    project: str
    customer: str
    actor: str
    summary: str
    detail: str
    source_path: str
    target_path: str
    duration: str


@dataclass(frozen=True)
class ActivityStatusSummary:
    total: int
    running: int
    warnings: int
    failed: int
    completed: int


@dataclass(frozen=True)
class ActivityPreview:
    entries: tuple[ActivityLogEntry, ...]

    @property
    def status_summary(self) -> ActivityStatusSummary:
        return summarize_activity_entries(self.entries)


class ActivityLogStore:
    """Small UI-free in-memory activity sink for the Qt preview."""

    def __init__(
        self,
        entries: Iterable[ActivityLogEntry] = (),
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._entries = list(entries)
        self._clock = clock or datetime.now

    @property
    def entries(self) -> tuple[ActivityLogEntry, ...]:
        return tuple(self._entries)

    def preview(self) -> ActivityPreview:
        return ActivityPreview(entries=self.entries)

    def add_entry(self, entry: ActivityLogEntry) -> ActivityLogEntry:
        self._entries.insert(0, entry)
        return entry

    def record_progress_event(
        self,
        event: ProgressEvent,
        *,
        action: str = ACTION_UPLOAD,
        project: str = "",
        customer: str = "",
        actor: str = "Service",
        source_path: str = "",
        target_path: str = "",
    ) -> ActivityLogEntry:
        return self.add_entry(
            progress_event_to_activity_entry(
                event,
                timestamp=_format_timestamp(self._clock()),
                action=action,
                project=project,
                customer=customer,
                actor=actor,
                source_path=source_path,
                target_path=target_path,
            )
        )

    def record_operation_summary(
        self,
        summary: Any,
        *,
        action: str = ACTION_UPDATE,
        project: str = "",
        customer: str = "",
        actor: str = "Service",
        source_path: str = "",
        target_path: str = "",
        duration: str = "-",
    ) -> ActivityLogEntry:
        return self.add_entry(
            operation_summary_to_activity_entry(
                summary,
                timestamp=_format_timestamp(self._clock()),
                action=action,
                project=project,
                customer=customer,
                actor=actor,
                source_path=source_path,
                target_path=target_path,
                duration=duration,
            )
        )

    def record_error(
        self,
        error: Any,
        *,
        action: str = ACTION_UPDATE,
        project: str = "",
        customer: str = "",
        actor: str = "Service",
        source_path: str = "",
        target_path: str = "",
        duration: str = "-",
    ) -> ActivityLogEntry:
        message = str(error or "").strip() or "Aktion fehlgeschlagen."
        return self.add_entry(
            ActivityLogEntry(
                timestamp=_format_timestamp(self._clock()),
                action=action,
                status=STATUS_FAILED,
                severity=SEVERITY_ERROR,
                project=project,
                customer=customer,
                actor=actor,
                summary=message,
                detail=f"Fehler: {message}",
                source_path=source_path,
                target_path=target_path,
                duration=duration,
            )
        )


def progress_event_to_activity_entry(
    event: ProgressEvent,
    *,
    timestamp: str,
    action: str = ACTION_UPLOAD,
    project: str = "",
    customer: str = "",
    actor: str = "Service",
    source_path: str = "",
    target_path: str = "",
) -> ActivityLogEntry:
    status, severity = _status_and_severity_for_progress(event)
    summary = event.message or event.detail or "Fortschritt empfangen."
    detail_parts = []
    if event.step is not None and event.total_steps is not None:
        detail_parts.append(f"Schritt {event.step}/{event.total_steps}")
    if event.percent is not None:
        detail_parts.append(_format_progress_percent(event.percent))
    if event.detail:
        detail_parts.append(event.detail)
    detail = " - ".join(detail_parts) or summary
    return ActivityLogEntry(
        timestamp=timestamp,
        action=action,
        status=status,
        severity=severity,
        project=project,
        customer=customer,
        actor=actor,
        summary=summary,
        detail=detail,
        source_path=source_path,
        target_path=target_path,
        duration="-",
    )


def normalize_progress_value(percent: float) -> int:
    """Map core progress (Bruch 0..1 oder Prozent >1) auf 0..100 fuer Balken."""

    value = float(percent)
    if 0 <= value <= 1:
        value *= 100
    return max(0, min(100, int(round(value))))


def _format_progress_percent(percent: float) -> str:
    return f"{normalize_progress_value(percent)}%"


def operation_summary_to_activity_entry(
    summary: Any,
    *,
    timestamp: str,
    action: str = ACTION_UPDATE,
    project: str = "",
    customer: str = "",
    actor: str = "Service",
    source_path: str = "",
    target_path: str = "",
    duration: str = "-",
) -> ActivityLogEntry:
    status_value = str(getattr(summary, "status", "") or "")
    status, severity = _status_and_severity_for_operation(status_value)
    message = str(getattr(summary, "message", "") or getattr(summary, "statusbar_text", "") or "Aktion beendet.")
    activity_lines = tuple(getattr(summary, "activity_lines", ()) or ())
    detail = "\n".join(activity_lines) if activity_lines else message
    return ActivityLogEntry(
        timestamp=timestamp,
        action=action,
        status=status,
        severity=severity,
        project=project,
        customer=customer,
        actor=actor,
        summary=message,
        detail=detail,
        source_path=source_path,
        target_path=target_path,
        duration=duration,
    )


def action_filter_accepts(action: str, selected_action: str) -> bool:
    if selected_action == ACTION_ALL:
        return True
    return action == selected_action


def status_filter_accepts(status: str, selected_status: str) -> bool:
    if selected_status == STATUS_ALL:
        return True
    return status == selected_status


def severity_filter_accepts(severity: str, selected_severity: str) -> bool:
    if selected_severity == SEVERITY_ALL:
        return True
    return severity == selected_severity


def filter_activity_entries(
    entries: tuple[ActivityLogEntry, ...],
    *,
    action: str = ACTION_ALL,
    status: str = STATUS_ALL,
    severity: str = SEVERITY_ALL,
    query: str = "",
) -> tuple[ActivityLogEntry, ...]:
    """Return activity entries matching action, status, severity and text search."""

    needle = query.strip().casefold()
    filtered = []
    for entry in entries:
        if not action_filter_accepts(entry.action, action):
            continue
        if not status_filter_accepts(entry.status, status):
            continue
        if not severity_filter_accepts(entry.severity, severity):
            continue
        if needle and needle not in format_activity_search_text(entry).casefold():
            continue
        filtered.append(entry)
    return tuple(filtered)


def summarize_activity_entries(entries: tuple[ActivityLogEntry, ...]) -> ActivityStatusSummary:
    return ActivityStatusSummary(
        total=len(entries),
        running=sum(1 for entry in entries if entry.status == STATUS_RUNNING),
        warnings=sum(1 for entry in entries if entry.severity == SEVERITY_WARNING),
        failed=sum(1 for entry in entries if entry.status == STATUS_FAILED),
        completed=sum(1 for entry in entries if entry.status == STATUS_SUCCESS),
    )


def _status_and_severity_for_progress(event: ProgressEvent) -> tuple[str, str]:
    if event.kind == "error":
        return STATUS_FAILED, SEVERITY_ERROR
    if event.kind == "warning":
        return STATUS_WARNING, SEVERITY_WARNING
    return STATUS_RUNNING, SEVERITY_INFO


def _status_and_severity_for_operation(status: str) -> tuple[str, str]:
    if status == "success":
        return STATUS_SUCCESS, SEVERITY_SUCCESS
    if status == "partial":
        return STATUS_WARNING, SEVERITY_WARNING
    if status == "cancelled":
        return STATUS_WARNING, SEVERITY_WARNING
    if status == "failed":
        return STATUS_FAILED, SEVERITY_ERROR
    return STATUS_SUCCESS, SEVERITY_INFO


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%d.%m.%Y %H:%M")


def format_activity_search_text(entry: ActivityLogEntry) -> str:
    return " ".join(
        (
            entry.timestamp,
            entry.action,
            entry.status,
            entry.severity,
            entry.project,
            entry.customer,
            entry.actor,
            entry.summary,
            entry.detail,
            entry.source_path,
            entry.target_path,
        )
    )


def format_activity_detail(entry: ActivityLogEntry) -> str:
    return (
        f"Aktion: {entry.action}\n"
        f"Status: {entry.status} / {entry.severity}\n"
        f"Projekt: {entry.project} ({entry.customer})\n"
        f"Ausgelöst von: {entry.actor}\n"
        f"Dauer: {entry.duration}\n"
        f"Quelle: {entry.source_path or '-'}\n"
        f"Ziel: {entry.target_path or '-'}\n\n"
        f"{entry.detail}"
    )


def example_activity_preview() -> ActivityPreview:
    """Return representative activity data for the disconnected Qt preview."""

    return ActivityPreview(
        entries=(
            ActivityLogEntry(
                timestamp="21.06.2026 15:42",
                action=ACTION_UPLOAD,
                status=STATUS_RUNNING,
                severity=SEVERITY_INFO,
                project="Beispielprojekt Nord",
                customer="Dronautix",
                actor="Preview Wizard",
                summary="3 Quellen werden vorbereitet",
                detail="Potree-Konvertierung für zwei LAS/LAZ-Dateien gestartet, COPC wird direkt hochgeladen.",
                source_path="D:/Projekte/Nord",
                target_path="s3://dronautix-viewer/viewer/projekte/beispielprojekt-nord",
                duration="00:03:18",
            ),
            ActivityLogEntry(
                timestamp="21.06.2026 15:36",
                action=ACTION_REPLACE,
                status=STATUS_WARNING,
                severity=SEVERITY_WARNING,
                project="Bestand EG",
                customer="Dronautix",
                actor="Projektverwaltung",
                summary="Punktwolkendaten ersetzt, CRS unvollständig",
                detail="Horizontales CRS wurde erkannt. Vertikales Datum fehlt und muss vor Release geprüft werden.",
                source_path="D:/Austausch/Bestand_EG_neu.laz",
                target_path="viewer/projekte/beispielprojekt-nord/bestand-eg",
                duration="00:11:04",
            ),
            ActivityLogEntry(
                timestamp="21.06.2026 14:58",
                action=ACTION_UPDATE,
                status=STATUS_SUCCESS,
                severity=SEVERITY_SUCCESS,
                project="COPC Demo",
                customer="Interner Test",
                actor="Metadatenservice",
                summary="projects_index.json und metadata.json aktualisiert",
                detail="Format, Viewer-Pfad und CRS-Metadaten wurden in allen relevanten Dateien synchronisiert.",
                source_path="",
                target_path="viewer/projekte/copc-demo",
                duration="00:00:09",
            ),
            ActivityLogEntry(
                timestamp="21.06.2026 15:18",
                action=ACTION_DOWNLOAD,
                status=STATUS_SUCCESS,
                severity=SEVERITY_SUCCESS,
                project="Bestand EG",
                customer="Dronautix",
                actor="Projektverwaltung",
                summary="Projekt lokal heruntergeladen",
                detail="Die Projektdateien wurden in den gewählten Zielordner geschrieben.",
                source_path="viewer/projekte/beispielprojekt-nord/bestand-eg",
                target_path="D:/Downloads/Bestand_EG",
                duration="00:02:41",
            ),
            ActivityLogEntry(
                timestamp="20.06.2026 18:21",
                action=ACTION_DELETE,
                status=STATUS_FAILED,
                severity=SEVERITY_ERROR,
                project="Deaktivierter Upload",
                customer="Kunde",
                actor="Projektverwaltung",
                summary="Löschen abgebrochen",
                detail="S3-Objekte konnten in der Preview nicht entfernt werden, weil keine Service-Integration aktiv ist.",
                source_path="",
                target_path="viewer/projekte/deaktivierter-upload",
                duration="00:00:01",
            ),
            ActivityLogEntry(
                timestamp="20.06.2026 09:12",
                action=ACTION_UPLOAD,
                status=STATUS_QUEUED,
                severity=SEVERITY_INFO,
                project="Dachaufmass Süd",
                customer="Kunde",
                actor="Preview Wizard",
                summary="Upload wartet auf Review-Freigabe",
                detail="Quellen sind gesammelt. Zielpfad und CRS-Auswahl müssen vor dem Start bestätigt werden.",
                source_path="D:/Projekte/Süd/Dachaufmass.las",
                target_path="viewer/projekte/dachaufmass-süd",
                duration="-",
            ),
        )
    )


__all__ = [
    "ACTION_ALL",
    "ACTION_CONVERT",
    "ACTION_DELETE",
    "ACTION_DOWNLOAD",
    "ACTION_FILTERS",
    "ACTION_REPLACE",
    "ACTION_UPDATE",
    "ACTION_UPLOAD",
    "ActivityLogEntry",
    "ActivityLogStore",
    "ActivityPreview",
    "ActivityStatusSummary",
    "SEVERITY_ALL",
    "SEVERITY_ERROR",
    "SEVERITY_FILTERS",
    "SEVERITY_INFO",
    "SEVERITY_SUCCESS",
    "SEVERITY_WARNING",
    "STATUS_ALL",
    "STATUS_FAILED",
    "STATUS_FILTERS",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "STATUS_SUCCESS",
    "STATUS_WARNING",
    "action_filter_accepts",
    "example_activity_preview",
    "filter_activity_entries",
    "format_activity_detail",
    "format_activity_search_text",
    "normalize_progress_value",
    "operation_summary_to_activity_entry",
    "progress_event_to_activity_entry",
    "severity_filter_accepts",
    "status_filter_accepts",
    "summarize_activity_entries",
]
