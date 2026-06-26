import hashlib
import importlib
import sys

from dronautix_uploader.qt_app.dashboard_settings_model import (
    UPDATE_CHANNEL_MANUAL,
    UPDATE_CHANNEL_PREVIEW,
    UPDATE_CHANNEL_STABLE,
)
from dronautix_uploader.qt_app.project_management_actions import FAILED_STATUS, SUCCESS_STATUS
from dronautix_uploader.qt_app.settings_controller import SettingsFormState
from dronautix_uploader.qt_app.update_controller import UpdateController, summarize_update_manifest


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


def test_update_controller_reports_available_update_without_launching_installer():
    calls = []
    controller = UpdateController(
        settings_controller=FakeSettingsController(),
        current_version="1.7.12",
        manifest_loader=lambda url: calls.append(url) or _manifest("1.7.13"),
    )

    summary = controller.check_for_updates()

    assert summary.status == SUCCESS_STATUS
    assert "1.7.13" in summary.message
    assert "nicht gestartet" in summary.message
    assert calls
    assert any("Installer:" in warning for warning in summary.warnings)


def test_update_controller_reports_current_version():
    controller = UpdateController(
        settings_controller=FakeSettingsController(),
        current_version="1.7.13",
        manifest_loader=lambda _url: _manifest("1.7.13"),
    )

    summary = controller.check_for_updates()

    assert summary.status == SUCCESS_STATUS
    assert "Keine neue Version" in summary.message


def test_update_controller_does_not_call_network_for_preview_or_manual_channel():
    def fail_if_called(_url):
        raise AssertionError("manifest loader should not run")

    preview = UpdateController(
        settings_controller=FakeSettingsController(UPDATE_CHANNEL_PREVIEW),
        manifest_loader=fail_if_called,
    ).check_for_updates()
    manual = UpdateController(
        settings_controller=FakeSettingsController(UPDATE_CHANNEL_MANUAL),
        manifest_loader=fail_if_called,
    ).check_for_updates()

    assert preview.status == SUCCESS_STATUS
    assert "Preview-Kanal" in preview.message
    assert manual.status == SUCCESS_STATUS
    assert "manueller Kanal" in manual.message


def test_update_controller_rejects_invalid_manifest_url():
    summary = summarize_update_manifest(
        _manifest("1.7.13", installer_url="https://example.com/setup.exe"),
        current_version="1.7.12",
    )

    assert summary.status == FAILED_STATUS
    assert "github.com" in summary.message


def test_update_controller_rejects_missing_installer_sha():
    manifest = _manifest("1.7.13")
    manifest.pop("installer_sha256")

    summary = summarize_update_manifest(manifest, current_version="1.7.12")

    assert summary.status == FAILED_STATUS
    assert "SHA-256" in summary.message


def test_update_controller_wraps_manifest_loader_errors():
    controller = UpdateController(
        settings_controller=FakeSettingsController(),
        manifest_loader=lambda _url: (_ for _ in ()).throw(RuntimeError("network down")),
    )

    summary = controller.check_for_updates()

    assert summary.status == FAILED_STATUS
    assert "network down" in summary.message
