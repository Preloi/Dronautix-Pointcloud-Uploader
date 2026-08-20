import json

import pytest

from dronautix_uploader.core.crs_service import CrsValidationError
from dronautix_uploader.core.metadata_service import (
    apply_crs_metadata,
    create_pointcloud_index_entry,
    get_common_crs_info,
    get_crs_summary_text,
    write_potree_metadata_crs,
    write_potree_metadata_name,
)


def test_apply_crs_metadata_writes_horizontal_and_vertical_fields():
    target = {}
    crs_info = {
        "value": "EPSG:25832",
        "projection": "EPSG:25832",
        "epsg": "EPSG:25832",
        "vertical_epsg": "EPSG:7837",
        "vertical_name": "DHHN2016",
    }

    apply_crs_metadata(target, crs_info)

    assert target["crs"] == "EPSG:25832"
    assert target["projection"] == "EPSG:25832"
    assert target["epsg"] == "EPSG:25832"
    assert target["vertical_crs"] == "EPSG:7837"
    assert target["vertical_epsg"] == "EPSG:7837"
    assert target["vertical_projection"] == "EPSG:7837"
    assert target["vertical_name"] == "DHHN2016"
    assert target["vertical_datum"] == "DHHN2016"
    assert target["crs_info"] == crs_info


def test_create_pointcloud_index_entry_matches_viewer_shape():
    entry = create_pointcloud_index_entry(
        "Scan 1",
        "copc",
        "kunde/id/projekt/scan/source.copc.laz",
        "pointclouds/kunde/id/projekt/scan/source.copc.laz",
        {"value": "EPSG:25832"},
    )

    assert entry == {
        "name": "Scan 1",
        "format": "copc",
        "viewer_path": "kunde/id/projekt/scan/source.copc.laz",
        "s3_path": "pointclouds/kunde/id/projekt/scan/source.copc.laz",
        "visible": True,
        "crs": "EPSG:25832",
        "projection": "EPSG:25832",
        "crs_info": {"value": "EPSG:25832"},
    }


def test_get_common_crs_info_requires_all_clouds_to_match():
    crs_a = {"value": "EPSG:25832", "vertical_epsg": "EPSG:7837"}
    crs_b = {"value": "EPSG:25832", "vertical_epsg": "EPSG:7837"}
    crs_c = {"value": "EPSG:4326"}

    assert get_common_crs_info([crs_a, crs_b]) == crs_a
    assert get_common_crs_info([crs_a, crs_c]) is None
    assert get_common_crs_info([crs_a, None]) is None


def test_metadata_serialization_is_canonical_and_keeps_names_separate():
    target = {}
    apply_crs_metadata(target, {
        "value": "25833", "crs_name": "ETRS89 / UTM zone 33N",
        "vertical_crs": "7837", "vertical_datum": "DHHN2016 height",
    })

    assert target["crs"] == "EPSG:25833"
    assert target["vertical_crs"] == "EPSG:7837"
    assert target["crs_name"] == "ETRS89 / UTM zone 33N"
    assert target["vertical_name"] == "DHHN2016 height"
    assert target["vertical_datum"] == "DHHN2016 height"
    assert "DHHN2016" not in target["vertical_crs"]


def test_metadata_serializes_non_epsg_authority_reference():
    target = {}
    apply_crs_metadata(target, {
        "value": "https://www.opengis.net/def/crs/OGC/1.3/CRS84",
        "vertical_crs": "urn:ogc:def:crs:IGNF:0:NGF-IGN69",
    })

    assert target["crs"] == "urn:ogc:def:crs:OGC:1.3:CRS84"
    assert target["vertical_crs"] == "urn:ogc:def:crs:IGNF:0:NGF-IGN69"


@pytest.mark.parametrize("crs_info", [
    {"name": "ETRS89 / UTM zone 33N"},
    {"value": "EPSG:25833", "vertical_datum": "DHHN2016 height"},
    {"value": "EPSG:25833", "vertical_crs": "DHHN2016 height"},
])
def test_metadata_rejects_missing_or_free_technical_reference_before_write(crs_info):
    target = {}
    with pytest.raises(CrsValidationError):
        apply_crs_metadata(target, crs_info)
    assert target == {}


def test_crs_summary_includes_vertical_datum():
    assert get_crs_summary_text(
        {
            "value": "EPSG:25832",
            "vertical_epsg": "EPSG:7837",
            "vertical_name": "DHHN2016",
        }
    ) == "EPSG:25832 | Vertikal: EPSG:7837 (DHHN2016)"


def test_write_potree_metadata_crs_updates_metadata_json_and_cloudjs(tmp_path):
    (tmp_path / "metadata.json").write_text('{"spacing": 0.1, "srs": {"existing": "kept"}}', encoding="utf-8")
    (tmp_path / "cloud.js").write_text('cloud.js = {"spacing": 0.1};', encoding="utf-8")

    updated = write_potree_metadata_crs(
        tmp_path,
        {
            "value": "EPSG:25832",
            "projection": "EPSG:25832",
            "epsg": "EPSG:25832",
            "vertical_epsg": "EPSG:7837",
            "vertical_name": "DHHN2016",
        },
    )

    metadata = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    cloudjs_text = (tmp_path / "cloud.js").read_text(encoding="utf-8")
    cloudjs = json.loads(cloudjs_text.removeprefix("cloud.js = ").rstrip(";"))
    assert updated == (tmp_path / "metadata.json", tmp_path / "cloud.js")
    assert metadata["projection"] == "EPSG:25832"
    assert metadata["crs"] == "EPSG:25832"
    assert metadata["vertical_crs"] == "EPSG:7837"
    assert metadata["vertical_datum"] == "DHHN2016"
    assert metadata["srs"] == {
        "existing": "kept",
        "authority": "EPSG",
        "horizontal": "25832",
        "vertical": "7837",
        "vertical_name": "DHHN2016",
    }
    assert cloudjs["projection"] == "EPSG:25832"
    assert cloudjs["crs_info"]["vertical_name"] == "DHHN2016"


def test_write_potree_metadata_name_replaces_technical_short_name(tmp_path):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text('{"name":"BUME~1","points":1096789}', encoding="utf-8")

    assert write_potree_metadata_name(tmp_path, "Bäume") == metadata_path
    assert json.loads(metadata_path.read_text(encoding="utf-8")) == {
        "name": "Bäume",
        "points": 1096789,
    }
