"""UI-free dashboard and settings preview helpers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_ERROR = "error"
STATUS_INFO = "info"

UPDATE_CHANNEL_STABLE = "Stable"
UPDATE_CHANNEL_PREVIEW = "Preview"
UPDATE_CHANNEL_MANUAL = "Manuell"

SETTINGS_ACTION_EDIT = "edit"
SETTINGS_ACTION_TEST_CONNECTION = "test_connection"
SETTINGS_ACTION_CHECK_UPDATE = "check_update"


@dataclass(frozen=True)
class StatusCardPreview:
    title: str
    value: str
    detail: str
    level: str = STATUS_INFO


@dataclass(frozen=True)
class SettingsStatusItem:
    name: str
    status: str
    detail: str
    level: str
    action: str


@dataclass(frozen=True)
class PreviewHint:
    title: str
    detail: str
    level: str = STATUS_INFO


@dataclass(frozen=True)
class CutoverChecklistItem:
    name: str
    complete: bool
    detail: str
    required: bool = True


@dataclass(frozen=True)
class CutoverReadiness:
    items: tuple[CutoverChecklistItem, ...]

    @property
    def required_items(self) -> tuple[CutoverChecklistItem, ...]:
        return tuple(item for item in self.items if item.required)

    @property
    def completed_required_count(self) -> int:
        return sum(1 for item in self.required_items if item.complete)

    @property
    def required_count(self) -> int:
        return len(self.required_items)

    @property
    def ready(self) -> bool:
        return bool(self.required_items) and self.completed_required_count == self.required_count

    @property
    def first_open_item(self) -> CutoverChecklistItem | None:
        for item in self.required_items:
            if not item.complete:
                return item
        return None


@dataclass(frozen=True)
class DashboardPreview:
    status_cards: tuple[StatusCardPreview, ...]
    settings_status: tuple[SettingsStatusItem, ...]
    cutover_hints: tuple[PreviewHint, ...]


@dataclass(frozen=True)
class SettingsPreview:
    settings_status: tuple[SettingsStatusItem, ...]
    update_channel: str
    output_folder: str
    converter_bundle: str
    converter_override: str
    aws_profile: str
    preview_hints: tuple[PreviewHint, ...]


def credential_status(has_access_key: bool, has_secret_key: bool, profile: str = "") -> SettingsStatusItem:
    """Return the AWS credential readiness shown by the preview settings."""

    if has_access_key and has_secret_key:
        status = "Bereit"
        detail = f"AWS-Zugangsdaten gefunden ({profile or 'Standardprofil'})."
        level = STATUS_OK
        action = "Verbindung testen"
    elif profile:
        status = "Profil prüfen"
        detail = f"Profil {profile} ist ausgewählt, direkte Keys fehlen in der Preview."
        level = STATUS_WARNING
        action = "Profil öffnen"
    else:
        status = "Nicht konfiguriert"
        detail = "Keine vollständigen AWS-Zugangsdaten in der Preview-Konfiguration."
        level = STATUS_ERROR
        action = "Credentials eintragen"
    return SettingsStatusItem("AWS Credentials", status, detail, level, action)


def converter_status(bundle_available: bool, override_path: str = "") -> SettingsStatusItem:
    """Return the PotreeConverter source status."""

    if override_path:
        return SettingsStatusItem(
            "Converter",
            "Override aktiv",
            f"PotreeConverter wird aus {override_path} verwendet.",
            STATUS_WARNING,
            "Pfad ändern",
        )
    if bundle_available:
        return SettingsStatusItem(
            "Converter",
            "Bundle bereit",
            "Gebündelter PotreeConverter ist verfügbar.",
            STATUS_OK,
            "Pfad ändern",
        )
    return SettingsStatusItem(
        "Converter",
        "Fehlt",
        "Kein Bundle und kein Override-Pfad hinterlegt.",
        STATUS_ERROR,
        "Pfad auswählen",
    )


def output_folder_status(path: str, writable: bool) -> SettingsStatusItem:
    """Return the local output folder status for conversions and installers."""

    if not path.strip():
        return SettingsStatusItem(
            "Output-Ordner",
            "Nicht gesetzt",
            "Konvertierungen und Installer brauchen einen lokalen Ausgabeordner.",
            STATUS_ERROR,
            "Ordner wählen",
        )
    if writable:
        return SettingsStatusItem(
            "Output-Ordner",
            "Schreibbar",
            path,
            STATUS_OK,
            "Ordner ändern",
        )
    return SettingsStatusItem(
        "Output-Ordner",
        "Nur lesbar",
        f"{path} ist hinterlegt, aber in der Preview nicht schreibbar.",
        STATUS_WARNING,
        "Ordner ändern",
    )


def update_channel_status(channel: str, manifest_version: str = "") -> SettingsStatusItem:
    """Return the update-channel summary shown in settings."""

    normalized = channel.strip() or UPDATE_CHANNEL_STABLE
    if normalized == UPDATE_CHANNEL_PREVIEW:
        level = STATUS_WARNING
        detail = "Preview-Kanal zeigt frühe Builds und sollte vor Cutover bewusst aktiviert werden."
    elif normalized == UPDATE_CHANNEL_MANUAL:
        level = STATUS_INFO
        detail = "Automatische Update-Hinweise sind deaktiviert; Releases werden manuell geprüft."
    else:
        level = STATUS_OK
        detail = "Stable-Kanal nutzt latest-release.json vom master-Branch."
    if manifest_version:
        detail = f"{detail} Manifest: {manifest_version}."
    return SettingsStatusItem("Update-Kanal", normalized, detail, level, "Kanal wechseln")


def make_preview_hints(cutover_ready: bool) -> tuple[PreviewHint, ...]:
    """Return dashboard/settings hints for preview versus production cutover."""

    if cutover_ready:
        return (
            PreviewHint(
                "Cutover bereit",
                "Dashboard, Settings und Wizard können an echte Services angebunden werden.",
                STATUS_OK,
            ),
            PreviewHint(
                "Preview bleibt getrennt",
                "Die Qt-App nutzt eigene Preview-Config und besetzt den produktiven Update-Kanal noch nicht.",
                STATUS_INFO,
            ),
        )
    return (
        PreviewHint(
            "Preview-Modus",
            "V2 nutzt getrennte Config-/Keyring-Daten; produktiver Update-Kanal und Installer bleiben unangetastet.",
            STATUS_INFO,
        ),
        PreviewHint(
            "Cutover-Hinweis",
            "Vor Final-Cutover echten S3-Test, Golden Files, GitHub Asset-SHA und Altversions-Update prüfen.",
            STATUS_WARNING,
        ),
    )


def build_cutover_readiness(
    *,
    runtime_connected: bool = False,
    golden_ready: bool = False,
    v2_golden_comparison_ready: bool = False,
    preview_packaging_ready: bool = True,
    final_packaging_ready: bool = False,
    real_s3_acceptance_passed: bool = False,
    github_asset_sha_verified: bool = False,
    altversion_update_verified: bool = False,
) -> CutoverReadiness:
    """Build the explicit V2 cutover checklist shown by the dashboard."""

    return CutoverReadiness(
        items=(
            CutoverChecklistItem(
                "Runtime verbunden",
                runtime_connected,
                "Qt-Runtime hat echte S3-Service-Controller geladen.",
            ),
            CutoverChecklistItem(
                "Golden Masters",
                golden_ready,
                "Alle Legacy-Outputs sind gecaptured und normalisiert.",
            ),
            CutoverChecklistItem(
                "V2-Golden-Vergleich",
                v2_golden_comparison_ready,
                "V2-Ausgaben matchen die normalisierten Legacy-Golden-Masters.",
            ),
            CutoverChecklistItem(
                "Preview-Paket getrennt",
                preview_packaging_ready,
                "Preview-Build nutzt keinen produktiven Update-Kanal.",
            ),
            CutoverChecklistItem(
                "Final-V2-Packaging",
                final_packaging_ready,
                "Finaler V2-Installer übernimmt AppId, Namen und Manifest-Vertrag.",
            ),
            CutoverChecklistItem(
                "Echter S3-Akzeptanztest",
                real_s3_acceptance_passed,
                "LAS/LAZ, COPC, Multi-Projekt und Projektverwaltung wurden gegen S3 getestet.",
            ),
            CutoverChecklistItem(
                "GitHub Asset SHA",
                github_asset_sha_verified,
                "Release-Asset und installer_sha256 wurden remote verifiziert.",
            ),
            CutoverChecklistItem(
                "Altversions-Update",
                altversion_update_verified,
                "Update von einer installierten Altversion auf Final V2 wurde getestet.",
            ),
        )
    )


def make_cutover_hints(readiness: CutoverReadiness) -> tuple[PreviewHint, ...]:
    if readiness.ready:
        return (
            PreviewHint(
                "Cutover bereit",
                f"{readiness.completed_required_count}/{readiness.required_count} Gates erfüllt.",
                STATUS_OK,
            ),
            PreviewHint(
                "Release-Gate",
                "Final V2 kann erst nach expliziter Freigabe den bestehenden Update-Kanal nutzen.",
                STATUS_INFO,
            ),
        )

    open_item = readiness.first_open_item
    next_detail = f"Nächster offener Gate: {open_item.name} - {open_item.detail}" if open_item else "Offene Gates prüfen."
    return (
        PreviewHint(
            "Cutover blockiert",
            f"{readiness.completed_required_count}/{readiness.required_count} Gates erfüllt.",
            STATUS_WARNING,
        ),
        PreviewHint(
            "Nächster Schritt",
            next_detail,
            STATUS_WARNING,
        ),
    )


def settings_status_action_id(item: SettingsStatusItem) -> str:
    """Return the Settings page action ID represented by a status-row button."""

    if item.name == "AWS Credentials" and item.action == "Verbindung testen":
        return SETTINGS_ACTION_TEST_CONNECTION
    return SETTINGS_ACTION_EDIT


def example_settings_preview() -> SettingsPreview:
    """Return representative settings data for the disconnected Qt preview."""

    output_folder = "C:/Dronautix/PointcloudUploader/Output"
    converter_bundle = "tools/PotreeConverter/PotreeConverter.exe"
    converter_override = ""
    settings = (
        credential_status(True, True, "dronautix-uploader"),
        converter_status(True, converter_override),
        output_folder_status(output_folder, True),
        update_channel_status(UPDATE_CHANNEL_STABLE, "1.7.10"),
    )
    return SettingsPreview(
        settings_status=settings,
        update_channel=UPDATE_CHANNEL_STABLE,
        output_folder=output_folder,
        converter_bundle=converter_bundle,
        converter_override=converter_override or "Kein Override",
        aws_profile="dronautix-uploader",
        preview_hints=make_preview_hints(False),
    )


def example_dashboard_preview() -> DashboardPreview:
    """Return representative dashboard data for the disconnected Qt preview."""

    settings = example_settings_preview().settings_status
    return DashboardPreview(
        status_cards=(
            StatusCardPreview("Aktive Projekte", "2", "1 deaktiviertes Projekt im Index", STATUS_OK),
            StatusCardPreview("Upload-Queue", "1", "Review wartet auf Freigabe", STATUS_WARNING),
            StatusCardPreview("Converter", "Bereit", "Bundle ohne Override", STATUS_OK),
            StatusCardPreview("Update", "Stable", "Manifest 1.7.10", STATUS_OK),
        ),
        settings_status=settings,
        cutover_hints=make_preview_hints(False),
    )


def build_dashboard_preview(
    *,
    projects: Iterable[Any] = (),
    settings_preview: SettingsPreview | None = None,
    activity_preview: Any = None,
    runtime_status: str = "",
    cutover_readiness: CutoverReadiness | None = None,
) -> DashboardPreview:
    """Build dashboard cards from live preview providers."""

    project_rows = tuple(projects)
    active_count = sum(1 for project in project_rows if not bool(getattr(project, "disabled", False)))
    disabled_count = sum(1 for project in project_rows if bool(getattr(project, "disabled", False)))
    settings = settings_preview or example_settings_preview()
    activity_summary = getattr(activity_preview, "status_summary", None)
    running = int(getattr(activity_summary, "running", 0) or 0)
    warnings = int(getattr(activity_summary, "warnings", 0) or 0)
    failed = int(getattr(activity_summary, "failed", 0) or 0)
    converter_item = _find_status_item(settings.settings_status, "Converter")
    credential_item = _find_status_item(settings.settings_status, "AWS Credentials")
    update_item = _find_status_item(settings.settings_status, "Update-Kanal")
    upload_level = STATUS_OK if running == 0 and failed == 0 else STATUS_WARNING if failed == 0 else STATUS_ERROR
    readiness = cutover_readiness or build_cutover_readiness(
        runtime_connected="S3 verbunden" in runtime_status,
        golden_ready=False,
        preview_packaging_ready=True,
    )
    return DashboardPreview(
        status_cards=(
            StatusCardPreview(
                "Aktive Projekte",
                str(active_count),
                f"{disabled_count} deaktiviert",
                STATUS_OK if active_count else STATUS_INFO,
            ),
            StatusCardPreview(
                "Operationen",
                str(running),
                f"{warnings} Warnungen, {failed} Fehler",
                upload_level,
            ),
            StatusCardPreview(
                "Converter",
                converter_item.status if converter_item else "Unbekannt",
                converter_item.detail if converter_item else "Keine Converter-Information.",
                converter_item.level if converter_item else STATUS_INFO,
            ),
            StatusCardPreview(
                "Update",
                settings.update_channel,
                update_item.detail if update_item else runtime_status or "Update-Kanal nicht geladen.",
                update_item.level if update_item else STATUS_INFO,
            ),
        ),
        settings_status=settings.settings_status,
        cutover_hints=(
            PreviewHint(
                "Runtime",
                runtime_status or "Preview bereit.",
                credential_item.level if credential_item else STATUS_INFO,
            ),
            *make_cutover_hints(readiness),
        ),
    )


def _find_status_item(items: tuple[SettingsStatusItem, ...], name: str) -> SettingsStatusItem | None:
    for item in items:
        if item.name == name:
            return item
    return None


def status_level_label(level: str) -> str:
    """Return the compact German label for a status level."""

    return {
        STATUS_OK: "OK",
        STATUS_WARNING: "Warnung",
        STATUS_ERROR: "Fehler",
        STATUS_INFO: "Info",
    }.get(level, "Info")


__all__ = [
    "DashboardPreview",
    "CutoverChecklistItem",
    "CutoverReadiness",
    "PreviewHint",
    "SettingsPreview",
    "SettingsStatusItem",
    "SETTINGS_ACTION_CHECK_UPDATE",
    "SETTINGS_ACTION_EDIT",
    "SETTINGS_ACTION_TEST_CONNECTION",
    "STATUS_ERROR",
    "STATUS_INFO",
    "STATUS_OK",
    "STATUS_WARNING",
    "StatusCardPreview",
    "UPDATE_CHANNEL_MANUAL",
    "UPDATE_CHANNEL_PREVIEW",
    "UPDATE_CHANNEL_STABLE",
    "converter_status",
    "build_cutover_readiness",
    "build_dashboard_preview",
    "credential_status",
    "example_dashboard_preview",
    "example_settings_preview",
    "make_preview_hints",
    "make_cutover_hints",
    "output_folder_status",
    "settings_status_action_id",
    "status_level_label",
    "update_channel_status",
]
