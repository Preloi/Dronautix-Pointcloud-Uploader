import copy
import io
import json
import os

import pytest

from dronautix_uploader.core.constants import S3_DISABLED_PROJECTS_KEY
from dronautix_uploader.core.contracts import (
    DownloadRequest,
    MultiReplacementRequest,
    PointcloudSource,
    ProjectDeleteRequest,
    ProjectLinkStateUpdate,
    ProjectMetadataUpdate,
    ReplacementRequest,
    UploadRequest,
)
from dronautix_uploader.core.project_management_service import ProjectManagementService
from dronautix_uploader.core.service_api import CoreServiceApi, build_upload_workflow_request
from dronautix_uploader.core.upload_workflow_service import UploadWorkflowService


class FakePaginator:
    def __init__(self, client):
        self.client = client

    def paginate(self, **kwargs):
        self.client.prefixes.append(kwargs.get("Prefix", ""))
        return self.client.pages


class FakeS3Client:
    def __init__(self, pages=None):
        self.pages = pages or []
        self.prefixes = []
        self.uploads = []
        self.deleted = []
        self.downloads = []
        self.copies = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return FakePaginator(self)

    def get_object(self, Bucket, Key):
        data = self.objects[Key]
        return {"Body": io.BytesIO(json.dumps(data).encode("utf-8"))}

    def upload_file(self, local_path, bucket, key, ExtraArgs=None, Callback=None):
        if Callback:
            Callback(os.path.getsize(local_path))
        self.uploads.append((local_path, bucket, key, ExtraArgs))

    def delete_objects(self, Bucket, Delete):
        keys = [entry["Key"] for entry in Delete["Objects"]]
        self.deleted.extend(keys)
        return {"Deleted": Delete["Objects"]}

    def download_file(self, bucket, key, local_path, Callback=None):
        self.downloads.append((bucket, key, local_path))
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as handle:
            handle.write(b"downloaded")
        if Callback:
            Callback(10)

    def copy_object(self, Bucket, CopySource, Key, CacheControl=None, MetadataDirective=None):
        self.copies.append((Bucket, CopySource, Key, CacheControl, MetadataDirective))


class FakeRepository:
    bucket_name = "test-bucket"

    def __init__(self, index_data=None, deleted_data=None, save_result=True):
        self.index_data = copy.deepcopy(index_data or {"projects": [], S3_DISABLED_PROJECTS_KEY: []})
        self.deleted_data = copy.deepcopy(deleted_data or {"deleted_projects": []})
        self.save_result = save_result
        self.saved_indexes = []
        self.saved_deleted = []

    def load_projects_index(self):
        return self.index_data

    def save_projects_index(self, index_data):
        self.saved_indexes.append(copy.deepcopy(index_data))
        return self.save_result

    def load_deleted_projects(self):
        return self.deleted_data

    def save_deleted_projects(self, deleted_data):
        self.saved_deleted.append(copy.deepcopy(deleted_data))
        return self.save_result


def make_api(repository, s3_client=None):
    client = s3_client or FakeS3Client()
    upload_service = UploadWorkflowService(
        repository=repository,
        s3_client=client,
        id_factory=lambda: "newid",
        timestamp_factory=lambda: "2026-06-21T12:00:00",
    )
    project_service = ProjectManagementService(
        repository=repository,
        s3_client=client,
        id_factory=lambda: "newid",
        timestamp_factory=lambda: "2026-06-21T12:00:00",
    )
    return CoreServiceApi(upload_service=upload_service, project_service=project_service), client


class CapturingUploadService:
    def __init__(self):
        self.request = None

    def upload_new_project(self, request, *, on_progress=None, converter_runner=None):
        self.request = request
        return "upload"


class CapturingProjectService:
    def __init__(self):
        self.single_args = None
        self.single_kwargs = None
        self.multi_args = None
        self.multi_kwargs = None

    def replace_single_project_pointcloud_from_source(self, *args, **kwargs):
        self.single_args = args
        self.single_kwargs = kwargs
        return "single"

    def replace_project_pointclouds_from_sources(self, *args, **kwargs):
        self.multi_args = args
        self.multi_kwargs = kwargs
        return "multi"


def test_build_upload_workflow_request_maps_contract_sources_and_crs_defaults():
    first = PointcloudSource("first.copc.laz", crs_info={"value": "EPSG:25832"})
    second = PointcloudSource("second.copc.laz")

    workflow_request = build_upload_workflow_request(
        UploadRequest(
            sources=(first, second),
            kunde="Kunde",
            projekt="Projekt",
            aws_access="access",
            aws_secret="secret",
            converter_path="converter.exe",
            output_base_dir="C:/out",
            crs_input="EPSG:4326",
            vertical_input="DHHN2016",
            overwrite=True,
        )
    )

    assert workflow_request.source_paths == ("first.copc.laz", "second.copc.laz")
    assert workflow_request.kunde == "Kunde"
    assert workflow_request.projekt == "Projekt"
    assert workflow_request.converter_path == "converter.exe"
    assert workflow_request.output_base_dir == "C:/out"
    assert workflow_request.overwrite is True
    assert workflow_request.crs_info_by_source_path == {
        "first.copc.laz": {"value": "EPSG:25832"},
        "second.copc.laz": {
            "value": "EPSG:4326",
            "projection": "EPSG:4326",
            "vertical_crs": "DHHN2016",
            "vertical_epsg": "DHHN2016",
            "vertical_projection": "DHHN2016",
        },
    }


def test_core_service_api_propagates_overwrite_to_upload_and_replacement_pipelines():
    upload_service = CapturingUploadService()
    project_service = CapturingProjectService()
    api = CoreServiceApi(upload_service=upload_service, project_service=project_service)
    project = {"id": "project", "s3_path": "pointclouds/kunde/project/projekt"}

    assert api.upload_project(
        UploadRequest(
            sources=(PointcloudSource("scan.laz"),),
            kunde="Kunde",
            projekt="Projekt",
            aws_access="access",
            aws_secret="secret",
            overwrite=True,
        )
    ) == "upload"
    assert api.replace_pointcloud(
        ReplacementRequest(
            project=project,
            replacement=PointcloudSource("replacement.laz"),
            aws_access="access",
            aws_secret="secret",
            overwrite=True,
        )
    ) == "single"
    assert api.replace_pointclouds(
        MultiReplacementRequest(
            project=project,
            replacements=(PointcloudSource("a.laz"), PointcloudSource("b.laz")),
            aws_access="access",
            aws_secret="secret",
            overwrite=True,
        )
    ) == "multi"

    assert upload_service.request.overwrite is True
    assert project_service.single_kwargs["overwrite"] is True
    assert project_service.multi_kwargs["overwrite"] is True


def test_core_service_api_upload_project_uses_contract_dataclass_and_existing_pipeline(tmp_path):
    source = tmp_path / "scan.copc.laz"
    source.write_bytes(b"copc")
    repository = FakeRepository()
    api, s3_client = make_api(repository)

    result = api.upload_project(
        UploadRequest(
            sources=(PointcloudSource(str(source), crs_info={"value": "EPSG:25832"}),),
            kunde="Kunde",
            projekt="Projekt",
            aws_access="access",
            aws_secret="secret",
        )
    )

    assert result.status == "success"
    assert repository.index_data["projects"][0]["id"] == "newid"
    assert repository.index_data["projects"][0]["crs"] == "EPSG:25832"
    assert s3_client.uploads[0][2] == "pointclouds/kunde/newid/projekt/source.copc.laz"


def test_core_service_api_routes_metadata_update_and_download_contracts(tmp_path):
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "project",
                    "kunde": "Alt",
                    "projekt": "Altprojekt",
                    "s3_path": "pointclouds/kunde/project/projekt",
                    "pointclouds": [{"name": "Alt Cloud"}],
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )
    s3_client = FakeS3Client(
        pages=[{"Contents": [{"Key": "pointclouds/kunde/project/projekt/cloud.js", "Size": 10}]}]
    )
    api, _client = make_api(repository, s3_client=s3_client)

    rename_result = api.rename_project_metadata(
        ProjectMetadataUpdate(
            project_id="project",
            kunde="Neu",
            projekt="Neuprojekt",
            pointcloud_names=("Neue Cloud",),
        )
    )
    download = api.download_project(
        DownloadRequest(
            project=repository.index_data["projects"][0],
            target_dir=str(tmp_path / "download"),
            aws_access="access",
            aws_secret="secret",
        )
    )

    assert rename_result.status == "success"
    assert rename_result.project_id == "project"
    assert repository.index_data["projects"][0]["kunde"] == "Neu"
    assert repository.index_data["projects"][0]["projekt"] == "Neuprojekt"
    assert repository.index_data["projects"][0]["pointclouds"][0]["name"] == "Neue Cloud"
    assert download.status == "success"
    assert s3_client.downloads[0][1] == "pointclouds/kunde/project/projekt/cloud.js"


def test_core_service_api_routes_duplicate_project_without_service_escape_hatch():
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "source",
                    "kunde": "Alt",
                    "projekt": "Altprojekt",
                    "link": "https://pointcloud.dronautix.at/index.html?id=source",
                    "viewer_path": "alt/source/altprojekt",
                    "s3_path": "pointclouds/alt/source/altprojekt",
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )
    s3_client = FakeS3Client(
        pages=[{"Contents": [{"Key": "pointclouds/alt/source/altprojekt/cloud.js", "Size": 10}]}]
    )
    api, _client = make_api(repository, s3_client=s3_client)

    result = api.duplicate_project("source", "Neu", "Neuprojekt")

    assert result.status == "success"
    assert result.project_id == "newid"
    assert repository.index_data["projects"][0]["id"] == "newid"
    assert repository.index_data["projects"][0]["kunde"] == "Neu"
    assert s3_client.copies[0][2] == "pointclouds/neu/newid/neuprojekt/cloud.js"


def test_core_service_api_routes_delete_and_link_state_contracts():
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "delete-me",
                    "kunde": "Kunde",
                    "projekt": "Delete",
                    "link": "https://pointcloud.dronautix.at/index.html?id=delete-me",
                    "s3_path": "pointclouds/kunde/delete/delete",
                },
                {
                    "id": "toggle-me",
                    "kunde": "Kunde",
                    "projekt": "Toggle",
                    "link": "https://pointcloud.dronautix.at/index.html?id=toggle-me",
                    "s3_path": "pointclouds/kunde/toggle/toggle",
                },
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )
    s3_client = FakeS3Client(
        pages=[{"Contents": [{"Key": "pointclouds/kunde/delete/delete/cloud.js", "Size": 10}]}]
    )
    api, _client = make_api(repository, s3_client=s3_client)

    delete_result = api.delete_project(ProjectDeleteRequest(project_id="delete-me", aws_access="access"))
    disable_result = api.set_project_link_state(ProjectLinkStateUpdate(project_id="toggle-me", disabled=True))
    enable_result = api.set_project_link_state(ProjectLinkStateUpdate(project_id="toggle-me", disabled=False))

    assert delete_result.status == "success"
    assert delete_result.project_id == "delete-me"
    assert repository.deleted_data["deleted_projects"][0]["id"] == "delete-me"
    assert "pointclouds/kunde/delete/delete/cloud.js" in s3_client.deleted
    assert disable_result.status == "success"
    assert enable_result.status == "success"
    assert [project["id"] for project in repository.index_data["projects"]] == ["toggle-me"]
    assert repository.index_data[S3_DISABLED_PROJECTS_KEY] == []


def test_core_service_api_routes_single_and_multi_replacement_contracts(tmp_path):
    single_source = tmp_path / "single.copc.laz"
    single_source.write_bytes(b"single")
    multi_source = tmp_path / "multi.copc.laz"
    multi_source.write_bytes(b"multi")
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "project",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "viewer_path": "kunde/project/projekt",
                    "s3_path": "pointclouds/kunde/project/projekt",
                    "pointclouds": [
                        {
                            "name": "Alt",
                            "viewer_path": "kunde/project/projekt/alt",
                            "s3_path": "pointclouds/kunde/project/projekt/alt",
                        }
                    ],
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )
    s3_client = FakeS3Client(
        pages=[
            {"Contents": [{"Key": "pointclouds/kunde/project/projekt/alt/cloud.js", "Size": 10}]}
        ]
    )
    api, _client = make_api(repository, s3_client=s3_client)

    single_result = api.replace_pointcloud(
        ReplacementRequest(
            project=repository.index_data["projects"][0],
            replacement=PointcloudSource(str(single_source), crs_info={"value": "EPSG:25832"}),
            target_pointcloud=repository.index_data["projects"][0]["pointclouds"][0],
            aws_access="access",
            aws_secret="secret",
        )
    )
    multi_result = api.replace_pointclouds(
        MultiReplacementRequest(
            project=repository.index_data["projects"][0],
            replacements=(
                PointcloudSource(
                    str(multi_source),
                    name="Benannte Cloud",
                    slug="custom_slug",
                    crs_info={"value": "EPSG:4326"},
                ),
            ),
            aws_access="access",
            aws_secret="secret",
        )
    )

    assert single_result.status == "success"
    assert multi_result.status in {"success", "partial"}
    assert repository.index_data["projects"][0]["pointclouds"][0]["name"] == "Benannte Cloud"
    assert repository.index_data["projects"][0]["pointclouds"][0]["crs"] == "EPSG:4326"
    uploaded_keys = [upload[2] for upload in s3_client.uploads]
    assert "pointclouds/kunde/project/projekt/single/source.copc.laz" in uploaded_keys
    assert "pointclouds/kunde/project/projekt/custom_slug/source.copc.laz" in uploaded_keys


def test_core_service_api_requires_explicit_target_for_multi_cloud_single_replacement(tmp_path):
    source = tmp_path / "scan.copc.laz"
    source.write_bytes(b"copc")
    repository = FakeRepository()
    api, _s3_client = make_api(repository)

    with pytest.raises(ValueError, match="konkrete Ziel-Punktwolke"):
        api.replace_pointcloud(
            ReplacementRequest(
                project={
                    "id": "project",
                    "s3_path": "pointclouds/kunde/project/projekt",
                    "pointclouds": [
                        {"s3_path": "pointclouds/kunde/project/projekt/a"},
                        {"s3_path": "pointclouds/kunde/project/projekt/b"},
                    ],
                },
                replacement=PointcloudSource(str(source)),
                aws_access="access",
                aws_secret="secret",
            )
        )
