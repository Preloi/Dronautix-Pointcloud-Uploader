import copy

import pytest

from dronautix_uploader.core.constants import S3_DISABLED_PROJECTS_KEY
from dronautix_uploader.core.project_management_service import ProjectManagementService


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **_kwargs):
        return self.pages


class FakeS3Client:
    def __init__(self, pages=None):
        self.pages = pages or []
        self.copies = []
        self.deleted = []
        self.downloads = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return FakePaginator(self.pages)

    def copy_object(self, **kwargs):
        self.copies.append(kwargs)

    def delete_objects(self, Bucket, Delete):
        keys = [entry["Key"] for entry in Delete["Objects"]]
        self.deleted.extend(keys)
        return {"Deleted": Delete["Objects"]}

    def download_file(self, bucket, key, local_path, Callback=None):
        self.downloads.append((bucket, key, local_path))
        with open(local_path, "wb") as handle:
            handle.write(b"downloaded")
        if Callback is not None:
            Callback(10)


class FakeRepository:
    bucket_name = "test-bucket"

    def __init__(self, index_data, deleted_data=None):
        self.index_data = copy.deepcopy(index_data)
        self.deleted_data = copy.deepcopy(deleted_data or {"deleted_projects": []})
        self.saved_indexes = []
        self.saved_deleted = []

    def load_projects_index(self):
        return self.index_data

    def save_projects_index(self, index_data):
        self.saved_indexes.append(copy.deepcopy(index_data))

    def load_deleted_projects(self):
        return self.deleted_data

    def save_deleted_projects(self, deleted_data):
        self.saved_deleted.append(copy.deepcopy(deleted_data))


def make_service(repository, s3_client=None, project_id="newid", timestamp="2026-06-21T13:00:00"):
    return ProjectManagementService(
        repository=repository,
        s3_client=s3_client or FakeS3Client(),
        id_factory=lambda: project_id,
        timestamp_factory=lambda: timestamp,
    )


def test_list_projects_for_management_returns_active_and_disabled_with_status():
    repository = FakeRepository(
        {
            "projects": [{"id": "active"}],
            S3_DISABLED_PROJECTS_KEY: [{"id": "disabled"}],
        }
    )

    assert make_service(repository).list_projects_for_management() == [
        ({"id": "active"}, False),
        ({"id": "disabled"}, True),
    ]


def test_rename_project_updates_active_project_without_changing_paths():
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "active",
                    "kunde": "Alt",
                    "projekt": "Altprojekt",
                    "viewer_path": "alt/active/altprojekt",
                    "s3_path": "pointclouds/alt/active/altprojekt",
                    "pointclouds": [{"name": "A"}, {"name": "B"}],
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )

    result = make_service(repository).rename_project("active", "Neu", "Neuprojekt", ("Cloud A", "Cloud B"))
    renamed = repository.index_data["projects"][0]

    assert result.status == "success"
    assert result.project_id == "active"
    assert result.message == "Projekt wurde umbenannt."
    assert renamed["kunde"] == "Neu"
    assert renamed["projekt"] == "Neuprojekt"
    assert renamed["viewer_path"] == "alt/active/altprojekt"
    assert renamed["s3_path"] == "pointclouds/alt/active/altprojekt"
    assert [cloud["name"] for cloud in renamed["pointclouds"]] == ["Cloud A", "Cloud B"]
    assert repository.index_data["projects"][0] == renamed
    assert repository.saved_indexes[-1]["projects"][0] == renamed


def test_rename_project_updates_disabled_project_and_preserves_disabled_status():
    repository = FakeRepository(
        {
            "projects": [{"id": "active", "kunde": "Aktiv"}],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "id": "disabled",
                    "kunde": "Alt",
                    "projekt": "Altprojekt",
                    "disabled_at": "2026-06-20T12:00:00",
                    "viewer_path": "alt/disabled/altprojekt",
                    "s3_path": "pointclouds/alt/disabled/altprojekt",
                }
            ],
        }
    )

    result = make_service(repository).rename_project("disabled", "Neu", "Neuprojekt")
    renamed = repository.index_data[S3_DISABLED_PROJECTS_KEY][0]

    assert result.status == "success"
    assert result.project_id == "disabled"
    assert repository.index_data["projects"] == [{"id": "active", "kunde": "Aktiv"}]
    assert renamed["disabled_at"] == "2026-06-20T12:00:00"
    assert renamed["viewer_path"] == "alt/disabled/altprojekt"
    assert renamed["s3_path"] == "pointclouds/alt/disabled/altprojekt"


def test_delete_disabled_project_removes_disabled_and_writes_deleted_entry():
    s3_client = FakeS3Client(
        pages=[{"Contents": [{"Key": "pointclouds/old/cloud.js", "Size": 10}]}]
    )
    repository = FakeRepository(
        {
            "projects": [{"id": "active"}],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "id": "disabled",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "s3_path": "pointclouds/old",
                    "link": "https://viewer/?id=disabled",
                }
            ],
        },
        deleted_data={"deleted_projects": [{"id": "other", "s3_path": "pointclouds/other"}]},
    )

    result = make_service(repository, s3_client=s3_client).delete_project("disabled")

    assert result.status == "success"
    assert result.deleted_keys == ("pointclouds/old/cloud.js",)
    assert repository.index_data[S3_DISABLED_PROJECTS_KEY] == []
    assert repository.deleted_data["deleted_projects"][0]["id"] == "disabled"
    assert repository.deleted_data["deleted_projects"][0]["deleted_at"] == "2026-06-21T13:00:00"
    assert repository.saved_indexes[-1][S3_DISABLED_PROJECTS_KEY] == []
    assert repository.saved_deleted[-1]["deleted_projects"][0]["id"] == "disabled"


def test_duplicate_from_multi_project_remaps_paths_and_inserts_active_project():
    s3_client = FakeS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": "pointclouds/alt/sourceid/altprojekt/cloud_a/cloud.js", "Size": 10},
                    {"Key": "pointclouds/alt/sourceid/altprojekt/cloud_a/metadata.json", "Size": 10},
                ]
            }
        ]
    )
    repository = FakeRepository(
        {
            "projects": [{"id": "existing"}],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "id": "sourceid",
                    "kunde": "Alt",
                    "projekt": "Altprojekt",
                    "format": "multi",
                    "link": "https://viewer/?id=sourceid",
                    "viewer_path": "alt/sourceid/altprojekt",
                    "s3_path": "pointclouds/alt/sourceid/altprojekt",
                    "disabled_at": "2026-06-20T12:00:00",
                    "pointclouds": [
                        {
                            "name": "Cloud A",
                            "viewer_path": "alt/sourceid/altprojekt/cloud_a",
                            "s3_path": "pointclouds/alt/sourceid/altprojekt/cloud_a",
                        }
                    ],
                }
            ],
        }
    )

    result = make_service(repository, s3_client=s3_client).duplicate_project("sourceid", "Neu Kunde", "Neu Projekt")

    assert result.status == "success"
    assert result.project_id == "newid"
    assert result.uploaded_keys == (
        "pointclouds/neu_kunde/newid/neu_projekt/cloud_a/cloud.js",
        "pointclouds/neu_kunde/newid/neu_projekt/cloud_a/metadata.json",
    )
    duplicated = repository.index_data["projects"][0]
    assert duplicated["id"] == "newid"
    assert duplicated["viewer_path"] == "neu_kunde/newid/neu_projekt"
    assert duplicated["s3_path"] == "pointclouds/neu_kunde/newid/neu_projekt"
    assert duplicated["pointclouds"][0]["viewer_path"] == "neu_kunde/newid/neu_projekt/cloud_a"
    assert duplicated["pointclouds"][0]["s3_path"] == "pointclouds/neu_kunde/newid/neu_projekt/cloud_a"
    assert "disabled_at" not in duplicated
    assert repository.index_data[S3_DISABLED_PROJECTS_KEY][0]["id"] == "sourceid"
    assert repository.saved_indexes[-1]["projects"][0]["id"] == "newid"


def test_download_project_uses_existing_project_s3_path_without_saving_index(tmp_path):
    s3_client = FakeS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": "pointclouds/kunde/id/projekt/cloud.js", "Size": 10},
                    {"Key": "pointclouds/kunde/id/projekt/metadata.json", "Size": 20},
                ]
            }
        ]
    )
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "id",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "s3_path": "pointclouds/kunde/id/projekt",
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )

    result = make_service(repository, s3_client=s3_client).download_project("id", str(tmp_path))

    assert result.status == "success"
    assert result.download_dir == str(tmp_path / "kunde_projekt_id")
    assert result.downloaded_files == (
        str(tmp_path / "kunde_projekt_id" / "cloud.js"),
        str(tmp_path / "kunde_projekt_id" / "metadata.json"),
    )
    assert s3_client.downloads[0][0] == "test-bucket"
    assert repository.saved_indexes == []


def test_download_project_returns_cancelled_result_with_partial_files(tmp_path):
    s3_client = FakeS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": "pointclouds/kunde/id/projekt/cloud.js", "Size": 10},
                    {"Key": "pointclouds/kunde/id/projekt/metadata.json", "Size": 20},
                ]
            }
        ]
    )
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "id",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "s3_path": "pointclouds/kunde/id/projekt",
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )
    cancel_checks = {"count": 0}

    def cancel_after_first_file():
        cancel_checks["count"] += 1
        return cancel_checks["count"] >= 3

    result = make_service(repository, s3_client=s3_client).download_project(
        "id",
        str(tmp_path),
        cancel_requested=cancel_after_first_file,
    )

    assert result.status == "cancelled"
    assert result.message == "Download wurde abgebrochen."
    assert result.download_dir == str(tmp_path / "kunde_projekt_id")
    assert result.downloaded_files == (str(tmp_path / "kunde_projekt_id" / "cloud.js"),)


def test_set_project_link_state_moves_active_project_to_disabled_and_saves_index():
    repository = FakeRepository(
        {
            "projects": [{"id": "active", "kunde": "Kunde", "projekt": "Projekt"}],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )

    result = make_service(repository).set_project_link_state("active", True)

    assert result.status == "success"
    assert result.message == "Projekt-Link wurde deaktiviert."
    assert repository.index_data["projects"] == []
    assert repository.index_data[S3_DISABLED_PROJECTS_KEY] == [
        {
            "id": "active",
            "kunde": "Kunde",
            "projekt": "Projekt",
            "disabled_at": "2026-06-21T13:00:00",
        }
    ]
    assert repository.saved_indexes[-1][S3_DISABLED_PROJECTS_KEY][0]["id"] == "active"


def test_set_project_link_state_moves_disabled_project_to_active_and_removes_disabled_at():
    repository = FakeRepository(
        {
            "projects": [],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "id": "disabled",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "disabled_at": "old",
                }
            ],
        }
    )

    result = make_service(repository).set_project_link_state("disabled", False)

    assert result.status == "success"
    assert result.message == "Projekt-Link wurde aktiviert."
    assert repository.index_data["projects"] == [{"id": "disabled", "kunde": "Kunde", "projekt": "Projekt"}]
    assert repository.index_data[S3_DISABLED_PROJECTS_KEY] == []


def test_set_project_link_state_noops_when_status_already_matches():
    repository = FakeRepository(
        {
            "projects": [{"id": "active"}],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )

    result = make_service(repository).set_project_link_state("active", False)

    assert result.status == "success"
    assert result.message == "Projekt-Link ist bereits aktiv."
    assert repository.saved_indexes == []


@pytest.mark.parametrize("method,args", [
    ("rename_project", ("missing", "Kunde", "Projekt")),
    ("delete_project", ("missing",)),
    ("duplicate_project", ("missing", "Kunde", "Projekt")),
    ("download_project", ("missing", "C:/Downloads")),
    ("set_project_link_state", ("missing", True)),
])
def test_unknown_project_id_raises_value_error(method, args):
    repository = FakeRepository({"projects": [{"id": "known"}], S3_DISABLED_PROJECTS_KEY: []})
    service = make_service(repository)

    with pytest.raises(ValueError, match="missing"):
        getattr(service, method)(*args)
