"""Configuration path and migration helpers for V2 cutover."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import APPDATA_FOLDER, KEYRING_SERVICE

PREVIEW_APPDATA_FOLDER = "DronautixUploaderV2Preview"
PREVIEW_KEYRING_SERVICE = "DronautixUploaderV2Preview"
CONFIG_FILE_NAME = "config.json"


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


def load_config_file(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_config_file(config_path: str | Path, config: dict[str, Any]) -> None:
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(config, file, indent=2, ensure_ascii=False)
        file.write("\n")


def migrate_legacy_config_if_missing(locations: ConfigLocations) -> bool:
    """Copy legacy config into the current V2 location if no current config exists."""

    if locations.current_config.exists() or not locations.legacy_config.is_file():
        return False
    locations.current_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(locations.legacy_config, locations.current_config)
    return True


def get_credential_keyring_services(preview: bool = False) -> tuple[str, ...]:
    """Return keyring services V2 should read during cutover, in priority order."""

    services = [PREVIEW_KEYRING_SERVICE if preview else KEYRING_SERVICE, KEYRING_SERVICE]
    return tuple(dict.fromkeys(services))


__all__ = [
    "CONFIG_FILE_NAME",
    "PREVIEW_APPDATA_FOLDER",
    "PREVIEW_KEYRING_SERVICE",
    "ConfigLocations",
    "get_appdata_base",
    "get_config_locations",
    "get_credential_keyring_services",
    "load_config_file",
    "migrate_legacy_config_if_missing",
    "save_config_file",
]
