import hashlib
import importlib
import json
import runpy
import sys
from types import ModuleType

import pytest

from app_version import APP_EXE_NAME, APP_ID, APP_NAME, APP_VERSION
from dronautix_uploader.core.constants import UPDATE_REPO_NAME, UPDATE_REPO_OWNER
from tools.check_v2_final_packaging_contract import (
    PENDING_SHA256,
    V2_ENTRYPOINT,
    build_final_packaging_contract,
    load_candidate_manifest,
    main as check_final_contract,
    validate_candidate_manifest_file,
    validate_final_packaging_contract,
    write_candidate_manifest,
)


def test_final_packaging_contract_tool_is_import_safe():
    module = importlib.import_module("tools.check_v2_final_packaging_contract")

    assert hasattr(module, "build_final_packaging_contract")


def test_final_packaging_contract_uses_v2_entrypoint_and_production_identity():
    installer_sha = hashlib.sha256(b"candidate installer").hexdigest()
    contract = build_final_packaging_contract(installer_sha)

    assert contract["candidate_only"] is True
    assert contract["entrypoint"] == V2_ENTRYPOINT
    assert contract["pyinstaller_name"] == "Dronautix_Pointcloud_Uploader"
    assert contract["runtime_mode"] == {
        "mode": "final",
        "detection": "explicit_final_entrypoint",
        "preview_entrypoint": "Dronautix_Pointcloud_Uploader_v2.py",
        "preview_entrypoint_default": "preview",
    }
    assert contract["app_identity"] == {
        "app_name": APP_NAME,
        "app_publisher": "Dronautix",
        "app_exe_name": APP_EXE_NAME,
        "app_id": APP_ID,
        "version": APP_VERSION,
    }
    assert validate_final_packaging_contract(contract, require_valid_sha=True) == ()


def test_final_packaging_contract_preserves_release_url_shape_and_sha_field():
    contract = build_final_packaging_contract()
    release_manifest = contract["release_manifest_candidate"]

    assert release_manifest["installer_sha256"] == PENDING_SHA256
    assert release_manifest["repo_owner"] == UPDATE_REPO_OWNER
    assert release_manifest["repo_name"] == UPDATE_REPO_NAME
    assert release_manifest["release_tag"] == f"v{APP_VERSION}"
    assert release_manifest["installer_url"] == (
        f"https://github.com/{UPDATE_REPO_OWNER}/{UPDATE_REPO_NAME}/"
        f"releases/download/v{APP_VERSION}/Dronautix_Pointcloud_Uploader_Setup_{APP_VERSION}.exe"
    )
    assert validate_final_packaging_contract(contract) == ()
    assert validate_final_packaging_contract(contract, require_valid_sha=True)


def test_final_v2_entrypoint_invokes_qt_app_in_final_mode(monkeypatch):
    calls = []
    fake_app = ModuleType("dronautix_uploader.qt_app.app")
    fake_app.run = lambda **kwargs: calls.append(kwargs) or 0
    monkeypatch.setitem(sys.modules, "dronautix_uploader.qt_app.app", fake_app)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(V2_ENTRYPOINT, run_name="__main__")

    assert exc_info.value.code == 0
    assert calls == [{"mode": "final"}]


def test_final_packaging_contract_uses_isolated_outputs_only():
    contract = build_final_packaging_contract()
    paths = contract["isolated_paths"]

    assert paths["dist_dir"] == "dist_v2_final_candidate"
    assert paths["output_dir"] == "Output_v2_final_candidate"
    assert paths["manifest_path"] == "artifacts/v2-final-candidate-release.json"
    assert paths["manifest_path"] != "latest-release.json"
    assert paths["output_dir"] != "Output"


def test_final_packaging_contract_rejects_legacy_entrypoint_or_real_release_output():
    contract = build_final_packaging_contract("a" * 64)
    contract["entrypoint"] = "Dronautix_Pointcloud_Uploader.py"
    contract["isolated_paths"]["manifest_path"] = "latest-release.json"
    contract["runtime_mode"]["mode"] = "preview"

    issues = validate_final_packaging_contract(contract, require_valid_sha=True)

    assert any("V2-Entrypoint" in issue for issue in issues)
    assert any("latest-release.json" in issue for issue in issues)
    assert any("runtime_mode.mode=final" in issue for issue in issues)


def test_final_packaging_contract_can_write_candidate_manifest_to_requested_path(tmp_path):
    contract = build_final_packaging_contract("a" * 64)
    output_path = tmp_path / "candidate.json"

    written = write_candidate_manifest(contract, output_path)

    assert written == output_path
    assert json.loads(output_path.read_text(encoding="utf-8"))["entrypoint"] == V2_ENTRYPOINT
    assert load_candidate_manifest(output_path)["entrypoint"] == V2_ENTRYPOINT


def test_validate_candidate_manifest_file_blocks_missing_or_malformed_file(tmp_path):
    contract = build_final_packaging_contract("a" * 64)

    missing_issues = validate_candidate_manifest_file(tmp_path / "missing.json", contract)

    assert any("Candidate-Manifest fehlt" in issue for issue in missing_issues)

    malformed = tmp_path / "candidate.json"
    malformed.write_text("{not-json", encoding="utf-8")

    malformed_issues = validate_candidate_manifest_file(malformed, contract)

    assert any("kein gueltiges JSON" in issue for issue in malformed_issues)


def test_validate_candidate_manifest_file_blocks_stale_contract(tmp_path):
    contract = build_final_packaging_contract("a" * 64)
    stale = build_final_packaging_contract("a" * 64)
    stale["app_identity"]["version"] = "0.0.0"
    output_path = write_candidate_manifest(stale, tmp_path / "candidate.json")

    issues = validate_candidate_manifest_file(output_path, contract, require_valid_sha=True)

    assert any("Produktidentitaet" in issue for issue in issues)
    assert any("driftet" in issue for issue in issues)


def test_validate_candidate_manifest_file_requires_real_sha_when_requested(tmp_path):
    pending = build_final_packaging_contract()
    output_path = write_candidate_manifest(pending, tmp_path / "candidate.json")

    issues = validate_candidate_manifest_file(output_path, pending, require_valid_sha=True)

    assert any("SHA" in issue for issue in issues)


def test_validate_candidate_manifest_file_accepts_current_contract_with_sha(tmp_path):
    contract = build_final_packaging_contract("a" * 64)
    output_path = write_candidate_manifest(contract, tmp_path / "candidate.json")

    assert validate_candidate_manifest_file(output_path, contract, require_valid_sha=True) == ()


def test_final_packaging_contract_cli_writes_only_requested_candidate_path(tmp_path, capsys):
    output_path = tmp_path / "candidate.json"

    exit_code = check_final_contract(["--write", "--output", str(output_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert output_path.is_file()
    assert "Candidate manifest written" in captured.out
