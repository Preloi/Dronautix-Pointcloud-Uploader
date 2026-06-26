import hashlib
import json

import pytest

from app_version import APP_VERSION
from dronautix_uploader.core.cutover_acceptance import (
    DEFAULT_S3_ACCEPTANCE_SCENARIOS,
    GITHUB_ASSET_SHA,
    LEGACY_UPDATE,
    REAL_S3_ACCEPTANCE,
    build_acceptance_evidence_template,
    evaluate_acceptance_evidence,
    load_acceptance_evidence,
)
from tools.check_v2_final_packaging_contract import build_final_packaging_contract


def test_acceptance_template_starts_with_all_required_gates_unpassed():
    template = build_acceptance_evidence_template()

    assert template["schema_version"] == 1
    assert template["candidate_version"] == APP_VERSION
    assert template["gates"][REAL_S3_ACCEPTANCE]["status"] == "pending"
    assert template["gates"][REAL_S3_ACCEPTANCE]["scenarios_passed"] == list(DEFAULT_S3_ACCEPTANCE_SCENARIOS)
    assert template["gates"][GITHUB_ASSET_SHA]["status"] == "pending"
    assert template["gates"][LEGACY_UPDATE]["status"] == "pending"


def test_acceptance_evidence_requires_schema_version():
    results = evaluate_acceptance_evidence({})

    assert len(results) == 3
    assert all(not result.complete for result in results)
    assert all("schema_version" in result.detail for result in results)


def test_acceptance_evidence_accepts_complete_manual_evidence():
    evidence = _complete_evidence()

    results = evaluate_acceptance_evidence(
        evidence,
        candidate_contract=build_final_packaging_contract(_sha()),
        required_s3_scenarios=evidence["gates"][REAL_S3_ACCEPTANCE]["scenarios_passed"],
    )

    assert all(result.complete for result in results)


def test_acceptance_evidence_rejects_missing_required_s3_scenario():
    evidence = _complete_evidence()
    evidence["gates"][REAL_S3_ACCEPTANCE]["scenarios_passed"] = ["single_copc_upload"]

    results = evaluate_acceptance_evidence(
        evidence,
        required_s3_scenarios=("single_copc_upload", "disabled_link_state"),
    )

    s3_result = next(result for result in results if result.gate_id == REAL_S3_ACCEPTANCE)
    assert not s3_result.complete
    assert "disabled_link_state" in s3_result.detail


def test_acceptance_evidence_accepts_required_s3_scenarios_in_any_order():
    evidence = _complete_evidence()
    evidence["gates"][REAL_S3_ACCEPTANCE]["scenarios_passed"] = ["multi_replace", "single_copc_upload"]

    results = evaluate_acceptance_evidence(
        evidence,
        required_s3_scenarios=("single_copc_upload", "multi_replace"),
    )

    s3_result = next(result for result in results if result.gate_id == REAL_S3_ACCEPTANCE)
    assert s3_result.complete


def test_acceptance_evidence_rejects_unknown_s3_scenario_when_manifest_set_is_required():
    evidence = _complete_evidence()
    evidence["gates"][REAL_S3_ACCEPTANCE]["scenarios_passed"] = [
        "single_copc_upload",
        "project_management_replace_delete_rename",
    ]

    results = evaluate_acceptance_evidence(
        evidence,
        required_s3_scenarios=("single_copc_upload",),
    )

    s3_result = next(result for result in results if result.gate_id == REAL_S3_ACCEPTANCE)
    assert not s3_result.complete
    assert "project_management_replace_delete_rename" in s3_result.detail


def test_acceptance_evidence_rejects_duplicate_s3_scenarios():
    evidence = _complete_evidence()
    evidence["gates"][REAL_S3_ACCEPTANCE]["scenarios_passed"] = ["single_copc_upload", "single_copc_upload"]

    results = evaluate_acceptance_evidence(
        evidence,
        required_s3_scenarios=("single_copc_upload",),
    )

    s3_result = next(result for result in results if result.gate_id == REAL_S3_ACCEPTANCE)
    assert not s3_result.complete
    assert "Duplikate" in s3_result.detail


def test_acceptance_evidence_rejects_missing_required_fields():
    evidence = _complete_evidence()
    evidence["gates"][REAL_S3_ACCEPTANCE]["test_prefix"] = ""

    results = evaluate_acceptance_evidence(evidence)

    s3_result = next(result for result in results if result.gate_id == REAL_S3_ACCEPTANCE)
    assert not s3_result.complete
    assert "test_prefix" in s3_result.detail


def test_acceptance_evidence_rejects_invalid_github_sha():
    evidence = _complete_evidence()
    evidence["gates"][GITHUB_ASSET_SHA]["asset_sha256"] = "not-a-sha"

    results = evaluate_acceptance_evidence(evidence)

    sha_result = next(result for result in results if result.gate_id == GITHUB_ASSET_SHA)
    assert not sha_result.complete
    assert "SHA-256" in sha_result.detail


@pytest.mark.parametrize("asset_size", [None, 0, -1, True, "9"])
def test_acceptance_evidence_rejects_github_sha_without_positive_asset_size(asset_size):
    evidence = _complete_evidence()
    if asset_size is None:
        evidence["gates"][GITHUB_ASSET_SHA].pop("asset_size", None)
    else:
        evidence["gates"][GITHUB_ASSET_SHA]["asset_size"] = asset_size

    results = evaluate_acceptance_evidence(evidence)

    sha_result = next(result for result in results if result.gate_id == GITHUB_ASSET_SHA)
    assert not sha_result.complete
    assert "asset_size" in sha_result.detail


def test_acceptance_evidence_rejects_candidate_contract_mismatch():
    evidence = _complete_evidence()
    evidence["candidate_installer_name"] = "wrong.exe"

    results = evaluate_acceptance_evidence(evidence, candidate_contract=build_final_packaging_contract(_sha()))

    sha_result = next(result for result in results if result.gate_id == GITHUB_ASSET_SHA)
    assert not sha_result.complete
    assert "candidate_installer_name" in sha_result.detail


def test_acceptance_evidence_rejects_candidate_manifest_sha_mismatch():
    evidence = _complete_evidence()
    contract = build_final_packaging_contract("b" * 64)

    results = evaluate_acceptance_evidence(evidence, candidate_contract=contract)

    sha_result = next(result for result in results if result.gate_id == GITHUB_ASSET_SHA)
    assert not sha_result.complete
    assert "release_manifest_candidate.installer_sha256" in sha_result.detail


def test_acceptance_evidence_rejects_legacy_update_without_completion_timestamp():
    evidence = _complete_evidence()
    evidence["gates"][LEGACY_UPDATE]["completed_at_utc"] = ""

    results = evaluate_acceptance_evidence(evidence)

    legacy_result = next(result for result in results if result.gate_id == LEGACY_UPDATE)
    assert not legacy_result.complete
    assert "completed_at_utc" in legacy_result.detail


def test_acceptance_evidence_rejects_legacy_update_when_target_is_not_newer():
    evidence = _complete_evidence()
    evidence["gates"][LEGACY_UPDATE]["from_version"] = APP_VERSION
    evidence["gates"][LEGACY_UPDATE]["to_version"] = APP_VERSION

    results = evaluate_acceptance_evidence(evidence)

    legacy_result = next(result for result in results if result.gate_id == LEGACY_UPDATE)
    assert not legacy_result.complete
    assert "neuer" in legacy_result.detail


def test_acceptance_evidence_rejects_legacy_update_to_wrong_candidate_version():
    evidence = _complete_evidence()
    evidence["gates"][LEGACY_UPDATE]["to_version"] = "1.7.11"

    results = evaluate_acceptance_evidence(evidence)

    legacy_result = next(result for result in results if result.gate_id == LEGACY_UPDATE)
    assert not legacy_result.complete
    assert "Final-V2-Version" in legacy_result.detail


def test_load_acceptance_evidence_returns_empty_for_missing_file(tmp_path):
    assert load_acceptance_evidence(tmp_path / "missing.json") == {}


def test_load_acceptance_evidence_rejects_malformed_json(tmp_path):
    evidence_path = tmp_path / "acceptance.json"
    evidence_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON"):
        load_acceptance_evidence(evidence_path)


def _complete_evidence():
    installer_name = f"Dronautix_Pointcloud_Uploader_Setup_{APP_VERSION}.exe"
    return {
        "schema_version": 1,
        "candidate_version": APP_VERSION,
        "candidate_manifest_path": "artifacts/v2-final-candidate-release.json",
        "candidate_installer_name": installer_name,
        "candidate_installer_sha256": _sha(),
        "gates": {
            REAL_S3_ACCEPTANCE: {
                "status": "passed",
                "completed_at_utc": "2026-06-21T12:00:00Z",
                "test_prefix": "v2-cutover-acceptance/run-1/",
                "scenarios_passed": list(DEFAULT_S3_ACCEPTANCE_SCENARIOS),
                "projects_index_verified": True,
                "metadata_verified": True,
                "cleanup_verified": True,
                "notes": "S3 acceptance passed.",
            },
            GITHUB_ASSET_SHA: {
                "status": "passed",
                "repo": "Preloi/Dronautix-Pointcloud-Uploader",
                "release_tag": f"v{APP_VERSION}",
                "asset_name": installer_name,
                "manifest_sha256": _sha(),
                "asset_sha256": _sha(),
                "asset_size": len(b"installer"),
                "match": True,
                "notes": "Remote asset verified.",
            },
            LEGACY_UPDATE: {
                "status": "passed",
                "completed_at_utc": "2026-06-21T12:30:00Z",
                "from_version": "1.7.10",
                "to_version": APP_VERSION,
                "installed_app_id_preserved": True,
                "update_prompt_seen": True,
                "download_sha_verified": True,
                "post_update_launch_ok": True,
                "legacy_config_or_keyring_available": True,
                "notes": "Installed legacy app updated.",
            },
        },
    }


def _sha():
    return hashlib.sha256(b"installer").hexdigest()
