"""Pure tests of the compact index schema 2 serializer and its save boundary."""

import copy
import json

import pytest

from dronautix_uploader.core.project_index_schema import (
    CrsDetailEvidence,
    IndexSaveContext,
    UnsupportedIndexSchemaError,
    compact_project_entry,
    crs_detail_evidence_from_documents,
    ensure_writable_index_schema,
    is_compact_index_project,
    unsecured_project_crs_details,
)
from dronautix_uploader.core.project_repository import prepare_projects_index_for_save

WKT_25832 = (
    'PROJCS["ETRS89 / UTM zone 32N",GEOGCS["ETRS89",AUTHORITY["EPSG","4258"]],'
    'AUTHORITY["EPSG","25832"]]'
)
COMPOUND_WKT = (
    'COMPD_CS["ETRS89 / UTM zone 32N + DHHN2016 height",'
    'PROJCS["ETRS89 / UTM zone 32N",AUTHORITY["EPSG","25832"]],'
    'VERT_CS["DHHN2016 height",AUTHORITY["EPSG","7837"]]]'
)


def bloated_single(**overrides):
    """A schema-2 single-cloud entry as the shared builders produce it."""

    project = {
        "index_schema_version": 2,
        "id": "target",
        "kunde": "Kunde",
        "projekt": "Projekt",
        "format": "potree",
        "viewer_path": "kunde/target/projekt",
        "s3_path": "pointclouds/kunde/target/projekt",
        "name": "Bestand",
        "crs": "EPSG:25832",
        "projection": "EPSG:25832",
        "epsg": "EPSG:25832",
        "crs_name": "ETRS89 / UTM zone 32N",
        "vertical_crs": "EPSG:7837",
        "vertical_epsg": "EPSG:7837",
        "vertical_projection": "EPSG:7837",
        "vertical_name": "DHHN2016 height",
        "vertical_datum": "DHHN2016 height",
        "crs_info": {
            "value": "EPSG:25832",
            "projection": "EPSG:25832",
            "epsg": "EPSG:25832",
            "code": "25832",
            "name": "ETRS89 / UTM zone 32N",
            "crs_name": "ETRS89 / UTM zone 32N",
            "vertical_crs": "EPSG:7837",
            "vertical_epsg": "EPSG:7837",
            "vertical_projection": "EPSG:7837",
            "vertical_name": "DHHN2016 height",
            "vertical_datum": "DHHN2016 height",
        },
    }
    project.update(overrides)
    return project


COMPACT_SINGLE = {
    "index_schema_version": 2,
    "id": "target",
    "kunde": "Kunde",
    "projekt": "Projekt",
    "format": "potree",
    "viewer_path": "kunde/target/projekt",
    "s3_path": "pointclouds/kunde/target/projekt",
    "name": "Bestand",
    "crs": "EPSG:25832",
    "crs_name": "ETRS89 / UTM zone 32N",
    "vertical_crs": "EPSG:7837",
    "vertical_name": "DHHN2016 height",
}


def evidence(path, **details):
    return CrsDetailEvidence(path, frozenset(details.items()))


@pytest.mark.parametrize("version", ("2", 2.0, True, None, 1, 3, [2]))
def test_only_the_integer_two_is_the_compact_schema(version):
    project = {"id": "p", "index_schema_version": version}

    assert not is_compact_index_project(project)
    with pytest.raises(UnsupportedIndexSchemaError, match="Indexschema-Version"):
        ensure_writable_index_schema(project)
    assert compact_project_entry(project) == project


def test_missing_marker_is_legacy_and_writable():
    legacy = bloated_single()
    legacy.pop("index_schema_version")

    ensure_writable_index_schema(legacy)
    ensure_writable_index_schema({"id": "p", "index_schema_version": 2})
    assert compact_project_entry(legacy) == legacy


def test_compact_entry_drops_proven_duplicates_and_keeps_viewer_fields():
    project = bloated_single()

    assert compact_project_entry(project) == COMPACT_SINGLE


def test_compaction_is_idempotent_and_never_mutates_its_input():
    project = bloated_single(crs_info={**bloated_single()["crs_info"], "vendor": "x"})
    original = copy.deepcopy(project)

    once = compact_project_entry(project)
    twice = compact_project_entry(once)

    assert project == original
    assert once == twice
    assert once["crs_info"] == original["crs_info"]  # unknown key keeps the whole block
    assert "projection" not in once  # safe alias duplicates still go


@pytest.mark.parametrize(
    "crs_info_update",
    (
        {"vendor_specific": "x"},
        {"code": 25832},
        {"code": "25833"},
        {"auth": "ESRI"},
        {"name": "ETRS89 / UTM 32N (anders)"},
        {"vertical_datum": "NHN"},
        {"projection": "EPSG:4326"},
        {"horizontal": WKT_25832},
        {"vertical_wkt": 'VERT_CS["DHHN2016 height",AUTHORITY["EPSG","7837"]]'},
    ),
)
def test_unknown_unresolvable_or_contradicting_crs_info_keeps_the_whole_block(crs_info_update):
    project = bloated_single()
    project["crs_info"].update(crs_info_update)

    compacted = compact_project_entry(project)

    assert compacted["crs_info"] == project["crs_info"]
    assert compacted["crs"] == "EPSG:25832"


@pytest.mark.parametrize("empty", (None, ""))
def test_an_unknown_key_keeps_the_block_even_with_an_empty_value(empty):
    project = bloated_single()
    project["crs_info"]["vendor_flag"] = empty

    compacted = compact_project_entry(project)

    assert compacted["crs_info"] == project["crs_info"]
    assert compacted["crs_info"]["vendor_flag"] == empty


def test_derivable_auth_and_code_and_empty_values_do_not_block_removal():
    project = bloated_single()
    project["crs_info"].update({"auth": "EPSG", "code": "25832", "wkt": "", "source": None})

    assert "crs_info" not in compact_project_entry(project)


@pytest.mark.parametrize("key", ("wkt", "source"))
def test_wkt_and_source_need_evidence_bound_to_the_same_dataset_and_value(key):
    value = WKT_25832 if key == "wkt" else "las_header"
    project = bloated_single()
    project["crs_info"][key] = value
    own_path = project["s3_path"]

    assert "crs_info" in compact_project_entry(project)
    assert "crs_info" in compact_project_entry(project, (evidence("pointclouds/other/project", **{key: value}),))
    assert "crs_info" in compact_project_entry(project, (evidence(own_path, **{key: value + " "}),))
    assert "crs_info" not in compact_project_entry(project, (evidence(f"/{own_path}/", **{key: value}),))


def test_a_name_only_in_crs_info_is_lifted_before_removal():
    project = bloated_single()
    del project["crs_name"]
    del project["vertical_name"]
    project.pop("vertical_datum")

    compacted = compact_project_entry(project)

    assert "crs_info" not in compacted
    assert compacted["crs_name"] == "ETRS89 / UTM zone 32N"
    assert compacted["vertical_name"] == "DHHN2016 height"


def test_an_explicitly_empty_flat_name_is_not_overwritten():
    project = bloated_single(crs_name="")

    compacted = compact_project_entry(project)

    assert compacted["crs_name"] == ""
    assert compacted["crs_info"] == project["crs_info"]


def test_a_crs_info_that_readers_would_see_differently_is_kept():
    # crs_info wins over flat fields today: without a vertical in the block the
    # project has no height reference; removing it would make one appear.
    project = bloated_single()
    for key in ("vertical_crs", "vertical_epsg", "vertical_projection", "vertical_name", "vertical_datum"):
        project["crs_info"].pop(key)

    assert compact_project_entry(project)["crs_info"] == project["crs_info"]


def test_wkt_aliases_are_never_collapsed_to_their_epsg_code():
    project = bloated_single(projection=WKT_25832)
    project["crs_info"]["projection"] = WKT_25832

    compacted = compact_project_entry(project)

    assert compacted["projection"] == WKT_25832
    assert compacted["crs_info"]["projection"] == WKT_25832


def test_a_compound_wkt_reference_stays_complete():
    canonical = " ".join(COMPOUND_WKT.split())
    project = {
        "index_schema_version": 2,
        "id": "target",
        "s3_path": "pointclouds/k/target/p",
        "crs": canonical,
        "projection": canonical,
        "crs_info": {"value": canonical, "projection": canonical, "wkt": COMPOUND_WKT},
    }

    compacted = compact_project_entry(project)

    assert compacted["crs"] == canonical
    assert "projection" not in compacted
    assert compacted["crs_info"]["wkt"] == COMPOUND_WKT  # not proven
    proven = compact_project_entry(project, (evidence("pointclouds/k/target/p", wkt=COMPOUND_WKT),))
    assert "crs_info" not in proven
    assert proven["crs"] == canonical


def test_invalid_crs_values_are_kept_without_raising():
    project = bloated_single(crs="kein CRS", projection="EPSG:25832")
    project["crs_info"]["value"] = "auch kein CRS"

    compacted = compact_project_entry(project)

    assert compacted["crs"] == "kein CRS"
    assert compacted["projection"] == "EPSG:25832"
    assert compacted["crs_info"] == project["crs_info"]


def test_vertical_datum_differing_from_vertical_name_is_kept():
    project = bloated_single(vertical_datum="NHN")

    assert compact_project_entry(project)["vertical_datum"] == "NHN"


def multi_project(first_visible=True):
    clouds = []
    for slug, visible in (("a", first_visible), ("b", True)):
        clouds.append(
            {
                "name": slug.upper(),
                "format": "potree",
                "viewer_path": f"k/p/{slug}",
                "s3_path": f"pointclouds/k/p/{slug}",
                "visible": visible,
                "crs": "EPSG:25832",
                "projection": "EPSG:25832",
                "crs_info": {"value": "EPSG:25832", "source": f"src-{slug}"},
            }
        )
    return {
        "index_schema_version": 2,
        "id": "multi",
        "format": "multi",
        "viewer_path": "k/p",
        "s3_path": "pointclouds/k/p",
        "crs": "EPSG:25832",
        "projection": "EPSG:25832",
        "crs_info": dict(clouds[0 if first_visible else 1]["crs_info"]),
        "pointcloud_count": 2,
        "pointclouds": clouds,
        "models": [
            {
                "id": "haus",
                "name": "Haus",
                "format": "glb",
                "viewer_path": "k/p/models/haus/versions/v/model.json",
                "s3_path": "pointclouds/k/p/models/haus/versions/v",
                "crs": "EPSG:25832",
                "vertical_crs": "EPSG:7837",
                "vertical_name": "DHHN2016",
                "vertical_datum": "DHHN2016",
            }
        ],
    }


def test_multi_summary_is_proven_by_the_first_active_cloud_only():
    project = multi_project()
    proof_a = evidence("pointclouds/k/p/a", source="src-a")

    compacted = compact_project_entry(project, (proof_a,))

    assert "crs_info" not in compacted
    assert "crs_info" not in compacted["pointclouds"][0]
    assert compacted["pointclouds"][1]["crs_info"] == {"value": "EPSG:25832", "source": "src-b"}
    assert all("projection" not in entry for entry in (compacted, *compacted["pointclouds"]))
    assert compacted["models"][0] == {key: value for key, value in project["models"][0].items() if key != "vertical_datum"}


def test_hidden_first_cloud_moves_the_summary_proof_to_the_next_active_cloud():
    project = multi_project(first_visible=False)

    assert "crs_info" in compact_project_entry(project, (evidence("pointclouds/k/p/a", source="src-b"),))
    assert "crs_info" not in compact_project_entry(project, (evidence("pointclouds/k/p/b", source="src-b"),))


def test_a_glb_manifest_path_is_no_evidence_for_model_crs_details():
    project = multi_project()
    model = project["models"][0]
    model["crs_info"] = {"value": "EPSG:25832", "source": "glb"}

    compacted = compact_project_entry(project, (evidence(model["s3_path"], source="glb"),))

    assert compacted["models"][0]["crs_info"] == model["crs_info"]


def test_document_evidence_reads_only_crs_fields_of_the_documents():
    documents = [
        {"source": "Bestand.laz", "crs_info": {"value": "EPSG:25832", "source": "manual", "wkt": ""}},
        {"srs": {"wkt": WKT_25832}},
        "kein Dokument",
    ]

    proof = crs_detail_evidence_from_documents("pointclouds/k/p/", documents)

    assert proof == CrsDetailEvidence("pointclouds/k/p", frozenset({("source", "manual"), ("wkt", WKT_25832)}))
    assert crs_detail_evidence_from_documents("pointclouds/k/p", [{"source": "Bestand.laz"}]) is None
    assert crs_detail_evidence_from_documents("", documents) is None


def test_unsecured_summary_details_are_reported_by_field_name():
    project = {
        "crs_info": {"value": "EPSG:25832", "source": "manual", "project_only": "x", "wkt": ""},
    }
    clouds = [{"crs_info": {"value": "EPSG:25832", "source": "manual"}}, {"crs": "EPSG:25832"}]

    assert unsecured_project_crs_details(project, clouds) == ("project_only",)
    assert unsecured_project_crs_details({"crs": "EPSG:25832"}, clouds) == ()


@pytest.mark.parametrize("empty", (None, ""))
def test_an_unknown_empty_summary_key_is_unsecured_unless_a_cloud_holds_it(empty):
    project = {"crs_info": {"value": "EPSG:25832", "vendor_flag": empty, "wkt": empty, "source": empty}}
    clouds = [{"crs_info": {"value": "EPSG:25832"}}, {"crs": "EPSG:25832"}]

    assert unsecured_project_crs_details(project, clouds) == ("vendor_flag",)
    held = [{"crs_info": {"value": "EPSG:25832", "vendor_flag": empty}}]
    assert unsecured_project_crs_details(project, held) == ()


def full_index():
    target = bloated_single()
    other_schema2 = bloated_single(id="other-schema2", s3_path="pointclouds/k/other/p")
    contradicting = {
        "index_schema_version": 2,
        "id": "contradicting",
        "crs": "EPSG:25832",
        "projection": "EPSG:4326",
        "crs_info": {"value": "kein CRS"},
    }
    unknown = {"index_schema_version": "2", "id": "unknown", "crs": "EPSG:25832", "projection": "EPSG:25832"}
    legacy = bloated_single(id="legacy")
    legacy.pop("index_schema_version")
    return {
        "projects": [other_schema2, {**target, "_link_disabled": True}, contradicting, "kein Eintrag"],
        "disabled_projects": [unknown, legacy],
        "last_updated": "alt",
    }


def test_save_boundary_compacts_only_the_named_target():
    index = full_index()
    original = copy.deepcopy(index)

    saved = prepare_projects_index_for_save(index, "neu", IndexSaveContext("target"))

    assert index == original
    assert saved["last_updated"] == "neu"
    assert saved["projects"][1] == COMPACT_SINGLE
    for position in (0, 2, 3):
        assert saved["projects"][position] == original["projects"][position]
    assert saved["disabled_projects"] == original["disabled_projects"]


def test_save_boundary_without_context_or_target_compacts_nothing():
    index = full_index()
    expected = copy.deepcopy(index)
    expected["projects"][1].pop("_link_disabled")
    expected["last_updated"] = "neu"

    assert prepare_projects_index_for_save(index, "neu") == expected
    assert prepare_projects_index_for_save(index, "neu", IndexSaveContext("")) == expected
    assert prepare_projects_index_for_save(index, "neu", IndexSaveContext("geloescht")) == expected


def test_save_boundary_compacts_a_disabled_target_and_keeps_json_order():
    index = full_index()
    target = index["projects"].pop(1)
    target.pop("_link_disabled")
    index["disabled_projects"].insert(1, target)

    saved = prepare_projects_index_for_save(index, "neu", IndexSaveContext(" target "))

    assert [entry.get("id") for entry in saved["disabled_projects"]] == ["unknown", "target", "legacy"]
    assert list(json.loads(json.dumps(saved["disabled_projects"][1]))) == list(COMPACT_SINGLE)
