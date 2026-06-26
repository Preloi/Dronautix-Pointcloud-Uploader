import json

from dronautix_uploader.core.golden_capture import (
    build_golden_capture_plan,
    build_golden_provenance_template,
    initialize_golden_capture_scenario,
    validate_golden_provenance,
)
from tools.init_golden_capture import main as init_golden_capture
from tools.plan_golden_captures import main as plan_golden_captures


def test_golden_capture_plan_reports_pending_and_missing_normalized_files(tmp_path):
    manifest_path, captured_root, _normalized_root = _write_manifest(
        tmp_path,
        provenance_file="provenance.json",
        scenarios=[
            {"id": "pending", "description": "Pending.", "required_files": ["projects_index.json"]},
            {"id": "captured", "description": "Captured.", "required_files": ["metadata.json"]},
        ],
    )
    captured_dir = captured_root / "captured"
    captured_dir.mkdir(parents=True)
    (captured_dir / "metadata.json").write_text('{"points": 1}', encoding="utf-8")

    plan = build_golden_capture_plan(manifest_path)
    scenarios = {scenario.scenario_id: scenario for scenario in plan.scenarios}

    assert plan.ready_count == 0
    assert scenarios["pending"].status == "pending"
    assert scenarios["captured"].status == "needs_normalization"
    assert scenarios["captured"].missing_normalized_files == ("metadata.normalized.json",)
    assert scenarios["captured"].missing_provenance is True


def test_golden_capture_plan_reports_ready_and_drift(tmp_path):
    manifest_path, captured_root, normalized_root = _write_manifest(
        tmp_path,
        provenance_file="provenance.json",
        scenarios=[
            {"id": "ready", "description": "Ready.", "required_files": ["projects_index.json"]},
            {"id": "drift", "description": "Drift.", "required_files": ["metadata.json"]},
        ],
    )
    ready_captured = captured_root / "ready"
    ready_normalized = normalized_root / "ready"
    drift_captured = captured_root / "drift"
    drift_normalized = normalized_root / "drift"
    for path in (ready_captured, ready_normalized, drift_captured, drift_normalized):
        path.mkdir(parents=True)
    (ready_captured / "projects_index.json").write_text('{"projekt":"Muenchen"}', encoding="utf-8")
    (ready_normalized / "projects_index.normalized.json").write_text(
        '{\n  "projekt": "Muenchen"\n}\n',
        encoding="utf-8",
    )
    _write_valid_provenance(ready_captured / "provenance.json", "ready", ["projects_index.json"])
    (drift_captured / "metadata.json").write_text('{"points": 1}', encoding="utf-8")
    (drift_normalized / "metadata.normalized.json").write_text('{"points": 2}\n', encoding="utf-8")
    _write_valid_provenance(drift_captured / "provenance.json", "drift", ["metadata.json"])

    plan = build_golden_capture_plan(manifest_path)
    scenarios = {scenario.scenario_id: scenario for scenario in plan.scenarios}

    assert plan.ready_count == 1
    assert scenarios["ready"].status == "ready"
    assert scenarios["drift"].status == "drift"
    assert scenarios["drift"].drifted_files == ("metadata.json",)


def test_golden_capture_plan_reports_invalid_provenance(tmp_path):
    manifest_path, captured_root, normalized_root = _write_manifest(
        tmp_path,
        provenance_file="provenance.json",
        scenarios=[
            {"id": "invalid", "description": "Invalid.", "required_files": ["metadata.json"]},
        ],
    )
    captured_dir = captured_root / "invalid"
    normalized_dir = normalized_root / "invalid"
    captured_dir.mkdir(parents=True)
    normalized_dir.mkdir(parents=True)
    (captured_dir / "metadata.json").write_text('{"points": 1}', encoding="utf-8")
    (normalized_dir / "metadata.normalized.json").write_text('{\n  "points": 1\n}\n', encoding="utf-8")
    (captured_dir / "provenance.json").write_text('{"scenario_id":"other"}', encoding="utf-8")

    scenario = build_golden_capture_plan(manifest_path).scenarios[0]

    assert scenario.status == "invalid_provenance"
    assert any("scenario_id" in issue for issue in scenario.provenance_issues)


def test_plan_golden_captures_cli_can_be_strict(tmp_path, capsys):
    manifest_path, _captured_root, _normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {"id": "pending", "description": "Pending.", "required_files": ["projects_index.json"]},
        ],
    )

    exit_code = plan_golden_captures(["--manifest", str(manifest_path), "--strict"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Golden capture plan: 0/1 ready" in captured.out
    assert "[PENDING] pending" in captured.out
    assert "next: python tools/init_golden_capture.py pending" in captured.out
    assert "--write-template" not in captured.out


def test_plan_golden_captures_cli_prints_v2_followup_for_ready_scenario(tmp_path, capsys):
    manifest_path, captured_root, normalized_root = _write_manifest(
        tmp_path,
        provenance_file="provenance.json",
        scenarios=[
            {"id": "ready", "description": "Ready.", "required_files": ["projects_index.json"]},
        ],
    )
    captured_dir = captured_root / "ready"
    normalized_dir = normalized_root / "ready"
    captured_dir.mkdir(parents=True)
    normalized_dir.mkdir(parents=True)
    (captured_dir / "projects_index.json").write_text('{"value":1}', encoding="utf-8")
    (normalized_dir / "projects_index.normalized.json").write_text('{\n  "value": 1\n}\n', encoding="utf-8")
    _write_valid_provenance(captured_dir / "provenance.json", "ready", ["projects_index.json"])

    exit_code = plan_golden_captures(["--manifest", str(manifest_path), "--scenario", "ready"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[READY] ready" in captured.out
    assert "next: python tools/import_v2_golden_output.py ready" in captured.out
    assert "then: python tools/compare_v2_to_golden.py --scenario ready" in captured.out


def test_build_golden_provenance_template_tracks_manifest_required_files(tmp_path):
    manifest_path, _captured_root, _normalized_root = _write_manifest(
        tmp_path,
        provenance_file="provenance.json",
        scenarios=[
            {
                "id": "multi_replace",
                "description": "Complete replacement.",
                "required_files": ["projects_index.json", "metadata.json", "cloud.js"],
            },
        ],
    )

    template = build_golden_provenance_template(
        manifest_path,
        scenario_id="multi_replace",
        legacy_app_version="1.7.10",
        legacy_git_ref="dronautix/develop@test",
        captured_at_utc="2026-06-21T00:00:00Z",
    )

    assert template["scenario_id"] == "multi_replace"
    assert template["captured_files"] == ["projects_index.json", "metadata.json", "cloud.js"]
    assert template["inputs"]["sources"][0] == {
        "source": "",
        "source_type": "",
        "format": "",
        "name": "",
        "slug": "",
    }
    assert template["inputs"]["original_link"] == ""
    assert "operation_order" in template["observed_contracts"]
    assert template["observed_contracts"]["delete_timing"] == "before_index_save"
    assert template["observed_contracts"]["legacy_uploaded_key_rollback"] == "none_observed"
    assert validate_golden_provenance(template, scenario_id="multi_replace") == ()


def test_build_golden_provenance_template_includes_disabled_link_state_fields(tmp_path):
    manifest_path, _captured_root, _normalized_root = _write_manifest(
        tmp_path,
        provenance_file="provenance.json",
        scenarios=[
            {
                "id": "disabled_link_state",
                "description": "Disabled state.",
                "required_files": ["projects_index.json", "deleted_projects.json"],
            },
        ],
    )

    template = build_golden_provenance_template(
        manifest_path,
        scenario_id="disabled_link_state",
        legacy_app_version="1.7.10",
        legacy_git_ref="dronautix/develop@test",
        captured_at_utc="2026-06-21T00:00:00Z",
    )

    assert template["inputs"]["disabled_state_before"]["list_membership"] == ""
    assert template["inputs"]["disabled_state_after"]["link_disabled"] == ""
    assert template["observed_contracts"]["replace_disabled_membership"] == ""
    assert template["observed_contracts"]["deleted_projects_entry"] == {
        "deleted_at": "",
        "original_link": "",
    }
    assert validate_golden_provenance(template, scenario_id="disabled_link_state") == ()


def test_initialize_golden_capture_scenario_creates_dirs_and_keeps_existing_provenance(tmp_path):
    manifest_path, captured_root, normalized_root = _write_manifest(
        tmp_path,
        provenance_file="provenance.json",
        scenarios=[
            {"id": "multi_replace", "description": "Multi.", "required_files": ["projects_index.json"]},
        ],
    )

    first = initialize_golden_capture_scenario(
        manifest_path,
        scenario_id="multi_replace",
        legacy_app_version="1.7.10",
        legacy_git_ref="dronautix/develop@test",
        captured_at_utc="2026-06-21T00:00:00Z",
    )
    provenance_path = captured_root / "multi_replace" / "provenance.json"
    first_text = provenance_path.read_text(encoding="utf-8")
    second = initialize_golden_capture_scenario(
        manifest_path,
        scenario_id="multi_replace",
        legacy_app_version="1.7.11",
        legacy_git_ref="dronautix/develop@newer",
        captured_at_utc="2026-06-22T00:00:00Z",
    )

    assert first.written is True
    assert second.written is False
    assert first.captured_dir == captured_root / "multi_replace"
    assert first.normalized_dir == normalized_root / "multi_replace"
    assert provenance_path.read_text(encoding="utf-8") == first_text


def test_init_golden_capture_cli_writes_provenance_template(tmp_path, capsys):
    manifest_path, captured_root, normalized_root = _write_manifest(
        tmp_path,
        provenance_file="provenance.json",
        scenarios=[
            {"id": "multi_replace", "description": "Multi.", "required_files": ["projects_index.json"]},
        ],
    )

    exit_code = init_golden_capture(
        [
            "multi_replace",
            "--manifest",
            str(manifest_path),
            "--legacy-app-version",
            "1.7.10",
            "--legacy-git-ref",
            "dronautix/develop@test",
            "--captured-at-utc",
            "2026-06-21T00:00:00Z",
        ]
    )

    captured = capsys.readouterr()
    provenance = json.loads((captured_root / "multi_replace" / "provenance.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert "Initialized Golden capture: multi_replace" in captured.out
    assert (captured_root / "multi_replace").is_dir()
    assert (normalized_root / "multi_replace").is_dir()
    assert provenance["legacy_app_version"] == "1.7.10"


def test_init_golden_capture_cli_can_initialize_all_manifest_scenarios(tmp_path, capsys):
    manifest_path, captured_root, normalized_root = _write_manifest(
        tmp_path,
        provenance_file="provenance.json",
        scenarios=[
            {"id": "single_potree_upload", "description": "Single.", "required_files": ["projects_index.json"]},
            {"id": "multi_replace", "description": "Multi.", "required_files": ["projects_index.json"]},
        ],
    )

    exit_code = init_golden_capture(
        [
            "--all",
            "--manifest",
            str(manifest_path),
            "--legacy-app-version",
            "1.7.10",
            "--legacy-git-ref",
            "dronautix/develop@test",
            "--captured-at-utc",
            "2026-06-21T00:00:00Z",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Initialized Golden captures: 2 scenarios (2 provenance written)" in captured.out
    for scenario_id in ("single_potree_upload", "multi_replace"):
        assert (captured_root / scenario_id / "provenance.json").is_file()
        assert (normalized_root / scenario_id).is_dir()


def _write_manifest(tmp_path, *, scenarios, provenance_file=""):
    captured_root = tmp_path / "captured"
    normalized_root = tmp_path / "normalized"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "captured_root": str(captured_root),
                "normalized_root": str(normalized_root),
                "provenance_file": provenance_file,
                "status": "pending_legacy_capture",
                "scenarios": scenarios,
            }
        ),
        encoding="utf-8",
    )
    return manifest_path, captured_root, normalized_root


def _write_valid_provenance(path, scenario_id, captured_files):
    path.write_text(
        json.dumps(
            {
                "scenario_id": scenario_id,
                "legacy_app_version": "1.7.10",
                "legacy_git_ref": "dronautix/develop@test",
                "captured_at_utc": "2026-06-21T00:00:00Z",
                "captured_files": captured_files,
                "inputs": {"notes": "test"},
                "observed_contracts": {"operation_order": ["capture"]},
            }
        ),
        encoding="utf-8",
    )
