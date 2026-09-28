"""The viewer contract: generated output must stay byte-identical to tests/snapshots."""

import json
from pathlib import Path

from dronautix_uploader.core.output_snapshots import (
    SNAPSHOT_SCENARIOS,
    compare_with_snapshots,
    describe_snapshot_differences,
    generate_output_snapshots,
)

SNAPSHOT_ROOT = Path(__file__).resolve().parent / "snapshots"


def test_generated_viewer_output_matches_committed_snapshots(tmp_path):
    generate_output_snapshots(tmp_path)

    differences = compare_with_snapshots(tmp_path, SNAPSHOT_ROOT)

    assert differences == [], (
        "Viewer-Ausgabe hat sich geändert. Ist das beabsichtigt, "
        "'python tools/update_output_snapshots.py --write' ausführen und den diff prüfen.\n"
        + describe_snapshot_differences(tmp_path, SNAPSHOT_ROOT)
    )


def test_every_scenario_has_complete_committed_snapshots():
    for scenario_id, files in SNAPSHOT_SCENARIOS.items():
        for name in (*files, "side_effects.json"):
            assert (SNAPSHOT_ROOT / scenario_id / name).is_file(), f"{scenario_id}/{name}"


def test_a_changed_field_in_a_viewer_file_is_reported(tmp_path):
    generate_output_snapshots(tmp_path)
    index_path = tmp_path / "vertical_crs_upload" / "projects_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["projects"][0]["vertical_crs"] = "EPSG:9999"
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")

    assert compare_with_snapshots(tmp_path, SNAPSHOT_ROOT) == [
        "vertical_crs_upload/projects_index.json: weicht vom Snapshot ab"
    ]


def test_a_code_change_to_the_written_metadata_is_detected(tmp_path, monkeypatch):
    from dronautix_uploader.core import metadata_service

    real_apply = metadata_service.apply_crs_metadata

    def apply_without_vertical(target, crs_info, include_projection=True):
        real_apply(target, crs_info, include_projection)
        for key in ("vertical_crs", "vertical_epsg", "vertical_projection"):
            target.pop(key, None)

    monkeypatch.setattr(metadata_service, "apply_crs_metadata", apply_without_vertical)
    generate_output_snapshots(tmp_path, scenario_id="vertical_crs_upload")

    differences = [d for d in compare_with_snapshots(tmp_path, SNAPSHOT_ROOT) if d.startswith("vertical_crs_upload/")]
    assert any("weicht vom Snapshot ab" in difference for difference in differences)
