import hashlib
from pathlib import Path

import pytest

from dronautix_uploader.core.update_service import (
    calculate_url_sha256,
    download_and_verify_installer,
    download_update_installer,
    get_update_installer_url,
    is_remote_version_newer,
    load_update_manifest,
    validate_installer_sha256,
    validate_update_download_info,
    verify_installer_hash,
)


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def read(self, size=-1):
        if size is None or size < 0:
            chunk = self.payload[self.offset :]
            self.offset = len(self.payload)
            return chunk
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def _manifest(version="1.7.13", installer_sha256=None, **overrides):
    installer_name = f"Dronautix_Pointcloud_Uploader_Setup_{version}.exe"
    manifest = {
        "version": version,
        "installer_name": installer_name,
        "repo_owner": "Preloi",
        "repo_name": "Dronautix-Pointcloud-Uploader",
        "release_tag": f"v{version}",
        "installer_sha256": installer_sha256 or hashlib.sha256(b"installer bytes").hexdigest(),
    }
    manifest.update(overrides)
    return manifest


def test_version_comparison_is_numeric():
    assert is_remote_version_newer("2.1.6", "2.1.5")
    assert is_remote_version_newer("1.7.12", "1.7.9")
    assert not is_remote_version_newer("1.7.9", "1.7.12")
    assert not is_remote_version_newer("1.7.12", "1.7.12")


def test_load_update_manifest_parses_json_object():
    manifest = load_update_manifest(opener=lambda _request, timeout: FakeResponse(b'{"version":"1.7.13"}'))

    assert manifest == {"version": "1.7.13"}


def test_load_update_manifest_rejects_non_object_json():
    with pytest.raises(ValueError, match="JSON-Objekt"):
        load_update_manifest(opener=lambda _request, timeout: FakeResponse(b'["not", "object"]'))


def test_update_download_info_accepts_expected_github_release_path():
    manifest = {
        "version": "1.7.13",
        "installer_name": "Dronautix_Pointcloud_Uploader_Setup_1.7.13.exe",
        "repo_owner": "Preloi",
        "repo_name": "Dronautix-Pointcloud-Uploader",
        "release_tag": "v1.7.13",
    }
    installer_url = get_update_installer_url(manifest)

    assert validate_update_download_info(
        manifest,
        installer_url,
        "Dronautix_Pointcloud_Uploader_Setup_1.7.13.exe",
    ) == (True, "OK")


def test_update_download_info_rejects_wrong_host():
    manifest = {
        "version": "1.7.13",
        "installer_name": "Dronautix_Pointcloud_Uploader_Setup_1.7.13.exe",
        "repo_owner": "Preloi",
        "repo_name": "Dronautix-Pointcloud-Uploader",
        "release_tag": "v1.7.13",
    }

    ok, message = validate_update_download_info(
        manifest,
        "https://example.com/Dronautix_Pointcloud_Uploader_Setup_1.7.13.exe",
        "Dronautix_Pointcloud_Uploader_Setup_1.7.13.exe",
    )

    assert not ok
    assert "github.com" in message


def test_verify_installer_hash_detects_sha_mismatch(tmp_path):
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"real installer bytes")
    wrong_hash = hashlib.sha256(b"different bytes").hexdigest()

    ok, message = verify_installer_hash(str(installer), wrong_hash)

    assert not ok
    assert "Hash" in message


def test_validate_installer_sha256_rejects_missing_or_malformed_hash():
    assert validate_installer_sha256("")[0] is False
    assert validate_installer_sha256("not-a-sha")[0] is False


def test_verify_installer_hash_accepts_matching_sha(tmp_path):
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"real installer bytes")
    matching_hash = hashlib.sha256(b"real installer bytes").hexdigest()

    assert verify_installer_hash(str(installer), matching_hash) == (True, "OK")


def test_calculate_url_sha256_streams_response_bytes():
    payload = b"installer bytes"

    sha256, byte_count = calculate_url_sha256(
        "https://github.com/Preloi/Dronautix-Pointcloud-Uploader/releases/download/v1.7.13/setup.exe",
        opener=lambda _request, timeout: FakeResponse(payload),
    )

    assert sha256 == hashlib.sha256(payload).hexdigest()
    assert byte_count == len(payload)


def test_download_update_installer_writes_temp_then_final(tmp_path):
    calls = []

    def opener(request, timeout):
        calls.append((request.full_url, timeout))
        return FakeResponse(b"installer bytes")

    installer_path = download_update_installer(
        "https://github.com/Preloi/Dronautix-Pointcloud-Uploader/releases/download/v1.7.13/setup.exe",
        "setup.exe",
        tmp_path,
        opener=opener,
        timeout_seconds=7,
    )

    assert calls
    assert (tmp_path / "setup.exe").read_bytes() == b"installer bytes"
    assert installer_path == str(tmp_path / "setup.exe")
    assert not (tmp_path / "setup.exe.download").exists()


def test_download_and_verify_installer_accepts_matching_sha(tmp_path):
    manifest = _manifest("1.7.13", installer_sha256=hashlib.sha256(b"installer bytes").hexdigest())

    result = download_and_verify_installer(
        manifest,
        tmp_path,
        opener=lambda _request, timeout: FakeResponse(b"installer bytes"),
    )

    assert result.ok
    assert result.message == "OK"
    installer = Path(result.installer_path)
    assert installer.name == manifest["installer_name"] and installer.exists()
    assert installer.parent.parent == tmp_path and installer.parent.name.startswith("update-")


def test_download_and_verify_installer_deletes_bad_hash(tmp_path):
    manifest = _manifest("1.7.13", installer_sha256=hashlib.sha256(b"expected bytes").hexdigest())

    result = download_and_verify_installer(
        manifest,
        tmp_path,
        opener=lambda _request, timeout: FakeResponse(b"actual bytes"),
    )

    assert not result.ok
    assert "Hash" in result.message
    assert not (tmp_path / manifest["installer_name"]).exists()
    assert not (tmp_path / f"{manifest['installer_name']}.download").exists()


def test_download_and_verify_installer_removes_previous_downloads(tmp_path):
    manifest = _manifest("1.7.13", installer_sha256=hashlib.sha256(b"installer bytes").hexdigest())
    old_dir = tmp_path / "update-old"
    old_dir.mkdir()
    (old_dir / "Dronautix_Pointcloud_Uploader_Setup_1.7.12.exe").write_bytes(b"old")
    (tmp_path / "Dronautix_Pointcloud_Uploader_Setup_1.7.11.exe").write_bytes(b"older")

    result = download_and_verify_installer(
        manifest,
        tmp_path,
        opener=lambda _request, timeout: FakeResponse(b"installer bytes"),
    )

    assert result.ok
    assert [path.name for path in tmp_path.iterdir()] == [Path(result.installer_path).parent.name]


def test_prerelease_versions_are_never_offered_as_stable_update():
    from dronautix_uploader.core.update_service import is_remote_version_newer

    assert not is_remote_version_newer("2.2.0-rc1", "2.1.8")
    assert not is_remote_version_newer("2.2.0b1", "2.1.8")
    assert is_remote_version_newer("2.2.0", "2.1.8")
    assert is_remote_version_newer("v2.1.10", "2.1.9")
    assert not is_remote_version_newer("2.1.8", "2.1.8")


def test_download_reports_progress_and_can_be_cancelled_without_leaving_files(tmp_path):
    payload = b"x" * (3 * 1024 * 1024)
    manifest = _manifest("1.7.13", installer_sha256=hashlib.sha256(payload).hexdigest())

    class SizedResponse(FakeResponse):
        headers = {"Content-Length": str(len(payload))}

    progress = []
    result = download_and_verify_installer(
        manifest,
        tmp_path,
        opener=lambda _request, timeout: SizedResponse(payload),
        on_progress=lambda done, total: progress.append((done, total)),
    )
    assert result.ok
    assert progress[-1] == (len(payload), len(payload)) and len(progress) == 3

    cancelled = download_and_verify_installer(
        manifest,
        tmp_path / "second",
        opener=lambda _request, timeout: SizedResponse(payload),
        cancel_requested=lambda: True,
    )
    assert cancelled.cancelled and not cancelled.ok
    assert not list((tmp_path / "second").rglob("*.exe")) and not list((tmp_path / "second").rglob("*.download"))
