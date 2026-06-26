from dronautix_uploader.core.constants import S3_DISABLED_PROJECTS_KEY
from dronautix_uploader.core.project_index_service import (
    apply_common_crs_or_clear,
    get_all_projects_for_management,
    remove_project_from_index,
    update_project_link_disabled_state,
    update_project_in_index,
)


def test_update_project_in_index_keeps_disabled_project_in_disabled_list():
    index_data = {
        "projects": [{"id": "active", "projekt": "Active"}],
        S3_DISABLED_PROJECTS_KEY: [{"id": "disabled", "projekt": "Old"}],
    }

    changed = update_project_in_index(
        index_data,
        "disabled",
        lambda project: project.update({"projekt": "Updated"}),
    )

    assert changed
    assert index_data["projects"] == [{"id": "active", "projekt": "Active"}]
    assert index_data[S3_DISABLED_PROJECTS_KEY] == [{"id": "disabled", "projekt": "Updated"}]
    assert get_all_projects_for_management(index_data) == [
        ({"id": "active", "projekt": "Active"}, False),
        ({"id": "disabled", "projekt": "Updated"}, True),
    ]


def test_remove_project_from_index_deletes_from_active_and_disabled_lists():
    index_data = {
        "projects": [{"id": "same"}, {"id": "active"}],
        S3_DISABLED_PROJECTS_KEY: [{"id": "same"}, {"id": "disabled"}],
    }

    changed = remove_project_from_index(index_data, "same")

    assert changed
    assert index_data["projects"] == [{"id": "active"}]
    assert index_data[S3_DISABLED_PROJECTS_KEY] == [{"id": "disabled"}]


def test_update_project_link_disabled_state_moves_active_to_disabled_with_timestamp_and_strips_ui_flags():
    index_data = {
        "projects": [
            {"id": "active", "projekt": "Active", "_link_disabled": False, "link_disabled": False},
            {"id": "other", "projekt": "Other"},
        ],
        S3_DISABLED_PROJECTS_KEY: [{"id": "old-disabled"}],
    }

    changed = update_project_link_disabled_state(
        index_data,
        ("active",),
        True,
        timestamp="2026-06-21T16:00:00",
    )

    assert changed == 1
    assert index_data["projects"] == [{"id": "other", "projekt": "Other"}]
    assert index_data[S3_DISABLED_PROJECTS_KEY][0] == {
        "id": "active",
        "projekt": "Active",
        "disabled_at": "2026-06-21T16:00:00",
    }


def test_update_project_link_disabled_state_moves_disabled_to_active_and_removes_disabled_at():
    index_data = {
        "projects": [{"id": "active"}],
        S3_DISABLED_PROJECTS_KEY: [
            {"id": "disabled", "projekt": "Disabled", "disabled_at": "old"},
            {"id": "other-disabled"},
        ],
    }

    changed = update_project_link_disabled_state(
        index_data,
        ("disabled",),
        False,
        timestamp="2026-06-21T16:00:00",
    )

    assert changed == 1
    assert index_data["projects"][0] == {"id": "disabled", "projekt": "Disabled"}
    assert index_data[S3_DISABLED_PROJECTS_KEY] == [{"id": "other-disabled"}]


def test_apply_common_crs_or_clear_removes_stale_top_level_keys_only():
    pointcloud_crs = {"value": "EPSG:25832", "source": "auto"}
    project = {
        "id": "project",
        "crs": "EPSG:4326",
        "projection": "EPSG:4326",
        "epsg": "EPSG:4326",
        "vertical_crs": "EPSG:7837",
        "vertical_epsg": "EPSG:7837",
        "vertical_projection": "EPSG:7837",
        "vertical_datum": "DHHN2016",
        "crs_info": {"value": "EPSG:4326"},
        "pointclouds": [{"name": "scan", "crs_info": pointcloud_crs}],
    }

    result = apply_common_crs_or_clear(project, None, lambda _project, _crs: None)

    assert result is project
    for key in (
        "crs",
        "projection",
        "epsg",
        "vertical_crs",
        "vertical_epsg",
        "vertical_projection",
        "vertical_datum",
        "crs_info",
    ):
        assert key not in project
    assert project["pointclouds"][0]["crs_info"] is pointcloud_crs


def test_apply_common_crs_or_clear_applies_common_crs_when_present():
    project = {"id": "project"}
    common_crs = {"value": "EPSG:25832"}

    apply_common_crs_or_clear(
        project,
        common_crs,
        lambda target, crs: target.update({"crs": crs["value"], "crs_info": dict(crs)}),
    )

    assert project == {"id": "project", "crs": "EPSG:25832", "crs_info": common_crs}
