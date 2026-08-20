import pytest

from dronautix_uploader.core.crs_service import (
    CrsSummary,
    CrsValidationError,
    crs_metadata_matches,
    extract_pointcloud_crs_metadata,
    get_common_active_pointcloud_crs,
    get_common_crs_metadata,
    get_crs_summary_text,
    get_crs_technical_value,
    get_vertical_crs_display_value,
    get_vertical_crs_technical_value,
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


def test_matching_uses_only_technical_references_not_display_names():
    left = {
        "value": "EPSG:25833", "crs_name": "ETRS89 / UTM zone 33N",
        "vertical_crs": "EPSG:7837", "vertical_name": "DHHN2016 height",
    }
    right = {
        "projection": "25833", "name": "UTM 33 (anderer Anzeigename)",
        "vertical_epsg": "7837", "vertical_datum": "NHN 2016",
    }

    assert crs_metadata_matches(left, right)
    assert get_common_crs_metadata([left, right]) == normalize_crs_metadata(left)
    assert get_crs_technical_value(left) == "EPSG:25833"
    assert get_vertical_crs_technical_value(left) == "EPSG:7837"
    assert not crs_metadata_matches(left, {**right, "vertical_epsg": "EPSG:5783"})
    assert not crs_metadata_matches(left, {**right, "projection": "EPSG:25832"})


def test_non_epsg_ogc_references_are_canonical_and_compatible():
    urn = {
        "value": "urn:ogc:def:crs:ogc:1.3:CRS84",
        "vertical_crs": "urn:ogc:def:crs:ignf:0:NGF-IGN69",
    }
    url = {
        "value": "https://www.opengis.net/def/crs/OGC/1.3/CRS84",
        "vertical_crs": "http://www.opengis.net/def/crs/IGNF/0/NGF-IGN69",
    }

    assert normalize_crs_metadata(url)["value"] == "urn:ogc:def:crs:OGC:1.3:CRS84"
    assert normalize_crs_metadata(url)["vertical_crs"] == "urn:ogc:def:crs:IGNF:0:NGF-IGN69"
    assert crs_metadata_matches(urn, url)


def test_non_epsg_wkt_with_authority_is_a_stable_technical_reference():
    wkt = 'ENGCRS["Werknetz",EDATUM["Werkdatum"],CS[Cartesian,3],ID["ACME","GRID-7"]]'

    normalized = normalize_crs_metadata({"value": "Werknetz", "wkt": wkt, "crs_name": "Werknetz Anzeige"})

    assert normalized["value"] == wkt
    assert normalized["crs_name"] == "Werknetz Anzeige"
    assert get_crs_technical_value(normalized) == wkt


@pytest.mark.parametrize("crs_info", [
    {"value": "ETRS89 / UTM zone 33N"},
    {"value": "EPSG:25833", "projection": "EPSG:25832"},
    {"value": "EPSG:25833", "vertical_crs": "DHHN2016 height"},
    {"value": "EPSG:25833", "vertical_crs": "EPSG:7837 (DHHN2016 height)"},
])
def test_free_or_ambiguous_technical_references_fail_closed(crs_info):
    with pytest.raises(CrsValidationError, match="technische CRS-Referenz|widersprüchliche"):
        normalize_crs_metadata(crs_info)


def test_extract_pointcloud_crs_falls_back_to_top_level_fields():
    pointcloud = {
        "name": "Scan", "crs": "EPSG:25832", "crs_name": "ETRS89 / UTM 32N",
        "vertical_datum": "DHHN2016",
    }

    assert extract_pointcloud_crs_metadata(pointcloud) == {
        "value": "EPSG:25832",
        "projection": "EPSG:25832",
        "epsg": "EPSG:25832",
        "code": "25832",
        "name": "ETRS89 / UTM 32N",
        "crs_name": "ETRS89 / UTM 32N",
        "vertical_name": "DHHN2016",
        "vertical_datum": "DHHN2016",
    }
