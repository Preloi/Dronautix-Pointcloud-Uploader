"""UI-free settings controller for the Qt preview."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Callable

from dronautix_uploader.core.config_service import (
    get_config_locations,
    get_credential_keyring_services,
    load_config_file,
    save_config_file,
)
from dronautix_uploader.core.constants import BUCKET_NAME, REGION_NAME
from dronautix_uploader.core.converter_bundle import (
    get_bundled_converter_path,
    is_converter_bundle_available,
    resolve_converter_path,
)

from .dashboard_settings_model import (
    UPDATE_CHANNEL_STABLE,
    SettingsPreview,
    converter_status,
    credential_status,
    make_preview_hints,
    output_folder_status,
    update_channel_status,
)
from .project_management_actions import FAILED_STATUS, ProjectOperationSummary, SUCCESS_STATUS


ConfigLoader = Callable[[str | Path], dict[str, Any]]
ConfigSaver = Callable[[str | Path, dict[str, Any]], None]
CredentialLoader = Callable[[str, str], str | None]
CredentialWriter = Callable[[str, str, str], None]
ConnectionTester = Callable[["SettingsFormState"], None]


@dataclass(frozen=True)
class SettingsFormState:
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    region_name: str = REGION_NAME
    bucket_name: str = BUCKET_NAME
    converter_path: str = ""
    output_base_dir: str = ""
    update_channel: str = UPDATE_CHANNEL_STABLE


class SettingsController:
    """Load, save and test V2 preview settings without importing Qt."""

    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        preview: bool = True,
        environ: dict[str, str] | None = None,
        config_loader: ConfigLoader = load_config_file,
        config_saver: ConfigSaver = save_config_file,
        credential_loader: CredentialLoader | None = None,
        credential_writer: CredentialWriter | None = None,
        connection_tester: ConnectionTester | None = None,
    ) -> None:
        locations = get_config_locations(
            preview=preview,
            environ=environ,
        )
        self.config_path = Path(config_path) if config_path is not None else locations.current_config
        self.keyring_service = locations.keyring_service
        self.credential_services = get_credential_keyring_services(preview=preview)
        self.config_loader = config_loader
        self.config_saver = config_saver
        self.credential_loader = credential_loader or _load_keyring_password
        self.credential_writer = credential_writer or _write_keyring_password
        self.connection_tester = connection_tester or _test_s3_connection

    def load_state(self) -> SettingsFormState:
        config = self.config_loader(self.config_path)
        if not isinstance(config, dict):
            config = {}
        access_key = _first_value(config, "aws_access_key_id", "aws_access", "aws_access_key", "access_key")
        secret_key = _first_value(config, "aws_secret_access_key", "aws_secret", "aws_secret_key", "secret_key")
        loaded_access, loaded_secret = _load_missing_credentials(
            self.credential_loader,
            self.credential_services,
            need_access=not access_key,
            need_secret=not secret_key,
        )
        access_key = access_key or loaded_access
        secret_key = secret_key or loaded_secret
        configured_converter_path = _first_value(config, "converter_path", "potree_converter_path")
        return SettingsFormState(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=_first_value(config, "region_name", "aws_region", "region") or REGION_NAME,
            bucket_name=_first_value(config, "bucket_name", "s3_bucket", "bucket") or BUCKET_NAME,
            converter_path=resolve_converter_path(configured_converter_path),
            output_base_dir=_first_value(config, "output_base_dir", "output_folder"),
            update_channel=_first_value(config, "update_channel") or UPDATE_CHANNEL_STABLE,
        )

    def save_state(self, state: SettingsFormState) -> ProjectOperationSummary:
        _validate_settings_state(state)
        config = self.config_loader(self.config_path)
        if not isinstance(config, dict):
            config = {}
        config.update(
            {
                "aws_access_key_id": state.aws_access_key_id.strip(),
                "region_name": state.region_name.strip(),
                "bucket_name": state.bucket_name.strip(),
                "converter_path": _configured_converter_path_for_save(state.converter_path),
                "output_base_dir": state.output_base_dir.strip(),
                "update_channel": state.update_channel.strip() or UPDATE_CHANNEL_STABLE,
            }
        )
        config.pop("aws_secret_access_key", None)
        config.pop("aws_secret", None)
        self.config_saver(self.config_path, config)
        if state.aws_access_key_id.strip():
            self.credential_writer(self.keyring_service, "aws_access", state.aws_access_key_id.strip())
        if state.aws_secret_access_key.strip():
            self.credential_writer(self.keyring_service, "aws_secret", state.aws_secret_access_key.strip())
        return ProjectOperationSummary(status=SUCCESS_STATUS, message="Einstellungen gespeichert.")

    def test_connection(self, state: SettingsFormState | None = None) -> ProjectOperationSummary:
        selected = state or self.load_state()
        _validate_connection_state(selected)
        try:
            self.connection_tester(selected)
        except Exception as exc:
            return ProjectOperationSummary(status=FAILED_STATUS, message=f"S3-Verbindung fehlgeschlagen: {exc}")
        return ProjectOperationSummary(status=SUCCESS_STATUS, message="S3-Verbindung erfolgreich getestet.")

    def preview(self) -> SettingsPreview:
        state = self.load_state()
        converter_path = state.converter_path.strip()
        output_dir = state.output_base_dir.strip()
        bundle_available = is_converter_bundle_available()
        bundled_converter_path = str(get_bundled_converter_path()) if bundle_available else "Nicht gefunden"
        config = self.config_loader(self.config_path)
        if not isinstance(config, dict):
            config = {}
        configured_converter = _first_value(config, "converter_path", "potree_converter_path")
        has_override = bool(configured_converter.strip())
        settings_status = (
            credential_status(bool(state.aws_access_key_id), bool(state.aws_secret_access_key), "Direkte Keys"),
            converter_status(bundle_available, configured_converter if has_override else ""),
            output_folder_status(output_dir, bool(output_dir and os.path.isdir(output_dir) and os.access(output_dir, os.W_OK))),
            update_channel_status(state.update_channel),
        )
        return SettingsPreview(
            settings_status=settings_status,
            update_channel=state.update_channel,
            output_folder=output_dir or "Nicht gesetzt",
            converter_bundle=bundled_converter_path,
            converter_override=configured_converter or "Kein Override",
            aws_profile="Direkte Keys" if state.aws_access_key_id else "Nicht gesetzt",
            preview_hints=make_preview_hints(False),
        )


def _validate_settings_state(state: SettingsFormState) -> None:
    if not state.region_name.strip():
        raise ValueError("AWS Region ist erforderlich.")
    if not state.bucket_name.strip():
        raise ValueError("S3 Bucket ist erforderlich.")


def _validate_connection_state(state: SettingsFormState) -> None:
    _validate_settings_state(state)
    if not state.aws_access_key_id.strip() or not state.aws_secret_access_key.strip():
        raise ValueError("AWS Access Key und Secret Key sind für den Verbindungstest erforderlich.")


def _test_s3_connection(state: SettingsFormState) -> None:
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 ist für den Verbindungstest erforderlich.") from exc
    session = boto3.Session(
        aws_access_key_id=state.aws_access_key_id.strip(),
        aws_secret_access_key=state.aws_secret_access_key.strip(),
        region_name=state.region_name.strip(),
    )
    session.client("s3").head_bucket(Bucket=state.bucket_name.strip())


def _load_keyring_password(service_name: str, username: str) -> str:
    try:
        import keyring
    except ImportError:
        return ""
    try:
        return str(keyring.get_password(service_name, username) or "")
    except Exception:
        return ""


def _load_missing_credentials(
    credential_loader: CredentialLoader,
    service_names: tuple[str, ...],
    *,
    need_access: bool,
    need_secret: bool,
) -> tuple[str, str]:
    if need_access and need_secret:
        for service_name in service_names:
            access = str(credential_loader(service_name, "aws_access") or "").strip()
            secret = str(credential_loader(service_name, "aws_secret") or "").strip()
            if access and secret:
                return access, secret
        return "", ""

    access = _load_first_credential(credential_loader, service_names, "aws_access") if need_access else ""
    secret = _load_first_credential(credential_loader, service_names, "aws_secret") if need_secret else ""
    return access, secret


def _load_first_credential(
    credential_loader: CredentialLoader,
    service_names: tuple[str, ...],
    username: str,
) -> str:
    for service_name in service_names:
        value = str(credential_loader(service_name, username) or "").strip()
        if value:
            return value
    return ""


def _write_keyring_password(service_name: str, username: str, password: str) -> None:
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("keyring ist zum Speichern der Credentials erforderlich.") from exc
    keyring.set_password(service_name, username, password)


def _first_value(config: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(config.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _configured_converter_path_for_save(converter_path: str) -> str:
    selected = str(converter_path or "").strip()
    if not selected:
        return ""
    try:
        if Path(selected).resolve() == get_bundled_converter_path().resolve():
            return ""
    except OSError:
        return selected
    return selected


__all__ = [
    "SettingsController",
    "SettingsFormState",
]
