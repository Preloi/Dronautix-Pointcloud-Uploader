from dronautix_uploader.qt_app.project_management import (
    STATUS_ACTIVE,
    STATUS_ALL,
    STATUS_DISABLED,
    example_project_previews,
    load_project_previews,
    make_project_preview,
    make_project_previews,
    project_status,
    status_filter_accepts,
)


def test_status_labels_and_filters_follow_v2_disabled_semantics():
    assert project_status(False) == STATUS_ACTIVE
    assert project_status(True) == STATUS_DISABLED

    assert status_filter_accepts(False, STATUS_ALL)
    assert status_filter_accepts(True, STATUS_ALL)
    assert status_filter_accepts(False, STATUS_ACTIVE)
    assert not status_filter_accepts(True, STATUS_ACTIVE)
    assert status_filter_accepts(True, STATUS_DISABLED)
    assert not status_filter_accepts(False, STATUS_DISABLED)


def test_example_project_previews_include_active_disabled_and_multi_cloud_details():
    projects = example_project_previews()

    assert any(not project.disabled for project in projects)
    assert any(project.disabled for project in projects)
    assert any(len(project.pointclouds) > 1 for project in projects)
    assert {project.status for project in projects} == {STATUS_ACTIVE, STATUS_DISABLED}


def test_make_project_preview_normalizes_missing_values_without_qt():
    preview = make_project_preview({"projekt": "", "pointclouds": [{}]}, disabled=True)

    assert preview.project == "Unbenanntes Projekt"
    assert preview.customer == "Ohne Kunde"
    assert preview.status == STATUS_DISABLED
    assert preview.pointclouds[0].name == "Punktwolke 1"
    assert preview.pointclouds[0].crs == "Unbekannt"
    assert preview.history == ()
    assert preview.created == "Noch nicht geladen"
    assert preview.updated == "Noch nicht geladen"


def test_make_project_preview_formats_change_history_newest_first():
    preview = make_project_preview(
        {
            "projekt": "Projekt",
            "datum": "2026-07-01T08:00:00",
            "history": [
                {"timestamp": "2026-07-17T10:15:00", "message": "Name geändert."},
                {"timestamp": "2026-07-18T12:30:00", "message": "Punktwolke ausgetauscht."},
            ],
        },
        disabled=False,
    )

    assert preview.history == (
        "18.07.2026 12:30 – Punktwolke ausgetauscht.",
        "17.07.2026 10:15 – Name geändert.",
    )
    assert preview.created == "01.07.2026 08:00"
    assert preview.updated == "18.07.2026 12:30"
    assert preview.updated_sort == "2026-07-18 12:30"


def test_make_project_preview_uses_creation_date_as_updated_fallback_without_history():
    preview = make_project_preview(
        {"projekt": "Projekt", "datum": "2026-07-01T08:00:00"},
        disabled=False,
    )

    assert preview.created == "01.07.2026 08:00"
    assert preview.updated == "01.07.2026 08:00"
    assert preview.updated_sort == "2026-07-01 08:00"


def test_project_format_column_shows_einzel_or_multi_instead_of_stored_format():
    single = make_project_preview(
        {"projekt": "P", "format": "Potree", "pointclouds": [{"name": "A"}]},
        disabled=False,
    )
    multi = make_project_preview(
        {"projekt": "P", "format": "Potree", "pointclouds": [{"name": "A"}, {"name": "B"}]},
        disabled=False,
    )
    legacy_without_pointcloud_list = make_project_preview(
        {"projekt": "P", "format": "COPC"},
        disabled=False,
    )

    assert single.format == "Einzel"
    assert multi.format == "Multi"
    # Legacy-Projekte ohne pointclouds[]-Liste sind Einzel-Cloud-Projekte.
    assert legacy_without_pointcloud_list.format == "Einzel"


def test_project_preview_marks_only_explicit_pointcloud_lists_for_pointcloud_management():
    explicit = make_project_preview(
        {"projekt": "Multi", "format": "multi", "pointclouds": [{"name": "A"}]},
        disabled=False,
    )
    legacy = make_project_preview({"projekt": "Legacy", "format": "COPC"}, disabled=False)

    assert explicit.has_explicit_pointclouds is True
    assert legacy.has_explicit_pointclouds is False


def test_make_project_previews_converts_service_rows_with_paths_and_crs_info():
    previews = make_project_previews(
        [
            (
                {
                    "id": "p1",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "viewer_path": "kunde/p1/projekt",
                    "s3_path": "pointclouds/kunde/p1/projekt",
                    "pointclouds": [
                        {
                            "name": "Cloud A",
                            "format": "copc",
                            "viewer_path": "kunde/p1/projekt/a/source.copc.laz",
                            "s3_path": "pointclouds/kunde/p1/projekt/a/source.copc.laz",
                            "crs_info": {"value": "EPSG:25832"},
                            "visible": False,
                        }
                    ],
                    "models": [
                        {
                            "id": "fassade",
                            "name": "Fassade",
                            "format": "glb",
                            "viewer_path": "kunde/p1/projekt/models/fassade/versions/v1/model.json",
                            "s3_path": "pointclouds/kunde/p1/projekt/models/fassade/versions/v1",
                            "crs": "EPSG:25832",
                            "vertical_crs": "EPSG:7837",
                        }
                    ],
                },
                False,
            )
        ]
    )

    assert len(previews) == 1
    assert previews[0].viewer_path == "kunde/p1/projekt"
    assert previews[0].s3_path == "pointclouds/kunde/p1/projekt"
    assert previews[0].pointclouds[0].crs == "EPSG:25832"
    assert previews[0].pointclouds[0].s3_path == "pointclouds/kunde/p1/projekt/a/source.copc.laz"
    assert previews[0].pointclouds[0].visible is False
    assert len(previews[0].models) == 1
    assert previews[0].models[0].model_id == "fassade"
    assert previews[0].models[0].name == "Fassade"
    assert previews[0].models[0].s3_path.endswith("/models/fassade/versions/v1")
    assert previews[0].models[0].vertical_crs == "EPSG:7837"


def test_load_project_previews_accepts_callable_or_service_object():
    rows = [({"id": "p1", "kunde": "Kunde", "projekt": "Projekt"}, False)]

    class Provider:
        def list_projects_for_management(self):
            return rows

    assert load_project_previews(lambda: rows)[0].project_id == "p1"
    assert load_project_previews(Provider())[0].project_id == "p1"
