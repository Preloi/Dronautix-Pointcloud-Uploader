import copy

import pytest

from dronautix_uploader.core.constants import S3_DISABLED_PROJECTS_KEY
from dronautix_uploader.core.project_management_service import ProjectManagementService
from dronautix_uploader.core.project_operations import PreparedCloudUpload


class FakePaginator:
    def __init__(self, pages, prefixes):
        self.pages = pages
        self.prefixes = prefixes

    def paginate(self, **kwargs):
        self.prefixes.append(kwargs.get("Prefix"))
        return self.pages


class FakeS3Client:
    def __init__(self, pages=None, fail_on_key=""):
        self.pages = pages or []
        self.fail_on_key = fail_on_key
        self.prefixes = []
        self.uploads = []
        self.deleted = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return FakePaginator(self.pages, self.prefixes)

    def upload_file(self, local_path, bucket, key, ExtraArgs=None, Callback=None):
        if key == self.fail_on_key:
            raise RuntimeError("simulated upload failure")
        if Callback:
            Callback(1)
        self.uploads.append((bucket, key, ExtraArgs))

    def delete_objects(self, Bucket, Delete):
        keys = [entry["Key"] for entry in Delete["Objects"]]
        self.deleted.extend(keys)
        return {"Deleted": Delete["Objects"]}


class FakeRepository:
    bucket_name = "test-bucket"

    def __init__(self, index_data, save_result=True):
        self.index_data = copy.deepcopy(index_data)
        self.save_result = save_result
        self.saved_indexes = []

    def load_projects_index(self):
        return self.index_data

    def save_projects_index(self, index_data):
        self.saved_indexes.append(copy.deepcopy(index_data))
        return self.save_result


def make_service(repository, s3_client=None):
    return ProjectManagementService(
        repository=repository,
        s3_client=s3_client or FakeS3Client(),
        id_factory=lambda: "newid",
        timestamp_factory=lambda: "2026-06-21T13:00:00",
        data_version_factory=lambda: "versionid",
    )


def make_prepared_cloud(tmp_path, *, name, slug, viewer_root, s3_root, content=b"data"):
    local_file = tmp_path / f"{slug}.bin"
    local_file.write_bytes(content)
    s3_prefix = f"{s3_root}/{slug}"
    return PreparedCloudUpload(
        name=name,
        slug=slug,
        input_format="copc",
        viewer_path=f"{viewer_root}/{slug}/source.copc.laz",
        s3_path=f"{s3_prefix}/source.copc.laz",
        s3_prefix=s3_prefix,
        files_to_upload=((str(local_file), f"{s3_prefix}/source.copc.laz"),),
        crs_info={"value": "EPSG:25832"},
    )


def test_full_replacement_disabled_multi_project_keeps_disabled_and_deletes_old_orphans(tmp_path):
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    repository = FakeRepository(
        {
            "projects": [{"id": "active"}],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "id": "project",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "format": "multi",
                    "viewer_path": viewer_root,
                    "s3_path": project_root,
                    "disabled_at": "2026-06-20T12:00:00",
                    "pointclouds": [
                        {"name": "Old A", "s3_path": f"{project_root}/old_a"},
                        {"name": "Old B", "s3_path": f"{project_root}/old_b"},
                    ],
                }
            ],
        }
    )
    s3_client = FakeS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": f"{project_root}/old_a/cloud.js", "Size": 10},
                    {"Key": f"{project_root}/old_b/metadata.json", "Size": 10},
                ]
            }
        ]
    )
    prepared = (
        make_prepared_cloud(tmp_path, name="New A", slug="new_a", viewer_root=viewer_root, s3_root=project_root),
        make_prepared_cloud(tmp_path, name="New B", slug="new_b", viewer_root=viewer_root, s3_root=project_root),
    )

    result = make_service(repository, s3_client).replace_project_pointclouds("project", prepared)

    assert result.status == "success"
    assert s3_client.prefixes == [project_root]
    assert repository.index_data["projects"] == [{"id": "active"}]
    disabled_project = repository.index_data[S3_DISABLED_PROJECTS_KEY][0]
    assert disabled_project["id"] == "project"
    assert disabled_project["disabled_at"] == "2026-06-20T12:00:00"
    assert disabled_project["format"] == "multi"
    assert [cloud["name"] for cloud in disabled_project["pointclouds"]] == ["New A", "New B"]
    assert [cloud["s3_path"] for cloud in disabled_project["pointclouds"]] == [
        f"{project_root}/versions/versionid/new_a/source.copc.laz",
        f"{project_root}/versions/versionid/new_b/source.copc.laz",
    ]
    assert disabled_project["history"][-1] == {
        "timestamp": "2026-06-21T13:00:00",
        "message": "Alle Punktwolken wurden ausgetauscht.",
    }
    assert s3_client.deleted == [
        f"{project_root}/old_a/cloud.js",
        f"{project_root}/old_b/metadata.json",
    ]
    assert repository.saved_indexes[-1][S3_DISABLED_PROJECTS_KEY][0]["id"] == "project"


def test_single_replacement_active_multi_project_replaces_only_target_and_deletes_target_keys(tmp_path):
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    target_path = f"{project_root}/cloud_b"
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "project",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "format": "multi",
                    "viewer_path": viewer_root,
                    "s3_path": project_root,
                    "pointclouds": [
                        {
                            "name": "Cloud A",
                            "format": "potree",
                            "viewer_path": f"{viewer_root}/cloud_a",
                            "s3_path": f"{project_root}/cloud_a",
                        },
                        {
                            "name": "Cloud B",
                            "format": "potree",
                            "viewer_path": f"{viewer_root}/cloud_b",
                            "s3_path": target_path,
                        },
                    ],
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )
    s3_client = FakeS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": f"{target_path}/cloud.js", "Size": 10},
                    {"Key": f"{target_path}/metadata.json", "Size": 10},
                ]
            }
        ]
    )
    prepared = make_prepared_cloud(
        tmp_path,
        name="Cloud B Replacement",
        slug="cloud_b_new",
        viewer_root=viewer_root,
        s3_root=project_root,
    )

    result = make_service(repository, s3_client).replace_single_project_pointcloud(
        "project",
        target_path,
        prepared,
    )

    assert result.status == "success"
    assert s3_client.prefixes == [target_path]
    pointclouds = repository.index_data["projects"][0]["pointclouds"]
    assert pointclouds[0] == {
        "name": "Cloud A",
        "format": "potree",
        "viewer_path": f"{viewer_root}/cloud_a",
        "s3_path": f"{project_root}/cloud_a",
    }
    assert pointclouds[1]["name"] == "Cloud B Replacement"
    assert pointclouds[1]["s3_path"] == f"{project_root}/versions/versionid/cloud_b_new/source.copc.laz"
    assert s3_client.deleted == [
        f"{target_path}/cloud.js",
        f"{target_path}/metadata.json",
    ]
    assert f"{project_root}/cloud_a/cloud.js" not in s3_client.deleted
    assert repository.saved_indexes[-1]["projects"][0]["pointclouds"][1]["name"] == "Cloud B Replacement"
    assert repository.index_data["projects"][0]["history"][-1] == {
        "timestamp": "2026-06-21T13:00:00",
        "message": "Punktwolke 'Cloud B' wurde ausgetauscht.",
    }


def test_single_replacement_active_legacy_single_project_keeps_top_level_shape_and_deletes_old_keys(tmp_path):
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "project",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "format": "copc",
                    "link": "https://viewer/?id=project",
                    "viewer_path": f"{viewer_root}/source.copc.laz",
                    "s3_path": f"{project_root}/source.copc.laz",
                    "crs": "EPSG:25832",
                    "projection": "EPSG:25832",
                    "crs_info": {"value": "EPSG:25832"},
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )
    s3_client = FakeS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": f"{project_root}/source.copc.laz", "Size": 10},
                    {"Key": f"{project_root}/old.bin", "Size": 10},
                ]
            }
        ]
    )
    prepared = PreparedCloudUpload(
        name="Replacement",
        slug="",
        input_format="copc",
        viewer_path=f"{viewer_root}/source.copc.laz",
        s3_path=f"{project_root}/source.copc.laz",
        s3_prefix=project_root,
        files_to_upload=((str(tmp_path / "replacement.copc.laz"), f"{project_root}/source.copc.laz"),),
        crs_info={"value": "EPSG:4326"},
    )
    (tmp_path / "replacement.copc.laz").write_bytes(b"replacement")

    result = make_service(repository, s3_client).replace_single_project_pointcloud(
        "project",
        f"{project_root}/source.copc.laz",
        prepared,
    )

    project = repository.index_data["projects"][0]
    assert result.status == "success"
    assert "pointclouds" not in project
    assert project["format"] == "copc"
    assert project["viewer_path"] == f"{viewer_root}/versions/versionid/source.copc.laz"
    assert project["s3_path"] == f"{project_root}/versions/versionid"
    assert project["crs"] == "EPSG:4326"
    assert project["crs_info"] == {"value": "EPSG:4326"}
    assert s3_client.prefixes == [f"{project_root}/source.copc.laz"]
    assert s3_client.deleted == [
        f"{project_root}/old.bin",
        f"{project_root}/source.copc.laz",
    ]


def test_replace_unknown_project_id_raises_value_error(tmp_path):
    repository = FakeRepository({"projects": [{"id": "known"}], S3_DISABLED_PROJECTS_KEY: []})
    prepared = (make_prepared_cloud(tmp_path, name="New", slug="new", viewer_root="viewer", s3_root="pointclouds"),)

    with pytest.raises(ValueError, match="missing"):
        make_service(repository).replace_project_pointclouds("missing", prepared)


def test_versioned_project_root_is_not_nested_again():
    service = make_service(FakeRepository({"projects": []}))

    assert service._stable_project_roots(
        {
            "viewer_path": "kunde/project/projekt/versions/old/source.copc.laz",
            "s3_path": "pointclouds/kunde/project/projekt/versions/old",
        }
    ) == (
        "kunde/project/projekt",
        "pointclouds/kunde/project/projekt",
    )


def test_stable_project_root_preserves_regular_versions_path_segments():
    service = make_service(FakeRepository({"projects": []}))

    assert service._stable_project_roots(
        {
            "viewer_path": "versions/project/projekt",
            "s3_path": "pointclouds/versions/project/projekt",
        }
    ) == (
        "versions/project/projekt",
        "pointclouds/versions/project/projekt",
    )
    assert service._stable_project_roots(
        {
            "viewer_path": "kunde/project/versions/versions/old/source.copc.laz",
            "s3_path": "pointclouds/kunde/project/versions/versions/old",
        }
    ) == (
        "kunde/project/versions",
        "pointclouds/kunde/project/versions",
    )


def test_replace_unknown_target_pointcloud_path_raises_value_error(tmp_path):
    project_root = "pointclouds/kunde/project/projekt"
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "project",
                    "viewer_path": "kunde/project/projekt",
                    "s3_path": project_root,
                    "pointclouds": [{"name": "Cloud A", "s3_path": f"{project_root}/cloud_a"}],
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )
    prepared = make_prepared_cloud(
        tmp_path,
        name="New",
        slug="new",
        viewer_root="kunde/project/projekt",
        s3_root=project_root,
    )

    with pytest.raises(ValueError, match="missing/path"):
        make_service(repository).replace_single_project_pointcloud("project", "missing/path", prepared)


def test_full_replacement_rolls_back_uploaded_keys_when_index_save_fails(tmp_path):
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    original_index = {
        "projects": [
            {
                "id": "project",
                "projekt": "Projekt",
                "viewer_path": viewer_root,
                "s3_path": project_root,
                "pointclouds": [{"name": "Old", "s3_path": f"{project_root}/old"}],
            }
        ],
        S3_DISABLED_PROJECTS_KEY: [],
    }
    repository = FakeRepository(original_index, save_result=False)
    prepared = (
        make_prepared_cloud(tmp_path, name="New", slug="new", viewer_root=viewer_root, s3_root=project_root),
    )
    s3_client = FakeS3Client()

    with pytest.raises(RuntimeError, match="Projekt-Index"):
        make_service(repository, s3_client).replace_project_pointclouds("project", prepared)

    assert repository.index_data == original_index
    assert s3_client.deleted == [f"{project_root}/versions/versionid/new/source.copc.laz"]


def test_remove_multi_child_lists_and_deletes_only_the_exact_copc_object():
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    target_path = f"{project_root}/remove/source.copc.laz"
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "project",
                    "format": "multi",
                    "viewer_path": viewer_root,
                    "s3_path": project_root,
                    "pointclouds": [
                        {
                            "name": "Keep",
                            "format": "potree",
                            "viewer_path": f"{viewer_root}/keep",
                            "s3_path": f"{project_root}/keep",
                        },
                        {
                            "name": "Remove",
                            "format": "copc",
                            "viewer_path": f"{viewer_root}/remove/source.copc.laz",
                            "s3_path": target_path,
                        },
                    ],
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )
    s3_client = FakeS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": target_path, "Size": 10},
                    {"Key": f"{target_path}.backup", "Size": 10},
                    {"Key": f"{project_root}/keep/cloud.js", "Size": 10},
                ]
            }
        ]
    )

    result = make_service(repository, s3_client).remove_project_pointcloud("project", target_path)

    assert result.status == "success"
    assert s3_client.prefixes == [target_path]
    assert s3_client.deleted == [target_path]
    assert [cloud["name"] for cloud in repository.index_data["projects"][0]["pointclouds"]] == ["Keep"]


def test_full_replacement_keeps_models_metadata_and_model_s3_objects(tmp_path):
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    models = [
        {
            "id": "building",
            "viewer_path": f"{viewer_root}/models/building/versions/hash/model.json",
            "s3_path": f"{project_root}/models/building/versions/hash/scene.glb",
        }
    ]
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "project",
                    "format": "multi",
                    "viewer_path": viewer_root,
                    "s3_path": project_root,
                    "models": copy.deepcopy(models),
                    "pointclouds": [
                        {
                            "name": "Old",
                            "format": "copc",
                            "viewer_path": f"{viewer_root}/old/source.copc.laz",
                            "s3_path": f"{project_root}/old/source.copc.laz",
                        }
                    ],
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )
    s3_client = FakeS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": f"{project_root}/old/source.copc.laz", "Size": 10},
                    {"Key": f"{project_root}/models/building/versions/hash/scene.glb", "Size": 10},
                    {"Key": f"{project_root}/models/building/versions/hash/model.json", "Size": 10},
                ]
            }
        ]
    )
    prepared = (make_prepared_cloud(tmp_path, name="New", slug="new", viewer_root=viewer_root, s3_root=project_root),)

    result = make_service(repository, s3_client).replace_project_pointclouds("project", prepared)

    assert result.status == "success"
    assert repository.index_data["projects"][0]["models"] == models
    assert s3_client.deleted == [f"{project_root}/old/source.copc.laz"]
