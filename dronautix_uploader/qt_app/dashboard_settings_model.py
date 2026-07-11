"""UI-freie Statusmodelle für die Einstellungsseite und Release-Gates."""

from __future__ import annotations

from dataclasses import dataclass


STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_ERROR = "error"
STATUS_INFO = "info"

UPDATE_CHANNEL_STABLE = "Stable"
UPDATE_CHANNEL_MANUAL = "Manuell"
UPDATE_CHANNELS = (UPDATE_CHANNEL_STABLE, UPDATE_CHANNEL_MANUAL)

SETTINGS_ACTION_EDIT = "edit"
SETTINGS_ACTION_TEST_CONNECTION = "test_connection"
SETTINGS_ACTION_CHECK_UPDATE = "check_update"


@dataclass(frozen=True)
class SettingsStatusItem:
    name: str
    status: str
    detail: str
    level: str
    action: str


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
class SettingsPreview:
    settings_status: tuple[SettingsStatusItem, ...]
    update_channel: str
    output_folder: str
    converter_bundle: str
    converter_override: str
    aws_profile: str


def credential_status(has_access_key: bool, has_secret_key: bool, profile: str = "") -> SettingsStatusItem:
    """AWS-Zugangsdaten-Status für die Einstellungsseite."""

    if has_access_key and has_secret_key:
        status = "Bereit"
        detail = "AWS-Zugangsdaten sind hinterlegt."
        level = STATUS_OK
        action = "Verbindung testen"
    else:
        status = "Nicht konfiguriert"
        detail = "AWS Access Key und Secret Key eintragen und speichern."
        level = STATUS_ERROR
        action = "Credentials eintragen"
    return SettingsStatusItem("AWS Credentials", status, detail, level, action)


def converter_status(bundle_available: bool, override_path: str = "") -> SettingsStatusItem:
    """Status der PotreeConverter-Quelle."""

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
            "Bereit",
            "Integrierter PotreeConverter wird verwendet.",
            STATUS_OK,
            "Pfad ändern",
        )
    return SettingsStatusItem(
        "Converter",
        "Fehlt",
        "Kein integrierter Converter gefunden und kein Pfad hinterlegt.",
        STATUS_ERROR,
        "Pfad auswählen",
    )


def output_folder_status(path: str, writable: bool) -> SettingsStatusItem:
    """Status des lokalen Ausgabeordners für 'Nur konvertieren'."""

    if not path.strip():
        return SettingsStatusItem(
            "Output-Ordner",
            "Nicht gesetzt",
            "Für 'Nur konvertieren' wird ein lokaler Ausgabeordner benötigt.",
            STATUS_WARNING,
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
        "Nicht schreibbar",
        f"{path} ist hinterlegt, aber nicht beschreibbar.",
        STATUS_WARNING,
        "Ordner ändern",
    )


def update_channel_status(channel: str, manifest_version: str = "") -> SettingsStatusItem:
    """Status der Update-Einstellung."""

    normalized = channel.strip() or UPDATE_CHANNEL_STABLE
    if normalized == UPDATE_CHANNEL_MANUAL:
        level = STATUS_INFO
        detail = "Keine automatische Prüfung beim Start; Updates werden über 'Update prüfen' geholt."
    else:
        normalized = UPDATE_CHANNEL_STABLE
        level = STATUS_OK
        detail = "Beim Start wird automatisch auf neue Versionen geprüft."
    if manifest_version:
        detail = f"{detail} Manifest: {manifest_version}."
    return SettingsStatusItem("Updates", normalized, detail, level, "Update prüfen")


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
    """Explizite V2-Release-Checkliste für die Cutover-Werkzeuge."""

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


def settings_status_action_id(item: SettingsStatusItem) -> str:
    """Settings-Aktions-ID für den Button einer Statuszeile."""

    if item.name == "AWS Credentials" and item.action == "Verbindung testen":
        return SETTINGS_ACTION_TEST_CONNECTION
    if item.name == "Updates" and item.action == "Update prüfen":
        return SETTINGS_ACTION_CHECK_UPDATE
    return SETTINGS_ACTION_EDIT


def example_settings_preview() -> SettingsPreview:
    """Repräsentative Einstellungsdaten für Tests ohne Service-Anbindung."""

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
    )


def status_level_label(level: str) -> str:
    """Kompaktes deutsches Label für ein Statuslevel."""

    return {
        STATUS_OK: "OK",
        STATUS_WARNING: "Warnung",
        STATUS_ERROR: "Fehler",
        STATUS_INFO: "Info",
    }.get(level, "Info")


__all__ = [
    "CutoverChecklistItem",
    "CutoverReadiness",
    "SettingsPreview",
    "SettingsStatusItem",
    "SETTINGS_ACTION_CHECK_UPDATE",
    "SETTINGS_ACTION_EDIT",
    "SETTINGS_ACTION_TEST_CONNECTION",
    "STATUS_ERROR",
    "STATUS_INFO",
    "STATUS_OK",
    "STATUS_WARNING",
    "UPDATE_CHANNELS",
    "UPDATE_CHANNEL_MANUAL",
    "UPDATE_CHANNEL_STABLE",
    "build_cutover_readiness",
    "converter_status",
    "credential_status",
    "example_settings_preview",
    "output_folder_status",
    "settings_status_action_id",
    "status_level_label",
    "update_channel_status",
]
