import json
from pathlib import Path

from dronautix_uploader.core.constants import S3_CACHE_CONTROL
from dronautix_uploader.core.v2_golden_output import (
    SIDE_EFFECTS_JSON,
    SUPPORTED_V2_GOLDEN_SCENARIOS,
    generate_v2_golden_outputs,
)
from tools.generate_v2_golden_output import main as generate_v2_golden_output_cli


MANIFEST_PATH = Path("tests/golden/manifest.json")


def test_generate_v2_golden_outputs_writes_manifest_required_files(tmp_path):
    results = generate_v2_golden_outputs(MANIFEST_PATH, output_root=tmp_path)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    required_by_id = {
        scenario["id"]: tuple(scenario["required_files"])
        for scenario in manifest["scenarios"]
        if scenario["id"] in SUPPORTED_V2_GOLDEN_SCENARIOS
    }

    assert [result.scenario_id for result in results] == list(SUPPORTED_V2_GOLDEN_SCENARIOS)
    for result in results:
        expected_files = required_by_id[result.scenario_id]
        assert tuple(path.name for path in result.generated_files) == expected_files
        assert {path.name for path in result.output_dir.iterdir()} == {*expected_files, SIDE_EFFECTS_JSON}
        assert result.side_effects_path == result.output_dir / SIDE_EFFECTS_JSON
        assert result.side_effects_path.is_file()

    single_copc_dir = tmp_path / "single_copc_upload"
    assert (single_copc_dir / "projects_index.json").is_file()
    assert not (single_copc_dir / "metadata.json").exists()
    assert not (single_copc_dir / "cloud.js").exists()
    assert not (tmp_path / "_work").exists()


def test_generate_v2_golden_outputs_are_deterministic(tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"

    first = generate_v2_golden_outputs(MANIFEST_PATH, output_root=first_root)
    second = generate_v2_golden_outputs(MANIFEST_PATH, output_root=second_root)

    for first_result, second_result in zip(first, second, strict=True):
        assert first_result.scenario_id == second_result.scenario_id
        for first_file in first_result.generated_files:
            second_file = second_result.output_dir / first_file.name
            assert first_file.read_bytes() == second_file.read_bytes()
        assert first_result.side_effects_path.read_bytes() == (
            second_result.output_dir / SIDE_EFFECTS_JSON
        ).read_bytes()


def test_generate_v2_side_effects_use_stable_contract_fields(tmp_path):
    results = generate_v2_golden_outputs(MANIFEST_PATH, output_root=tmp_path)

    for result in results:
        side_effects = _read_side_effects(result)
        events = side_effects["events"]

        assert side_effects["event_count"] == len(events)
        assert side_effects["summary"]["uploaded_keys"] == side_effects["uploaded_keys"]
        assert side_effects["summary"]["copied_keys"] == side_effects["copied_keys"]
        assert side_effects["summary"]["deleted_keys"] == side_effects["deleted_keys"]
        assert side_effects["summary"]["put_object_keys"] == side_effects["put_object_keys"]
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert all(event["purpose"] for event in events)
        assert not any("size" in event or "body_size" in event for event in events)


def test_generate_v2_vertical_crs_output_includes_index_metadata_and_potree_files(tmp_path):
    result = generate_v2_golden_outputs(
        MANIFEST_PATH,
        output_root=tmp_path,
        scenario_id="vertical_crs_upload",
    )[0]

    projects_index = json.loads((result.output_dir / "projects_index.json").read_text(encoding="utf-8"))
    project = projects_index["projects"][0]
    metadata = json.loads((result.output_dir / "metadata.json").read_text(encoding="utf-8"))
    cloudjs = _read_cloudjs_json(result.output_dir / "cloud.js")

    assert project["crs"] == "EPSG:25832"
    assert project["vertical_crs"] == "EPSG:7837"
    assert project["vertical_epsg"] == "EPSG:7837"
    assert project["vertical_datum"] == "DHHN2016"
    assert project["crs_info"]["vertical_name"] == "DHHN2016"
    assert metadata["projection"] == "EPSG:25832"
    assert metadata["srs"]["vertical"] == "7837"
    assert cloudjs["vertical_projection"] == "EPSG:7837"


def test_generate_v2_duplicate_project_output_preserves_disabled_source_and_adds_active_clone(tmp_path):
    result = generate_v2_golden_outputs(
        MANIFEST_PATH,
        output_root=tmp_path,
        scenario_id="duplicate_project",
    )[0]

    projects_index = json.loads((result.output_dir / "projects_index.json").read_text(encoding="utf-8"))
    duplicated = projects_index["projects"][0]
    source = projects_index["disabled_projects"][0]

    assert duplicated["id"] == "abc12d01"
    assert duplicated["kunde"] == "Golden Kunde"
    assert duplicated["projekt"] == "Duplicate Clone"
    assert duplicated["s3_path"] == "pointclouds/golden_kunde/abc12d01/duplicate_clone"
    assert "disabled_at" not in duplicated
    assert source["id"] == "dup-source"
    assert source["disabled_at"] == "2026-06-20T12:00:00"
    assert result.uploaded_keys == (
        "pointclouds/golden_kunde/abc12d01/duplicate_clone/cloud_a/cloud.js",
        "pointclouds/golden_kunde/abc12d01/duplicate_clone/cloud_a/metadata.json",
        "pointclouds/golden_kunde/abc12d01/duplicate_clone/cloud_b/source.copc.laz",
    )


def test_generate_v2_multi_replace_output_clears_common_crs_on_mismatch(tmp_path):
    result = generate_v2_golden_outputs(
        MANIFEST_PATH,
        output_root=tmp_path,
        scenario_id="multi_replace",
    )[0]

    projects_index = json.loads((result.output_dir / "projects_index.json").read_text(encoding="utf-8"))
    project = projects_index["disabled_projects"][0]
    metadata = json.loads((result.output_dir / "metadata.json").read_text(encoding="utf-8"))
    cloudjs = _read_cloudjs_json(result.output_dir / "cloud.js")

    assert project["id"] == "replace-multi"
    assert project["disabled_at"] == "2026-06-20T12:00:00"
    assert project["format"] == "multi"
    assert "crs" not in project
    assert "projection" not in project
    assert [cloud["format"] for cloud in project["pointclouds"]] == ["copc", "potree"]
    assert [cloud["crs"] for cloud in project["pointclouds"]] == ["EPSG:25832", "EPSG:4326"]
    assert metadata["projection"] == "EPSG:4326"
    assert cloudjs["projection"] == "EPSG:4326"


def test_generate_v2_multi_replace_records_index_save_before_orphan_cleanup(tmp_path):
    result = generate_v2_golden_outputs(
        MANIFEST_PATH,
        output_root=tmp_path,
        scenario_id="multi_replace",
    )[0]

    side_effects = _read_side_effects(result)
    events = side_effects["events"]
    upload_sequences = [event["sequence"] for event in events if event["type"] == "upload_file"]
    index_put = next(
        event for event in events if event["type"] == "put_object" and event["key"] == "projects_index.json"
    )
    cleanup_delete = next(event for event in events if event["type"] == "delete_objects")

    assert upload_sequences
    assert max(upload_sequences) < index_put["sequence"] < cleanup_delete["sequence"]
    assert cleanup_delete["keys"] == [
        "pointclouds/golden/replace_multi/old_a/cloud.js",
        "pointclouds/golden/replace_multi/old_a/metadata.json",
        "pointclouds/golden/replace_multi/old_b/source.copc.laz",
        "pointclouds/golden/replace_multi/old_orphan.bin",
    ]


def test_generate_v2_upload_records_upload_extra_args_and_index_save(tmp_path):
    result = generate_v2_golden_outputs(
        MANIFEST_PATH,
        output_root=tmp_path,
        scenario_id="single_potree_upload",
    )[0]

    side_effects = _read_side_effects(result)
    upload_events = [event for event in side_effects["events"] if event["type"] == "upload_file"]
    index_put = next(
        event for event in side_effects["events"] if event["type"] == "put_object" and event["key"] == "projects_index.json"
    )

    assert upload_events
    assert all(event["purpose"] == "new_project_upload" for event in upload_events)
    assert all(event["extra_args"]["CacheControl"] == S3_CACHE_CONTROL for event in upload_events)
    assert all(event["extra_args"]["ContentType"] for event in upload_events)
    assert index_put["purpose"] == "save_projects_index"
    assert index_put["cache_control"] == "no-cache"


def test_generate_v2_duplicate_project_records_copy_operations(tmp_path):
    result = generate_v2_golden_outputs(
        MANIFEST_PATH,
        output_root=tmp_path,
        scenario_id="duplicate_project",
    )[0]

    side_effects = _read_side_effects(result)
    copy_events = [event for event in side_effects["events"] if event["type"] == "copy_object"]
    upload_events = [event for event in side_effects["events"] if event["type"] == "upload_file"]

    assert not upload_events
    assert [event["key"] for event in copy_events] == list(result.uploaded_keys)
    assert all(event["purpose"] == "duplicate_copy" for event in copy_events)
    assert all(event["cache_control"] == S3_CACHE_CONTROL for event in copy_events)


def test_generate_v2_disabled_link_state_output_keeps_disabled_projects_disabled(tmp_path):
    result = generate_v2_golden_outputs(
        MANIFEST_PATH,
        output_root=tmp_path,
        scenario_id="disabled_link_state",
    )[0]

    projects_index = json.loads((result.output_dir / "projects_index.json").read_text(encoding="utf-8"))
    deleted = json.loads((result.output_dir / "deleted_projects.json").read_text(encoding="utf-8"))
    disabled_by_id = {project["id"]: project for project in projects_index["disabled_projects"]}

    assert projects_index["projects"] == []
    assert disabled_by_id["disable-target"]["disabled_at"] == "2026-06-21T12:00:00"
    assert disabled_by_id["disabled-target"]["disabled_at"] == "2026-06-20T12:00:00"
    assert disabled_by_id["disabled-target"]["kunde"] == "Disabled Kunde Neu"
    assert disabled_by_id["disabled-target"]["crs"] == "EPSG:4326"
    assert deleted == {"deleted_projects": [], "last_updated": None}


def test_generate_v2_golden_output_cli_generates_selected_scenario(tmp_path, capsys):
    exit_code = generate_v2_golden_output_cli(
        [
            "--scenario",
            "single_copc_upload",
            "--output-root",
            str(tmp_path),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Generated V2 Golden output: 1 scenario(s)" in output
    assert (tmp_path / "single_copc_upload" / "projects_index.json").is_file()


def test_generate_v2_golden_outputs_refuses_existing_files_without_overwrite(tmp_path):
    generate_v2_golden_outputs(MANIFEST_PATH, output_root=tmp_path, scenario_id="single_copc_upload")

    try:
        generate_v2_golden_outputs(MANIFEST_PATH, output_root=tmp_path, scenario_id="single_copc_upload")
    except FileExistsError as exc:
        assert "projects_index.json" in str(exc)
    else:
        raise AssertionError("Expected FileExistsError for existing generated output")


def _read_cloudjs_json(path):
    text = path.read_text(encoding="utf-8")
    return json.loads(text.removeprefix("cloud.js = ").rstrip(";"))


def _read_side_effects(result):
    return json.loads(result.side_effects_path.read_text(encoding="utf-8"))
