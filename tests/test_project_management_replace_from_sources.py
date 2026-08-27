import copy
import json
import os

import pytest

from dronautix_uploader.core.constants import COPC_OBJECT_NAME, S3_DISABLED_PROJECTS_KEY
from dronautix_uploader.core.contracts import ProgressEvent
from dronautix_uploader.core.project_management_service import ProjectManagementService


class FakePaginator:
    def __init__(self, pages, prefixes):
        self.pages = pages
        self.prefixes = prefixes

    def paginate(self, **kwargs):
        self.prefixes.append(kwargs.get("Prefix"))
        return self.pages


class FakeS3Client:
    def __init__(self, pages=None):
        self.pages = pages or []
        self.prefixes = []
        self.uploads = []
        self.deleted = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return FakePaginator(self.pages, self.prefixes)

    def upload_file(self, local_path, bucket, key, ExtraArgs=None, Callback=None):
        if Callback:
            Callback(os.path.getsize(local_path))
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


def write_copc(tmp_path, name="Scan.copc.laz"):
    source = tmp_path / name
    source.write_bytes(b"copc")
    return source


def write_raw(tmp_path, name="Raw.laz"):
    source = tmp_path / name
    source.write_bytes(b"raw")
    return source


def write_converter(tmp_path):
    converter = tmp_path / "PotreeConverter.exe"
    converter.write_bytes(b"exe")
    return converter


def fake_converter_factory(calls, *, progress_message="converted"):
    def fake_converter(source_file, converter_path, output_dir, on_progress):
        calls.append((source_file, converter_path, output_dir))
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "cloud.js"), "w", encoding="utf-8") as file:
            file.write("cloud.js = {};")
        with open(os.path.join(output_dir, "metadata.json"), "w", encoding="utf-8") as file:
            file.write("{}")
        if on_progress:
            on_progress(ProgressEvent(kind="log", message=progress_message))

    return fake_converter


def test_full_replace_from_sources_disabled_multi_copc_and_raw_keeps_disabled_and_deletes_old_keys(tmp_path):
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    copc = write_copc(tmp_path, "Scan.copc.laz")
    raw = write_raw(tmp_path, "Raw.laz")
    converter = write_converter(tmp_path)
    output_base = tmp_path / "converted"
    converter_calls = []
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
                    "crs": "EPSG:25832",
                    "projection": "EPSG:25832",
                    "crs_info": {"value": "EPSG:25832"},
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

    result = make_service(repository, s3_client).replace_project_pointclouds_from_sources(
        "project",
        (str(copc), str(raw)),
        converter_path=str(converter),
        output_base_dir=str(output_base),
        overwrite=True,
        converter_runner=fake_converter_factory(converter_calls),
        crs_info_by_source_path={
            str(copc): {"value": "EPSG:25832", "epsg": 25832},
            str(raw): {"value": "EPSG:4326", "epsg": 4326},
        },
    )

    assert result.status == "success"
    assert [call[0] for call in converter_calls] == [str(raw)]
    assert s3_client.prefixes == [project_root]
    assert repository.index_data["projects"] == [{"id": "active"}]
    disabled_project = repository.index_data[S3_DISABLED_PROJECTS_KEY][0]
    assert disabled_project["id"] == "project"
    assert disabled_project["disabled_at"] == "2026-06-20T12:00:00"
    assert disabled_project["format"] == "multi"
    assert "crs" not in disabled_project
    assert "projection" not in disabled_project
    assert "crs_info" not in disabled_project
    assert [cloud["name"] for cloud in disabled_project["pointclouds"]] == ["Scan", "Raw"]
    assert [cloud["format"] for cloud in disabled_project["pointclouds"]] == ["copc", "potree"]
    assert [cloud["s3_path"] for cloud in disabled_project["pointclouds"]] == [
        f"{project_root}/versions/versionid/scan/{COPC_OBJECT_NAME}",
        f"{project_root}/versions/versionid/raw",
    ]
    assert [cloud["crs_info"] for cloud in disabled_project["pointclouds"]] == [
        {"value": "EPSG:25832", "epsg": "EPSG:25832"},
        {"value": "EPSG:4326", "epsg": "EPSG:4326"},
    ]
    raw_output_dir = converter_calls[0][2]
    raw_metadata = json.loads(open(os.path.join(raw_output_dir, "metadata.json"), encoding="utf-8").read())
    raw_cloudjs_text = open(os.path.join(raw_output_dir, "cloud.js"), encoding="utf-8").read()
    raw_cloudjs = json.loads(raw_cloudjs_text.removeprefix("cloud.js = ").rstrip(";"))
    assert raw_metadata["projection"] == "EPSG:4326"
    assert raw_metadata["srs"]["horizontal"] == "4326"
    assert raw_cloudjs["projection"] == "EPSG:4326"
    assert sorted(key for _bucket, key, _extra in s3_client.uploads) == [
        f"{project_root}/versions/versionid/raw/cloud.js",
        f"{project_root}/versions/versionid/raw/metadata.json",
        f"{project_root}/versions/versionid/scan/{COPC_OBJECT_NAME}",
    ]
    assert s3_client.deleted == [
        f"{project_root}/old_a/cloud.js",
        f"{project_root}/old_b/metadata.json",
    ]
    assert repository.saved_indexes[-1][S3_DISABLED_PROJECTS_KEY][0]["id"] == "project"


def test_single_replace_from_source_active_multi_copc_replaces_only_target_and_deletes_target_keys(tmp_path):
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    target_path = f"{project_root}/cloud_b"
    copc = write_copc(tmp_path, "Cloud B Replacement.copc.laz")
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
                            "visible": False,
                        },
                        {
                            "name": "Cloud B",
                            "format": "potree",
                            "viewer_path": f"{viewer_root}/cloud_b",
                            "s3_path": target_path,
                            "visible": False,
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

    result = make_service(repository, s3_client).replace_single_project_pointcloud_from_source(
        "project",
        target_path,
        str(copc),
    )

    assert result.status == "success"
    assert s3_client.prefixes == [target_path]
    pointclouds = repository.index_data["projects"][0]["pointclouds"]
    assert pointclouds[0] == {
        "name": "Cloud A",
        "format": "potree",
        "viewer_path": f"{viewer_root}/cloud_a",
        "s3_path": f"{project_root}/cloud_a",
        "visible": False,
    }
    assert pointclouds[1]["name"] == "Cloud B Replacement"
    assert pointclouds[1]["format"] == "copc"
    assert pointclouds[1]["viewer_path"] == f"{viewer_root}/versions/versionid/cloud_b_replacement/{COPC_OBJECT_NAME}"
    assert pointclouds[1]["s3_path"] == f"{project_root}/versions/versionid/cloud_b_replacement/{COPC_OBJECT_NAME}"
    assert pointclouds[1]["visible"] is False
    assert s3_client.deleted == [
        f"{target_path}/cloud.js",
        f"{target_path}/metadata.json",
    ]
    assert f"{project_root}/cloud_a/cloud.js" not in s3_client.deleted


def test_single_replace_from_source_legacy_single_copc_keeps_root_paths(tmp_path):
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    copc = write_copc(tmp_path, "Replacement.copc.laz")
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "project",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "format": "copc",
                    "link": "https://viewer/?id=project",
                    "viewer_path": f"{viewer_root}/{COPC_OBJECT_NAME}",
                    "s3_path": f"{project_root}/{COPC_OBJECT_NAME}",
                    "disabled_at": "2026-06-21T12:00:00",
                    "crs": "EPSG:25832",
                    "projection": "EPSG:25832",
                    "crs_info": {"value": "EPSG:25832"},
                    "pointclouds": [],
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )
    s3_client = FakeS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": f"{project_root}/{COPC_OBJECT_NAME}", "Size": 10},
                    {"Key": f"{project_root}/old.bin", "Size": 10},
                ]
            }
        ]
    )

    result = make_service(repository, s3_client).replace_single_project_pointcloud_from_source(
        "project",
        f"{project_root}/{COPC_OBJECT_NAME}",
        str(copc),
        crs_info={"value": "EPSG:4326"},
    )

    project = repository.index_data["projects"][0]
    assert result.status == "success"
    assert "pointclouds" not in project
    assert project["format"] == "copc"
    assert project["viewer_path"] == f"{viewer_root}/versions/versionid/{COPC_OBJECT_NAME}"
    assert project["s3_path"] == f"{project_root}/versions/versionid"
    assert project["disabled_at"] == "2026-06-21T12:00:00"
    assert project["crs"] == "EPSG:4326"
    assert sorted(key for _bucket, key, _extra in s3_client.uploads) == [
        f"{project_root}/versions/versionid/{COPC_OBJECT_NAME}"
    ]
    assert s3_client.deleted == [
        f"{project_root}/old.bin",
        f"{project_root}/{COPC_OBJECT_NAME}",
    ]


def test_full_replace_from_sources_uses_common_crs_as_top_level_project_crs(tmp_path):
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    first = write_copc(tmp_path, "First.copc.laz")
    second = write_copc(tmp_path, "Second.copc.laz")
    crs_info = {
        "value": "EPSG:25832",
        "epsg": "EPSG:25832",
        "vertical_crs": "EPSG:7837",
        "vertical_datum": "DHHN2016 height",
    }
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
                    "pointclouds": [{"name": "Old", "s3_path": f"{project_root}/old"}],
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )

    result = make_service(repository).replace_project_pointclouds_from_sources(
        "project",
        (str(first), str(second)),
        crs_info_by_source_path={str(first): crs_info, str(second): crs_info},
    )

    assert result.status == "success"
    project = repository.index_data["projects"][0]
    assert project["crs_info"] == crs_info
    assert project["crs"] == "EPSG:25832"
    assert project["vertical_crs"] == "EPSG:7837"
    assert project["vertical_datum"] == "DHHN2016 height"
    assert [cloud["crs_info"] for cloud in project["pointclouds"]] == [crs_info, crs_info]


@pytest.mark.parametrize(
    ("missing_field", "expected_message"),
    (
        ("converter_path", "Potree Converter"),
        ("output_base_dir", "Ausgabeordner"),
    ),
)
def test_replace_from_sources_rejects_raw_without_converter_or_output(tmp_path, missing_field, expected_message):
    project_root = "pointclouds/kunde/project/projekt"
    raw = write_raw(tmp_path, "Raw.laz")
    converter = write_converter(tmp_path)
    kwargs = {
        "converter_path": str(converter),
        "output_base_dir": str(tmp_path / "converted"),
    }
    kwargs[missing_field] = ""
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "project",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "format": "multi",
                    "viewer_path": "kunde/project/projekt",
                    "s3_path": project_root,
                    "pointclouds": [{"name": "Old", "s3_path": f"{project_root}/old"}],
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )

    with pytest.raises(ValueError, match=expected_message):
        make_service(repository).replace_project_pointclouds_from_sources(
            "project",
            (str(raw),),
            converter_runner=fake_converter_factory([]),
            **kwargs,
        )


def test_replace_from_sources_forwards_preparation_and_upload_progress_events(tmp_path):
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    raw = write_raw(tmp_path, "Raw.laz")
    converter = write_converter(tmp_path)
    events = []
    converter_calls = []
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
                    "pointclouds": [{"name": "Old", "s3_path": f"{project_root}/old"}],
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )

    result = make_service(repository).replace_project_pointclouds_from_sources(
        "project",
        (str(raw),),
        converter_path=str(converter),
        output_base_dir=str(tmp_path / "converted"),
        overwrite=True,
        on_progress=events.append,
        converter_runner=fake_converter_factory(converter_calls, progress_message="runner progress"),
    )

    assert result.status == "success"
    assert [call[0] for call in converter_calls] == [str(raw)]
    assert any(event.kind == "step" and event.message == "Bereite Punktwolke vor..." for event in events)
    assert any(event.kind == "log" and event.message == "runner progress" for event in events)
    assert any(event.kind == "log" and event.message.startswith("[UPLOAD]") for event in events)
    assert events[-1] == ProgressEvent(kind="log", message="[UPLOAD] Alle Dateien hochgeladen", phase="upload")


def test_add_from_sources_uses_immutable_child_path_and_preserves_project_metadata(tmp_path):
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    source = write_copc(tmp_path, "New.copc.laz")
    repository = FakeRepository(
        {
            "projects": [],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "id": "project",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "datum": "2026-06-20T12:00:00",
                    "link": "https://viewer/?id=project",
                    "format": "multi",
                    "viewer_path": viewer_root,
                    "s3_path": project_root,
                    "disabled_at": "2026-06-21T12:00:00",
                    "models": [{"s3_path": "models/model/versions/one/model.json"}],
                    "pointclouds": [
                        {
                            "name": "Keep",
                            "format": "potree",
                            "viewer_path": f"{viewer_root}/keep",
                            "s3_path": f"{project_root}/keep",
                            "crs_info": {"value": "EPSG:25832"},
                        }
                    ],
                }
            ],
        }
    )
    s3_client = FakeS3Client()

    result = make_service(repository, s3_client).add_project_pointclouds_from_sources(
        "project",
        (str(source),),
        crs_info_by_source_path={str(source): {"value": "EPSG:25832"}},
    )

    project = repository.index_data[S3_DISABLED_PROJECTS_KEY][0]
    assert result.status == "success"
    assert project["viewer_path"] == viewer_root
    assert project["s3_path"] == project_root
    assert project["models"] == [{"s3_path": "models/model/versions/one/model.json"}]
    assert project["disabled_at"] == "2026-06-21T12:00:00"
    assert project["pointcloud_count"] == 2
    assert project["pointclouds"][1]["s3_path"] == f"{project_root}/versions/versionid/new/{COPC_OBJECT_NAME}"
    assert [key for _bucket, key, _extra in s3_client.uploads] == [
        f"{project_root}/versions/versionid/new/{COPC_OBJECT_NAME}"
    ]


def test_add_from_sources_rejects_existing_child_slug_collision(tmp_path):
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    source = write_copc(tmp_path, "Scan.copc.laz")
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
                            "name": "Scan",
                            "format": "copc",
                            "viewer_path": f"{viewer_root}/scan/{COPC_OBJECT_NAME}",
                            "s3_path": f"{project_root}/scan/{COPC_OBJECT_NAME}",
                        }
                    ],
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )

    with pytest.raises(ValueError, match="Slug"):
        make_service(repository).add_project_pointclouds_from_sources("project", (str(source),))
