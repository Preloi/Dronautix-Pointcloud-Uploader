import hashlib
import importlib
import sys

from dronautix_uploader.core.update_service import UpdateDownloadResult
from dronautix_uploader.qt_app.dashboard_settings_model import (
    UPDATE_CHANNEL_MANUAL,
    UPDATE_CHANNEL_STABLE,
)
from dronautix_uploader.qt_app.project_management_actions import FAILED_STATUS, SUCCESS_STATUS
from dronautix_uploader.qt_app.settings_controller import SettingsFormState
from dronautix_uploader.qt_app.update_controller import UpdateController, evaluate_update_manifest


def _manifest(version="1.7.13", installer_sha256=None, **overrides):
    installer_name = f"Dronautix_Pointcloud_Uploader_Setup_{version}.exe"
    manifest = {
        "version": version,
        "installer_name": installer_name,
        "repo_owner": "Preloi",
        "repo_name": "Dronautix-Pointcloud-Uploader",
        "release_tag": f"v{version}",
        "installer_sha256": installer_sha256 or hashlib.sha256(b"installer").hexdigest(),
    }
    manifest.update(overrides)
    return manifest


class FakeSettingsController:
    def __init__(self, channel=UPDATE_CHANNEL_STABLE):
        self.channel = channel

    def load_state(self):
        return SettingsFormState(update_channel=self.channel)


def test_update_controller_imports_without_qt():
    _assert_import_does_not_load_modules(
        ("dronautix_uploader.qt_app.update_controller",),
        forbidden_prefixes=("PySide6",),
    )


def _assert_import_does_not_load_modules(module_names, *, forbidden_prefixes):
    before = _loaded_modules(forbidden_prefixes)
    for module_name in module_names:
        sys.modules.pop(module_name, None)
    for module_name in module_names:
        importlib.import_module(module_name)
    assert _loaded_modules(forbidden_prefixes) == before


def _loaded_modules(prefixes):
    return {name for name in sys.modules if any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes)}


def test_update_controller_reports_available_update_with_manifest_payload():
    calls = []
    controller = UpdateController(
        settings_controller=FakeSettingsController(),
        current_version="1.7.12",
        manifest_loader=lambda url: calls.append(url) or _manifest("1.7.13"),
    )

    result = controller.check_for_updates()

    assert result.status == SUCCESS_STATUS
    assert result.update_available
    assert result.remote_version == "1.7.13"
    assert result.installer_name.endswith(".exe")
    assert result.installer_url.startswith("https://github.com/")
    assert result.manifest["version"] == "1.7.13"
    assert calls


def test_update_controller_reports_current_version():
    controller = UpdateController(
        settings_controller=FakeSettingsController(),
        current_version="1.7.13",
        manifest_loader=lambda _url: _manifest("1.7.13"),
    )

    result = controller.check_for_updates()

    assert result.status == SUCCESS_STATUS
    assert not result.update_available
    assert "Keine neue Version" in result.message


def test_update_controller_startup_check_follows_channel_setting():
    stable = UpdateController(settings_controller=FakeSettingsController(UPDATE_CHANNEL_STABLE))
    manual = UpdateController(settings_controller=FakeSettingsController(UPDATE_CHANNEL_MANUAL))
    legacy_preview_value = UpdateController(settings_controller=FakeSettingsController("Preview"))

    assert stable.checks_on_startup
    assert not manual.checks_on_startup
    # Alte Configs mit dem frueheren Preview-Kanal verhalten sich wie Stable.
    assert legacy_preview_value.checks_on_startup


def test_update_controller_manual_channel_still_allows_explicit_check():
    controller = UpdateController(
        settings_controller=FakeSettingsController(UPDATE_CHANNEL_MANUAL),
        current_version="1.7.12",
        manifest_loader=lambda _url: _manifest("1.7.13"),
    )

    result = controller.check_for_updates()

    assert result.update_available


def test_update_controller_rejects_invalid_manifest_url():
    result = evaluate_update_manifest(
        _manifest("1.7.13", installer_url="https://example.com/setup.exe"),
        current_version="1.7.12",
    )

    assert result.status == FAILED_STATUS
    assert "github.com" in result.message


def test_update_controller_rejects_missing_installer_sha():
    manifest = _manifest("1.7.13")
    manifest.pop("installer_sha256")

    result = evaluate_update_manifest(manifest, current_version="1.7.12")

    assert result.status == FAILED_STATUS
    assert "SHA-256" in result.message


def test_update_controller_wraps_manifest_loader_errors():
    controller = UpdateController(
        settings_controller=FakeSettingsController(),
        manifest_loader=lambda _url: (_ for _ in ()).throw(RuntimeError("network down")),
    )

    result = controller.check_for_updates()

    assert result.status == FAILED_STATUS
    assert "network down" in result.message


def test_update_controller_downloads_verifies_and_launches_installer(tmp_path):
    manifest = _manifest("1.7.13")
    downloads = []
    launched = []

    def fake_downloader(selected_manifest, download_dir, **_kwargs):
        downloads.append((selected_manifest, download_dir))
        return UpdateDownloadResult(True, "OK", installer_path=str(tmp_path / "setup.exe"))

    controller = UpdateController(
        settings_controller=FakeSettingsController(),
        installer_downloader=fake_downloader,
        installer_launcher=launched.append,
        download_dir=tmp_path,
    )

    summary = controller.download_and_install(manifest)

    assert summary.status == SUCCESS_STATUS
    assert downloads == [(manifest, tmp_path)]
    assert launched == [str(tmp_path / "setup.exe")]


def test_update_controller_does_not_launch_installer_on_failed_download(tmp_path):
    launched = []
    controller = UpdateController(
        settings_controller=FakeSettingsController(),
        installer_downloader=lambda _manifest, _dir, **_kwargs: UpdateDownloadResult(
            False, "Installer-Hash stimmt nicht mit dem Release-Manifest ueberein"
        ),
        installer_launcher=launched.append,
        download_dir=tmp_path,
    )

    summary = controller.download_and_install(_manifest("1.7.13"))

    assert summary.status == FAILED_STATUS
    assert "Installer-Hash" in summary.message
    assert launched == []


def test_update_controller_reports_launcher_errors(tmp_path):
    def failing_launcher(_path):
        raise OSError("blocked by policy")

    controller = UpdateController(
        settings_controller=FakeSettingsController(),
        installer_downloader=lambda _manifest, _dir, **_kwargs: UpdateDownloadResult(
            True, "OK", installer_path=str(tmp_path / "setup.exe")
        ),
        installer_launcher=failing_launcher,
        download_dir=tmp_path,
    )

    summary = controller.download_and_install(_manifest("1.7.13"))

    assert summary.status == FAILED_STATUS
    assert "blocked by policy" in summary.message
