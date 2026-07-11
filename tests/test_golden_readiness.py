import hashlib
import json
from pathlib import Path

from app_version import APP_VERSION
from dronautix_uploader.core.golden_capture import (
    check_golden_capture_readiness,
    check_v2_output_freshness,
    check_v2_output_readiness,
    normalize_capture_text,
)
from dronautix_uploader.core.v2_golden_output import generate_v2_golden_outputs
from tools.check_v2_final_packaging_contract import build_final_packaging_contract, write_candidate_manifest
from tools.check_v2_cutover_ready import main as check_cutover_ready


def _write_manifest(tmp_path: Path, *, provenance_file: str = "", v2_output_root: str | None = None) -> Path:
    return _write_manifest_with_scenarios(
        tmp_path,
        provenance_file=provenance_file,
        v2_output_root=v2_output_root,
        scenarios=[
            {
                "id": "single_copc_upload",
                "description": "Single COPC direct upload.",
                "required_files": ["projects_index.json"],
            }
        ],
    )


def _write_manifest_with_scenarios(
    tmp_path: Path,
    *,
    scenarios: list[dict],
    provenance_file: str = "",
    v2_output_root: str | None = None,
) -> Path:
    manifest = {
        "schema_version": 1,
        "captured_root": str(tmp_path / "captured"),
        "normalized_root": str(tmp_path / "normalized"),
        "provenance_file": provenance_file,
        "scenarios": scenarios,
    }
    if v2_output_root is not None:
        manifest["v2_output_root"] = v2_output_root
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def test_golden_readiness_reports_missing_legacy_captures(tmp_path):
    manifest_path = _write_manifest(tmp_path)

    report = check_golden_capture_readiness(manifest_path)

    assert not report.ready
    assert report.scenario_count == 1
    assert report.captured_count == 0
    assert report.issues[0].scenario_id == "single_copc_upload"
    assert "fehlt" in report.issues[0].message


def test_golden_readiness_does_not_count_empty_capture_skeleton_as_captured(tmp_path):
    manifest_path = _write_manifest(tmp_path, provenance_file="provenance.json")
    captured_dir = tmp_path / "captured" / "single_copc_upload"
    normalized_dir = tmp_path / "normalized" / "single_copc_upload"
    captured_dir.mkdir(parents=True)
    normalized_dir.mkdir(parents=True)
    (captured_dir / "provenance.json").write_text(
        json.dumps(
            {
                "scenario_id": "single_copc_upload",
                "legacy_app_version": "1.7.10",
                "legacy_git_ref": "dronautix/develop@test",
                "captured_at_utc": "2026-06-21T00:00:00Z",
                "captured_files": ["projects_index.json"],
                "inputs": {"source": "test"},
                "observed_contracts": {"operation_order": ["upload"]},
            }
        ),
        encoding="utf-8",
    )

    report = check_golden_capture_readiness(manifest_path)

    assert report.captured_count == 0
    assert any("Captured File fehlt" in issue.message for issue in report.issues)


def test_golden_readiness_accepts_matching_normalized_capture(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    captured_dir = tmp_path / "captured" / "single_copc_upload"
    normalized_dir = tmp_path / "normalized" / "single_copc_upload"
    captured_dir.mkdir(parents=True)
    normalized_dir.mkdir(parents=True)
    raw = '{"projekt":"Muenchen","value":1}'
    normalized = '{\n  "projekt": "Muenchen",\n  "value": 1\n}\n'
    (captured_dir / "projects_index.json").write_text(raw, encoding="utf-8")
    (normalized_dir / "projects_index.normalized.json").write_text(normalized, encoding="utf-8")

    report = check_golden_capture_readiness(manifest_path)

    assert report.ready
    assert report.captured_count == 1
    assert report.issues == ()


def test_golden_readiness_requires_valid_provenance_when_manifest_declares_it(tmp_path):
    manifest_path = _write_manifest(tmp_path, provenance_file="provenance.json")
    captured_dir = tmp_path / "captured" / "single_copc_upload"
    normalized_dir = tmp_path / "normalized" / "single_copc_upload"
    captured_dir.mkdir(parents=True)
    normalized_dir.mkdir(parents=True)
    (captured_dir / "projects_index.json").write_text('{"projekt":"Muenchen"}', encoding="utf-8")
    (normalized_dir / "projects_index.normalized.json").write_text(
        '{\n  "projekt": "Muenchen"\n}\n',
        encoding="utf-8",
    )

    missing_report = check_golden_capture_readiness(manifest_path)

    assert not missing_report.ready
    assert "Provenienz fehlt" in missing_report.issues[0].message

    (captured_dir / "provenance.json").write_text(
        json.dumps(
            {
                "scenario_id": "single_copc_upload",
                "legacy_app_version": "1.7.10",
                "legacy_git_ref": "dronautix/develop@test",
                "captured_at_utc": "2026-06-21T00:00:00Z",
                "captured_files": ["projects_index.json"],
                "inputs": {"source": "test"},
                "observed_contracts": {"operation_order": ["upload", "save_index"]},
            }
        ),
        encoding="utf-8",
    )

    ready_report = check_golden_capture_readiness(manifest_path)

    assert ready_report.ready
    assert ready_report.issues == ()


def test_golden_readiness_rejects_provenance_file_list_that_does_not_match_manifest(tmp_path):
    manifest_path = _write_manifest_with_scenarios(
        tmp_path,
        provenance_file="provenance.json",
        scenarios=[
            {
                "id": "multi_replace",
                "description": "Multi replace.",
                "required_files": ["projects_index.json", "metadata.json", "cloud.js"],
            }
        ],
    )
    captured_dir = tmp_path / "captured" / "multi_replace"
    normalized_dir = tmp_path / "normalized" / "multi_replace"
    captured_dir.mkdir(parents=True)
    normalized_dir.mkdir(parents=True)
    for raw_name, normalized_name in (
        ("projects_index.json", "projects_index.normalized.json"),
        ("metadata.json", "metadata.normalized.json"),
        ("cloud.js", "cloud.normalized.json"),
    ):
        (captured_dir / raw_name).write_text('{"value":1}', encoding="utf-8")
        (normalized_dir / normalized_name).write_text('{\n  "value": 1\n}\n', encoding="utf-8")
    (captured_dir / "provenance.json").write_text(
        json.dumps(
            {
                "scenario_id": "multi_replace",
                "legacy_app_version": "1.7.10",
                "legacy_git_ref": "dronautix/develop@test",
                "captured_at_utc": "2026-06-21T00:00:00Z",
                "captured_files": ["projects_index.json"],
                "inputs": {"source": "test"},
                "observed_contracts": {"operation_order": ["upload", "save_index"]},
            }
        ),
        encoding="utf-8",
    )

    report = check_golden_capture_readiness(manifest_path)

    assert not report.ready
    assert any("captured_files passt nicht" in issue.message for issue in report.issues)


def test_golden_readiness_rejects_unfilled_multi_replace_provenance(tmp_path):
    manifest_path = _write_manifest_with_scenarios(
        tmp_path,
        provenance_file="provenance.json",
        scenarios=[
            {
                "id": "multi_replace",
                "description": "Multi replace.",
                "required_files": ["projects_index.json"],
            }
        ],
    )
    captured_dir = tmp_path / "captured" / "multi_replace"
    normalized_dir = tmp_path / "normalized" / "multi_replace"
    captured_dir.mkdir(parents=True)
    normalized_dir.mkdir(parents=True)
    (captured_dir / "projects_index.json").write_text('{"value":1}', encoding="utf-8")
    (normalized_dir / "projects_index.normalized.json").write_text('{\n  "value": 1\n}\n', encoding="utf-8")
    (captured_dir / "provenance.json").write_text(
        json.dumps(
            {
                "scenario_id": "multi_replace",
                "legacy_app_version": "1.7.12",
                "legacy_git_ref": "dronautix/develop@test",
                "captured_at_utc": "2026-06-21T00:00:00Z",
                "captured_files": ["projects_index.json"],
                "inputs": {
                    "project_id": "",
                    "original_link": "",
                    "original_disabled_state": "",
                    "sources": [{"source": "", "source_type": "", "format": "", "name": "", "slug": ""}],
                },
                "observed_contracts": {
                    "converter_command": "",
                    "converter_working_directory": "",
                    "s3_extra_args": "",
                    "index_update_order": "",
                    "crs_behavior": "",
                    "cleanup_behavior": "",
                    "rollback_behavior": "",
                    "common_crs_decision": "",
                    "stale_project_crs_on_mismatch": "",
                    "existing_keys": [],
                    "uploaded_keys": [],
                    "obsolete_keys": [],
                },
            }
        ),
        encoding="utf-8",
    )

    blocked = check_golden_capture_readiness(manifest_path)

    assert not blocked.ready
    assert any("Beobachtungsfeld" in issue.message for issue in blocked.issues)

    (captured_dir / "provenance.json").write_text(
        json.dumps(_filled_multi_replace_provenance(["projects_index.json"])),
        encoding="utf-8",
    )

    ready = check_golden_capture_readiness(manifest_path)

    assert ready.ready


def test_golden_readiness_rejects_unfilled_disabled_link_state_provenance(tmp_path):
    manifest_path = _write_manifest_with_scenarios(
        tmp_path,
        provenance_file="provenance.json",
        scenarios=[
            {
                "id": "disabled_link_state",
                "description": "Disabled state.",
                "required_files": ["projects_index.json"],
            }
        ],
    )
    captured_dir = tmp_path / "captured" / "disabled_link_state"
    normalized_dir = tmp_path / "normalized" / "disabled_link_state"
    captured_dir.mkdir(parents=True)
    normalized_dir.mkdir(parents=True)
    (captured_dir / "projects_index.json").write_text('{"value":1}', encoding="utf-8")
    (normalized_dir / "projects_index.normalized.json").write_text('{\n  "value": 1\n}\n', encoding="utf-8")
    (captured_dir / "provenance.json").write_text(
        json.dumps(
            {
                "scenario_id": "disabled_link_state",
                "legacy_app_version": "1.7.12",
                "legacy_git_ref": "dronautix/develop@test",
                "captured_at_utc": "2026-06-21T00:00:00Z",
                "captured_files": ["projects_index.json"],
                "inputs": {
                    "project_id": "",
                    "link": "",
                    "operation_sequence": [],
                    "disabled_state_before": {"list_membership": ""},
                    "disabled_state_after": {"list_membership": ""},
                },
                "observed_contracts": {
                    "disabled_link_behavior": "",
                    "replace_disabled_membership": "",
                    "rename_disabled_membership": "",
                    "delete_behavior": "",
                    "deleted_projects_entry": {"deleted_at": "", "original_link": ""},
                },
            }
        ),
        encoding="utf-8",
    )

    blocked = check_golden_capture_readiness(manifest_path)

    assert not blocked.ready
    assert any("disabled_link_state" == issue.scenario_id for issue in blocked.issues)

    (captured_dir / "provenance.json").write_text(
        json.dumps(_filled_disabled_link_state_provenance(["projects_index.json"])),
        encoding="utf-8",
    )

    ready = check_golden_capture_readiness(manifest_path)

    assert ready.ready


def test_golden_readiness_detects_normalized_drift(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    captured_dir = tmp_path / "captured" / "single_copc_upload"
    normalized_dir = tmp_path / "normalized" / "single_copc_upload"
    captured_dir.mkdir(parents=True)
    normalized_dir.mkdir(parents=True)
    (captured_dir / "projects_index.json").write_text('{"value":1}', encoding="utf-8")
    (normalized_dir / "projects_index.normalized.json").write_text('{"value":2}\n', encoding="utf-8")

    report = check_golden_capture_readiness(manifest_path)

    assert not report.ready
    assert "driftet" in report.issues[0].message


def test_v2_output_readiness_reports_present_and_missing_required_files(tmp_path):
    manifest_path = _write_manifest_with_scenarios(
        tmp_path,
        v2_output_root=str(tmp_path / "v2"),
        scenarios=[
            {
                "id": "single_potree_upload",
                "description": "Single Potree.",
                "required_files": ["projects_index.json", "metadata.json", "cloud.js"],
            },
            {
                "id": "single_copc_upload",
                "description": "Single COPC.",
                "required_files": ["projects_index.json"],
            },
        ],
    )
    potree_dir = tmp_path / "v2" / "single_potree_upload"
    copc_dir = tmp_path / "v2" / "single_copc_upload"
    potree_dir.mkdir(parents=True)
    copc_dir.mkdir(parents=True)
    (potree_dir / "projects_index.json").write_text('{"value":1}', encoding="utf-8")
    (potree_dir / "metadata.json").write_text('{"points":1}', encoding="utf-8")
    (potree_dir / "cloud.js").write_text('cloud.js = {"spacing": 1};', encoding="utf-8")
    (copc_dir / "projects_index.json").write_text('{"value":2}', encoding="utf-8")

    ready_report = check_v2_output_readiness(manifest_path)

    assert ready_report.ready
    assert ready_report.output_count == 2

    (potree_dir / "metadata.json").unlink()

    blocked_report = check_v2_output_readiness(manifest_path)

    assert not blocked_report.ready
    assert blocked_report.output_count == 2
    assert any("metadata.json" in issue.message for issue in blocked_report.issues)


def test_v2_output_readiness_rejects_unparseable_cloudjs(tmp_path):
    manifest_path = _write_manifest_with_scenarios(
        tmp_path,
        v2_output_root=str(tmp_path / "v2"),
        scenarios=[
            {
                "id": "vertical_crs_upload",
                "description": "Vertical CRS.",
                "required_files": ["cloud.js"],
            },
        ],
    )
    output_dir = tmp_path / "v2" / "vertical_crs_upload"
    output_dir.mkdir(parents=True)
    (output_dir / "cloud.js").write_text("cloud.js = {not-json};", encoding="utf-8")

    report = check_v2_output_readiness(manifest_path)

    assert not report.ready
    assert "nicht normalisierbar" in report.issues[0].message


def test_v2_output_freshness_detects_stale_imported_output(tmp_path):
    manifest_path = _write_manifest_with_scenarios(
        tmp_path,
        v2_output_root=str(tmp_path / "v2"),
        scenarios=[
            {
                "id": "single_copc_upload",
                "description": "Single COPC.",
                "required_files": ["projects_index.json"],
            },
        ],
    )
    output_dir = tmp_path / "v2" / "single_copc_upload"
    output_dir.mkdir(parents=True)
    (output_dir / "projects_index.json").write_text('{"projects":[{"id":"stale"}]}', encoding="utf-8")

    stale_report = check_v2_output_freshness(manifest_path)

    assert not stale_report.ready
    assert stale_report.checked_count == 1
    assert "veraltet" in stale_report.issues[0].message


def test_cutover_ready_cli_returns_nonzero_for_missing_captures(tmp_path, capsys):
    manifest_path = _write_manifest(tmp_path)

    exit_code = check_cutover_ready(["--manifest", str(manifest_path), "--acceptance", str(tmp_path / "missing.json")])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "BLOCKED" in captured.out
    assert "Akzeptanz-Evidenzdatei fehlt" in captured.out
    assert "--write-template" in captured.out


def test_cutover_ready_cli_blocks_when_v2_outputs_do_not_match_ready_golden(tmp_path, capsys):
    manifest_path = _write_manifest(tmp_path, v2_output_root=str(tmp_path / "v2"))
    captured_dir = tmp_path / "captured" / "single_copc_upload"
    normalized_dir = tmp_path / "normalized" / "single_copc_upload"
    captured_dir.mkdir(parents=True)
    normalized_dir.mkdir(parents=True)
    (captured_dir / "projects_index.json").write_text('{"value":1}', encoding="utf-8")
    (normalized_dir / "projects_index.normalized.json").write_text('{\n  "value": 1\n}\n', encoding="utf-8")

    exit_code = check_cutover_ready(["--manifest", str(manifest_path), "--acceptance", str(tmp_path / "missing.json")])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[OK] Golden Masters" in captured.out
    assert "[BLOCKED] V2 output files" in captured.out
    assert "[BLOCKED] V2 outputs match Golden Masters" in captured.out
    assert "V2-Ausgabe fehlt" in captured.out


def test_cutover_ready_cli_requires_matching_v2_outputs_for_success(tmp_path, capsys):
    manifest_path = _write_manifest(tmp_path, v2_output_root=str(tmp_path / "v2"))
    _seed_current_single_copc_v2_and_golden(tmp_path, manifest_path, v2_root=tmp_path / "v2")
    candidate_manifest_path = tmp_path / "candidate.json"
    acceptance_path = tmp_path / "acceptance.json"
    evidence = _complete_acceptance_evidence(candidate_manifest_path=candidate_manifest_path)
    _write_matching_candidate_manifest(candidate_manifest_path, evidence)
    acceptance_path.write_text(json.dumps(evidence), encoding="utf-8")

    exit_code = check_cutover_ready(
        [
            "--manifest",
            str(manifest_path),
            "--acceptance",
            str(acceptance_path),
            "--candidate-manifest",
            str(candidate_manifest_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[OK] Golden Masters" in captured.out
    assert "[OK] V2 output files" in captured.out
    assert "[OK] V2 output freshness" in captured.out
    assert "[OK] V2 outputs match Golden Masters" in captured.out
    assert "V2 cutover gate: OK" in captured.out


def test_cutover_ready_cli_blocks_when_imported_v2_outputs_are_stale(tmp_path, capsys):
    manifest_path = _write_manifest(tmp_path, v2_output_root=str(tmp_path / "v2"))
    captured_dir = tmp_path / "captured" / "single_copc_upload"
    normalized_dir = tmp_path / "normalized" / "single_copc_upload"
    v2_dir = tmp_path / "v2" / "single_copc_upload"
    captured_dir.mkdir(parents=True)
    normalized_dir.mkdir(parents=True)
    v2_dir.mkdir(parents=True)
    stale_raw = '{"projects":[{"id":"deadbeef"}]}'
    stale_normalized = '{\n  "projects": [\n    {\n      "id": "<id>"\n    }\n  ]\n}\n'
    (captured_dir / "projects_index.json").write_text(stale_raw, encoding="utf-8")
    (normalized_dir / "projects_index.normalized.json").write_text(stale_normalized, encoding="utf-8")
    (v2_dir / "projects_index.json").write_text(stale_raw, encoding="utf-8")
    candidate_manifest_path = tmp_path / "candidate.json"
    acceptance_path = tmp_path / "acceptance.json"
    evidence = _complete_acceptance_evidence(candidate_manifest_path=candidate_manifest_path)
    _write_matching_candidate_manifest(candidate_manifest_path, evidence)
    acceptance_path.write_text(json.dumps(evidence), encoding="utf-8")

    exit_code = check_cutover_ready(
        [
            "--manifest",
            str(manifest_path),
            "--acceptance",
            str(acceptance_path),
            "--candidate-manifest",
            str(candidate_manifest_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[OK] Golden Masters" in captured.out
    assert "[OK] V2 output files" in captured.out
    assert "[BLOCKED] V2 output freshness" in captured.out
    assert "Importierte V2-Datei ist veraltet" in captured.out
    assert "[OK] V2 outputs match Golden Masters" in captured.out


def test_cutover_ready_cli_blocks_when_candidate_manifest_is_missing(tmp_path, capsys):
    manifest_path = _write_manifest(tmp_path, v2_output_root=str(tmp_path / "v2"))
    _seed_current_single_copc_v2_and_golden(tmp_path, manifest_path, v2_root=tmp_path / "v2")
    candidate_manifest_path = tmp_path / "missing-candidate.json"
    acceptance_path = tmp_path / "acceptance.json"
    evidence = _complete_acceptance_evidence(candidate_manifest_path=candidate_manifest_path)
    acceptance_path.write_text(json.dumps(evidence), encoding="utf-8")

    exit_code = check_cutover_ready(
        [
            "--manifest",
            str(manifest_path),
            "--acceptance",
            str(acceptance_path),
            "--candidate-manifest",
            str(candidate_manifest_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[OK] Golden Masters" in captured.out
    assert "[OK] V2 output freshness" in captured.out
    assert "[BLOCKED] Final-V2 candidate manifest" in captured.out
    assert "Candidate-Manifest fehlt" in captured.out


def test_cutover_ready_cli_blocks_when_s3_acceptance_misses_manifest_scenario(tmp_path, capsys):
    manifest_path = _write_manifest_with_scenarios(
        tmp_path,
        v2_output_root=str(tmp_path / "v2"),
        scenarios=[
            {
                "id": "single_copc_upload",
                "description": "Single COPC direct upload.",
                "required_files": ["projects_index.json"],
            },
            {
                "id": "multi_replace",
                "description": "Multi replace.",
                "required_files": ["projects_index.json"],
            },
        ],
    )
    for scenario_id in ("single_copc_upload", "multi_replace"):
        captured_dir = tmp_path / "captured" / scenario_id
        normalized_dir = tmp_path / "normalized" / scenario_id
        v2_dir = tmp_path / "v2" / scenario_id
        captured_dir.mkdir(parents=True)
        normalized_dir.mkdir(parents=True)
        v2_dir.mkdir(parents=True)
        (captured_dir / "projects_index.json").write_text('{"value":1}', encoding="utf-8")
        (normalized_dir / "projects_index.normalized.json").write_text('{\n  "value": 1\n}\n', encoding="utf-8")
        (v2_dir / "projects_index.json").write_text('{"value":1}', encoding="utf-8")
    acceptance_path = tmp_path / "acceptance.json"
    evidence = _complete_acceptance_evidence()
    evidence["gates"]["real_s3_acceptance"]["scenarios_passed"] = ["single_copc_upload"]
    acceptance_path.write_text(json.dumps(evidence), encoding="utf-8")

    exit_code = check_cutover_ready(["--manifest", str(manifest_path), "--acceptance", str(acceptance_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[OK] Golden Masters" in captured.out
    assert "[OK] V2 outputs match Golden Masters" in captured.out
    assert "S3-Szenarien fehlen: multi_replace" in captured.out


def test_cutover_ready_cli_does_not_allow_v2_outputs_to_substitute_legacy_captures(tmp_path, capsys):
    manifest_path = _write_manifest(tmp_path, v2_output_root=str(tmp_path / "v2"))
    normalized_dir = tmp_path / "normalized" / "single_copc_upload"
    v2_dir = tmp_path / "v2" / "single_copc_upload"
    normalized_dir.mkdir(parents=True)
    v2_dir.mkdir(parents=True)
    (normalized_dir / "projects_index.normalized.json").write_text('{\n  "value": 1\n}\n', encoding="utf-8")
    (v2_dir / "projects_index.json").write_text('{"value":1}', encoding="utf-8")
    acceptance_path = tmp_path / "acceptance.json"
    acceptance_path.write_text(json.dumps(_complete_acceptance_evidence()), encoding="utf-8")

    exit_code = check_cutover_ready(["--manifest", str(manifest_path), "--acceptance", str(acceptance_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[BLOCKED] Golden Masters" in captured.out
    assert "Legacy capture fehlt" in captured.out
    assert "[OK] V2 outputs match Golden Masters" in captured.out


def test_cutover_ready_cli_accepts_v2_output_root_override(tmp_path, capsys):
    manifest_path = _write_manifest(tmp_path, v2_output_root=str(tmp_path / "missing_manifest_v2_root"))
    _seed_current_single_copc_v2_and_golden(tmp_path, manifest_path, v2_root=tmp_path / "override_v2")
    candidate_manifest_path = tmp_path / "candidate.json"
    acceptance_path = tmp_path / "acceptance.json"
    evidence = _complete_acceptance_evidence(candidate_manifest_path=candidate_manifest_path)
    _write_matching_candidate_manifest(candidate_manifest_path, evidence)
    acceptance_path.write_text(json.dumps(evidence), encoding="utf-8")

    exit_code = check_cutover_ready(
        [
            "--manifest",
            str(manifest_path),
            "--acceptance",
            str(acceptance_path),
            "--v2-output-root",
            str(tmp_path / "override_v2"),
            "--candidate-manifest",
            str(candidate_manifest_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[OK] V2 outputs match Golden Masters" in captured.out
    assert "V2 cutover gate: OK" in captured.out


def test_cutover_ready_cli_can_write_acceptance_template(tmp_path, capsys):
    manifest_path = _write_manifest(tmp_path)
    acceptance_path = tmp_path / "acceptance.json"

    exit_code = check_cutover_ready(
        ["--manifest", str(manifest_path), "--acceptance", str(acceptance_path), "--write-template"]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert acceptance_path.is_file()
    template = json.loads(acceptance_path.read_text(encoding="utf-8"))
    assert template["gates"]["real_s3_acceptance"]["scenarios_passed"] == ["single_copc_upload"]
    assert "template written" in captured.out


def _complete_acceptance_evidence(candidate_manifest_path=None):
    installer_name = f"Dronautix_Pointcloud_Uploader_Setup_{APP_VERSION}.exe"
    sha = hashlib.sha256(b"installer").hexdigest()
    return {
        "schema_version": 1,
        "candidate_version": APP_VERSION,
        "candidate_manifest_path": (
            Path(candidate_manifest_path).as_posix()
            if candidate_manifest_path is not None
            else "artifacts/v2-final-candidate-release.json"
        ),
        "candidate_installer_name": installer_name,
        "candidate_installer_sha256": sha,
        "gates": {
            "real_s3_acceptance": {
                "status": "passed",
                "completed_at_utc": "2026-06-21T12:00:00Z",
                "test_prefix": "v2-cutover-acceptance/test/",
                "scenarios_passed": ["single_copc_upload"],
                "projects_index_verified": True,
                "metadata_verified": True,
                "cleanup_verified": True,
            },
            "github_asset_sha": {
                "status": "passed",
                "repo": "Preloi/Dronautix-Pointcloud-Uploader",
                "release_tag": f"v{APP_VERSION}",
                "asset_name": installer_name,
                "manifest_sha256": sha,
                "asset_sha256": sha,
                "asset_size": len(b"installer"),
                "match": True,
            },
            "legacy_installed_update": {
                "status": "passed",
                "completed_at_utc": "2026-06-21T12:30:00Z",
                "from_version": "1.7.10",
                "to_version": APP_VERSION,
                "installed_app_id_preserved": True,
                "update_prompt_seen": True,
                "download_sha_verified": True,
                "post_update_launch_ok": True,
                "legacy_config_or_keyring_available": True,
            },
        },
    }


def _write_matching_candidate_manifest(path, evidence):
    contract = build_final_packaging_contract(evidence["candidate_installer_sha256"])
    contract["isolated_paths"]["manifest_path"] = Path(path).as_posix()
    write_candidate_manifest(contract, path)


def _seed_current_single_copc_v2_and_golden(tmp_path, manifest_path, *, v2_root):
    generated_root = tmp_path / "generated_current_v2"
    result = generate_v2_golden_outputs(
        manifest_path,
        output_root=generated_root,
        scenario_id="single_copc_upload",
    )[0]
    raw_text = (result.output_dir / "projects_index.json").read_text(encoding="utf-8")

    captured_dir = tmp_path / "captured" / "single_copc_upload"
    normalized_dir = tmp_path / "normalized" / "single_copc_upload"
    v2_dir = v2_root / "single_copc_upload"
    captured_dir.mkdir(parents=True)
    normalized_dir.mkdir(parents=True)
    v2_dir.mkdir(parents=True)

    (captured_dir / "projects_index.json").write_text(raw_text, encoding="utf-8")
    (normalized_dir / "projects_index.normalized.json").write_text(
        normalize_capture_text("projects_index.json", raw_text),
        encoding="utf-8",
    )
    (v2_dir / "projects_index.json").write_text(raw_text, encoding="utf-8")


def _filled_multi_replace_provenance(captured_files):
    return {
        "scenario_id": "multi_replace",
        "legacy_app_version": "1.7.12",
        "legacy_git_ref": "dronautix/develop@test",
        "captured_at_utc": "2026-06-21T00:00:00Z",
        "captured_files": captured_files,
        "inputs": {
            "project_id": "replace-multi",
            "original_link": "https://pointcloud.dronautix.at/index.html?id=replace-multi",
            "original_disabled_state": "disabled_projects",
            "sources": [
                {
                    "source": "Scan.copc.laz",
                    "source_type": "raw_file",
                    "format": "copc",
                    "name": "Scan",
                    "slug": "scan",
                }
            ],
        },
        "observed_contracts": {
            "converter_command": "[converter_path, source_file, '-o', output_dir, '--overwrite']",
            "converter_working_directory": "dirname(converter_path)",
            "s3_extra_args": "ContentType plus no-cache CacheControl",
            "operation_order": ["prepare", "upload", "save_index", "delete_obsolete_keys"],
            "index_update_order": "save index before deleting old keys",
            "crs_behavior": "project CRS cleared on mismatch; pointcloud CRS kept",
            "cleanup_behavior": "old keys deleted after index save",
            "rollback_behavior": "uploaded ledger deleted before index save on failure",
            "common_crs_decision": "mismatch",
            "stale_project_crs_on_mismatch": "removed",
            "existing_keys": ["pointclouds/old/cloud.js"],
            "uploaded_keys": ["pointclouds/new/cloud.js"],
            "obsolete_keys": ["pointclouds/old/cloud.js"],
        },
    }


def _filled_disabled_link_state_provenance(captured_files):
    return {
        "scenario_id": "disabled_link_state",
        "legacy_app_version": "1.7.12",
        "legacy_git_ref": "dronautix/develop@test",
        "captured_at_utc": "2026-06-21T00:00:00Z",
        "captured_files": captured_files,
        "inputs": {
            "project_id": "disabled-target",
            "link": "https://pointcloud.dronautix.at/index.html?id=disabled-target",
            "operation_sequence": ["disable", "rename", "replace"],
            "disabled_state_before": {"list_membership": "disabled_projects"},
            "disabled_state_after": {"list_membership": "disabled_projects"},
        },
        "observed_contracts": {
            "disabled_link_behavior": "disabled_at remains unchanged across rename/replace",
            "replace_disabled_membership": "disabled_projects",
            "rename_disabled_membership": "disabled_projects",
            "delete_behavior": "deleted_projects entry keeps original link",
            "deleted_projects_entry": {
                "deleted_at": "2026-06-21T12:00:00",
                "original_link": "https://pointcloud.dronautix.at/index.html?id=disabled-target",
            },
        },
    }
