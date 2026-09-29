"""UI-freier Update-Controller: Manifest prüfen, Installer laden und starten."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import subprocess
from typing import Any

from app_version import APP_NAME, APP_VERSION
from dronautix_uploader.core.config_service import get_appdata_base
from dronautix_uploader.core.constants import APPDATA_FOLDER, UPDATE_MANIFEST_URL
from dronautix_uploader.core.update_service import (
    download_and_verify_installer,
    get_update_installer_url,
    is_remote_version_newer,
    load_update_manifest,
    validate_installer_sha256,
    validate_update_download_info,
    verify_installer_hash,
)

from .dashboard_settings_model import UPDATE_CHANNEL_MANUAL, UPDATE_CHANNEL_STABLE
from .error_messages import describe_error
from .project_management_actions import FAILED_STATUS, ProjectOperationSummary, SUCCESS_STATUS


ManifestLoader = Callable[[str], dict[str, object]]
InstallerDownloader = Callable[..., Any]
InstallerLauncher = Callable[[str], None]


@dataclass(frozen=True)
class UpdateCheckResult:
    """Ergebnis einer Update-Prüfung ohne UI-Seiteneffekte."""

    status: str
    message: str
    update_available: bool = False
    remote_version: str = ""
    installer_name: str = ""
    installer_url: str = ""
    manifest: dict[str, object] = field(default_factory=dict)

    @property
    def statusbar_text(self) -> str:
        return self.message

    @property
    def activity_lines(self) -> tuple[str, ...]:
        return (self.message,)


def default_update_download_dir() -> Path:
    return get_appdata_base() / APPDATA_FOLDER / "updates"


def _launch_installer(installer_path: str) -> None:
    # /CLOSEAPPLICATIONS entspricht dem Inno-Setup-Verhalten des V1-Updaters.
    subprocess.Popen([installer_path, "/CLOSEAPPLICATIONS"], shell=False)


class UpdateController:
    """Prüft das Release-Manifest und installiert Updates nach Bestaetigung."""

    def __init__(
        self,
        *,
        settings_controller: Any,
        current_version: str = APP_VERSION,
        manifest_url: str = UPDATE_MANIFEST_URL,
        manifest_loader: ManifestLoader = load_update_manifest,
        installer_downloader: InstallerDownloader = download_and_verify_installer,
        installer_launcher: InstallerLauncher = _launch_installer,
        download_dir: str | Path | None = None,
    ) -> None:
        self.settings_controller = settings_controller
        self.current_version = current_version
        self.manifest_url = manifest_url
        self.manifest_loader = manifest_loader
        self.installer_downloader = installer_downloader
        self.installer_launcher = installer_launcher
        self.download_dir = Path(download_dir) if download_dir is not None else default_update_download_dir()

    @property
    def checks_on_startup(self) -> bool:
        """Beim Start wird nur im Stable-Kanal automatisch geprüft."""

        return self._selected_channel() != UPDATE_CHANNEL_MANUAL

    def check_for_updates(self) -> UpdateCheckResult:
        try:
            manifest = self.manifest_loader(self.manifest_url)
        except Exception as exc:
            return UpdateCheckResult(
                status=FAILED_STATUS,
                message=f"Update-Manifest konnte nicht geladen werden: {describe_error(exc)}",
            )
        return evaluate_update_manifest(manifest, current_version=self.current_version)

    def download_and_install(
        self,
        manifest: dict[str, object],
        on_progress=None,
        cancel_requested=None,
    ) -> ProjectOperationSummary:
        """Laedt den Installer, prüft den SHA-256 und startet die Installation."""

        extra = {}
        if on_progress is not None:
            extra["on_progress"] = on_progress
        if cancel_requested is not None:
            extra["cancel_requested"] = cancel_requested
        result = self.installer_downloader(manifest, self.download_dir, **extra)
        if getattr(result, "cancelled", False):
            return ProjectOperationSummary(status="cancelled", message="Update-Download abgebrochen.")
        if not getattr(result, "ok", False):
            return ProjectOperationSummary(
                status=FAILED_STATUS,
                message=f"Update fehlgeschlagen: {getattr(result, 'message', 'Unbekannter Fehler')}",
            )
        installer_path = str(getattr(result, "installer_path", "") or "")
        # Re-check right before starting the (elevated) installer so the file
        # cannot be swapped between the download check and the launch.
        expected_sha256 = str(getattr(result, "installer_sha256", "") or manifest.get("installer_sha256", "") or "")
        try:
            hash_ok, hash_message = verify_installer_hash(installer_path, expected_sha256)
        except OSError as exc:
            hash_ok, hash_message = False, str(exc)
        if not hash_ok:
            return ProjectOperationSummary(
                status=FAILED_STATUS,
                message=f"Update fehlgeschlagen: {hash_message}",
            )
        # Last point of no return: starting the installer closes the app, so a
        # cancel after the final chunk or during hashing must still win.
        if cancel_requested is not None and cancel_requested():
            return ProjectOperationSummary(status="cancelled", message="Update-Download abgebrochen.")
        try:
            self.installer_launcher(installer_path)
        except Exception as exc:
            return ProjectOperationSummary(
                status=FAILED_STATUS,
                message=f"Installer konnte nicht gestartet werden: {exc}",
            )
        return ProjectOperationSummary(
            status=SUCCESS_STATUS,
            message=f"Installer gestartet: {Path(installer_path).name}. {APP_NAME} wird beendet.",
        )

    def _selected_channel(self) -> str:
        state = self.settings_controller.load_state()
        channel = str(getattr(state, "update_channel", "") or "").strip()
        if channel == UPDATE_CHANNEL_MANUAL:
            return UPDATE_CHANNEL_MANUAL
        return UPDATE_CHANNEL_STABLE


def evaluate_update_manifest(
    manifest: dict[str, object],
    *,
    current_version: str = APP_VERSION,
) -> UpdateCheckResult:
    remote_version = str(manifest.get("version", "") or "").strip()
    installer_name = str(manifest.get("installer_name", "") or "").strip()
    installer_url = get_update_installer_url(manifest)

    valid_download, download_message = validate_update_download_info(manifest, installer_url, installer_name)
    if not valid_download:
        return UpdateCheckResult(
            status=FAILED_STATUS,
            message=f"Update-Manifest ungültig: {download_message}",
        )

    valid_sha, sha_message = validate_installer_sha256(str(manifest.get("installer_sha256", "") or ""))
    if not valid_sha:
        return UpdateCheckResult(status=FAILED_STATUS, message=sha_message)

    if not is_remote_version_newer(remote_version, current_version):
        return UpdateCheckResult(
            status=SUCCESS_STATUS,
            message=f"Keine neue Version verfügbar (installiert: {current_version}).",
            remote_version=remote_version,
        )

    return UpdateCheckResult(
        status=SUCCESS_STATUS,
        message=f"Update verfügbar: Version {remote_version}.",
        update_available=True,
        remote_version=remote_version,
        installer_name=installer_name,
        installer_url=installer_url,
        manifest=dict(manifest),
    )


__all__ = [
    "InstallerDownloader",
    "InstallerLauncher",
    "ManifestLoader",
    "UpdateCheckResult",
    "UpdateController",
    "default_update_download_dir",
    "evaluate_update_manifest",
]
