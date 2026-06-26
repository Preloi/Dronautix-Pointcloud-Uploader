import json

import pytest

from dronautix_uploader.core.golden_capture import (
    compare_golden_outputs,
    import_v2_golden_output_files,
)
from tools.compare_v2_to_golden import main as compare_v2_to_golden
from tools.import_v2_golden_output import main as import_v2_golden_output_cli


def test_compare_golden_outputs_accepts_matching_normalized_v2_output(tmp_path):
    manifest_path, normalized_root, v2_root = _write_manifest(
        tmp_path,
        scenarios=[
            {"id": "single_copc_upload", "description": "Single.", "required_files": ["projects_index.json"]},
        ],
    )
    _write_text(normalized_root / "single_copc_upload" / "projects_index.normalized.json", '{\n  "value": 1\n}\n')
    _write_text(v2_root / "single_copc_upload" / "projects_index.json", '{"value":1}')

    report = compare_golden_outputs(manifest_path)

    assert report.ready
    assert report.ready_count == 1
    assert report.scenarios[0].files[0].status == "match"


def test_compare_golden_outputs_matches_projects_metadata_and_cloudjs_with_volatile_values(tmp_path):
    manifest_path, normalized_root, v2_root = _write_manifest(
        tmp_path,
        scenarios=[
            {
                "id": "multi_replace",
                "description": "Multi.",
                "required_files": ["projects_index.json", "metadata.json", "cloud.js"],
            },
        ],
    )
    _write_text(
        normalized_root / "multi_replace" / "projects_index.normalized.json",
        '{\n'
        '  "projects": [\n'
        "    {\n"
        '      "datum": "<volatile>",\n'
        '      "id": "<id>",\n'
        '      "viewer_path": "viewer/projekt"\n'
        "    }\n"
        "  ]\n"
        "}\n",
    )
    _write_text(
        normalized_root / "multi_replace" / "metadata.normalized.json",
        '{\n'
        '  "crs": "EPSG:25832",\n'
        '  "spacing": 0.12345679\n'
        "}\n",
    )
    _write_text(
        normalized_root / "multi_replace" / "cloud.normalized.json",
        '{\n'
        '  "projection": "EPSG:25832",\n'
        '  "spacing": 0.12345679\n'
        "}\n",
    )
    _write_text(
        v2_root / "multi_replace" / "projects_index.json",
        '{"projects":[{"viewer_path":"viewer/projekt","id":"abcd1234","datum":"2026-06-21T12:00:00Z"}]}',
    )
    _write_text(
        v2_root / "multi_replace" / "metadata.json",
        '{"spacing":0.123456789,"crs":"EPSG:25832"}',
    )
    _write_text(
        v2_root / "multi_replace" / "cloud.js",
        'cloud.js = {"spacing":0.123456789,"projection":"EPSG:25832"};',
    )

    report = compare_golden_outputs(manifest_path)

    assert report.ready
    assert [file_result.status for file_result in report.scenarios[0].files] == ["match", "match", "match"]


def test_compare_golden_outputs_reports_missing_v2_output_and_missing_golden(tmp_path):
    manifest_path, normalized_root, _v2_root = _write_manifest(
        tmp_path,
        scenarios=[
            {
                "id": "single_potree_upload",
                "description": "Single.",
                "required_files": ["projects_index.json", "metadata.json"],
            },
        ],
    )
    _write_text(normalized_root / "single_potree_upload" / "projects_index.normalized.json", '{"value": 1}\n')

    report = compare_golden_outputs(manifest_path)
    statuses = {file_result.file_name: file_result.status for file_result in report.scenarios[0].files}

    assert not report.ready
    assert statuses == {
        "projects_index.json": "missing_actual",
        "metadata.json": "missing_golden",
    }


def test_compare_golden_outputs_reports_mismatch_and_normalizes_cloudjs(tmp_path):
    manifest_path, normalized_root, v2_root = _write_manifest(
        tmp_path,
        scenarios=[
            {"id": "vertical_crs_upload", "description": "CRS.", "required_files": ["metadata.json", "cloud.js"]},
        ],
    )
    _write_text(normalized_root / "vertical_crs_upload" / "metadata.normalized.json", '{"points": 2}\n')
    _write_text(normalized_root / "vertical_crs_upload" / "cloud.normalized.json", '{\n  "spacing": 0.12345679\n}\n')
    _write_text(v2_root / "vertical_crs_upload" / "metadata.json", '{"points": 1}')
    _write_text(v2_root / "vertical_crs_upload" / "cloud.js", 'cloud.js = {"spacing": 0.123456789};')

    report = compare_golden_outputs(manifest_path)
    statuses = {file_result.file_name: file_result.status for file_result in report.scenarios[0].files}

    assert not report.ready
    assert statuses == {
        "metadata.json": "mismatch",
        "cloud.js": "match",
    }


def test_compare_v2_to_golden_cli_strict_returns_nonzero_for_blocked_comparison(tmp_path, capsys):
    manifest_path, normalized_root, _v2_root = _write_manifest(
        tmp_path,
        scenarios=[
            {"id": "multi_replace", "description": "Multi.", "required_files": ["projects_index.json"]},
        ],
    )
    _write_text(normalized_root / "multi_replace" / "projects_index.normalized.json", '{"value": 1}\n')

    exit_code = compare_v2_to_golden(["--manifest", str(manifest_path), "--strict"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "V2 vs Golden comparison: 0/1 scenarios match" in captured.out
    assert "[MISSING_ACTUAL] projects_index.json" in captured.out


def test_compare_v2_to_golden_cli_strict_returns_zero_for_matching_comparison(tmp_path, capsys):
    manifest_path, normalized_root, v2_root = _write_manifest(
        tmp_path,
        scenarios=[
            {"id": "single_copc_upload", "description": "Single.", "required_files": ["projects_index.json"]},
        ],
    )
    _write_text(normalized_root / "single_copc_upload" / "projects_index.normalized.json", '{\n  "value": 1\n}\n')
    _write_text(v2_root / "single_copc_upload" / "projects_index.json", '{"value":1}')

    exit_code = compare_v2_to_golden(["--manifest", str(manifest_path), "--strict"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "V2 vs Golden comparison: 1/1 scenarios match" in captured.out
    assert "[MATCH] single_copc_upload" in captured.out


def test_import_v2_golden_output_files_copies_required_files_and_feeds_comparison(tmp_path):
    manifest_path, normalized_root, v2_root = _write_manifest(
        tmp_path,
        scenarios=[
            {"id": "single_copc_upload", "description": "Single.", "required_files": ["projects_index.json"]},
        ],
    )
    _write_text(normalized_root / "single_copc_upload" / "projects_index.normalized.json", '{\n  "value": 1\n}\n')
    source_dir = tmp_path / "v2-staging"
    _write_text(source_dir / "projects_index.json", '{"value":1}')

    result = import_v2_golden_output_files(
        manifest_path,
        scenario_id="single_copc_upload",
        source_dir=source_dir,
    )

    assert result.scenario_id == "single_copc_upload"
    assert result.output_dir == v2_root / "single_copc_upload"
    assert (v2_root / "single_copc_upload" / "projects_index.json").read_text(encoding="utf-8") == '{"value":1}'
    assert compare_golden_outputs(manifest_path).ready


def test_import_v2_golden_output_files_refuses_legacy_capture_source(tmp_path):
    manifest_path, _normalized_root, _v2_root = _write_manifest(
        tmp_path,
        scenarios=[
            {"id": "single_copc_upload", "description": "Single.", "required_files": ["projects_index.json"]},
        ],
    )
    source_dir = tmp_path / "captured" / "single_copc_upload"
    _write_text(source_dir / "projects_index.json", '{"value":1}')

    with pytest.raises(ValueError, match="generated Golden output root"):
        import_v2_golden_output_files(
            manifest_path,
            scenario_id="single_copc_upload",
            source_dir=source_dir,
        )


def test_import_v2_golden_output_files_refuses_existing_target_without_overwrite(tmp_path):
    manifest_path, _normalized_root, v2_root = _write_manifest(
        tmp_path,
        scenarios=[
            {"id": "single_copc_upload", "description": "Single.", "required_files": ["projects_index.json"]},
        ],
    )
    source_dir = tmp_path / "v2-staging"
    _write_text(source_dir / "projects_index.json", '{"value":2}')
    existing_file = v2_root / "single_copc_upload" / "projects_index.json"
    _write_text(existing_file, '{"value":1}')

    with pytest.raises(FileExistsError, match="projects_index.json"):
        import_v2_golden_output_files(
            manifest_path,
            scenario_id="single_copc_upload",
            source_dir=source_dir,
        )

    assert existing_file.read_text(encoding="utf-8") == '{"value":1}'


def test_import_v2_golden_output_cli_dry_run_writes_nothing(tmp_path, capsys):
    manifest_path, _normalized_root, v2_root = _write_manifest(
        tmp_path,
        scenarios=[
            {"id": "single_copc_upload", "description": "Single.", "required_files": ["projects_index.json"]},
        ],
    )
    source_dir = tmp_path / "v2-staging"
    _write_text(source_dir / "projects_index.json", '{"value":1}')

    exit_code = import_v2_golden_output_cli(
        ["single_copc_upload", "--source-dir", str(source_dir), "--manifest", str(manifest_path), "--dry-run"]
    )

    assert exit_code == 0
    assert not (v2_root / "single_copc_upload").exists()
    output = capsys.readouterr().out
    assert "Planned V2 Golden output import: single_copc_upload" in output
    assert "no files written" in output


def _write_manifest(tmp_path, *, scenarios):
    captured_root = tmp_path / "captured"
    normalized_root = tmp_path / "captured_normalized"
    v2_root = tmp_path / "v2_outputs"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "captured_root": str(captured_root),
                "normalized_root": str(normalized_root),
                "v2_output_root": str(v2_root),
                "status": "pending_legacy_capture",
                "scenarios": scenarios,
            }
        ),
        encoding="utf-8",
    )
    return manifest_path, normalized_root, v2_root


def _write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
