"""UI-free update check controller for the Qt preview."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app_version import APP_VERSION
from dronautix_uploader.core.constants import UPDATE_MANIFEST_URL
from dronautix_uploader.core.update_service import (
    get_update_installer_url,
    is_remote_version_newer,
    load_update_manifest,
    validate_installer_sha256,
    validate_update_download_info,
)

from .dashboard_settings_model import (
    UPDATE_CHANNEL_MANUAL,
    UPDATE_CHANNEL_PREVIEW,
    UPDATE_CHANNEL_STABLE,
)
from .project_management_actions import FAILED_STATUS, ProjectOperationSummary, SUCCESS_STATUS


ManifestLoader = Callable[[str], dict[str, object]]


class UpdateController:
    """Check update manifests without downloading or launching installers."""

    def __init__(
        self,
        *,
        settings_controller: Any,
        current_version: str = APP_VERSION,
        manifest_url: str = UPDATE_MANIFEST_URL,
        manifest_loader: ManifestLoader = load_update_manifest,
    ) -> None:
        self.settings_controller = settings_controller
        self.current_version = current_version
        self.manifest_url = manifest_url
        self.manifest_loader = manifest_loader

    def check_for_updates(self) -> ProjectOperationSummary:
        channel = self._selected_channel()
        if channel == UPDATE_CHANNEL_MANUAL:
            return ProjectOperationSummary(
                status=SUCCESS_STATUS,
                message="Update-Prüfung übersprungen: manueller Kanal ist aktiv.",
            )
        if channel == UPDATE_CHANNEL_PREVIEW:
            return ProjectOperationSummary(
                status=SUCCESS_STATUS,
                message="Preview-Kanal ist getrennt; kein produktiver Update-Check gestartet.",
            )

        try:
            manifest = self.manifest_loader(self.manifest_url)
        except Exception as exc:
            return ProjectOperationSummary(
                status=FAILED_STATUS,
                message=f"Update-Manifest konnte nicht geladen werden: {exc}",
            )

        return summarize_update_manifest(
            manifest,
            current_version=self.current_version,
            channel=channel,
        )

    def _selected_channel(self) -> str:
        state = self.settings_controller.load_state()
        channel = str(getattr(state, "update_channel", "") or "").strip()
        return channel or UPDATE_CHANNEL_STABLE


def summarize_update_manifest(
    manifest: dict[str, object],
    *,
    current_version: str = APP_VERSION,
    channel: str = UPDATE_CHANNEL_STABLE,
) -> ProjectOperationSummary:
    remote_version = str(manifest.get("version", "") or "").strip()
    installer_name = str(manifest.get("installer_name", "") or "").strip()
    installer_url = get_update_installer_url(manifest)

    valid_download, download_message = validate_update_download_info(manifest, installer_url, installer_name)
    if not valid_download:
        return ProjectOperationSummary(status=FAILED_STATUS, message=f"Update-Manifest ungültig: {download_message}")

    valid_sha, sha_message = validate_installer_sha256(str(manifest.get("installer_sha256", "") or ""))
    if not valid_sha:
        return ProjectOperationSummary(status=FAILED_STATUS, message=sha_message)

    if not is_remote_version_newer(remote_version, current_version):
        return ProjectOperationSummary(
            status=SUCCESS_STATUS,
            message=f"Keine neue Version verfügbar. Installiert: {current_version}, Manifest: {remote_version}.",
        )

    return ProjectOperationSummary(
        status=SUCCESS_STATUS,
        message=f"Update verfügbar: {remote_version} ({channel}). Installer wurde nicht gestartet.",
        warnings=(
            "V2-Preview bleibt ausserhalb des bestehenden Update-Kanals; Download und Start erfolgen erst im Cutover.",
            f"Installer: {installer_name}",
            f"URL: {installer_url}",
        ),
    )


__all__ = [
    "ManifestLoader",
    "UpdateController",
    "summarize_update_manifest",
]
