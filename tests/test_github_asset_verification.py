import hashlib
import json

from dronautix_uploader.core.cutover_acceptance import (
    GITHUB_ASSET_SHA,
    build_acceptance_evidence_template,
    evaluate_acceptance_evidence,
)
from dronautix_uploader.core.github_asset_verification import (
    github_asset_sha_result_to_acceptance_gate,
    merge_github_asset_sha_into_acceptance_evidence,
    verify_github_asset_sha,
)
from tools.check_v2_final_packaging_contract import build_final_packaging_contract, write_candidate_manifest
from tools.check_v2_github_asset_sha import main as check_github_asset_sha


def test_github_asset_sha_verifier_accepts_matching_local_asset(tmp_path):
    asset_bytes = b"final v2 installer"
    installer_sha = hashlib.sha256(asset_bytes).hexdigest()
    contract = build_final_packaging_contract(installer_sha)
    asset_path = tmp_path / contract["release_manifest_candidate"]["installer_name"]
    asset_path.write_bytes(asset_bytes)

    result = verify_github_asset_sha(contract, asset_path=asset_path)

    assert result.passed is True
    assert result.manifest_sha256 == installer_sha
    assert result.asset_sha256 == installer_sha
    assert result.asset_size == len(asset_bytes)


def test_github_asset_sha_verifier_rejects_sha_mismatch(tmp_path):
    contract = build_final_packaging_contract(hashlib.sha256(b"expected").hexdigest())
    asset_path = tmp_path / contract["release_manifest_candidate"]["installer_name"]
    asset_path.write_bytes(b"actual")

    result = verify_github_asset_sha(contract, asset_path=asset_path)
    gate = github_asset_sha_result_to_acceptance_gate(result)

    assert result.passed is False
    assert "unterscheidet" in result.message
    assert gate["status"] == "failed"
    assert gate["match"] is False
    assert gate["asset_sha256"] == hashlib.sha256(b"actual").hexdigest()


def test_github_asset_sha_verifier_rejects_empty_asset(tmp_path):
    installer_sha = hashlib.sha256(b"").hexdigest()
    contract = build_final_packaging_contract(installer_sha)
    asset_path = tmp_path / contract["release_manifest_candidate"]["installer_name"]
    asset_path.write_bytes(b"")

    result = verify_github_asset_sha(contract, asset_path=asset_path)
    gate = github_asset_sha_result_to_acceptance_gate(result)

    assert result.passed is False
    assert result.asset_size == 0
    assert "leer" in result.message
    assert gate["status"] == "failed"
    assert gate["match"] is False


def test_github_asset_sha_verifier_rejects_pending_candidate_sha(tmp_path):
    contract = build_final_packaging_contract()
    asset_path = tmp_path / contract["release_manifest_candidate"]["installer_name"]
    asset_path.write_bytes(b"actual")

    result = verify_github_asset_sha(contract, asset_path=asset_path)

    assert result.passed is False
    assert "SHA-256" in result.message
    assert result.asset_sha256 == ""


def test_github_asset_sha_gate_merges_into_acceptance_evidence(tmp_path):
    asset_bytes = b"final v2 installer"
    installer_sha = hashlib.sha256(asset_bytes).hexdigest()
    contract = build_final_packaging_contract(installer_sha)
    candidate_manifest = tmp_path / "candidate.json"
    asset_path = tmp_path / contract["release_manifest_candidate"]["installer_name"]
    asset_path.write_bytes(asset_bytes)
    result = verify_github_asset_sha(contract, asset_path=asset_path)

    evidence = merge_github_asset_sha_into_acceptance_evidence(
        build_acceptance_evidence_template(required_s3_scenarios=["single_copc_upload"]),
        result,
        contract,
        candidate_manifest_path=candidate_manifest,
    )

    assert evidence["candidate_manifest_path"] == str(candidate_manifest)
    assert evidence["candidate_installer_sha256"] == installer_sha
    gate = evidence["gates"][GITHUB_ASSET_SHA]
    assert gate["status"] == "passed"
    assert gate["match"] is True
    assert gate["asset_size"] == len(asset_bytes)

    results = evaluate_acceptance_evidence(
        evidence,
        candidate_contract={
            **contract,
            "isolated_paths": {
                **contract["isolated_paths"],
                "manifest_path": str(candidate_manifest),
            },
        },
        required_s3_scenarios=["single_copc_upload"],
    )
    sha_result = next(result for result in results if result.gate_id == GITHUB_ASSET_SHA)
    assert sha_result.complete is True


def test_github_asset_sha_cli_writes_acceptance_gate(tmp_path, capsys):
    asset_bytes = b"final v2 installer"
    installer_sha = hashlib.sha256(asset_bytes).hexdigest()
    contract = build_final_packaging_contract(installer_sha)
    candidate_path = write_candidate_manifest(contract, tmp_path / "candidate.json")
    asset_path = tmp_path / contract["release_manifest_candidate"]["installer_name"]
    acceptance_path = tmp_path / "acceptance.json"
    manifest_path = tmp_path / "manifest.json"
    asset_path.write_bytes(asset_bytes)
    manifest_path.write_text(json.dumps({"scenarios": [{"id": "single_copc_upload"}]}), encoding="utf-8")

    exit_code = check_github_asset_sha(
        [
            "--candidate-manifest",
            str(candidate_path),
            "--asset-path",
            str(asset_path),
            "--acceptance",
            str(acceptance_path),
            "--manifest",
            str(manifest_path),
            "--write-acceptance",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "GitHub Asset SHA: OK" in captured.out
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    assert acceptance["gates"][GITHUB_ASSET_SHA]["status"] == "passed"


def test_github_asset_sha_cli_blocks_on_sha_mismatch(tmp_path, capsys):
    contract = build_final_packaging_contract(hashlib.sha256(b"expected").hexdigest())
    candidate_path = write_candidate_manifest(contract, tmp_path / "candidate.json")
    asset_path = tmp_path / contract["release_manifest_candidate"]["installer_name"]
    asset_path.write_bytes(b"actual")

    exit_code = check_github_asset_sha(
        [
            "--candidate-manifest",
            str(candidate_path),
            "--asset-path",
            str(asset_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "GitHub Asset SHA: BLOCKED" in captured.out
    assert "unterscheidet" in captured.out
