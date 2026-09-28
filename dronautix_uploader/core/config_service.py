"""Configuration path and migration helpers for V2 cutover."""

from __future__ import annotations

from datetime import datetime
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import APPDATA_FOLDER, KEYRING_SERVICE

PREVIEW_APPDATA_FOLDER = "DronautixUploaderV2Preview"
PREVIEW_KEYRING_SERVICE = "DronautixUploaderV2Preview"
CONFIG_FILE_NAME = "config.json"

_AWS_REGION_PATTERN = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-\d{1,2}$")
_S3_BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


def is_valid_aws_region(region_name: str) -> bool:
    """Formal check only; boto3 rejects malformed names with InvalidRegionError."""

    return bool(_AWS_REGION_PATTERN.fullmatch(str(region_name or "").strip()))


def is_valid_s3_bucket_name(bucket_name: str) -> bool:
    name = str(bucket_name or "").strip()
    return bool(_S3_BUCKET_PATTERN.fullmatch(name)) and ".." not in name


@dataclass(frozen=True)
class ConfigLocations:
    current_dir: Path
    current_config: Path
    legacy_dir: Path
    legacy_config: Path
    keyring_service: str = KEYRING_SERVICE
    legacy_keyring_service: str = KEYRING_SERVICE


def get_appdata_base(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env.get("APPDATA") or Path.home())


def get_config_locations(
    preview: bool = False,
    environ: dict[str, str] | None = None,
) -> ConfigLocations:
    base = get_appdata_base(environ)
    current_folder = PREVIEW_APPDATA_FOLDER if preview else APPDATA_FOLDER
    current_dir = base / current_folder
    legacy_dir = base / APPDATA_FOLDER
    current_keyring_service = PREVIEW_KEYRING_SERVICE if preview else KEYRING_SERVICE
    return ConfigLocations(
        current_dir=current_dir,
        current_config=current_dir / CONFIG_FILE_NAME,
        legacy_dir=legacy_dir,
        legacy_config=legacy_dir / CONFIG_FILE_NAME,
        keyring_service=current_keyring_service,
        legacy_keyring_service=KEYRING_SERVICE,
    )


SECRET_CONFIG_KEYS = ("aws_secret_access_key", "aws_secret", "aws_secret_key", "secret_key")
# Set to False by "Zugangsdaten entfernen": only the app's own keyring service
# is read afterwards, never the fallback of the installed app.
KEYRING_FALLBACK_CONFIG_KEY = "keyring_fallback"
ACCESS_CONFIG_KEYS = ("aws_access_key_id", "aws_access", "aws_access_key", "access_key")


def load_config_file(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except OSError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        # The next save would otherwise silently replace every unknown key.
        _backup_unreadable_config(path)
        return {}
    return data if isinstance(data, dict) else {}


def _backup_unreadable_config(path: Path) -> Path | None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.defekt-{stamp}")
    try:
        if not any(existing.read_bytes() == path.read_bytes() for existing in path.parent.glob(f"{path.name}.defekt-*")):
            shutil.copyfile(path, backup)
            print(f"[CONFIG] Unlesbare Konfiguration gesichert: {backup}", file=sys.stderr, flush=True)
            return backup
    except OSError:
        pass
    return None


def save_config_file(config_path: str | Path, config: dict[str, Any]) -> None:
    """Write the config atomically: a crash never leaves a truncated file."""

    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            json.dump(config, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def migrate_legacy_config_if_missing(locations: ConfigLocations) -> bool:
    """Copy legacy config into the current V2 location if no current config exists.

    Secrets are never copied: the legacy app stored the AWS secret in plain
    text when the keyring failed, and the preview keeps its own keyring.
    """

    if locations.current_config.exists() or not locations.legacy_config.is_file():
        return False
    config = load_config_file(locations.legacy_config)
    for key in SECRET_CONFIG_KEYS:
        config.pop(key, None)
    save_config_file(locations.current_config, config)
    return True


def migrate_plaintext_secret_to_keyring(
    config_path: str | Path,
    keyring_service: str,
    credential_writer,
    credential_loader=None,
) -> bool:
    """Move a plain-text AWS secret from config.json into the keyring.

    A complete pair already in the keyring is newer (the app only fell back to
    plain text when the keyring failed) and is kept. The file is only
    rewritten after the keyring accepted the values, so a broken keyring never
    loses the credentials.
    """

    path = Path(config_path)
    config = load_config_file(path)
    secret = next((str(config.get(key) or "").strip() for key in SECRET_CONFIG_KEYS if str(config.get(key) or "").strip()), "")
    if not secret:
        return False
    access = next((str(config.get(key) or "").strip() for key in ACCESS_CONFIG_KEYS if str(config.get(key) or "").strip()), "")
    try:
        stored_pair_complete = bool(
            credential_loader
            and str(credential_loader(keyring_service, "aws_access") or "").strip()
            and str(credential_loader(keyring_service, "aws_secret") or "").strip()
        )
        if not stored_pair_complete:
            if access:
                credential_writer(keyring_service, "aws_access", access)
            credential_writer(keyring_service, "aws_secret", secret)
    except Exception:
        return False
    for key in SECRET_CONFIG_KEYS:
        config.pop(key, None)
    save_config_file(path, config)
    return True


def get_credential_keyring_services(preview: bool = False, config: dict[str, Any] | None = None) -> tuple[str, ...]:
    """Return keyring services V2 should read during cutover, in priority order."""

    services = [PREVIEW_KEYRING_SERVICE if preview else KEYRING_SERVICE, KEYRING_SERVICE]
    if isinstance(config, dict) and config.get(KEYRING_FALLBACK_CONFIG_KEY) is False:
        services = services[:1]
    return tuple(dict.fromkeys(services))


__all__ = [
    "ACCESS_CONFIG_KEYS",
    "KEYRING_FALLBACK_CONFIG_KEY",
    "SECRET_CONFIG_KEYS",
    "CONFIG_FILE_NAME",
    "PREVIEW_APPDATA_FOLDER",
    "PREVIEW_KEYRING_SERVICE",
    "ConfigLocations",
    "get_appdata_base",
    "get_config_locations",
    "get_credential_keyring_services",
    "is_valid_aws_region",
    "is_valid_s3_bucket_name",
    "load_config_file",
    "migrate_legacy_config_if_missing",
    "migrate_plaintext_secret_to_keyring",
    "save_config_file",
]
