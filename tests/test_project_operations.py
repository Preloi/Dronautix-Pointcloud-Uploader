import base64
import copy
import hashlib
import json
import os

import pytest

from dronautix_uploader.core.constants import S3_DISABLED_PROJECTS_KEY
from dronautix_uploader.core.contracts import (
    GLBOptimizationResult,
    ModelIndexEntry,
    ModelUploadInput,
    PointcloudSource,
    PreparedModelUpload,
    ProgressEvent,
)
from dronautix_uploader.core.project_operations import PreparedProjectUpload
from dronautix_uploader.core.project_operations import (
    add_project_pointclouds,
    apply_project_rename_metadata,
    build_duplicate_project_metadata,
    build_new_project_upload,
    delete_project,
    download_project,
    duplicate_project,
    compute_orphaned_keys,
    prepare_cloud_uploads,
    prepare_single_project_upload,
    ProjectDownloadCancelledError,
    replace_project_pointclouds,
    replace_single_project_pointcloud,
    replace_single_project_model,
    add_project_models,
    remove_project_model,
    remove_project_pointcloud,
    upload_new_project,
)
from dronautix_uploader.core.project_repository import ProjectMetadataConflictError, ProjectMetadataWriteUncertainError


class FakeS3Client:
    def __init__(self, fail_on_key=""):
        self.fail_on_key = fail_on_key
        self.uploads = []
        self.objects = {}
        self.puts = []

    def upload_file(self, local_path, bucket, key, ExtraArgs=None, Callback=None):
        if key == self.fail_on_key:
            raise RuntimeError("simulated upload failure")
        if Callback:
            Callback(os.path.getsize(local_path))
        self.uploads.append((bucket, key, ExtraArgs))
        with open(local_path, "rb") as stream:
            content = stream.read()
        self.objects[key] = {
            "ContentLength": len(content),
            "Metadata": dict((ExtraArgs or {}).get("Metadata") or {}),
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(content).digest()).decode("ascii"),
            "ChecksumType": "FULL_OBJECT",
        }

    def head_object(self, Bucket, Key, ChecksumMode=None):
        assert ChecksumMode == "ENABLED"
        return dict(self.objects[Key])

    def put_object(self, **kwargs):
        self.puts.append(kwargs)


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **_kwargs):
        return self.pages


class FakeProjectS3Client(FakeS3Client):
    def __init__(self, pages=None, fail_on_key=""):
        super().__init__(fail_on_key=fail_on_key)
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
        if Callback:
            Callback(4)
        self.downloads.append((bucket, key, local_path))
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as file:
            file.write(b"data")


def write_potree(tmp_path, name="potree", content=b"cloud"):
    potree_dir = tmp_path / name
    potree_dir.mkdir()
    (potree_dir / "cloud.js").write_bytes(content)
    (potree_dir / "metadata.json").write_text("{}", encoding="utf-8")
    return potree_dir


def test_prepare_cloud_uploads_builds_potree_entries(tmp_path):
    first = write_potree(tmp_path, "Scan Ä")
    potree_dir = write_potree(tmp_path, "potree")

    prepared = prepare_cloud_uploads(
        (
            PointcloudSource(str(first), input_format="potree", crs_info={"value": "EPSG:25832"}),
            PointcloudSource(str(potree_dir), name="Potree Cloud", input_format="potree"),
        ),
        "kunde/id/projekt",
        "pointclouds/kunde/id/projekt",
    )

    assert prepared[0].slug == "scan_ae"
    assert prepared[0].viewer_path == "kunde/id/projekt/scan_ae"
    assert prepared[0].s3_path == "pointclouds/kunde/id/projekt/scan_ae"
    assert prepared[0].index_entry["crs"] == "EPSG:25832"
    assert prepared[1].slug == "potree_cloud"
    assert [key for _local, key in prepared[1].files_to_upload][-1].endswith("metadata.json")


def test_compute_orphaned_keys_keeps_reuploaded_keys():
    assert compute_orphaned_keys(
        ["prefix/cloud.js", "prefix/metadata.json", "prefix/old.bin"],
        ["prefix/cloud.js", "prefix/metadata.json"],
    ) == ("prefix/old.bin",)


def test_build_new_project_upload_single_potree_uses_legacy_project_shape(tmp_path):
    potree = write_potree(tmp_path, "single")

    upload = build_new_project_upload(
        sources=(PointcloudSource(str(potree), input_format="potree", crs_info={"value": "EPSG:25832"}),),
        timestamp="2026-06-21T12:00:00",
        kunde="Kunde",
        projekt="Projekt",
        project_id="abc123ef",
        project_url="https://viewer/?id=abc123ef",
        project_viewer_root="kunde/abc123ef/projekt",
        project_s3_prefix="pointclouds/kunde/abc123ef/projekt",
    )

    assert upload.project_metadata == {
        "datum": "2026-06-21T12:00:00",
        "kunde": "Kunde",
        "id": "abc123ef",
        "projekt": "Projekt",
        "format": "potree",
        "link": "https://viewer/?id=abc123ef",
        "viewer_path": "kunde/abc123ef/projekt",
        "s3_path": "pointclouds/kunde/abc123ef/projekt",
        "crs": "EPSG:25832",
        "projection": "EPSG:25832",
        "crs_info": {"value": "EPSG:25832"},
    }
    assert upload.files_to_upload == (
        (str(potree / "cloud.js"), "pointclouds/kunde/abc123ef/projekt/cloud.js"),
        (str(potree / "metadata.json"), "pointclouds/kunde/abc123ef/projekt/metadata.json"),
    )


def test_build_new_project_upload_multi_potree_sources_clears_crs_mismatch(tmp_path):
    scan = write_potree(tmp_path, "scan")
    potree = write_potree(tmp_path, "potree")

    upload = build_new_project_upload(
        sources=(
            PointcloudSource(str(scan), name="Scan", input_format="potree", crs_info={"value": "EPSG:25832"}),
            PointcloudSource(str(potree), name="Potree", input_format="potree", crs_info={"value": "EPSG:4326"}),
        ),
        timestamp="2026-06-21T12:00:00",
        kunde="Kunde",
        projekt="Projekt",
        project_id="abc123ef",
        project_url="https://viewer/?id=abc123ef",
        project_viewer_root="kunde/abc123ef/projekt",
        project_s3_prefix="pointclouds/kunde/abc123ef/projekt",
    )

    assert upload.project_metadata["format"] == "multi"
    assert upload.project_metadata["viewer_path"] == "kunde/abc123ef/projekt"
    assert upload.project_metadata["s3_path"] == "pointclouds/kunde/abc123ef/projekt"
    assert upload.project_metadata["pointcloud_count"] == 2
    assert "crs" not in upload.project_metadata
    assert upload.project_metadata["pointclouds"][0]["crs"] == "EPSG:25832"
    assert upload.project_metadata["pointclouds"][1]["crs"] == "EPSG:4326"
    assert [key for _local, key in upload.files_to_upload][-1].endswith("metadata.json")


def test_upload_new_project_inserts_index_after_upload_success(tmp_path):
    potree = write_potree(tmp_path, "single")
    prepared_upload = build_new_project_upload(
        sources=(PointcloudSource(str(potree), input_format="potree"),),
        timestamp="2026-06-21T12:00:00",
        kunde="Kunde",
        projekt="Projekt",
        project_id="abc123ef",
        project_url="https://viewer/?id=abc123ef",
        project_viewer_root="kunde/abc123ef/projekt",
        project_s3_prefix="pointclouds/kunde/abc123ef/projekt",
    )
    index_data = {"projects": [{"id": "old"}]}
    saved_indexes = []
    deleted_keys = []

    result = upload_new_project(
        s3_client=FakeS3Client(),
        index_data=index_data,
        prepared_upload=prepared_upload,
        save_index=lambda data: saved_indexes.append([project["id"] for project in data["projects"]]) or True,
        delete_keys=lambda keys: deleted_keys.extend(keys),
    )

    assert result.status == "success"
    assert result.project_id == "abc123ef"
    assert result.uploaded_keys == (
        "pointclouds/kunde/abc123ef/projekt/cloud.js",
        "pointclouds/kunde/abc123ef/projekt/metadata.json",
    )
    assert [project["id"] for project in index_data["projects"]] == ["abc123ef", "old"]
    assert saved_indexes == [["abc123ef", "old"]]
    assert deleted_keys == []


def test_upload_new_project_forwards_progress_events(tmp_path):
    potree = write_potree(tmp_path, "single")
    prepared_upload = build_new_project_upload(
        sources=(PointcloudSource(str(potree), input_format="potree"),),
        timestamp="2026-06-21T12:00:00",
        kunde="Kunde",
        projekt="Projekt",
        project_id="abc123ef",
        project_url="https://viewer/?id=abc123ef",
        project_viewer_root="kunde/abc123ef/projekt",
        project_s3_prefix="pointclouds/kunde/abc123ef/projekt",
    )
    events = []

    result = upload_new_project(
        s3_client=FakeS3Client(),
        index_data={"projects": []},
        prepared_upload=prepared_upload,
        save_index=lambda _data: True,
        delete_keys=lambda _keys: None,
        on_progress=events.append,
    )

    assert result.status == "success"
    assert {event.phase for event in events} == {"upload", "index"}
    assert events[-1] == ProgressEvent(
        kind="progress",
        message="Projekt wurde gespeichert.",
        percent=1.0,
        phase="index",
    )


def test_upload_new_project_cancel_rolls_back_uploaded_keys_and_returns_cancelled(tmp_path):
    first = write_potree(tmp_path, "first", b"cloud-1")
    second = write_potree(tmp_path, "second", b"cloud-2")
    prepared_upload = build_new_project_upload(
        sources=(
            PointcloudSource(str(first), input_format="potree"),
            PointcloudSource(str(second), input_format="potree"),
        ),
        timestamp="2026-06-21T12:00:00",
        kunde="Kunde",
        projekt="Projekt",
        project_id="abc123ef",
        project_url="https://viewer/?id=abc123ef",
        project_viewer_root="kunde/abc123ef/projekt",
        project_s3_prefix="pointclouds/kunde/abc123ef/projekt",
    )
    index_data = {"projects": [{"id": "old"}]}
    deleted_keys = []
    saved = []
    s3_client = FakeS3Client()

    # Abbruch, sobald die erste Datei hochgeladen wurde.
    def cancel_after_first_upload():
        return len(s3_client.uploads) >= 1

    result = upload_new_project(
        s3_client=s3_client,
        index_data=index_data,
        prepared_upload=prepared_upload,
        save_index=lambda data: saved.append(True) or True,
        delete_keys=lambda keys: deleted_keys.extend(keys),
        cancel_requested=cancel_after_first_upload,
    )

    assert result.status == "cancelled"
    assert "abgebrochen" in result.message
    # Genau die bereits hochgeladenen Keys wurden entfernt, der Index blieb unberuehrt.
    assert deleted_keys == [key for _bucket, key, _extra in s3_client.uploads]
    assert index_data == {"projects": [{"id": "old"}]}
    assert saved == []


def test_upload_new_project_rolls_back_uploaded_keys_when_index_save_fails(tmp_path):
    potree = write_potree(tmp_path, "single")
    prepared_upload = build_new_project_upload(
        sources=(PointcloudSource(str(potree), input_format="potree"),),
        timestamp="2026-06-21T12:00:00",
        kunde="Kunde",
        projekt="Projekt",
        project_id="abc123ef",
        project_url="https://viewer/?id=abc123ef",
        project_viewer_root="kunde/abc123ef/projekt",
        project_s3_prefix="pointclouds/kunde/abc123ef/projekt",
    )
    index_data = {"projects": [{"id": "old"}]}
    deleted_keys = []

    with pytest.raises(RuntimeError):
        upload_new_project(
            s3_client=FakeS3Client(),
            index_data=index_data,
            prepared_upload=prepared_upload,
            save_index=lambda _data: False,
            delete_keys=lambda keys: deleted_keys.extend(keys),
        )

    assert index_data == {"projects": [{"id": "old"}]}
    assert deleted_keys == [
        "pointclouds/kunde/abc123ef/projekt/cloud.js",
        "pointclouds/kunde/abc123ef/projekt/metadata.json",
    ]


def test_upload_rollback_keeps_model_hash_version_referenced_by_previous_index(tmp_path):
    content_hash = "b" * 64
    model_prefix = f"pointclouds/kunde/abc123ef/projekt/models/halle/versions/{content_hash}"
    viewer_prefix = f"kunde/abc123ef/projekt/models/halle/versions/{content_hash}"
    pointcloud = tmp_path / "cloud.js"
    scene = tmp_path / "scene.glb"
    manifest = tmp_path / "model.json"
    pointcloud.write_bytes(b"cloud")
    scene.write_bytes(b"glTF")
    manifest.write_text("{}", encoding="utf-8")
    model_entry = {
        "id": "halle",
        "name": "Halle",
        "format": "glb",
        "viewer_path": f"{viewer_prefix}/model.json",
        "s3_path": model_prefix,
    }
    prepared_upload = PreparedProjectUpload(
        project_metadata={
            "id": "abc123ef",
            "format": "multi",
            "viewer_path": "kunde/abc123ef/projekt",
            "s3_path": "pointclouds/kunde/abc123ef/projekt",
            "models": [model_entry],
        },
        files_to_upload=(
            (str(pointcloud), "pointclouds/kunde/abc123ef/projekt/cloud.js"),
            (str(scene), f"{model_prefix}/scene.glb"),
            (str(manifest), f"{model_prefix}/model.json"),
        ),
    )
    previous_index = {
        "projects": [{"id": "abc123ef", "models": [dict(model_entry)]}],
    }
    deleted_keys = []

    with pytest.raises(RuntimeError, match="Projekt-Index"):
        upload_new_project(
            s3_client=FakeS3Client(),
            index_data=previous_index,
            prepared_upload=prepared_upload,
            save_index=lambda _data: False,
            delete_keys=lambda keys: deleted_keys.extend(keys),
        )

    assert previous_index == {"projects": [{"id": "abc123ef", "models": [model_entry]}]}
    assert deleted_keys == ["pointclouds/kunde/abc123ef/projekt/cloud.js"]


@pytest.mark.parametrize("include_manifest, duplicate_scene", ((False, False), (True, True)))
def test_model_package_must_contain_each_scene_and_manifest_exactly_once_before_s3_upload(
    tmp_path,
    include_manifest,
    duplicate_scene,
):
    data_version = "c" * 64
    model_prefix = f"pointclouds/kunde/abc123ef/projekt/models/halle/versions/{data_version}"
    viewer_prefix = f"kunde/abc123ef/projekt/models/halle/versions/{data_version}"
    scene = tmp_path / "scene.glb"
    manifest = tmp_path / "model.json"
    scene.write_bytes(b"glTF")
    manifest.write_text("{}", encoding="utf-8")
    model_entry = {
        "id": "halle",
        "format": "glb",
        "viewer_path": f"{viewer_prefix}/model.json",
        "s3_path": model_prefix,
    }
    files = [(str(scene), f"{model_prefix}/scene.glb")]
    if include_manifest:
        files.append((str(manifest), f"{model_prefix}/model.json"))
    if duplicate_scene:
        files.append((str(scene), f"{model_prefix}/scene.glb"))
    prepared_upload = PreparedProjectUpload(
        project_metadata={
            "id": "abc123ef",
            "format": "multi",
            "viewer_path": "kunde/abc123ef/projekt",
            "s3_path": "pointclouds/kunde/abc123ef/projekt",
            "models": [model_entry],
        },
        files_to_upload=tuple(files),
    )
    s3_client = FakeS3Client()
    index_data = {"projects": []}

    with pytest.raises(ValueError, match="Paketdatei (fehlt|doppelt vorbereitet)"):
        upload_new_project(
            s3_client=s3_client,
            index_data=index_data,
            prepared_upload=prepared_upload,
            save_index=lambda _data: True,
            delete_keys=lambda _keys: None,
        )

    assert s3_client.uploads == []
    assert index_data == {"projects": []}


def test_build_duplicate_project_metadata_preserves_multi_clouds_and_rewrites_paths():
    source_project = {
        "datum": "old",
        "kunde": "Alt",
        "id": "oldid",
        "projekt": "Altprojekt",
        "format": "multi",
        "link": "https://viewer/?id=oldid",
        "viewer_path": "alt/oldid/altprojekt",
        "s3_path": "pointclouds/alt/oldid/altprojekt",
        "disabled_at": "2026-06-21T12:00:00",
        "pointcloud_count": 2,
        "pointclouds": [
            {
                "name": "Cloud A",
                "format": "potree",
                "viewer_path": "alt/oldid/altprojekt/cloud_a",
                "s3_path": "pointclouds/alt/oldid/altprojekt/cloud_a",
                "visible": True,
                "crs_info": {"value": "EPSG:25832"},
            },
            {
                "name": "Cloud B",
                "format": "potree",
                "viewer_path": "alt/oldid/altprojekt/cloud_b",
                "s3_path": "pointclouds/alt/oldid/altprojekt/cloud_b",
                "visible": False,
            },
        ],
    }

    duplicated = build_duplicate_project_metadata(
        source_project=source_project,
        timestamp="2026-06-21T13:00:00",
        new_kunde="Neu",
        new_projekt="Neuprojekt",
        new_project_id="newid",
        new_project_url="https://viewer/?id=newid",
        new_viewer_root="neu/newid/neuprojekt",
        new_s3_prefix="pointclouds/neu/newid/neuprojekt",
    )

    assert duplicated["id"] == "newid"
    assert duplicated["kunde"] == "Neu"
    assert duplicated["projekt"] == "Neuprojekt"
    assert duplicated["link"] == "https://viewer/?id=newid"
    assert duplicated["viewer_path"] == "neu/newid/neuprojekt"
    assert duplicated["s3_path"] == "pointclouds/neu/newid/neuprojekt"
    assert "disabled_at" not in duplicated
    assert duplicated["pointcloud_count"] == 2
    assert duplicated["pointclouds"][0]["viewer_path"] == "neu/newid/neuprojekt/cloud_a"
    assert duplicated["pointclouds"][0]["s3_path"] == "pointclouds/neu/newid/neuprojekt/cloud_a"
    assert duplicated["pointclouds"][0]["crs_info"] == {"value": "EPSG:25832"}
    assert duplicated["pointclouds"][1]["viewer_path"] == "neu/newid/neuprojekt/cloud_b"
    assert duplicated["pointclouds"][1]["s3_path"] == "pointclouds/neu/newid/neuprojekt/cloud_b"
    assert duplicated["pointclouds"][1]["visible"] is False


def test_build_duplicate_project_metadata_remaps_model_and_manifest_paths():
    version = "a" * 64
    source_project = {
        "format": "multi",
        "viewer_path": "alt/oldid/altprojekt",
        "s3_path": "pointclouds/alt/oldid/altprojekt",
        "models": [
            {
                "id": "building",
                "viewer_path": f"alt/oldid/altprojekt/models/building/versions/{version}/model.json",
                "s3_path": f"pointclouds/alt/oldid/altprojekt/models/building/versions/{version}",
                "manifest_path": f"alt/oldid/altprojekt/models/building/versions/{version}/model.json",
                "assets": [
                    {
                        "viewer_path": f"alt/oldid/altprojekt/models/building/versions/{version}/texture.ktx2",
                        "s3_path": f"pointclouds/alt/oldid/altprojekt/models/building/versions/{version}/texture.ktx2",
                    }
                ],
            }
        ],
    }

    duplicated = build_duplicate_project_metadata(
        source_project=source_project,
        timestamp="2026-06-21T13:00:00",
        new_kunde="Neu",
        new_projekt="Neuprojekt",
        new_project_id="newid",
        new_project_url="https://viewer/?id=newid",
        new_viewer_root="neu/newid/neuprojekt",
        new_s3_prefix="pointclouds/neu/newid/neuprojekt",
    )

    model = duplicated["models"][0]
    assert model["viewer_path"] == f"neu/newid/neuprojekt/models/building/versions/{version}/model.json"
    assert model["s3_path"] == f"pointclouds/neu/newid/neuprojekt/models/building/versions/{version}"
    assert model["manifest_path"] == f"neu/newid/neuprojekt/models/building/versions/{version}/model.json"
    assert model["assets"][0]["viewer_path"] == f"neu/newid/neuprojekt/models/building/versions/{version}/texture.ktx2"
    assert model["assets"][0]["s3_path"] == f"pointclouds/neu/newid/neuprojekt/models/building/versions/{version}/texture.ktx2"
    assert source_project["models"][0]["viewer_path"].startswith("alt/")


def test_apply_project_rename_metadata_changes_names_without_paths():
    version = "a" * 64
    project = {
        "kunde": "Alt",
        "projekt": "Altprojekt",
        "viewer_path": "alt/id/altprojekt",
        "s3_path": "pointclouds/alt/id/altprojekt",
        "pointclouds": [
            {"name": "A", "viewer_path": "alt/id/altprojekt/a"},
            {"name": "B", "viewer_path": "alt/id/altprojekt/b"},
        ],
        "models": [
            {
                "id": "building",
                "format": "glb",
                "viewer_path": f"alt/id/altprojekt/models/building/versions/{version}/model.json",
                "s3_path": f"pointclouds/alt/id/altprojekt/models/building/versions/{version}",
            }
        ],
    }

    renamed = apply_project_rename_metadata(project, "Neu", "Neuprojekt", ("Cloud A", "Cloud B"))

    assert renamed["kunde"] == "Neu"
    assert renamed["projekt"] == "Neuprojekt"
    assert renamed["viewer_path"] == "alt/id/altprojekt"
    assert renamed["s3_path"] == "pointclouds/alt/id/altprojekt"
    assert [cloud["name"] for cloud in renamed["pointclouds"]] == ["Cloud A", "Cloud B"]
    assert renamed["models"] == project["models"]
    assert project["kunde"] == "Alt"


def test_apply_project_rename_metadata_keeps_legacy_cloud_name_independent_when_only_project_changes():
    project = {
        "kunde": "Kunde",
        "projekt": "Altprojekt",
        "format": "potree",
        "viewer_path": "kunde/id/altprojekt",
        "s3_path": "pointclouds/kunde/id/altprojekt",
    }

    renamed = apply_project_rename_metadata(project, "Kunde", "Neuprojekt", ("Altprojekt",))

    assert renamed["projekt"] == "Neuprojekt"
    assert renamed["name"] == "Altprojekt"
    assert renamed["viewer_path"] == project["viewer_path"]
    assert renamed["s3_path"] == project["s3_path"]


def test_duplicate_project_copies_s3_objects_and_inserts_active_multi_clone():
    version = "a" * 64
    source_project = {
        "id": "oldid",
        "kunde": "Alt",
        "projekt": "Altprojekt",
        "format": "multi",
        "link": "https://viewer/?id=oldid",
        "viewer_path": "alt/oldid/altprojekt",
        "s3_path": "pointclouds/alt/oldid/altprojekt",
        "pointclouds": [
            {
                "name": "Cloud A",
                "viewer_path": "alt/oldid/altprojekt/cloud_a",
                "s3_path": "pointclouds/alt/oldid/altprojekt/cloud_a",
            }
        ],
        "models": [
            {
                "id": "building",
                "viewer_path": f"alt/oldid/altprojekt/models/building/versions/{version}/model.json",
                "s3_path": f"pointclouds/alt/oldid/altprojekt/models/building/versions/{version}",
            }
        ],
    }
    s3_client = FakeProjectS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": "pointclouds/alt/oldid/altprojekt/cloud_a/cloud.js", "Size": 10},
                    {"Key": "pointclouds/alt/oldid/altprojekt/cloud_a/metadata.json", "Size": 10},
                    {"Key": f"pointclouds/alt/oldid/altprojekt/models/building/versions/{version}/scene.glb", "Size": 10},
                    {"Key": f"pointclouds/alt/oldid/altprojekt/models/building/versions/{version}/model.json", "Size": 10},
                ]
            }
        ]
    )
    index_data = {"projects": [{"id": "existing"}], S3_DISABLED_PROJECTS_KEY: [{"id": "oldid"}]}
    saved = []
    deleted = []

    result = duplicate_project(
        s3_client=s3_client,
        index_data=index_data,
        source_project=source_project,
        timestamp="2026-06-21T13:00:00",
        new_kunde="Neu",
        new_projekt="Neuprojekt",
        new_project_id="newid",
        new_project_url="https://viewer/?id=newid",
        new_viewer_root="neu/newid/neuprojekt",
        new_s3_prefix="pointclouds/neu/newid/neuprojekt",
        save_index=lambda data: saved.append([project["id"] for project in data["projects"]]) or True,
        delete_keys=lambda keys: deleted.extend(keys),
    )

    assert result.status == "success"
    assert result.uploaded_keys == (
        "pointclouds/neu/newid/neuprojekt/cloud_a/cloud.js",
        "pointclouds/neu/newid/neuprojekt/cloud_a/metadata.json",
        f"pointclouds/neu/newid/neuprojekt/models/building/versions/{version}/scene.glb",
        f"pointclouds/neu/newid/neuprojekt/models/building/versions/{version}/model.json",
    )
    assert [project["id"] for project in index_data["projects"]] == ["newid", "existing"]
    assert index_data[S3_DISABLED_PROJECTS_KEY] == [{"id": "oldid"}]
    assert index_data["projects"][0]["pointclouds"][0]["s3_path"] == "pointclouds/neu/newid/neuprojekt/cloud_a"
    assert index_data["projects"][0]["models"] == [
        {
            "id": "building",
            "viewer_path": f"neu/newid/neuprojekt/models/building/versions/{version}/model.json",
            "s3_path": f"pointclouds/neu/newid/neuprojekt/models/building/versions/{version}",
        }
    ]
    assert saved == [["newid", "existing"]]
    assert deleted == []


def test_duplicate_project_emits_step_and_copy_progress_events():
    source_project = {
        "id": "oldid",
        "kunde": "Alt",
        "projekt": "Altprojekt",
        "link": "https://viewer/?id=oldid",
        "viewer_path": "alt/oldid/altprojekt",
        "s3_path": "pointclouds/alt/oldid/altprojekt",
    }
    s3_client = FakeProjectS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": "pointclouds/alt/oldid/altprojekt/cloud.js", "Size": 10},
                    {"Key": "pointclouds/alt/oldid/altprojekt/metadata.json", "Size": 10},
                ]
            }
        ]
    )
    events = []

    result = duplicate_project(
        s3_client=s3_client,
        index_data={"projects": []},
        source_project=source_project,
        timestamp="2026-06-21T13:00:00",
        new_kunde="Neu",
        new_projekt="Neuprojekt",
        new_project_id="newid",
        new_project_url="https://viewer/?id=newid",
        new_viewer_root="neu/newid/neuprojekt",
        new_s3_prefix="pointclouds/neu/newid/neuprojekt",
        save_index=lambda data: True,
        delete_keys=lambda keys: None,
        on_progress=events.append,
    )

    assert result.status == "success"
    steps = [(event.step, event.total_steps) for event in events if event.kind == "step"]
    copy_events = [event for event in events if event.kind == "progress"]
    assert steps == [(1, 3), (2, 3), (3, 3)]
    # Byte-gewichteter Fortschritt (Bruch 0..1) ueber beide 10-Byte-Dateien.
    assert [event.percent for event in copy_events] == [0.5, 1.0]
    assert "2/2" in copy_events[-1].message
    assert "20" in copy_events[-1].detail.replace(",", ".") or "Bytes" in copy_events[-1].detail


def test_duplicate_project_rolls_back_copied_keys_when_index_save_fails():
    source_project = {"id": "oldid", "s3_path": "pointclouds/old"}
    s3_client = FakeProjectS3Client(pages=[{"Contents": [{"Key": "pointclouds/old/cloud.js", "Size": 10}]}])
    index_data = {"projects": [{"id": "existing"}]}
    deleted = []

    with pytest.raises(RuntimeError):
        duplicate_project(
            s3_client=s3_client,
            index_data=index_data,
            source_project=source_project,
            timestamp="2026-06-21T13:00:00",
            new_kunde="Neu",
            new_projekt="Neuprojekt",
            new_project_id="newid",
            new_project_url="https://viewer/?id=newid",
            new_viewer_root="neu/newid/neuprojekt",
            new_s3_prefix="pointclouds/neu/newid/neuprojekt",
            save_index=lambda _data: False,
            delete_keys=lambda keys: deleted.extend(keys),
        )

    assert index_data == {"projects": [{"id": "existing"}]}
    assert deleted == ["pointclouds/neu/newid/neuprojekt/cloud.js"]


@pytest.mark.parametrize("cleanup_fails", (False, True))
def test_duplicate_project_cleans_first_copy_when_second_copy_fails(cleanup_fails):
    class FailSecondCopy(FakeProjectS3Client):
        def copy_object(self, **kwargs):
            if self.copies:
                raise RuntimeError("second copy failed")
            super().copy_object(**kwargs)

    source_project = {"id": "oldid", "s3_path": "pointclouds/old"}
    s3_client = FailSecondCopy(pages=[{"Contents": [
        {"Key": "pointclouds/old/first.bin", "Size": 1},
        {"Key": "pointclouds/old/second.bin", "Size": 1},
    ]}])
    cleaned = []

    def cleanup(keys):
        cleaned.extend(keys)
        if cleanup_fails:
            raise RuntimeError("cleanup denied")

    expected = "Cleanup unvollstaendig" if cleanup_fails else "second copy failed"
    with pytest.raises(RuntimeError, match=expected):
        duplicate_project(
            s3_client=s3_client,
            index_data={"projects": []},
            source_project=source_project,
            timestamp="now",
            new_kunde="Neu",
            new_projekt="Projekt",
            new_project_id="newid",
            new_project_url="url",
            new_viewer_root="neu/newid/projekt",
            new_s3_prefix="pointclouds/neu/newid/projekt",
            save_index=lambda _data: True,
            delete_keys=cleanup,
        )

    assert cleaned == ["pointclouds/neu/newid/projekt/first.bin"]


def test_conflict_without_current_snapshot_never_deletes_possible_winner_objects():
    source_project = {"id": "oldid", "s3_path": "pointclouds/old"}
    s3_client = FakeProjectS3Client(
        pages=[{"Contents": [{"Key": "pointclouds/old/models/scene.glb", "Size": 1}]}]
    )
    cleaned = []

    def conflict(_data):
        raise ProjectMetadataConflictError("projects_index.json", current_data=None)

    with pytest.raises(ProjectMetadataConflictError, match="Cleanup.*ausgelassen"):
        duplicate_project(
            s3_client=s3_client,
            index_data={"projects": []},
            source_project=source_project,
            timestamp="now",
            new_kunde="Neu",
            new_projekt="Projekt",
            new_project_id="shared",
            new_project_url="url",
            new_viewer_root="neu/shared/projekt",
            new_s3_prefix="pointclouds/neu/shared/projekt",
            save_index=conflict,
            delete_keys=lambda keys: cleaned.extend(keys),
        )

    assert cleaned == []


def test_uncertain_committed_index_preserves_now_referenced_copied_objects():
    source_project = {"id": "oldid", "s3_path": "pointclouds/old"}
    s3_client = FakeProjectS3Client(
        pages=[{"Contents": [{"Key": "pointclouds/old/scene.glb", "Size": 1}]}]
    )
    cleaned = []
    target = "pointclouds/neu/shared/projekt"

    def uncertain(_data):
        raise ProjectMetadataWriteUncertainError(
            "projects_index.json",
            current_data={"projects": [{"id": "winner", "s3_path": target}]},
        )

    with pytest.raises(ProjectMetadataWriteUncertainError):
        duplicate_project(
            s3_client=s3_client, index_data={"projects": []}, source_project=source_project,
            timestamp="now", new_kunde="Neu", new_projekt="Projekt", new_project_id="shared",
            new_project_url="url", new_viewer_root="neu/shared/projekt", new_s3_prefix=target,
            save_index=uncertain, delete_keys=lambda keys: cleaned.extend(keys),
        )

    assert cleaned == []


def test_delete_project_removes_from_disabled_list_and_upserts_deleted_entry():
    version = "a" * 64
    s3_client = FakeProjectS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": "pointclouds/old/cloud.js", "Size": 10},
                    {"Key": f"pointclouds/old/models/building/versions/{version}/scene.glb", "Size": 10},
                    {"Key": f"pointclouds/old/models/building/versions/{version}/model.json", "Size": 10},
                ]
            }
        ]
    )
    index_data = {"projects": [{"id": "active"}], S3_DISABLED_PROJECTS_KEY: [{"id": "oldid"}]}
    deleted_data = {"deleted_projects": [{"id": "other", "s3_path": "pointclouds/other"}]}
    saved_index = []
    saved_deleted = []

    result = delete_project(
        s3_client=s3_client,
        index_data=index_data,
        deleted_data=deleted_data,
        project_info={
            "id": "oldid",
            "kunde": "Kunde",
            "projekt": "Projekt",
            "s3_path": "pointclouds/old",
            "link": "https://viewer/?id=oldid",
        },
        deleted_at="2026-06-21T13:00:00",
        save_index=lambda data: saved_index.append(data.copy()) or True,
        save_deleted=lambda data: saved_deleted.append(data.copy()) or True,
    )

    assert result.status == "success"
    assert result.deleted_keys == (
        "pointclouds/old/cloud.js",
        f"pointclouds/old/models/building/versions/{version}/scene.glb",
        f"pointclouds/old/models/building/versions/{version}/model.json",
    )
    assert index_data == {"projects": [{"id": "active"}], S3_DISABLED_PROJECTS_KEY: []}
    assert deleted_data["deleted_projects"][0]["id"] == "oldid"
    assert deleted_data["deleted_projects"][0]["deleted_at"] == "2026-06-21T13:00:00"
    assert saved_index and saved_deleted


def test_delete_project_keeps_data_when_index_tombstone_save_fails():
    s3_client = FakeProjectS3Client(
        pages=[{"Contents": [{"Key": "pointclouds/old/cloud.js", "Size": 10}]}]
    )
    index_data = {"projects": [{"id": "oldid"}]}
    deleted_data = {"deleted_projects": []}

    result = delete_project(
        s3_client=s3_client,
        index_data=index_data,
        deleted_data=deleted_data,
        project_info={"id": "oldid", "s3_path": "pointclouds/old"},
        deleted_at="2026-06-21T13:00:00",
        save_index=lambda _data: False,
        save_deleted=lambda _data: True,
    )

    assert result.status == "partial"
    assert result.deleted_keys == ()
    assert "projects_index.json" in result.warnings[0]
    assert s3_client.deleted == []


def test_delete_project_journal_exception_keeps_index_and_data():
    s3_client = FakeProjectS3Client(pages=[{"Contents": [{"Key": "pointclouds/old/cloud.js", "Size": 1}]}])
    index_data = {"projects": [{"id": "oldid", "s3_path": "pointclouds/old"}]}

    def fail_journal(_data):
        raise RuntimeError("journal denied")

    result = delete_project(
        s3_client=s3_client,
        index_data=index_data,
        deleted_data={"deleted_projects": []},
        project_info=index_data["projects"][0],
        deleted_at="now",
        save_index=lambda _data: True,
        save_deleted=fail_journal,
    )

    assert result.status == "failed"
    assert index_data["projects"][0]["id"] == "oldid"
    assert s3_client.deleted == []


def test_delete_project_partial_delete_leaves_retryable_tombstone_then_completes():
    s3_client = FakeProjectS3Client(pages=[{"Contents": [{"Key": "pointclouds/old/cloud.js", "Size": 1}]}])
    original_delete = s3_client.delete_objects
    calls = {"count": 0}

    def partial_once(Bucket, Delete):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"Errors": [{"Key": "pointclouds/old/cloud.js", "Code": "Denied", "Message": "no"}]}
        return original_delete(Bucket=Bucket, Delete=Delete)

    s3_client.delete_objects = partial_once
    index_data = {"projects": [{"id": "oldid", "s3_path": "pointclouds/old"}], S3_DISABLED_PROJECTS_KEY: []}
    deleted_data = {"deleted_projects": []}
    saves = []
    save = lambda data: saves.append(copy.deepcopy(data)) or True

    first = delete_project(
        s3_client=s3_client, index_data=index_data, deleted_data=deleted_data,
        project_info=index_data["projects"][0], deleted_at="now", save_index=save, save_deleted=save,
    )
    tombstone = index_data[S3_DISABLED_PROJECTS_KEY][0]
    assert first.status == "partial" and tombstone["cleanup_pending"] is True

    second = delete_project(
        s3_client=s3_client, index_data=index_data, deleted_data=deleted_data,
        project_info=tombstone, deleted_at="later", save_index=save, save_deleted=save,
    )
    assert second.status == "success"
    assert index_data[S3_DISABLED_PROJECTS_KEY] == []
    assert deleted_data["deleted_projects"][0]["cleanup_status"] == "complete"


def test_delete_project_refuses_s3_path_referenced_by_another_project():
    s3_client = FakeProjectS3Client()
    target = {"id": "old", "s3_path": "pointclouds/shared"}
    index_data = {"projects": [target, {"id": "restored", "s3_path": "pointclouds/shared"}]}
    result = delete_project(
        s3_client=s3_client, index_data=index_data, deleted_data={"deleted_projects": []},
        project_info=target, deleted_at="now", save_index=lambda _data: True, save_deleted=lambda _data: True,
    )
    assert result.status == "failed"
    assert s3_client.deleted == []


def test_download_project_uses_legacy_folder_name_and_safe_paths(tmp_path):
    s3_client = FakeProjectS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": "pointclouds/kunde/id/projekt/cloud.js", "Size": 4},
                    {"Key": "pointclouds/kunde/id/projekt/nested/data.bin", "Size": 4},
                    {"Key": "pointclouds/kunde/id/projekt/folder/", "Size": 0},
                ]
            }
        ]
    )

    download_dir, downloaded = download_project(
        s3_client=s3_client,
        project_info={
            "kunde": "Künde",
            "projekt": "Projekt A",
            "id": "id123",
            "s3_path": "pointclouds/kunde/id/projekt",
        },
        target_dir=str(tmp_path),
        sanitize_func=lambda value: str(value).lower().replace("ü", "ue").replace(" ", "_"),
    )

    assert download_dir == str(tmp_path / "kuende_projekt_a_id123")
    assert downloaded == (
        str(tmp_path / "kuende_projekt_a_id123" / "cloud.js"),
        str(tmp_path / "kuende_projekt_a_id123" / "nested" / "data.bin"),
    )
    assert s3_client.downloads[0][1] == "pointclouds/kunde/id/projekt/cloud.js"


def test_download_project_forwards_progress_events(tmp_path):
    s3_client = FakeProjectS3Client(
        pages=[{"Contents": [{"Key": "pointclouds/kunde/id/projekt/cloud.js", "Size": 4}]}]
    )
    events = []

    _download_dir, downloaded = download_project(
        s3_client=s3_client,
        project_info={
            "kunde": "Kunde",
            "projekt": "Projekt",
            "id": "id123",
            "s3_path": "pointclouds/kunde/id/projekt",
        },
        target_dir=str(tmp_path),
        sanitize_func=lambda value: str(value).lower(),
        on_progress=events.append,
    )

    assert len(downloaded) == 1
    assert [event.kind for event in events] == ["detail", "progress", "progress"]
    assert events[-1].percent == 1.0


def test_download_project_raises_project_cancelled_error_with_download_dir_and_partial_files(tmp_path):
    s3_client = FakeProjectS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": "pointclouds/kunde/id/projekt/cloud.js", "Size": 4},
                    {"Key": "pointclouds/kunde/id/projekt/metadata.json", "Size": 4},
                ]
            }
        ]
    )

    with pytest.raises(ProjectDownloadCancelledError) as exc_info:
        download_project(
            s3_client=s3_client,
            project_info={
                "kunde": "Kunde",
                "projekt": "Projekt",
                "id": "id123",
                "s3_path": "pointclouds/kunde/id/projekt",
            },
            target_dir=str(tmp_path),
            sanitize_func=lambda value: str(value).lower(),
            cancel_requested=lambda: len(s3_client.downloads) >= 1,
        )

    assert exc_info.value.download_dir == str(tmp_path / "kunde_projekt_id123")
    assert exc_info.value.downloaded_files == (str(tmp_path / "kunde_projekt_id123" / "cloud.js"),)
    assert [download[1] for download in s3_client.downloads] == ["pointclouds/kunde/id/projekt/cloud.js"]


def test_replace_project_pointclouds_success_updates_disabled_project_and_deletes_orphans(tmp_path):
    first = write_potree(tmp_path, "first", b"first")
    second = write_potree(tmp_path, "second", b"second")
    prepared = prepare_cloud_uploads(
        (
            PointcloudSource(str(first), name="First", input_format="potree", crs_info={"value": "EPSG:25832"}),
            PointcloudSource(str(second), name="Second", input_format="potree", crs_info={"value": "EPSG:4326"}),
        ),
        "kunde/project/projekt",
        "pointclouds/kunde/project/projekt",
    )
    index_data = {
        "projects": [],
        S3_DISABLED_PROJECTS_KEY: [
            {
                "id": "project",
                "projekt": "Old",
                "crs": "EPSG:25832",
                "projection": "EPSG:25832",
                "crs_info": {"value": "EPSG:25832"},
            }
        ],
    }
    saved_indexes = []
    deleted_keys = []

    result = replace_project_pointclouds(
        s3_client=FakeS3Client(),
        index_data=index_data,
        project_id="project",
        base_viewer_path="kunde/project/projekt",
        s3_prefix="pointclouds/kunde/project/projekt",
        prepared_clouds=prepared,
        existing_keys=(
            "pointclouds/kunde/project/projekt/first/cloud.js",
            "pointclouds/kunde/project/projekt/old/orphan.bin",
        ),
        save_index=lambda data: saved_indexes.append(data.copy()) or True,
        delete_keys=lambda keys: deleted_keys.extend(keys),
    )

    assert result.status == "success"
    assert index_data["projects"] == []
    disabled_project = index_data[S3_DISABLED_PROJECTS_KEY][0]
    assert disabled_project["format"] == "multi"
    assert disabled_project["pointcloud_count"] == 2
    assert "crs" not in disabled_project
    assert disabled_project["pointclouds"][0]["crs"] == "EPSG:25832"
    assert disabled_project["pointclouds"][1]["crs"] == "EPSG:4326"
    assert deleted_keys == ["pointclouds/kunde/project/projekt/old/orphan.bin"]
    assert saved_indexes


def test_replace_project_pointclouds_forwards_progress_events(tmp_path):
    first = write_potree(tmp_path, "first", b"first")
    prepared = prepare_cloud_uploads(
        (PointcloudSource(str(first), name="First", input_format="potree"),),
        "kunde/project/projekt",
        "pointclouds/kunde/project/projekt",
    )
    index_data = {"projects": [{"id": "project", "projekt": "Old"}]}
    events = []

    result = replace_project_pointclouds(
        s3_client=FakeS3Client(),
        index_data=index_data,
        project_id="project",
        base_viewer_path="kunde/project/projekt",
        s3_prefix="pointclouds/kunde/project/projekt",
        prepared_clouds=prepared,
        existing_keys=(),
        save_index=lambda _data: True,
        delete_keys=lambda _keys: None,
        on_progress=events.append,
    )

    assert result.status == "success"
    assert events[0].kind == "log"
    assert events[-1].kind == "log"
    assert {event.kind for event in events} == {"log", "progress"}


def test_replace_project_pointclouds_rolls_back_uploaded_keys_before_index_save(tmp_path):
    first = write_potree(tmp_path, "first", b"first")
    second = write_potree(tmp_path, "second", b"second")
    prepared = prepare_cloud_uploads(
        (
            PointcloudSource(str(first), name="First", input_format="potree"),
            PointcloudSource(str(second), name="Second", input_format="potree"),
        ),
        "kunde/project/projekt",
        "pointclouds/kunde/project/projekt",
    )
    failing_key = prepared[1].files_to_upload[0][1]
    index_data = {"projects": [{"id": "project", "projekt": "Old"}]}
    deleted_keys = []

    with pytest.raises(RuntimeError):
        replace_project_pointclouds(
            s3_client=FakeS3Client(fail_on_key=failing_key),
            index_data=index_data,
            project_id="project",
            base_viewer_path="kunde/project/projekt",
            s3_prefix="pointclouds/kunde/project/projekt",
            prepared_clouds=prepared,
            existing_keys=(),
            save_index=lambda _data: True,
            delete_keys=lambda keys: deleted_keys.extend(keys),
        )

    assert deleted_keys == [key for _local_path, key in prepared[0].files_to_upload]
    assert index_data == {"projects": [{"id": "project", "projekt": "Old"}]}


def test_replace_project_pointclouds_restores_history_when_index_save_fails(tmp_path):
    source = write_potree(tmp_path, "first", b"first")
    prepared = prepare_cloud_uploads(
        (PointcloudSource(str(source), name="First", input_format="potree"),),
        "kunde/project/projekt",
        "pointclouds/kunde/project/projekt",
    )
    original = {
        "projects": [
            {
                "id": "project",
                "projekt": "Old",
                "history": [{"timestamp": "old", "message": "Vorhanden"}],
            }
        ]
    }
    index_data = copy.deepcopy(original)

    with pytest.raises(RuntimeError):
        replace_project_pointclouds(
            s3_client=FakeS3Client(),
            index_data=index_data,
            project_id="project",
            base_viewer_path="kunde/project/projekt",
            s3_prefix="pointclouds/kunde/project/projekt",
            prepared_clouds=prepared,
            existing_keys=(),
            save_index=lambda _data: False,
            delete_keys=lambda _keys: None,
            timestamp="new",
        )

    assert index_data == original


def test_replace_project_pointclouds_reports_orphan_cleanup_failure_after_index_save(tmp_path):
    first = write_potree(tmp_path, "first", b"first")
    prepared = prepare_cloud_uploads(
        (PointcloudSource(str(first), name="First", input_format="potree"),),
        "kunde/project/projekt",
        "pointclouds/kunde/project/projekt",
    )
    index_data = {"projects": [{"id": "project", "projekt": "Old"}]}

    def fail_delete(_keys):
        raise RuntimeError("delete denied")

    result = replace_project_pointclouds(
        s3_client=FakeS3Client(),
        index_data=index_data,
        project_id="project",
        base_viewer_path="kunde/project/projekt",
        s3_prefix="pointclouds/kunde/project/projekt",
        prepared_clouds=prepared,
        existing_keys=("pointclouds/kunde/project/projekt/old.bin",),
        save_index=lambda _data: True,
        delete_keys=fail_delete,
    )

    assert result.status == "partial"
    assert result.orphaned_keys == ("pointclouds/kunde/project/projekt/old.bin",)
    assert index_data["projects"][0]["pointclouds"][0]["name"] == "First"


def test_replace_single_project_pointcloud_preserves_other_clouds_and_deletes_target_orphans(tmp_path):
    replacement = write_potree(tmp_path, "replacement", b"replacement")
    prepared = prepare_cloud_uploads(
        (PointcloudSource(str(replacement), name="Replacement", input_format="potree", crs_info={"value": "EPSG:25832"}),),
        "kunde/project/projekt",
        "pointclouds/kunde/project/projekt",
    )[0]
    index_data = {
        "projects": [
            {
                "id": "project",
                "projekt": "Multi",
                "format": "multi",
                "viewer_path": "kunde/project/projekt",
                "s3_path": "pointclouds/kunde/project/projekt",
                "pointclouds": [
                    {
                        "name": "Keep",
                        "format": "potree",
                        "viewer_path": "kunde/project/projekt/keep",
                        "s3_path": "pointclouds/kunde/project/projekt/keep",
                        "visible": False,
                    },
                    {
                        "name": "Target",
                        "format": "potree",
                        "viewer_path": "kunde/project/projekt/target",
                        "s3_path": "pointclouds/kunde/project/projekt/target",
                        "visible": True,
                    },
                ],
            }
        ]
    }
    deleted_keys = []

    result = replace_single_project_pointcloud(
        s3_client=FakeS3Client(),
        index_data=index_data,
        project_id="project",
        base_viewer_path="kunde/project/projekt",
        s3_prefix="pointclouds/kunde/project/projekt",
        prepared_cloud=prepared,
        target_pointcloud_s3_path="pointclouds/kunde/project/projekt/target",
        existing_target_keys=(
            "pointclouds/kunde/project/projekt/target/cloud.js",
            "pointclouds/kunde/project/projekt/target/metadata.json",
        ),
        save_index=lambda _data: True,
        delete_keys=lambda keys: deleted_keys.extend(keys),
    )

    pointclouds = index_data["projects"][0]["pointclouds"]
    assert result.status == "success"
    assert pointclouds[0]["name"] == "Keep"
    assert pointclouds[0]["visible"] is False
    assert pointclouds[1]["name"] == "Replacement"
    assert pointclouds[1]["format"] == "potree"
    assert pointclouds[1]["crs"] == "EPSG:25832"
    assert deleted_keys == [
        "pointclouds/kunde/project/projekt/target/cloud.js",
        "pointclouds/kunde/project/projekt/target/metadata.json",
    ]


def test_replace_single_project_pointcloud_supports_disabled_legacy_single_project(tmp_path):
    replacement = write_potree(tmp_path, "replacement", b"replacement")
    prepared = prepare_single_project_upload(
        PointcloudSource(str(replacement), name="Replacement", input_format="potree", crs_info={"value": "EPSG:4326"}),
        "kunde/project/projekt",
        "pointclouds/kunde/project/projekt",
    )
    index_data = {
        "projects": [],
        S3_DISABLED_PROJECTS_KEY: [
            {
                "id": "project",
                "datum": "2026-06-20T12:00:00",
                "kunde": "Kunde",
                "projekt": "Single",
                "format": "potree",
                "link": "https://viewer/?id=project",
                "viewer_path": "kunde/project/projekt",
                "s3_path": "pointclouds/kunde/project/projekt",
                "disabled_at": "2026-06-21T12:00:00",
            }
        ],
    }
    deleted_keys = []

    result = replace_single_project_pointcloud(
        s3_client=FakeS3Client(),
        index_data=index_data,
        project_id="project",
        base_viewer_path="kunde/project/projekt",
        s3_prefix="pointclouds/kunde/project/projekt",
        prepared_cloud=prepared,
        target_pointcloud_s3_path="pointclouds/kunde/project/projekt",
        existing_target_keys=(
            "pointclouds/kunde/project/projekt/cloud.js",
            "pointclouds/kunde/project/projekt/old.bin",
        ),
        save_index=lambda _data: True,
        delete_keys=lambda keys: deleted_keys.extend(keys),
    )

    project = index_data[S3_DISABLED_PROJECTS_KEY][0]
    assert result.status == "success"
    assert index_data["projects"] == []
    assert project["id"] == "project"
    assert project["disabled_at"] == "2026-06-21T12:00:00"
    assert project["format"] == "potree"
    assert "pointclouds" not in project
    assert project["crs"] == "EPSG:4326"
    assert deleted_keys == ["pointclouds/kunde/project/projekt/old.bin"]


def test_replace_empty_legacy_potree_with_models_migrates_custom_name_to_child_and_metadata(tmp_path):
    crs_info = {"value": "EPSG:25832", "vertical_crs": "EPSG:7837"}
    replacement = tmp_path / "potree"
    replacement.mkdir()
    (replacement / "metadata.json").write_text(
        '{"name":"Replacement","points":5}',
        encoding="utf-8",
    )
    prepared = prepare_single_project_upload(
        PointcloudSource(str(replacement), name="Replacement", input_format="potree", crs_info=crs_info),
        "kunde/project/projekt",
        "pointclouds/kunde/project/projekt",
    )
    model = {
        "id": "halle",
        "name": "Halle",
        **crs_info,
        "s3_path": "pointclouds/kunde/project/projekt/models/halle/versions/v1",
    }
    index_data = {
        "projects": [
            {
                "id": "project",
                "datum": "2026-06-20T12:00:00",
                "kunde": "Kunde",
                "projekt": "Single",
                "name": "Separater Name",
                "format": "potree",
                "link": "https://viewer/?id=project",
                "viewer_path": "kunde/project/projekt",
                "s3_path": "pointclouds/kunde/project/projekt",
                "pointclouds": [],
                "models": [model],
            }
        ],
    }
    s3_client = FakeS3Client()

    result = replace_single_project_pointcloud(
        s3_client=s3_client,
        index_data=index_data,
        project_id="project",
        base_viewer_path="kunde/project/projekt",
        s3_prefix="pointclouds/kunde/project/projekt",
        prepared_cloud=prepared,
        target_pointcloud_s3_path="pointclouds/kunde/project/projekt",
        existing_target_keys=(
            "pointclouds/kunde/project/projekt/metadata.json",
            f"{model['s3_path']}/scene.glb",
        ),
        save_index=lambda _data: True,
        delete_keys=lambda _keys: None,
    )

    project = index_data["projects"][0]
    assert result.status == "success"
    assert project["format"] == "multi"
    assert project["pointclouds"][0]["name"] == "Separater Name"
    assert "name" not in project
    assert project["models"] == [model]
    assert json.loads(s3_client.puts[0]["Body"])["name"] == "Separater Name"
    assert s3_client.puts[0]["CacheControl"] == "no-cache"


def test_replace_single_project_model_switches_only_selected_model_after_verified_upload(tmp_path):
    old_prefix = "pointclouds/kunde/project/projekt/models/fassade/versions/old"
    new_version = "a" * 64
    new_prefix = f"pointclouds/kunde/project/projekt/models/fassade/versions/{new_version}"
    prepared = _prepared_model_upload(tmp_path, model_id="fassade", version=new_version)
    original_other = {
        "id": "dach",
        "name": "Dach",
        "format": "glb",
        "viewer_path": "kunde/project/projekt/models/dach/versions/keep/model.json",
        "s3_path": "pointclouds/kunde/project/projekt/models/dach/versions/keep",
        "crs": "EPSG:25833",
        "vertical_crs": "EPSG:7837",
    }
    index_data = {
        "projects": [
            {
                "id": "project",
                "kunde": "Kunde",
                "projekt": "Projekt",
                "models": [
                    {
                        "id": "fassade",
                        "name": "Fassade",
                        "format": "glb",
                        "viewer_path": "kunde/project/projekt/models/fassade/versions/old/model.json",
                        "s3_path": old_prefix,
                        "crs": "EPSG:25833",
                        "vertical_crs": "EPSG:7837",
                        "visible": False,
                    },
                    copy.deepcopy(original_other),
                ],
            }
        ]
    }
    client = FakeProjectS3Client()
    deleted = []

    result = replace_single_project_model(
        s3_client=client,
        index_data=index_data,
        project_id="project",
        prepared_model=prepared,
        target_model_s3_path=old_prefix,
        existing_target_keys=(f"{old_prefix}/scene.glb", f"{old_prefix}/model.json"),
        save_index=lambda _data: True,
        delete_keys=lambda keys: deleted.extend(keys),
        timestamp="2026-08-20T12:00:00",
    )

    models = index_data["projects"][0]["models"]
    assert result.status == "success"
    assert [key for _bucket, key, _args in client.uploads] == [
        f"{new_prefix}/scene.glb",
        f"{new_prefix}/model.json",
    ]
    assert all(upload[2]["ChecksumAlgorithm"] == "SHA256" for upload in client.uploads)
    assert models[0]["id"] == "fassade"
    assert models[0]["name"] == "Fassade"
    assert models[0]["s3_path"] == new_prefix
    assert models[0]["visible"] is False
    assert models[1] == original_other
    assert deleted == [f"{old_prefix}/scene.glb", f"{old_prefix}/model.json"]
    assert index_data["projects"][0]["history"][-1]["message"] == "3D-Modell 'Fassade' wurde ausgetauscht."


def test_replace_single_project_model_rolls_back_new_package_when_index_save_fails(tmp_path):
    old_prefix = "pointclouds/kunde/project/projekt/models/fassade/versions/old"
    prepared = _prepared_model_upload(tmp_path, model_id="fassade", version="b" * 64)
    index_data = {
        "projects": [
            {
                "id": "project",
                "models": [
                    {
                        "id": "fassade",
                        "name": "Fassade",
                        "format": "glb",
                        "viewer_path": "kunde/project/projekt/models/fassade/versions/old/model.json",
                        "s3_path": old_prefix,
                        "crs": "EPSG:25833",
                        "vertical_crs": "EPSG:7837",
                    }
                ],
            }
        ]
    }
    original = copy.deepcopy(index_data)
    deleted = []

    with pytest.raises(RuntimeError, match="Index"):
        replace_single_project_model(
            s3_client=FakeProjectS3Client(),
            index_data=index_data,
            project_id="project",
            prepared_model=prepared,
            target_model_s3_path=old_prefix,
            existing_target_keys=(f"{old_prefix}/scene.glb", f"{old_prefix}/model.json"),
            save_index=lambda _data: False,
            delete_keys=lambda keys: deleted.extend(keys),
        )

    new_prefix = prepared.index_entry.s3_path
    assert index_data == original
    assert deleted == [f"{new_prefix}/scene.glb", f"{new_prefix}/model.json"]


def test_replace_single_project_model_keeps_old_package_referenced_by_another_project(tmp_path):
    old_prefix = "pointclouds/kunde/shared/models/fassade/versions/old"
    shared_model = {
        "id": "fassade",
        "name": "Fassade",
        "format": "glb",
        "viewer_path": "kunde/shared/models/fassade/versions/old/model.json",
        "s3_path": old_prefix,
        "crs": "EPSG:25833",
        "vertical_crs": "EPSG:7837",
    }
    prepared = _prepared_model_upload(tmp_path, model_id="fassade", version="c" * 64)
    index_data = {
        "projects": [
            {"id": "replace-here", "models": [copy.deepcopy(shared_model)]},
            {"id": "keep-here", "models": [copy.deepcopy(shared_model)]},
        ]
    }
    deleted = []

    result = replace_single_project_model(
        s3_client=FakeProjectS3Client(),
        index_data=index_data,
        project_id="replace-here",
        prepared_model=prepared,
        target_model_s3_path=old_prefix,
        existing_target_keys=(f"{old_prefix}/scene.glb", f"{old_prefix}/model.json"),
        save_index=lambda _data: True,
        delete_keys=lambda keys: deleted.extend(keys),
    )

    assert result.status == "success"
    assert index_data["projects"][0]["models"][0]["s3_path"] == prepared.index_entry.s3_path
    assert index_data["projects"][1]["models"] == [shared_model]
    assert deleted == []


def _prepared_model_upload(tmp_path, *, model_id: str, version: str) -> PreparedModelUpload:
    staging = tmp_path / f"staging-{version[:4]}"
    staging.mkdir()
    scene = staging / "scene.glb"
    manifest = staging / "model.json"
    scene.write_bytes(b"replacement-glb")
    manifest.write_text('{"entrypoint":"scene.glb"}', encoding="utf-8")
    relative = f"models/{model_id}/versions/{version}"
    return PreparedModelUpload(
        model_input=ModelUploadInput(str(scene), name="Fassade", slug=model_id),
        name="Fassade",
        slug=model_id,
        staging_dir=str(staging),
        scene_path=str(scene),
        manifest_path=str(manifest),
        original_sha256="c" * 64,
        model_to_project=(1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0),
        bounds_min=(0.0, 0.0, 0.0),
        bounds_max=(1.0, 1.0, 1.0),
        crs_info={"value": "EPSG:25833", "vertical_crs": "EPSG:7837"},
        optimization=GLBOptimizationResult(output_sha256="d" * 64),
        data_version=version,
        index_entry=ModelIndexEntry(
            id=model_id,
            name="Fassade",
            viewer_path=f"kunde/project/projekt/{relative}/model.json",
            s3_path=f"pointclouds/kunde/project/projekt/{relative}",
            crs="EPSG:25833",
            vertical_crs="EPSG:7837",
        ),
    )


def test_add_project_models_appends_verified_packages_without_changing_existing_models(tmp_path):
    existing = {
        "id": "bestand",
        "name": "Bestand",
        "format": "glb",
        "viewer_path": "kunde/project/projekt/models/bestand/versions/old/model.json",
        "s3_path": "pointclouds/kunde/project/projekt/models/bestand/versions/old",
        "crs": "EPSG:25833",
        "vertical_crs": "EPSG:7837",
    }
    first = _prepared_model_upload(tmp_path, model_id="fassade", version="2" * 64)
    second = _prepared_model_upload(tmp_path, model_id="dach", version="3" * 64)
    index_data = {
        "projects": [
            {
                "id": "project",
                "kunde": "Kunde",
                "projekt": "Projekt",
                "viewer_path": "kunde/project/projekt",
                "s3_path": "pointclouds/kunde/project/projekt",
                "models": [copy.deepcopy(existing)],
            }
        ]
    }
    client = FakeProjectS3Client()
    deleted = []

    result = add_project_models(
        s3_client=client,
        index_data=index_data,
        project_id="project",
        project_viewer_root="kunde/project/projekt",
        project_s3_prefix="pointclouds/kunde/project/projekt",
        prepared_models=(first, second),
        save_index=lambda _data: True,
        delete_keys=lambda keys: deleted.extend(keys),
        timestamp="2026-08-21T10:00:00",
    )

    models = index_data["projects"][0]["models"]
    assert result.status == "success"
    assert models[0] == existing
    assert [model["id"] for model in models] == ["bestand", "fassade", "dach"]
    assert [key for _bucket, key, _args in client.uploads] == [
        f"{first.index_entry.s3_path}/scene.glb",
        f"{first.index_entry.s3_path}/model.json",
        f"{second.index_entry.s3_path}/scene.glb",
        f"{second.index_entry.s3_path}/model.json",
    ]
    assert deleted == []
    assert index_data["projects"][0]["history"][-1]["message"].startswith("2 3D-Modelle wurden hinzugefügt")


def test_add_project_models_rolls_back_all_new_packages_when_index_save_fails(tmp_path):
    prepared = _prepared_model_upload(tmp_path, model_id="fassade", version="4" * 64)
    index_data = {"projects": [{"id": "project", "models": []}]}
    original = copy.deepcopy(index_data)
    deleted = []

    with pytest.raises(RuntimeError, match="Index"):
        add_project_models(
            s3_client=FakeProjectS3Client(),
            index_data=index_data,
            project_id="project",
            project_viewer_root="kunde/project/projekt",
            project_s3_prefix="pointclouds/kunde/project/projekt",
            prepared_models=(prepared,),
            save_index=lambda _data: False,
            delete_keys=lambda keys: deleted.extend(keys),
        )

    assert index_data == original
    assert deleted == [
        f"{prepared.index_entry.s3_path}/scene.glb",
        f"{prepared.index_entry.s3_path}/model.json",
    ]


def test_remove_project_model_saves_remaining_models_before_deleting_only_selected_package():
    target_prefix = "pointclouds/kunde/project/models/fassade/versions/old"
    keep = {"id": "dach", "name": "Dach", "s3_path": "pointclouds/kunde/project/models/dach/versions/old"}
    index_data = {
        "projects": [{
            "id": "project",
            "projekt": "Unverändert",
            "models": [
                {"id": "fassade", "name": "Fassade", "s3_path": target_prefix},
                copy.deepcopy(keep),
            ],
        }]
    }
    events = []

    result = remove_project_model(
        index_data=index_data,
        project_id="project",
        target_model_s3_path=target_prefix,
        existing_target_keys=(
            f"{target_prefix}/scene.glb",
            f"{target_prefix}/model.json",
            f"{target_prefix}-backup/scene.glb",
        ),
        save_index=lambda data: events.append(("save", copy.deepcopy(data))) or True,
        delete_keys=lambda keys: events.append(("delete", keys)),
        timestamp="2026-08-21T12:00:00",
    )

    assert result.status == "success"
    assert index_data["projects"][0]["projekt"] == "Unverändert"
    assert index_data["projects"][0]["models"] == [keep]
    assert index_data["projects"][0]["history"][-1]["message"] == "3D-Modell 'Fassade' wurde entfernt."
    assert events[0][0] == "save"
    assert events[1] == ("delete", (f"{target_prefix}/scene.glb", f"{target_prefix}/model.json"))


def test_remove_project_model_restores_index_and_never_deletes_when_index_save_fails():
    target_prefix = "pointclouds/kunde/project/models/fassade/versions/old"
    index_data = {"projects": [{"id": "project", "models": [{"id": "fassade", "s3_path": target_prefix}]}]}
    original = copy.deepcopy(index_data)
    deleted = []

    with pytest.raises(RuntimeError, match="Index"):
        remove_project_model(
            index_data=index_data,
            project_id="project",
            target_model_s3_path=target_prefix,
            existing_target_keys=(f"{target_prefix}/scene.glb",),
            save_index=lambda _data: False,
            delete_keys=lambda keys: deleted.extend(keys),
        )

    assert index_data == original
    assert deleted == []


def test_remove_project_model_keeps_package_referenced_by_another_project():
    target_prefix = "pointclouds/kunde/shared/models/fassade/versions/old"
    shared = {"id": "fassade", "name": "Fassade", "s3_path": target_prefix}
    index_data = {
        "projects": [
            {"id": "remove-here", "models": [copy.deepcopy(shared)]},
            {"id": "keep-here", "models": [copy.deepcopy(shared)]},
        ]
    }
    deleted = []

    result = remove_project_model(
        index_data=index_data,
        project_id="remove-here",
        target_model_s3_path=target_prefix,
        existing_target_keys=(f"{target_prefix}/scene.glb", f"{target_prefix}/model.json"),
        save_index=lambda _data: True,
        delete_keys=lambda keys: deleted.extend(keys),
    )

    assert result.status == "success"
    assert index_data["projects"][0]["models"] == []
    assert index_data["projects"][1]["models"] == [shared]
    assert deleted == []


def test_add_project_pointclouds_preserves_multi_project_identity_and_existing_children(tmp_path):
    crs_info = {"value": "EPSG:25832", "vertical_crs": "EPSG:7837"}
    source = write_potree(tmp_path, "new", b"new")
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    original_child = {
        "name": "Keep",
        "format": "potree",
        "viewer_path": f"{viewer_root}/keep",
        "s3_path": f"{project_root}/keep",
        "crs_info": {"value": "EPSG:25832"},
        "custom": {"unchanged": True},
    }
    index_data = {
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
                "models": [{"viewer_path": "models/model/model.json", **crs_info}],
                "unknown": {"keep": True},
                "crs_info": {"value": "EPSG:25832"},
                "pointclouds": [original_child],
            }
        ],
    }
    prepared = prepare_cloud_uploads(
        (PointcloudSource(str(source), name="New", input_format="potree", crs_info=crs_info),),
        f"{viewer_root}/versions/versionid",
        f"{project_root}/versions/versionid",
    )

    result = add_project_pointclouds(
        s3_client=FakeS3Client(),
        index_data=index_data,
        project_id="project",
        project_viewer_root=viewer_root,
        project_s3_prefix=project_root,
        prepared_clouds=prepared,
        save_index=lambda _data: True,
        delete_keys=lambda _keys: None,
        timestamp="2026-06-21T13:00:00",
    )

    project = index_data[S3_DISABLED_PROJECTS_KEY][0]
    assert result.status == "success"
    assert project["id"] == "project"
    assert project["kunde"] == "Kunde"
    assert project["projekt"] == "Projekt"
    assert project["datum"] == "2026-06-20T12:00:00"
    assert project["link"] == "https://viewer/?id=project"
    assert project["viewer_path"] == viewer_root
    assert project["s3_path"] == project_root
    assert project["disabled_at"] == "2026-06-21T12:00:00"
    assert project["models"] == [{"viewer_path": "models/model/model.json", **crs_info}]
    assert project["unknown"] == {"keep": True}
    assert project["pointcloud_count"] == 2
    assert project["pointclouds"][0] == original_child
    assert project["pointclouds"][0] is not original_child
    assert project["pointclouds"][1]["s3_path"] == f"{project_root}/versions/versionid/new"
    assert project["history"][-1]["message"] == "1 Punktwolke(n) wurden hinzugefuegt."


def test_add_project_pointclouds_ignores_hidden_cloud_for_common_crs(tmp_path):
    source = write_potree(tmp_path, "new", b"new")
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    index_data = {
        "projects": [
            {
                "id": "project",
                "format": "multi",
                "viewer_path": viewer_root,
                "s3_path": project_root,
                "pointclouds": [
                    {
                        "name": "Hidden",
                        "format": "potree",
                        "viewer_path": f"{viewer_root}/hidden",
                        "s3_path": f"{project_root}/hidden",
                        "visible": False,
                        "crs_info": {"value": "EPSG:4326"},
                    }
                ],
            }
        ],
        S3_DISABLED_PROJECTS_KEY: [],
    }
    prepared = prepare_cloud_uploads(
        (PointcloudSource(str(source), name="New", input_format="potree", crs_info={"value": "EPSG:25832"}),),
        f"{viewer_root}/versions/versionid",
        f"{project_root}/versions/versionid",
    )

    add_project_pointclouds(
        s3_client=FakeS3Client(),
        index_data=index_data,
        project_id="project",
        project_viewer_root=viewer_root,
        project_s3_prefix=project_root,
        prepared_clouds=prepared,
        save_index=lambda _data: True,
        delete_keys=lambda _keys: None,
        timestamp="2026-06-21T13:00:00",
    )

    project = index_data["projects"][0]
    assert project["crs_info"]["value"] == "EPSG:25832"
    assert project["pointclouds"][0]["crs_info"]["value"] == "EPSG:4326"


def test_add_project_pointclouds_promotes_legacy_potree_without_moving_existing_data(tmp_path):
    source = write_potree(tmp_path, "new", b"new")
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    index_data = {
        "projects": [
            {
                "id": "project",
                "kunde": "Kunde",
                "projekt": "Projekt",
                "name": "Bestand",
                "datum": "2026-06-20T12:00:00",
                "link": "https://viewer/?id=project",
                "format": "potree",
                "viewer_path": viewer_root,
                "s3_path": project_root,
                "crs": "EPSG:25832",
                "projection": "EPSG:25832",
                "unknown": {"keep": True},
            }
        ],
        S3_DISABLED_PROJECTS_KEY: [],
    }
    prepared = prepare_cloud_uploads(
        (PointcloudSource(str(source), name="Projekt", input_format="potree", crs_info={"value": "EPSG:25832"}),),
        f"{viewer_root}/versions/versionid",
        f"{project_root}/versions/versionid",
    )

    result = add_project_pointclouds(
        s3_client=FakeS3Client(),
        index_data=index_data,
        project_id="project",
        project_viewer_root=viewer_root,
        project_s3_prefix=project_root,
        prepared_clouds=prepared,
        save_index=lambda _data: True,
        delete_keys=lambda _keys: None,
        timestamp="2026-06-21T13:00:00",
    )

    project = index_data["projects"][0]
    assert result.status == "success"
    assert project["id"] == "project"
    assert project["link"] == "https://viewer/?id=project"
    assert project["viewer_path"] == viewer_root
    assert project["s3_path"] == project_root
    assert project["unknown"] == {"keep": True}
    assert project["format"] == "multi"
    assert project["pointcloud_count"] == 2
    existing = project["pointclouds"][0]
    assert existing["name"] == "Bestand"
    assert existing["format"] == "potree"
    assert existing["viewer_path"] == viewer_root
    assert existing["s3_path"] == project_root
    assert existing["visible"] is True
    assert existing["crs_info"]["value"] == "EPSG:25832"
    assert project["pointclouds"][1]["s3_path"] == (
        f"{project_root}/versions/versionid/projekt"
    )


def test_remove_promoted_root_potree_keeps_versioned_sibling_files():
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    old_cloud = {
        "name": "Bestand",
        "format": "potree",
        "viewer_path": viewer_root,
        "s3_path": project_root,
    }
    new_cloud = {
        "name": "Neu",
        "format": "potree",
        "viewer_path": f"{viewer_root}/versions/v1/neu",
        "s3_path": f"{project_root}/versions/v1/neu",
    }
    index_data = {
        "projects": [
            {
                "id": "project",
                "format": "multi",
                "viewer_path": viewer_root,
                "s3_path": project_root,
                "pointclouds": [old_cloud, new_cloud],
            }
        ],
        S3_DISABLED_PROJECTS_KEY: [],
    }
    deleted_keys = []

    result = remove_project_pointcloud(
        index_data=index_data,
        project_id="project",
        project_viewer_root=viewer_root,
        project_s3_prefix=project_root,
        target_pointcloud_s3_path=project_root,
        existing_target_keys=(
            f"{project_root}/metadata.json",
            f"{project_root}/octree.bin",
            f"{project_root}/versions/v1/neu/cloud.js",
            f"{project_root}/models/halle/model.json",
        ),
        save_index=lambda _data: True,
        delete_keys=lambda keys: deleted_keys.extend(keys),
    )

    assert result.status == "success"
    assert deleted_keys == [f"{project_root}/metadata.json", f"{project_root}/octree.bin"]
    assert index_data["projects"][0]["pointclouds"] == [new_cloud]


def test_add_project_pointclouds_rolls_back_uploaded_keys_before_index_save(tmp_path):
    source = write_potree(tmp_path, "new", b"new")
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    original = {
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
                    }
                ],
            }
        ],
        S3_DISABLED_PROJECTS_KEY: [],
    }
    index_data = copy.deepcopy(original)
    prepared = prepare_cloud_uploads(
        (PointcloudSource(str(source), name="New", input_format="potree"),),
        f"{viewer_root}/versions/versionid",
        f"{project_root}/versions/versionid",
    )
    deleted_keys = []

    with pytest.raises(RuntimeError, match="Projekt-Index"):
        add_project_pointclouds(
            s3_client=FakeS3Client(),
            index_data=index_data,
            project_id="project",
            project_viewer_root=viewer_root,
            project_s3_prefix=project_root,
            prepared_clouds=prepared,
            save_index=lambda _data: False,
            delete_keys=lambda keys: deleted_keys.extend(keys),
        )

    assert index_data == original
    assert deleted_keys == [
        f"{project_root}/versions/versionid/new/cloud.js",
        f"{project_root}/versions/versionid/new/metadata.json",
    ]


def test_add_project_pointclouds_restores_index_when_upload_cleanup_also_fails(tmp_path):
    source = write_potree(tmp_path, "new", b"new")
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    original = {
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
                    }
                ],
            }
        ],
        S3_DISABLED_PROJECTS_KEY: [],
    }
    index_data = copy.deepcopy(original)
    prepared = prepare_cloud_uploads(
        (PointcloudSource(str(source), name="New", input_format="potree"),),
        f"{viewer_root}/versions/versionid",
        f"{project_root}/versions/versionid",
    )

    with pytest.raises(RuntimeError, match="verwaiste S3-Keys") as error:
        add_project_pointclouds(
            s3_client=FakeS3Client(),
            index_data=index_data,
            project_id="project",
            project_viewer_root=viewer_root,
            project_s3_prefix=project_root,
            prepared_clouds=prepared,
            save_index=lambda _data: False,
            delete_keys=lambda _keys: (_ for _ in ()).throw(RuntimeError("delete denied")),
        )

    assert index_data == original
    assert "Projekt-Index konnte nicht gespeichert werden" in str(error.value)
    assert f"{project_root}/versions/versionid/new/cloud.js" in str(error.value)


def test_add_and_remove_preserve_legacy_child_crs_without_crs_info(tmp_path):
    source = write_potree(tmp_path, "new", b"new")
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    index_data = {
        "projects": [
            {
                "id": "project",
                "format": "multi",
                "viewer_path": viewer_root,
                "s3_path": project_root,
                "crs": "EPSG:25832",
                "projection": "EPSG:25832",
                "pointclouds": [
                    {
                        "name": "Keep",
                        "format": "potree",
                        "viewer_path": f"{viewer_root}/keep",
                        "s3_path": f"{project_root}/keep",
                        "crs_info": {},
                        "crs": "EPSG:25832",
                        "projection": "EPSG:25832",
                    },
                    {
                        "name": "Remove",
                        "format": "potree",
                        "viewer_path": f"{viewer_root}/remove",
                        "s3_path": f"{project_root}/remove",
                        "crs": "EPSG:25832",
                        "projection": "EPSG:25832",
                    },
                ],
            }
        ],
        S3_DISABLED_PROJECTS_KEY: [],
    }
    prepared = prepare_cloud_uploads(
        (PointcloudSource(str(source), name="New", input_format="potree", crs_info={"value": "EPSG:25832"}),),
        f"{viewer_root}/versions/versionid",
        f"{project_root}/versions/versionid",
    )

    add_project_pointclouds(
        s3_client=FakeS3Client(),
        index_data=index_data,
        project_id="project",
        project_viewer_root=viewer_root,
        project_s3_prefix=project_root,
        prepared_clouds=prepared,
        save_index=lambda _data: True,
        delete_keys=lambda _keys: None,
    )
    project = index_data["projects"][0]
    assert project["crs"] == "EPSG:25832"
    assert project["projection"] == "EPSG:25832"

    remove_project_pointcloud(
        index_data=index_data,
        project_id="project",
        project_viewer_root=viewer_root,
        project_s3_prefix=project_root,
        target_pointcloud_s3_path=f"{project_root}/remove",
        existing_target_keys=(f"{project_root}/remove/cloud.js",),
        save_index=lambda _data: True,
        delete_keys=lambda _keys: None,
    )
    project = index_data["projects"][0]
    assert project["crs"] == "EPSG:25832"
    assert project["projection"] == "EPSG:25832"


def test_remove_project_pointcloud_deletes_only_the_exact_child_after_index_save():
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    target_path = f"{project_root}/remove"
    index_data = {
        "projects": [
            {
                "id": "project",
                "format": "multi",
                "viewer_path": viewer_root,
                "s3_path": project_root,
                "models": [{"s3_path": "models/model/versions/one/model.json"}],
                "pointclouds": [
                    {
                        "name": "Keep",
                        "format": "potree",
                        "viewer_path": f"{viewer_root}/keep",
                        "s3_path": f"{project_root}/keep",
                        "crs_info": {"value": "EPSG:25832"},
                    },
                    {
                        "name": "Remove",
                        "format": "potree",
                        "viewer_path": f"{viewer_root}/remove",
                        "s3_path": target_path,
                        "crs_info": {"value": "EPSG:4326"},
                    },
                ],
            }
        ],
        S3_DISABLED_PROJECTS_KEY: [],
    }
    actions = []

    result = remove_project_pointcloud(
        index_data=index_data,
        project_id="project",
        project_viewer_root=viewer_root,
        project_s3_prefix=project_root,
        target_pointcloud_s3_path=target_path,
        existing_target_keys=(
            f"{target_path}/cloud.js",
            f"{target_path}/metadata.json",
            f"{target_path}-other/cloud.js",
            f"{project_root}/keep/cloud.js",
            f"{project_root}/root-file.bin",
        ),
        save_index=lambda _data: actions.append("save") or True,
        delete_keys=lambda keys: actions.append(("delete", keys)),
        timestamp="2026-06-21T13:00:00",
    )

    project = index_data["projects"][0]
    assert result.status == "success"
    assert actions == [
        "save",
        ("delete", (f"{target_path}/cloud.js", f"{target_path}/metadata.json")),
    ]
    assert project["models"] == [{"s3_path": "models/model/versions/one/model.json"}]
    assert project["pointcloud_count"] == 1
    assert project["pointclouds"][0]["name"] == "Keep"
    assert project["crs"] == "EPSG:25832"
    assert project["history"][-1]["message"] == "Punktwolke 'Remove' wurde entfernt."


def test_remove_project_pointcloud_rejects_last_child_and_reports_cleanup_failure():
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    only_project = {
        "projects": [
            {
                "id": "project",
                "format": "multi",
                "viewer_path": viewer_root,
                "s3_path": project_root,
                "pointclouds": [
                    {
                        "name": "Only",
                        "format": "potree",
                        "viewer_path": f"{viewer_root}/only",
                        "s3_path": f"{project_root}/only",
                    }
                ],
            }
        ],
        S3_DISABLED_PROJECTS_KEY: [],
    }

    with pytest.raises(ValueError, match="letzte Punktwolke"):
        remove_project_pointcloud(
            index_data=only_project,
            project_id="project",
            project_viewer_root=viewer_root,
            project_s3_prefix=project_root,
            target_pointcloud_s3_path=f"{project_root}/only",
            existing_target_keys=(),
            save_index=lambda _data: True,
            delete_keys=lambda _keys: None,
        )

    index_data = copy.deepcopy(only_project)
    second = {
        "name": "Second",
        "format": "potree",
        "viewer_path": f"{viewer_root}/second",
        "s3_path": f"{project_root}/second",
    }
    index_data["projects"][0]["pointclouds"].append(second)
    result = remove_project_pointcloud(
        index_data=index_data,
        project_id="project",
        project_viewer_root=viewer_root,
        project_s3_prefix=project_root,
        target_pointcloud_s3_path=second["s3_path"],
        existing_target_keys=(f"{second['s3_path']}/cloud.js",),
        save_index=lambda _data: True,
        delete_keys=lambda _keys: (_ for _ in ()).throw(RuntimeError("delete denied")),
    )

    assert result.status == "partial"
    assert result.orphaned_keys == (f"{second['s3_path']}/cloud.js",)
    assert [cloud["name"] for cloud in index_data["projects"][0]["pointclouds"]] == ["Only"]


def test_replacing_all_pointclouds_preserves_models_and_never_cleans_model_objects(tmp_path):
    crs_info = {"value": "EPSG:25832", "vertical_crs": "EPSG:7837"}
    source = write_potree(tmp_path, "replacement", b"replacement")
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    version = "a" * 64
    models = [
        {
            "id": "building",
            **crs_info,
            "viewer_path": f"{viewer_root}/models/building/versions/{version}/model.json",
            "s3_path": f"{project_root}/models/building/versions/{version}",
        }
    ]
    index_data = {
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
                        "format": "potree",
                        "viewer_path": f"{viewer_root}/old",
                        "s3_path": f"{project_root}/old",
                    }
                ],
            }
        ]
    }
    prepared = prepare_cloud_uploads(
        (PointcloudSource(str(source), name="Replacement", input_format="potree", crs_info=crs_info),),
        viewer_root,
        project_root,
    )
    deleted_keys = []

    result = replace_project_pointclouds(
        s3_client=FakeS3Client(),
        index_data=index_data,
        project_id="project",
        base_viewer_path=viewer_root,
        s3_prefix=project_root,
        prepared_clouds=prepared,
        existing_keys=(
            f"{project_root}/old/cloud.js",
            f"{project_root}/models/building/versions/{version}/scene.glb",
            f"{project_root}/models/building/versions/{version}/model.json",
        ),
        save_index=lambda _data: True,
        delete_keys=lambda keys: deleted_keys.extend(keys),
    )

    assert result.status == "success"
    assert index_data["projects"][0]["models"] == models
    assert deleted_keys == [f"{project_root}/old/cloud.js"]


def test_replacing_single_pointcloud_preserves_models_and_never_cleans_model_objects(tmp_path):
    crs_info = {"value": "EPSG:25832", "vertical_crs": "EPSG:7837"}
    source = write_potree(tmp_path, "replacement", b"replacement")
    project_root = "pointclouds/kunde/project/projekt"
    viewer_root = "kunde/project/projekt"
    version = "a" * 64
    models = [{
        "id": "building",
        **crs_info,
        "format": "glb",
        "viewer_path": f"{viewer_root}/models/building/versions/{version}/model.json",
        "s3_path": f"{project_root}/models/building/versions/{version}",
    }]
    index_data = {
        "projects": [
            {
                "id": "project",
                "format": "multi",
                "viewer_path": viewer_root,
                "s3_path": project_root,
                "models": copy.deepcopy(models),
                "pointclouds": [
                    {
                        "name": "Target",
                        "format": "potree",
                        "viewer_path": f"{viewer_root}/target",
                        "s3_path": f"{project_root}/target",
                    }
                ],
            }
        ]
    }
    prepared = prepare_cloud_uploads(
        (PointcloudSource(str(source), name="Replacement", input_format="potree", crs_info=crs_info),),
        viewer_root,
        project_root,
    )[0]
    deleted_keys = []

    result = replace_single_project_pointcloud(
        s3_client=FakeS3Client(),
        index_data=index_data,
        project_id="project",
        base_viewer_path=viewer_root,
        s3_prefix=project_root,
        prepared_cloud=prepared,
        target_pointcloud_s3_path=f"{project_root}/target",
        existing_target_keys=(
            f"{project_root}/target/cloud.js",
            f"{project_root}/models/building/versions/{version}/scene.glb",
        ),
        save_index=lambda _data: True,
        delete_keys=lambda keys: deleted_keys.extend(keys),
    )

    assert result.status == "success"
    assert index_data["projects"][0]["models"] == models
    assert deleted_keys == [f"{project_root}/target/cloud.js"]
