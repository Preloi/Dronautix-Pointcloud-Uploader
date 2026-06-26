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


def test_load_project_previews_accepts_callable_or_service_object():
    rows = [({"id": "p1", "kunde": "Kunde", "projekt": "Projekt"}, False)]

    class Provider:
        def list_projects_for_management(self):
            return rows

    assert load_project_previews(lambda: rows)[0].project_id == "p1"
    assert load_project_previews(Provider())[0].project_id == "p1"
