from pathlib import Path
import json

import pytest

from dronautix_uploader.core.golden_normalization import (
    canonical_cloudjs_text,
    canonical_json_text,
)
from dronautix_uploader.core.golden_capture import normalized_capture_file_name


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "golden_examples"
GOLDEN_DIR = Path(__file__).parent / "golden"
MANIFEST_PATH = GOLDEN_DIR / "manifest.json"
REQUIRED_SCENARIOS = {
    "single_potree_upload",
    "single_copc_upload",
    "multi_mix_upload",
    "vertical_crs_upload",
    "existing_potree_folder_upload",
    "duplicate_project",
    "delete_project",
    "rename_project",
    "single_replace",
    "multi_replace",
    "disabled_link_state",
}


@pytest.mark.parametrize(
    ("fixture_name", "golden_name"),
    [
        ("projects_index.json", "example_projects_index.normalized.json"),
        ("metadata.json", "example_metadata.normalized.json"),
    ],
)
def test_example_json_golden_fixtures_match_normalized_snapshots(fixture_name, golden_name):
    raw_text = (FIXTURE_DIR / fixture_name).read_text(encoding="utf-8")
    expected = (GOLDEN_DIR / golden_name).read_text(encoding="utf-8")

    assert canonical_json_text(raw_text) == expected


def test_example_cloudjs_golden_fixture_matches_normalized_snapshot():
    raw_text = (FIXTURE_DIR / "cloud.js").read_text(encoding="utf-8")
    expected = (GOLDEN_DIR / "example_cloud.normalized.json").read_text(encoding="utf-8")

    assert canonical_cloudjs_text(raw_text) == expected


def test_golden_manifest_tracks_required_legacy_capture_scenarios():
    manifest = _load_manifest()
    scenarios = manifest["scenarios"]
    scenario_ids = {scenario["id"] for scenario in scenarios}
    scenarios_by_id = {scenario["id"]: scenario for scenario in scenarios}

    assert manifest["status"] == "pending_legacy_capture"
    assert manifest["provenance_file"] == "provenance.json"
    assert manifest["v2_output_root"] == "tests/golden/v2_outputs"
    assert REQUIRED_SCENARIOS.issubset(scenario_ids)
    assert scenarios_by_id["single_copc_upload"]["required_files"] == ["projects_index.json"]
    assert len(scenarios) == len(scenario_ids)
    for scenario in scenarios:
        assert scenario["description"].strip()
        assert scenario["required_files"]
        assert all(Path(file_name).name == file_name for file_name in scenario["required_files"])


@pytest.mark.parametrize("scenario", json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["scenarios"], ids=lambda item: item["id"])
def test_captured_legacy_golden_files_match_normalized_snapshots_when_present(scenario):
    manifest = _load_manifest()
    captured_dir = Path(manifest["captured_root"]) / scenario["id"]
    normalized_dir = Path(manifest["normalized_root"]) / scenario["id"]
    if not captured_dir.exists():
        pytest.skip(f"Legacy capture pending for {scenario['id']}")
    required_raw_paths = tuple(captured_dir / file_name for file_name in scenario["required_files"])
    if not any(raw_path.is_file() for raw_path in required_raw_paths):
        pytest.skip(f"Legacy capture initialized but raw files pending for {scenario['id']}")

    for file_name, raw_path in zip(scenario["required_files"], required_raw_paths):
        expected_path = normalized_dir / normalized_capture_file_name(file_name)
        assert raw_path.is_file(), f"Missing captured legacy file: {raw_path}"
        assert expected_path.is_file(), f"Missing normalized golden file: {expected_path}"
        raw_text = raw_path.read_text(encoding="utf-8")
        expected = expected_path.read_text(encoding="utf-8")
        if file_name == "cloud.js":
            actual = canonical_cloudjs_text(raw_text)
        else:
            actual = canonical_json_text(raw_text)
        assert actual == expected


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
