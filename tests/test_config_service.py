from dronautix_uploader.core.config_service import (
    PREVIEW_APPDATA_FOLDER,
    PREVIEW_KEYRING_SERVICE,
    get_config_locations,
    get_credential_keyring_services,
    load_config_file,
    migrate_legacy_config_if_missing,
    save_config_file,
)
from dronautix_uploader.core.constants import APPDATA_FOLDER, KEYRING_SERVICE


def test_config_locations_keep_legacy_cutover_path(tmp_path):
    locations = get_config_locations(preview=True, environ={"APPDATA": str(tmp_path)})

    assert locations.current_dir == tmp_path / PREVIEW_APPDATA_FOLDER
    assert locations.current_config == tmp_path / PREVIEW_APPDATA_FOLDER / "config.json"
    assert locations.legacy_dir == tmp_path / APPDATA_FOLDER
    assert locations.legacy_config == tmp_path / APPDATA_FOLDER / "config.json"
    assert locations.keyring_service == PREVIEW_KEYRING_SERVICE
    assert locations.legacy_keyring_service == KEYRING_SERVICE
    assert get_credential_keyring_services(preview=True) == (PREVIEW_KEYRING_SERVICE, KEYRING_SERVICE)
    assert get_credential_keyring_services(preview=False) == (KEYRING_SERVICE,)


def test_load_and_save_config_file_preserves_unicode(tmp_path):
    config_path = tmp_path / "config.json"

    save_config_file(config_path, {"output_base_dir": "C:/München/Potree"})

    assert load_config_file(config_path) == {"output_base_dir": "C:/München/Potree"}


def test_migrate_legacy_config_if_missing_copies_once(tmp_path):
    locations = get_config_locations(preview=True, environ={"APPDATA": str(tmp_path)})
    locations.legacy_dir.mkdir(parents=True)
    save_config_file(locations.legacy_config, {"converter_path": "legacy.exe"})

    assert migrate_legacy_config_if_missing(locations)
    assert load_config_file(locations.current_config) == {"converter_path": "legacy.exe"}

    save_config_file(locations.current_config, {"converter_path": "preview.exe"})
    assert not migrate_legacy_config_if_missing(locations)
    assert load_config_file(locations.current_config) == {"converter_path": "preview.exe"}
