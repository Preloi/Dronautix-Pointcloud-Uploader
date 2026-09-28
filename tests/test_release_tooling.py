import hashlib
import io
import json
from pathlib import Path
import urllib.error

import pytest

import build_exe
from tools import ci_build_check
from tools.verify_release_manifest import main as verify_main, verify_release_manifest

INSTALLER_BYTES = b"installer bytes"


def _manifest(version="2.2.0", sha=None, name=None):
    name = name or f"Dronautix_Pointcloud_Uploader_Setup_{version}.exe"
    return {
        "version": version,
        "installer_name": name,
        "repo_owner": "Preloi",
        "repo_name": "Dronautix-Pointcloud-Uploader",
        "manifest_branch": "master",
        "release_tag": f"v{version}",
        "installer_url": f"https://github.com/Preloi/Dronautix-Pointcloud-Uploader/releases/download/v{version}/{name}",
        "installer_sha256": sha or hashlib.sha256(INSTALLER_BYTES).hexdigest(),
    }


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _opener(payload=INSTALLER_BYTES):
    return lambda _request, timeout: _Response(payload)


def test_manifest_matching_uploaded_asset_may_be_published():
    assert verify_release_manifest(_manifest(), expected_version="2.2.0", opener=_opener()) == []


def test_manifest_with_wrong_sha_is_rejected():
    problems = verify_release_manifest(_manifest(sha="a" * 64), opener=_opener())

    assert problems and "SHA-256" in problems[0]


def test_manifest_pointing_to_missing_asset_is_rejected():
    def missing(_request, timeout):
        raise urllib.error.HTTPError("https://github.com/x", 404, "Not Found", {}, None)

    problems = verify_release_manifest(_manifest(), opener=missing)

    assert problems and "nicht abrufbar" in problems[0]


def test_manifest_version_must_match_app_version_and_release_path():
    assert "passt nicht zu app_version.py" in verify_release_manifest(
        _manifest(version="2.2.0"), expected_version="2.2.1", opener=_opener()
    )[0]
    renamed = _manifest()
    renamed["installer_url"] = renamed["installer_url"].replace(".exe", "_korrigiert.exe")
    assert verify_release_manifest(renamed, opener=_opener())[0].startswith("Manifest ungültig")


def test_verify_cli_returns_non_zero_for_invalid_manifest(tmp_path, capsys):
    path = tmp_path / "latest-release.json"
    path.write_text(json.dumps(_manifest(sha="zz")), encoding="utf-8")

    assert verify_main(["--manifest", str(path)]) == 1
    assert "darf so nicht veröffentlicht werden" in capsys.readouterr().out


def test_ci_build_uses_the_release_pyinstaller_command_plus_isolated_output(tmp_path, monkeypatch):
    calls = []
    verified = []
    monkeypatch.setattr(ci_build_check.subprocess, "run", lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(ci_build_check, "verify_frozen_startup", lambda exe: verified.append(Path(exe)) or {"ok": True})

    ci_build_check.build_and_verify(tmp_path)

    release_command = build_exe.pyinstaller_command()
    ci_command = calls[0]
    root = str(ci_build_check.REPO_ROOT)
    # Same flags as the release build; only paths are absolute and output is isolated.
    normalized = [arg.replace(root + "\\", "").replace(root + "/", "") for arg in ci_command]
    assert [arg for arg in normalized if not arg.startswith(("--distpath", "--workpath", "--specpath", "--noconfirm"))] == release_command
    assert f"--distpath={tmp_path / 'dist'}" in ci_command and f"--specpath={tmp_path}" in ci_command
    assert verified == [tmp_path / "dist" / "Dronautix_Pointcloud_Uploader.exe"]


def test_ci_build_fails_when_startup_self_test_fails(monkeypatch):
    monkeypatch.setattr(build_exe, "check_build_prerequisites", lambda: True)
    monkeypatch.setattr(ci_build_check.subprocess, "run", lambda *args, **kwargs: None)

    def broken_startup(_exe):
        raise RuntimeError("EXE startup self-test failed: QtCore DLL import failed")

    monkeypatch.setattr(ci_build_check, "verify_frozen_startup", broken_startup)

    assert ci_build_check.main([]) == 1


def test_ci_build_fails_on_stale_version_files(monkeypatch):
    class Result:
        returncode = 1
        stdout = "-  filevers=(2, 1, 8, 0)"

    monkeypatch.setattr(build_exe, "sync_version_files", lambda: None)
    monkeypatch.setattr(ci_build_check.subprocess, "run", lambda *args, **kwargs: Result())

    assert ci_build_check.main(["--check-version-files"]) == 1


def test_importing_build_exe_has_no_side_effects():
    # CI imports the release script; importing must not build or clean anything.
    assert callable(build_exe.main)
    assert build_exe.pyinstaller_command()[:3] == [build_exe.sys.executable, "-m", "PyInstaller"]
