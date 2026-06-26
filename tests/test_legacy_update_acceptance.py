import json

from app_version import APP_VERSION
from dronautix_uploader.core.cutover_acceptance import LEGACY_UPDATE, evaluate_acceptance_evidence
from dronautix_uploader.core.legacy_update_acceptance import (
    build_legacy_update_acceptance_result,
    merge_legacy_update_into_acceptance_evidence,
)
from tools.record_v2_legacy_update_acceptance import main as record_legacy_update


def test_legacy_update_acceptance_result_passes_when_all_evidence_is_present():
    result = build_legacy_update_acceptance_result(
        from_version="1.7.10",
        to_version=APP_VERSION,
        installed_app_id_preserved=True,
        update_prompt_seen=True,
        download_sha_verified=True,
        post_update_launch_ok=True,
        legacy_config_or_keyring_available=True,
        completed_at_utc="2026-06-21T12:00:00Z",
    )

    assert result.passed is True
    evidence = merge_legacy_update_into_acceptance_evidence(
        {"schema_version": 1, "gates": {}},
        result,
    )
    legacy_gate = next(gate for gate in evaluate_acceptance_evidence(evidence) if gate.gate_id == LEGACY_UPDATE)
    assert legacy_gate.complete is True


def test_legacy_update_acceptance_result_blocks_missing_flags():
    result = build_legacy_update_acceptance_result(
        from_version="1.7.10",
        to_version="1.7.12",
        installed_app_id_preserved=True,
    )

    assert result.passed is False
    assert "update_prompt_seen" in result.message
    assert "download_sha_verified" in result.message


def test_legacy_update_acceptance_result_blocks_when_target_is_not_newer():
    result = build_legacy_update_acceptance_result(
        from_version="1.7.12",
        to_version="1.7.12",
        installed_app_id_preserved=True,
        update_prompt_seen=True,
        download_sha_verified=True,
        post_update_launch_ok=True,
        legacy_config_or_keyring_available=True,
    )

    assert result.passed is False
    assert "neuer" in result.message


def test_legacy_update_acceptance_cli_writes_gate(tmp_path, capsys):
    acceptance_path = tmp_path / "acceptance.json"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"scenarios": [{"id": "single_copc_upload"}]}), encoding="utf-8")

    exit_code = record_legacy_update(
        [
            "--from-version",
            "1.7.10",
            "--to-version",
            APP_VERSION,
            "--installed-app-id-preserved",
            "--update-prompt-seen",
            "--download-sha-verified",
            "--post-update-launch-ok",
            "--legacy-config-or-keyring-available",
            "--completed-at-utc",
            "2026-06-21T12:00:00Z",
            "--acceptance",
            str(acceptance_path),
            "--manifest",
            str(manifest_path),
            "--write-acceptance",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Altversions-Update: OK" in captured.out
    evidence = json.loads(acceptance_path.read_text(encoding="utf-8"))
    assert evidence["gates"][LEGACY_UPDATE]["status"] == "passed"
    assert evidence["gates"][LEGACY_UPDATE]["from_version"] == "1.7.10"


def test_legacy_update_acceptance_cli_blocks_without_all_flags(capsys):
    exit_code = record_legacy_update(["--from-version", "1.7.10", "--to-version", "1.7.12"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Altversions-Update: BLOCKED" in captured.out
