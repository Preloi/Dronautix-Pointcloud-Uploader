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


def test_save_config_file_is_atomic_and_leaves_no_temp_files(tmp_path):
    from dronautix_uploader.core.config_service import load_config_file, save_config_file

    path = tmp_path / "config.json"
    save_config_file(path, {"a": 1})
    save_config_file(path, {"a": 2, "b": "ä"})

    assert load_config_file(path) == {"a": 2, "b": "ä"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json"]


def test_unreadable_config_is_backed_up_once_before_it_can_be_overwritten(tmp_path):
    from dronautix_uploader.core.config_service import load_config_file

    path = tmp_path / "config.json"
    path.write_text('{"output_base_dir": "D:/Out", ', encoding="utf-8")

    assert load_config_file(path) == {}
    assert load_config_file(path) == {}

    backups = list(tmp_path.glob("config.json.defekt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '{"output_base_dir": "D:/Out", '


def test_preview_migration_never_copies_plaintext_secret(tmp_path):
    from dronautix_uploader.core.config_service import (
        get_config_locations,
        load_config_file,
        migrate_legacy_config_if_missing,
        save_config_file,
    )

    locations = get_config_locations(preview=True, environ={"APPDATA": str(tmp_path)})
    save_config_file(locations.legacy_config, {"aws_access": "AKIA", "aws_secret": "plain", "output_base_dir": "X"})

    assert migrate_legacy_config_if_missing(locations)
    assert load_config_file(locations.current_config) == {"aws_access": "AKIA", "output_base_dir": "X"}


def test_plaintext_secret_moves_to_keyring_and_is_removed_from_config(tmp_path):
    from dronautix_uploader.core.config_service import (
        load_config_file,
        migrate_plaintext_secret_to_keyring,
        save_config_file,
    )

    path = tmp_path / "config.json"
    save_config_file(path, {"aws_access": "AKIA", "aws_secret": "plain", "region_name": "eu-central-1"})
    keyring = {}

    assert migrate_plaintext_secret_to_keyring(
        path, "Service", lambda service, user, value: keyring.__setitem__((service, user), value), lambda *_: ""
    )
    assert keyring == {("Service", "aws_access"): "AKIA", ("Service", "aws_secret"): "plain"}
    assert load_config_file(path) == {"aws_access": "AKIA", "region_name": "eu-central-1"}


def test_plaintext_secret_migration_keeps_newer_keyring_pair_and_survives_keyring_failure(tmp_path):
    from dronautix_uploader.core.config_service import (
        load_config_file,
        migrate_plaintext_secret_to_keyring,
        save_config_file,
    )

    path = tmp_path / "config.json"
    save_config_file(path, {"aws_secret": "stale"})
    writes = []
    stored = {"aws_access": "AKIA_NEW", "aws_secret": "new"}

    assert migrate_plaintext_secret_to_keyring(path, "S", lambda *args: writes.append(args), lambda _s, user: stored[user])
    assert writes == [] and load_config_file(path) == {}

    save_config_file(path, {"aws_secret": "plain"})

    def broken_writer(*_args):
        raise RuntimeError("keyring locked")

    assert not migrate_plaintext_secret_to_keyring(path, "S", broken_writer, lambda *_: "")
    assert load_config_file(path) == {"aws_secret": "plain"}
