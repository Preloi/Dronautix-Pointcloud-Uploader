import json

import pytest

from dronautix_uploader.core.golden_capture import (
    build_golden_capture_plan,
    import_golden_capture_files,
    normalize_captured_golden_files,
    normalized_capture_file_name,
)
from tools.import_golden_capture import main as import_golden_capture_cli


def test_normalize_captured_golden_files_writes_normalized_snapshots(tmp_path):
    manifest_path, captured_root, normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {
                "id": "single_copc_upload",
                "description": "Single COPC direct upload.",
                "required_files": ["projects_index.json", "cloud.js"],
            }
        ],
    )
    captured_dir = captured_root / "single_copc_upload"
    captured_dir.mkdir(parents=True)
    (captured_dir / "projects_index.json").write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "abc12345",
                        "projekt": "Muenchen",
                        "datum": "2026-06-21T12:00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (captured_dir / "cloud.js").write_text(
        'cloud.js = {"spacing": 0.123456789, "hierarchy": {"firstChunkSize": 10}};',
        encoding="utf-8",
    )

    results = normalize_captured_golden_files(manifest_path)

    assert len(results) == 1
    assert results[0].scenario_id == "single_copc_upload"
    assert results[0].status == "normalized"
    assert len(results[0].files) == 2
    assert (normalized_root / "single_copc_upload" / "projects_index.normalized.json").read_text(
        encoding="utf-8"
    ) == (
        '{\n'
        '  "projects": [\n'
        "    {\n"
        '      "datum": "<volatile>",\n'
        '      "id": "<id>",\n'
        '      "projekt": "Muenchen"\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )
    assert (normalized_root / "single_copc_upload" / "cloud.normalized.json").read_text(encoding="utf-8") == (
        '{\n'
        '  "hierarchy": {\n'
        '    "firstChunkSize": 10\n'
        "  },\n"
        '  "spacing": 0.12345679\n'
        "}\n"
    )


def test_normalize_captured_golden_files_can_select_one_scenario(tmp_path):
    manifest_path, captured_root, normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {"id": "missing", "description": "Missing.", "required_files": ["projects_index.json"]},
            {"id": "captured", "description": "Captured.", "required_files": ["metadata.json"]},
        ],
    )
    captured_dir = captured_root / "captured"
    captured_dir.mkdir(parents=True)
    (captured_dir / "metadata.json").write_text('{"points": 1}', encoding="utf-8")

    results = normalize_captured_golden_files(manifest_path, scenario_id="captured")

    assert [result.scenario_id for result in results] == ["captured"]
    assert results[0].status == "normalized"
    assert (normalized_root / "captured" / "metadata.normalized.json").is_file()


def test_normalize_captured_golden_files_reports_pending_when_capture_dir_is_missing(tmp_path):
    manifest_path, _captured_root, normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {"id": "pending", "description": "Pending.", "required_files": ["projects_index.json"]},
        ],
    )

    results = normalize_captured_golden_files(manifest_path)

    assert results[0].scenario_id == "pending"
    assert results[0].status == "pending"
    assert not (normalized_root / "pending").exists()


def test_normalize_captured_golden_files_fails_for_incomplete_capture(tmp_path):
    manifest_path, captured_root, _normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {"id": "incomplete", "description": "Incomplete.", "required_files": ["metadata.json"]},
        ],
    )
    (captured_root / "incomplete").mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="metadata.json"):
        normalize_captured_golden_files(manifest_path)


def test_normalized_capture_file_name_matches_golden_scaffolding_names():
    assert normalized_capture_file_name("cloud.js") == "cloud.normalized.json"
    assert normalized_capture_file_name("projects_index.json") == "projects_index.normalized.json"


def test_import_golden_capture_files_copies_required_files_without_normalizing(tmp_path):
    manifest_path, captured_root, normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {
                "id": "single_upload",
                "description": "Single upload.",
                "required_files": ["projects_index.json", "metadata.json"],
            }
        ],
    )
    source_dir = tmp_path / "legacy-output"
    source_dir.mkdir()
    projects_text = '{"projects":[{"projekt":"Köln"}]}'
    metadata_text = '{"points": 123}'
    (source_dir / "projects_index.json").write_text(projects_text, encoding="utf-8")
    (source_dir / "metadata.json").write_text(metadata_text, encoding="utf-8")

    result = import_golden_capture_files(manifest_path, scenario_id="single_upload", source_dir=source_dir)

    assert result.scenario_id == "single_upload"
    assert [file_result.captured_path.name for file_result in result.copied_files] == [
        "projects_index.json",
        "metadata.json",
    ]
    assert (captured_root / "single_upload" / "projects_index.json").read_text(encoding="utf-8") == projects_text
    assert (captured_root / "single_upload" / "metadata.json").read_text(encoding="utf-8") == metadata_text
    assert not (normalized_root / "single_upload").exists()


def test_import_golden_capture_files_fails_before_partial_copy_when_required_file_is_missing(tmp_path):
    manifest_path, captured_root, _normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {
                "id": "multi_upload",
                "description": "Multi upload.",
                "required_files": ["projects_index.json", "metadata.json"],
            }
        ],
    )
    source_dir = tmp_path / "legacy-output"
    source_dir.mkdir()
    (source_dir / "projects_index.json").write_text('{"projects":[]}', encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="metadata.json"):
        import_golden_capture_files(manifest_path, scenario_id="multi_upload", source_dir=source_dir)

    assert not (captured_root / "multi_upload").exists()


def test_import_golden_capture_files_refuses_existing_target_without_overwrite(tmp_path):
    manifest_path, captured_root, _normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {
                "id": "replace",
                "description": "Replace.",
                "required_files": ["projects_index.json", "metadata.json"],
            }
        ],
    )
    source_dir = tmp_path / "legacy-output"
    source_dir.mkdir()
    (source_dir / "projects_index.json").write_text('{"projects":[{"projekt":"new"}]}', encoding="utf-8")
    (source_dir / "metadata.json").write_text('{"points": 10}', encoding="utf-8")
    captured_dir = captured_root / "replace"
    captured_dir.mkdir(parents=True)
    existing_file = captured_dir / "projects_index.json"
    existing_file.write_text('{"projects":[{"projekt":"old"}]}', encoding="utf-8")

    with pytest.raises(FileExistsError, match="projects_index.json"):
        import_golden_capture_files(manifest_path, scenario_id="replace", source_dir=source_dir)

    assert existing_file.read_text(encoding="utf-8") == '{"projects":[{"projekt":"old"}]}'
    assert not (captured_dir / "metadata.json").exists()


def test_import_golden_capture_files_overwrites_when_requested(tmp_path):
    manifest_path, captured_root, _normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {
                "id": "replace",
                "description": "Replace.",
                "required_files": ["projects_index.json"],
            }
        ],
    )
    source_dir = tmp_path / "legacy-output"
    source_dir.mkdir()
    (source_dir / "projects_index.json").write_text('{"projects":[{"projekt":"new"}]}', encoding="utf-8")
    captured_dir = captured_root / "replace"
    captured_dir.mkdir(parents=True)
    (captured_dir / "projects_index.json").write_text('{"projects":[{"projekt":"old"}]}', encoding="utf-8")

    import_golden_capture_files(manifest_path, scenario_id="replace", source_dir=source_dir, overwrite=True)

    assert (captured_dir / "projects_index.json").read_text(encoding="utf-8") == '{"projects":[{"projekt":"new"}]}'


def test_import_golden_capture_files_preflights_invalid_json_before_copying(tmp_path):
    manifest_path, captured_root, _normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {
                "id": "single_upload",
                "description": "Single upload.",
                "required_files": ["projects_index.json", "metadata.json"],
            }
        ],
    )
    source_dir = tmp_path / "legacy-output"
    source_dir.mkdir()
    (source_dir / "projects_index.json").write_text('{"projects":[]}', encoding="utf-8")
    (source_dir / "metadata.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        import_golden_capture_files(manifest_path, scenario_id="single_upload", source_dir=source_dir)

    assert not (captured_root / "single_upload").exists()


def test_import_golden_capture_files_rejects_generated_output_sources(tmp_path):
    v2_output_root = tmp_path / "v2_outputs"
    manifest_path, _captured_root, _normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {
                "id": "single_upload",
                "description": "Single upload.",
                "required_files": ["projects_index.json"],
            }
        ],
        extra_manifest={"v2_output_root": str(v2_output_root)},
    )
    source_dir = v2_output_root / "single_upload"
    source_dir.mkdir(parents=True)
    (source_dir / "projects_index.json").write_text('{"projects":[]}', encoding="utf-8")

    with pytest.raises(ValueError, match="generated Golden output root"):
        import_golden_capture_files(manifest_path, scenario_id="single_upload", source_dir=source_dir)


def test_import_golden_capture_files_rejects_v2_generated_artifact_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manifest_path, _captured_root, _normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {
                "id": "single_upload",
                "description": "Single upload.",
                "required_files": ["projects_index.json"],
            }
        ],
    )
    source_dir = tmp_path / "artifacts" / "v2-golden-generated" / "single_upload"
    source_dir.mkdir(parents=True)
    (source_dir / "projects_index.json").write_text('{"projects":[]}', encoding="utf-8")

    with pytest.raises(ValueError, match="generated Golden output root"):
        import_golden_capture_files(manifest_path, scenario_id="single_upload", source_dir=source_dir)


def test_import_golden_capture_files_rejects_captured_root_source(tmp_path):
    manifest_path, captured_root, _normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {
                "id": "single_upload",
                "description": "Single upload.",
                "required_files": ["projects_index.json"],
            }
        ],
    )
    source_dir = captured_root / "staging"
    source_dir.mkdir(parents=True)
    (source_dir / "projects_index.json").write_text('{"projects":[]}', encoding="utf-8")

    with pytest.raises(ValueError, match="generated Golden output root"):
        import_golden_capture_files(manifest_path, scenario_id="single_upload", source_dir=source_dir)


def test_import_golden_capture_files_rejects_unsafe_scenario_id(tmp_path):
    manifest_path, _captured_root, _normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {
                "id": "../unsafe",
                "description": "Unsafe.",
                "required_files": ["projects_index.json"],
            }
        ],
    )
    source_dir = tmp_path / "legacy-output"
    source_dir.mkdir()
    (source_dir / "projects_index.json").write_text('{"projects":[]}', encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe id"):
        import_golden_capture_files(manifest_path, scenario_id="../unsafe", source_dir=source_dir)


def test_import_golden_capture_files_leaves_plan_needing_normalization(tmp_path):
    manifest_path, _captured_root, _normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {
                "id": "single_upload",
                "description": "Single upload.",
                "required_files": ["projects_index.json"],
            }
        ],
    )
    source_dir = tmp_path / "legacy-output"
    source_dir.mkdir()
    (source_dir / "projects_index.json").write_text('{"projects":[]}', encoding="utf-8")

    import_golden_capture_files(manifest_path, scenario_id="single_upload", source_dir=source_dir)

    plan = build_golden_capture_plan(manifest_path, scenario_id="single_upload")
    assert plan.scenarios[0].status == "needs_normalization"


def test_import_golden_capture_cli_copies_required_files(tmp_path, capsys):
    manifest_path, captured_root, _normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {
                "id": "single_copc_upload",
                "description": "Single COPC.",
                "required_files": ["projects_index.json"],
            }
        ],
    )
    source_dir = tmp_path / "legacy-output"
    source_dir.mkdir()
    (source_dir / "projects_index.json").write_text('{"projects":[]}', encoding="utf-8")

    exit_code = import_golden_capture_cli(
        ["single_copc_upload", "--source-dir", str(source_dir), "--manifest", str(manifest_path)]
    )

    assert exit_code == 0
    assert (captured_root / "single_copc_upload" / "projects_index.json").is_file()
    assert "Imported Golden capture: single_copc_upload" in capsys.readouterr().out


def test_import_golden_capture_cli_dry_run_writes_nothing(tmp_path, capsys):
    manifest_path, captured_root, _normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {
                "id": "single_copc_upload",
                "description": "Single COPC.",
                "required_files": ["projects_index.json"],
            }
        ],
    )
    source_dir = tmp_path / "legacy-output"
    source_dir.mkdir()
    (source_dir / "projects_index.json").write_text('{"projects":[]}', encoding="utf-8")

    exit_code = import_golden_capture_cli(
        ["single_copc_upload", "--source-dir", str(source_dir), "--manifest", str(manifest_path), "--dry-run"]
    )

    assert exit_code == 0
    assert not (captured_root / "single_copc_upload").exists()
    output = capsys.readouterr().out
    assert "Planned Golden capture import: single_copc_upload" in output
    assert "no files written" in output


def test_import_golden_capture_cli_can_import_all_from_source_root(tmp_path, capsys):
    manifest_path, captured_root, _normalized_root = _write_manifest(
        tmp_path,
        scenarios=[
            {
                "id": "single_copc_upload",
                "description": "Single COPC.",
                "required_files": ["projects_index.json"],
            },
            {
                "id": "delete_project",
                "description": "Delete.",
                "required_files": ["projects_index.json", "deleted_projects.json"],
            },
        ],
    )
    source_root = tmp_path / "legacy-output"
    single_source = source_root / "single_copc_upload"
    delete_source = source_root / "delete_project"
    single_source.mkdir(parents=True)
    delete_source.mkdir(parents=True)
    (single_source / "projects_index.json").write_text('{"projects":[]}', encoding="utf-8")
    (delete_source / "projects_index.json").write_text('{"projects":[]}', encoding="utf-8")
    (delete_source / "deleted_projects.json").write_text("[]", encoding="utf-8")

    exit_code = import_golden_capture_cli(
        ["--all", "--source-root", str(source_root), "--manifest", str(manifest_path)]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Imported Golden captures: 2 scenarios" in output
    assert (captured_root / "single_copc_upload" / "projects_index.json").is_file()
    assert (captured_root / "delete_project" / "deleted_projects.json").is_file()


def _write_manifest(tmp_path, *, scenarios, extra_manifest=None):
    captured_root = tmp_path / "captured"
    normalized_root = tmp_path / "captured_normalized"
    manifest_path = tmp_path / "manifest.json"
    manifest = {
        "schema_version": 1,
        "captured_root": str(captured_root),
        "normalized_root": str(normalized_root),
        "status": "pending_legacy_capture",
        "scenarios": scenarios,
    }
    if extra_manifest:
        manifest.update(extra_manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, captured_root, normalized_root
