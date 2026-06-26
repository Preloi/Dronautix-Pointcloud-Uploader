import os

import pytest

from dronautix_uploader.core.constants import S3_DISABLED_PROJECTS_KEY
from dronautix_uploader.core.contracts import PointcloudSource
from dronautix_uploader.core.project_operations import (
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
    upload_new_project,
)


class FakeS3Client:
    def __init__(self, fail_on_key=""):
        self.fail_on_key = fail_on_key
        self.uploads = []

    def upload_file(self, local_path, bucket, key, ExtraArgs=None, Callback=None):
        if key == self.fail_on_key:
            raise RuntimeError("simulated upload failure")
        if Callback:
            Callback(os.path.getsize(local_path))
        self.uploads.append((bucket, key, ExtraArgs))


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


def test_prepare_cloud_uploads_builds_copc_and_potree_entries(tmp_path):
    copc = tmp_path / "Scan Ä.copc.laz"
    copc.write_bytes(b"copc")
    potree_dir = tmp_path / "potree"
    potree_dir.mkdir()
    (potree_dir / "cloud.js").write_text("cloud.js = {};", encoding="utf-8")
    (potree_dir / "metadata.json").write_text("{}", encoding="utf-8")

    prepared = prepare_cloud_uploads(
        (
            PointcloudSource(str(copc), input_format="copc", crs_info={"value": "EPSG:25832"}),
            PointcloudSource(str(potree_dir), name="Potree Cloud", input_format="potree"),
        ),
        "kunde/id/projekt",
        "pointclouds/kunde/id/projekt",
    )

    assert prepared[0].slug == "scan_ae"
    assert prepared[0].viewer_path == "kunde/id/projekt/scan_ae/source.copc.laz"
    assert prepared[0].s3_path == "pointclouds/kunde/id/projekt/scan_ae/source.copc.laz"
    assert prepared[0].index_entry["crs"] == "EPSG:25832"
    assert prepared[1].slug == "potree_cloud"
    assert [key for _local, key in prepared[1].files_to_upload][-1].endswith("metadata.json")


def test_compute_orphaned_keys_keeps_reuploaded_keys():
    assert compute_orphaned_keys(
        ["prefix/cloud.js", "prefix/metadata.json", "prefix/old.bin"],
        ["prefix/cloud.js", "prefix/metadata.json"],
    ) == ("prefix/old.bin",)


def test_build_new_project_upload_single_copc_uses_legacy_project_shape(tmp_path):
    copc = tmp_path / "single.copc.laz"
    copc.write_bytes(b"copc")

    upload = build_new_project_upload(
        sources=(PointcloudSource(str(copc), input_format="copc", crs_info={"value": "EPSG:25832"}),),
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
        "format": "copc",
        "link": "https://viewer/?id=abc123ef",
        "viewer_path": "kunde/abc123ef/projekt/source.copc.laz",
        "s3_path": "pointclouds/kunde/abc123ef/projekt",
        "crs": "EPSG:25832",
        "projection": "EPSG:25832",
        "crs_info": {"value": "EPSG:25832"},
    }
    assert upload.files_to_upload == ((str(copc), "pointclouds/kunde/abc123ef/projekt/source.copc.laz"),)


def test_build_new_project_upload_multi_mixed_sources_sets_pointclouds_and_clears_mismatch(tmp_path):
    copc = tmp_path / "scan.copc.laz"
    copc.write_bytes(b"copc")
    potree = tmp_path / "potree"
    potree.mkdir()
    (potree / "cloud.js").write_text("cloud.js = {};", encoding="utf-8")
    (potree / "metadata.json").write_text("{}", encoding="utf-8")

    upload = build_new_project_upload(
        sources=(
            PointcloudSource(str(copc), name="Scan", input_format="copc", crs_info={"value": "EPSG:25832"}),
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
    copc = tmp_path / "single.copc.laz"
    copc.write_bytes(b"copc")
    prepared_upload = build_new_project_upload(
        sources=(PointcloudSource(str(copc), input_format="copc"),),
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
    assert result.uploaded_keys == ("pointclouds/kunde/abc123ef/projekt/source.copc.laz",)
    assert [project["id"] for project in index_data["projects"]] == ["abc123ef", "old"]
    assert saved_indexes == [["abc123ef", "old"]]
    assert deleted_keys == []


def test_upload_new_project_forwards_progress_events(tmp_path):
    copc = tmp_path / "single.copc.laz"
    copc.write_bytes(b"copc")
    prepared_upload = build_new_project_upload(
        sources=(PointcloudSource(str(copc), input_format="copc"),),
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
    assert [event.kind for event in events] == ["log", "log", "progress", "progress", "log"]
    assert events[-2].percent == 1.0


def test_upload_new_project_rolls_back_uploaded_keys_when_index_save_fails(tmp_path):
    copc = tmp_path / "single.copc.laz"
    copc.write_bytes(b"copc")
    prepared_upload = build_new_project_upload(
        sources=(PointcloudSource(str(copc), input_format="copc"),),
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
    assert deleted_keys == ["pointclouds/kunde/abc123ef/projekt/source.copc.laz"]


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
                "format": "copc",
                "viewer_path": "alt/oldid/altprojekt/cloud_b/source.copc.laz",
                "s3_path": "pointclouds/alt/oldid/altprojekt/cloud_b/source.copc.laz",
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
    assert duplicated["pointclouds"][1]["viewer_path"] == "neu/newid/neuprojekt/cloud_b/source.copc.laz"
    assert duplicated["pointclouds"][1]["s3_path"] == "pointclouds/neu/newid/neuprojekt/cloud_b/source.copc.laz"
    assert duplicated["pointclouds"][1]["visible"] is False


def test_apply_project_rename_metadata_changes_names_without_paths():
    project = {
        "kunde": "Alt",
        "projekt": "Altprojekt",
        "viewer_path": "alt/id/altprojekt",
        "s3_path": "pointclouds/alt/id/altprojekt",
        "pointclouds": [
            {"name": "A", "viewer_path": "alt/id/altprojekt/a"},
            {"name": "B", "viewer_path": "alt/id/altprojekt/b"},
        ],
    }

    renamed = apply_project_rename_metadata(project, "Neu", "Neuprojekt", ("Cloud A", "Cloud B"))

    assert renamed["kunde"] == "Neu"
    assert renamed["projekt"] == "Neuprojekt"
    assert renamed["viewer_path"] == "alt/id/altprojekt"
    assert renamed["s3_path"] == "pointclouds/alt/id/altprojekt"
    assert [cloud["name"] for cloud in renamed["pointclouds"]] == ["Cloud A", "Cloud B"]
    assert project["kunde"] == "Alt"


def test_duplicate_project_copies_s3_objects_and_inserts_active_multi_clone():
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
    }
    s3_client = FakeProjectS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": "pointclouds/alt/oldid/altprojekt/cloud_a/cloud.js", "Size": 10},
                    {"Key": "pointclouds/alt/oldid/altprojekt/cloud_a/metadata.json", "Size": 10},
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
    )
    assert [project["id"] for project in index_data["projects"]] == ["newid", "existing"]
    assert index_data[S3_DISABLED_PROJECTS_KEY] == [{"id": "oldid"}]
    assert index_data["projects"][0]["pointclouds"][0]["s3_path"] == "pointclouds/neu/newid/neuprojekt/cloud_a"
    assert saved == [["newid", "existing"]]
    assert deleted == []


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


def test_delete_project_removes_from_disabled_list_and_upserts_deleted_entry():
    s3_client = FakeProjectS3Client(
        pages=[{"Contents": [{"Key": "pointclouds/old/cloud.js", "Size": 10}]}]
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
    assert result.deleted_keys == ("pointclouds/old/cloud.js",)
    assert index_data == {"projects": [{"id": "active"}], S3_DISABLED_PROJECTS_KEY: []}
    assert deleted_data["deleted_projects"][0]["id"] == "oldid"
    assert deleted_data["deleted_projects"][0]["deleted_at"] == "2026-06-21T13:00:00"
    assert saved_index and saved_deleted


def test_delete_project_reports_partial_when_metadata_save_fails():
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
    assert result.deleted_keys == ("pointclouds/old/cloud.js",)
    assert "projects_index.json" in result.warnings[0]


def test_download_project_uses_legacy_folder_name_and_safe_paths(tmp_path):
    s3_client = FakeProjectS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": "pointclouds/kunde/id/projekt/cloud.js", "Size": 4},
                    {"Key": "pointclouds/kunde/id/projekt/../nested/data.bin", "Size": 4},
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
    first = tmp_path / "first.copc.laz"
    second = tmp_path / "second.copc.laz"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    prepared = prepare_cloud_uploads(
        (
            PointcloudSource(str(first), name="First", input_format="copc", crs_info={"value": "EPSG:25832"}),
            PointcloudSource(str(second), name="Second", input_format="copc", crs_info={"value": "EPSG:4326"}),
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
            "pointclouds/kunde/project/projekt/first/source.copc.laz",
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
    first = tmp_path / "first.copc.laz"
    first.write_bytes(b"first")
    prepared = prepare_cloud_uploads(
        (PointcloudSource(str(first), name="First", input_format="copc"),),
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
    assert [event.kind for event in events] == ["log", "log", "progress", "progress", "log"]


def test_replace_project_pointclouds_rolls_back_uploaded_keys_before_index_save(tmp_path):
    first = tmp_path / "first.copc.laz"
    second = tmp_path / "second.copc.laz"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    prepared = prepare_cloud_uploads(
        (
            PointcloudSource(str(first), name="First", input_format="copc"),
            PointcloudSource(str(second), name="Second", input_format="copc"),
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

    assert deleted_keys == [prepared[0].files_to_upload[0][1]]
    assert index_data == {"projects": [{"id": "project", "projekt": "Old"}]}


def test_replace_project_pointclouds_reports_orphan_cleanup_failure_after_index_save(tmp_path):
    first = tmp_path / "first.copc.laz"
    first.write_bytes(b"first")
    prepared = prepare_cloud_uploads(
        (PointcloudSource(str(first), name="First", input_format="copc"),),
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
    replacement = tmp_path / "replacement.copc.laz"
    replacement.write_bytes(b"replacement")
    prepared = prepare_cloud_uploads(
        (PointcloudSource(str(replacement), name="Replacement", input_format="copc", crs_info={"value": "EPSG:25832"}),),
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
                        "format": "copc",
                        "viewer_path": "kunde/project/projekt/keep/source.copc.laz",
                        "s3_path": "pointclouds/kunde/project/projekt/keep/source.copc.laz",
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
    assert pointclouds[1]["format"] == "copc"
    assert pointclouds[1]["crs"] == "EPSG:25832"
    assert deleted_keys == [
        "pointclouds/kunde/project/projekt/target/cloud.js",
        "pointclouds/kunde/project/projekt/target/metadata.json",
    ]


def test_replace_single_project_pointcloud_supports_disabled_legacy_single_project(tmp_path):
    replacement = tmp_path / "replacement.copc.laz"
    replacement.write_bytes(b"replacement")
    prepared = prepare_single_project_upload(
        PointcloudSource(str(replacement), name="Replacement", input_format="copc", crs_info={"value": "EPSG:4326"}),
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
                "format": "copc",
                "link": "https://viewer/?id=project",
                "viewer_path": "kunde/project/projekt/source.copc.laz",
                "s3_path": "pointclouds/kunde/project/projekt/source.copc.laz",
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
        target_pointcloud_s3_path="pointclouds/kunde/project/projekt/source.copc.laz",
        existing_target_keys=(
            "pointclouds/kunde/project/projekt/source.copc.laz",
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
    assert project["format"] == "copc"
    assert "pointclouds" not in project
    assert project["crs"] == "EPSG:4326"
    assert deleted_keys == ["pointclouds/kunde/project/projekt/old.bin"]
