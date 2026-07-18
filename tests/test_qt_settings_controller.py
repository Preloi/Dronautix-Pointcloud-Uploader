import importlib
import sys

import pytest

from dronautix_uploader.core.config_service import PREVIEW_KEYRING_SERVICE, load_config_file
from dronautix_uploader.core.constants import KEYRING_SERVICE
from dronautix_uploader.qt_app.project_management_actions import FAILED_STATUS, SUCCESS_STATUS
from dronautix_uploader.qt_app.settings_controller import SettingsController, SettingsFormState


def test_settings_controller_imports_without_qt_boto3_or_keyring():
    _assert_import_does_not_load_modules(
        ("dronautix_uploader.qt_app.settings_controller",),
        forbidden_prefixes=("PySide6", "boto3", "keyring"),
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


def test_settings_controller_loads_config_and_keyring_credentials(tmp_path):
    config_path = tmp_path / "config.json"
    writes = {}

    controller = SettingsController(
        config_path=config_path,
        config_loader=lambda _path: {
            "aws_access_key_id": "access-from-config",
            "region_name": "eu-central-1",
            "bucket_name": "bucket",
            "converter_path": "PotreeConverter.exe",
            "output_base_dir": "C:/out",
            "update_channel": "Preview",
        },
        credential_loader=lambda service, user: {
            (KEYRING_SERVICE, "aws_secret"): "secret-from-legacy-keyring",
        }.get((service, user), ""),
        credential_writer=lambda service, user, value: writes.setdefault((service, user), value),
    )

    state = controller.load_state()

    assert state.aws_access_key_id == "access-from-config"
    assert state.aws_secret_access_key == "secret-from-legacy-keyring"
    assert state.region_name == "eu-central-1"
    assert state.bucket_name == "bucket"
    assert state.converter_path.endswith("bundled_tools\\PotreeConverter\\PotreeConverter.exe")
    assert state.output_base_dir == "C:/out"
    assert state.update_channel == "Preview"


def test_settings_controller_uses_bundled_converter_when_no_override_is_configured(tmp_path, monkeypatch):
    import dronautix_uploader.qt_app.settings_controller as settings_controller

    bundled_converter = tmp_path / "bundled_tools" / "PotreeConverter" / "PotreeConverter.exe"
    bundled_converter.parent.mkdir(parents=True)
    bundled_converter.write_bytes(b"converter")

    monkeypatch.setattr(settings_controller, "resolve_converter_path", lambda: str(bundled_converter))
    monkeypatch.setattr(settings_controller, "is_converter_bundle_available", lambda: True)
    monkeypatch.setattr(settings_controller, "get_bundled_converter_path", lambda: bundled_converter)

    controller = settings_controller.SettingsController(
        config_path=tmp_path / "config.json",
        config_loader=lambda _path: {"region_name": "eu-central-1", "bucket_name": "bucket"},
        credential_loader=lambda service, user: "",
    )

    state = controller.load_state()
    preview = controller.preview()

    assert state.converter_path == str(bundled_converter)
    assert preview.converter_bundle == str(bundled_converter)
    assert preview.converter_override == "Nicht unterstützt"
    assert any(item.name == "Converter" and item.status == "Bereit" for item in preview.settings_status)


def test_settings_controller_prefers_complete_preview_keyring_pair(tmp_path):
    controller = SettingsController(
        config_path=tmp_path / "config.json",
        config_loader=lambda _path: {"region_name": "eu-central-1", "bucket_name": "bucket"},
        credential_loader=lambda service, user: {
            (PREVIEW_KEYRING_SERVICE, "aws_access"): "access-preview",
            (PREVIEW_KEYRING_SERVICE, "aws_secret"): "secret-preview",
            (KEYRING_SERVICE, "aws_access"): "access-legacy",
            (KEYRING_SERVICE, "aws_secret"): "secret-legacy",
        }.get((service, user), ""),
    )

    state = controller.load_state()

    assert state.aws_access_key_id == "access-preview"
    assert state.aws_secret_access_key == "secret-preview"


def test_settings_controller_falls_back_to_complete_legacy_pair_instead_of_mixing_keyrings(tmp_path):
    controller = SettingsController(
        config_path=tmp_path / "config.json",
        config_loader=lambda _path: {"region_name": "eu-central-1", "bucket_name": "bucket"},
        credential_loader=lambda service, user: {
            (PREVIEW_KEYRING_SERVICE, "aws_access"): "access-preview-only",
            (KEYRING_SERVICE, "aws_access"): "access-legacy",
            (KEYRING_SERVICE, "aws_secret"): "secret-legacy",
        }.get((service, user), ""),
    )

    state = controller.load_state()

    assert state.aws_access_key_id == "access-legacy"
    assert state.aws_secret_access_key == "secret-legacy"


def test_default_settings_keyring_loader_reads_keyring_module(monkeypatch):
    import dronautix_uploader.qt_app.settings_controller as settings_controller

    class FakeKeyring:
        @staticmethod
        def get_password(service, username):
            return f"{service}:{username}"

    monkeypatch.setitem(sys.modules, "keyring", FakeKeyring())

    assert settings_controller._load_keyring_password("service", "user") == "service:user"


def test_settings_controller_saves_config_and_secrets_to_keyring(tmp_path):
    config_path = tmp_path / "config.json"
    credentials = {}
    controller = SettingsController(
        config_path=config_path,
        credential_loader=lambda service, user: "",
        credential_writer=lambda service, user, value: credentials.setdefault((service, user), value),
    )

    summary = controller.save_state(
        SettingsFormState(
            aws_access_key_id="access",
            aws_secret_access_key="secret",
            region_name="eu-central-1",
            bucket_name="bucket",
            converter_path="converter.exe",
            output_base_dir="C:/out",
            update_channel="Stable",
        )
    )

    saved = load_config_file(config_path)
    assert summary.status == SUCCESS_STATUS
    assert saved["aws_access_key_id"] == "access"
    assert saved["region_name"] == "eu-central-1"
    assert saved["bucket_name"] == "bucket"
    assert "converter_path" not in saved
    assert saved["output_base_dir"] == "C:/out"
    assert "aws_secret_access_key" not in saved
    assert credentials == {
        (PREVIEW_KEYRING_SERVICE, "aws_access"): "access",
        (PREVIEW_KEYRING_SERVICE, "aws_secret"): "secret",
    }


def test_settings_controller_removes_legacy_converter_overrides_on_save(tmp_path, monkeypatch):
    import dronautix_uploader.qt_app.settings_controller as settings_controller

    bundled_converter = tmp_path / "bundle" / "PotreeConverter.exe"
    bundled_converter.parent.mkdir()
    bundled_converter.write_bytes(b"converter")
    monkeypatch.setattr(settings_controller, "get_bundled_converter_path", lambda: bundled_converter)

    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"converter_path": "external.exe", "potree_converter_path": "legacy.exe"}',
        encoding="utf-8",
    )
    controller = settings_controller.SettingsController(
        config_path=config_path,
        credential_loader=lambda service, user: "",
    )

    controller.save_state(
        settings_controller.SettingsFormState(
            aws_access_key_id="access",
            aws_secret_access_key="secret",
            region_name="eu-central-1",
            bucket_name="bucket",
            converter_path=str(bundled_converter),
        )
    )

    saved = load_config_file(config_path)
    assert "converter_path" not in saved
    assert "potree_converter_path" not in saved


def test_settings_controller_rejects_missing_region_or_bucket(tmp_path):
    controller = SettingsController(config_path=tmp_path / "config.json")

    with pytest.raises(ValueError, match="Region"):
        controller.save_state(SettingsFormState(region_name="", bucket_name="bucket"))
    with pytest.raises(ValueError, match="Bucket"):
        controller.save_state(SettingsFormState(region_name="eu-central-1", bucket_name=""))


def test_settings_controller_tests_connection_success_and_failure(tmp_path):
    calls = []
    controller = SettingsController(
        config_path=tmp_path / "config.json",
        credential_loader=lambda service, user: "",
        connection_tester=lambda state: calls.append((state.region_name, state.bucket_name)),
    )
    state = SettingsFormState(
        aws_access_key_id="access",
        aws_secret_access_key="secret",
        region_name="eu-central-1",
        bucket_name="bucket",
    )

    success = controller.test_connection(state)

    assert success.status == SUCCESS_STATUS
    assert calls == [("eu-central-1", "bucket")]

    failing = SettingsController(
        config_path=tmp_path / "config.json",
        credential_loader=lambda service, user: "",
        connection_tester=lambda state: (_ for _ in ()).throw(RuntimeError("denied")),
    )

    failed = failing.test_connection(state)

    assert failed.status == FAILED_STATUS
    assert "denied" in failed.message


def test_settings_controller_connection_requires_credentials(tmp_path):
    controller = SettingsController(config_path=tmp_path / "config.json", credential_loader=lambda service, user: "")

    with pytest.raises(ValueError, match="Access Key und Secret Key"):
        controller.test_connection(SettingsFormState(aws_access_key_id="", aws_secret_access_key=""))


def test_settings_controller_preview_reflects_loaded_state(tmp_path):
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    controller = SettingsController(
        config_path=tmp_path / "config.json",
        config_loader=lambda _path: {
            "aws_access_key_id": "access",
            "region_name": "eu-central-1",
            "bucket_name": "bucket",
            "output_base_dir": str(output_dir),
        },
        credential_loader=lambda service, user: {"aws_secret": "secret"}.get(user, ""),
    )

    preview = controller.preview()

    assert preview.aws_profile == "Direkte Keys"
    assert preview.output_folder == str(output_dir)
    assert any(item.name == "AWS Credentials" and item.status == "Bereit" for item in preview.settings_status)
