from dronautix_uploader.core.crs_service import (
    CrsSummary,
    crs_metadata_matches,
    extract_pointcloud_crs_metadata,
    get_common_active_pointcloud_crs,
    get_common_crs_metadata,
    get_crs_summary_text,
    get_vertical_crs_display_value,
    normalize_crs_metadata,
    summarize_crs_metadata,
)


def test_normalize_crs_metadata_returns_none_for_empty_or_missing_values():
    assert normalize_crs_metadata(None) is None
    assert normalize_crs_metadata({}) is None
    assert normalize_crs_metadata({"value": "  ", "vertical_datum": ""}) is None
    assert get_common_crs_metadata([]) is None
    assert get_common_crs_metadata([{"value": "EPSG:25832"}, None]) is None


def test_common_crs_is_set_when_multiple_active_clouds_have_same_summary():
    pointclouds = [
        {"name": "Scan 1", "crs_info": {"value": "25832", "vertical_epsg": "7837"}},
        {"name": "Scan 2", "crs_info": {"epsg": "epsg:25832", "vertical_crs": "EPSG:7837"}},
        {"name": "Hidden", "visible": False, "crs_info": {"value": "EPSG:4326"}},
    ]

    decision = get_common_active_pointcloud_crs(pointclouds)

    assert decision.should_set_project_crs
    assert not decision.has_mismatch
    assert decision.active_count == 2
    assert decision.common_crs == {
        "value": "EPSG:25832",
        "projection": "EPSG:25832",
        "epsg": "EPSG:25832",
        "code": "25832",
        "vertical_crs": "EPSG:7837",
        "vertical_epsg": "EPSG:7837",
        "vertical_projection": "EPSG:7837",
    }
    assert decision.summary.text == "EPSG:25832 | Vertikal: EPSG:7837"


def test_mismatch_returns_no_project_crs_and_keeps_pointcloud_crs_unchanged():
    pointcloud_crs = {"value": "EPSG:25832", "source": "auto"}
    pointclouds = [
        {"name": "Scan 1", "crs_info": pointcloud_crs},
        {"name": "Scan 2", "crs_info": {"value": "EPSG:4326"}},
    ]

    decision = get_common_active_pointcloud_crs(pointclouds)

    assert not decision.should_set_project_crs
    assert decision.common_crs is None
    assert decision.summary == CrsSummary()
    assert decision.has_mismatch
    assert pointclouds[0]["crs_info"] is pointcloud_crs


def test_vertical_crs_and_datum_are_normalized_and_summarized():
    crs_info = {
        "value": "EPSG:25832",
        "vertical_crs": "7837",
        "vertical_datum": " DHHN2016 ",
    }

    normalized = normalize_crs_metadata(crs_info)

    assert normalized["vertical_crs"] == "EPSG:7837"
    assert normalized["vertical_epsg"] == "EPSG:7837"
    assert normalized["vertical_projection"] == "EPSG:7837"
    assert normalized["vertical_name"] == "DHHN2016"
    assert normalized["vertical_datum"] == "DHHN2016"
    assert get_vertical_crs_display_value(crs_info) == "EPSG:7837 (DHHN2016)"
    assert get_crs_summary_text(crs_info) == "EPSG:25832 | Vertikal: EPSG:7837 (DHHN2016)"


def test_summary_and_matching_are_stable_across_key_variants():
    left = {"epsg": "25832", "vertical_epsg": "7837", "vertical_name": "DHHN2016"}
    right = {"projection": "EPSG:25832", "vertical_projection": "EPSG:7837", "vertical_datum": "DHHN2016"}

    assert summarize_crs_metadata(left) == summarize_crs_metadata(right)
    assert crs_metadata_matches(left, right)
    assert get_common_crs_metadata([left, right]) == normalize_crs_metadata(left)


def test_extract_pointcloud_crs_falls_back_to_top_level_fields():
    pointcloud = {"name": "Scan", "crs": "EPSG:25832", "vertical_datum": "DHHN2016"}

    assert extract_pointcloud_crs_metadata(pointcloud) == {
        "value": "EPSG:25832",
        "projection": "EPSG:25832",
        "epsg": "EPSG:25832",
        "code": "25832",
        "vertical_name": "DHHN2016",
        "vertical_datum": "DHHN2016",
    }
