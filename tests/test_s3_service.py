import os

import pytest

from dronautix_uploader.core.constants import S3_CACHE_CONTROL
from dronautix_uploader.core.contracts import UploadedKeyLedger
from dronautix_uploader.core.s3_service import (
    build_safe_download_path,
    collect_project_object_entries,
    collect_upload_files,
    copy_project_objects,
    delete_s3_objects,
    DownloadCancelledError,
    download_project_objects,
    upload_files_to_s3,
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
        self.uploads.append(
            {
                "local_path": local_path,
                "bucket": bucket,
                "key": key,
                "extra_args": ExtraArgs,
            }
        )


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def paginate(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages


class FakeObjectS3Client:
    def __init__(self, pages=None, delete_errors=None):
        self.paginator = FakePaginator(pages or [])
        self.delete_errors = delete_errors or []
        self.deleted_batches = []
        self.copies = []
        self.downloads = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self.paginator

    def delete_objects(self, Bucket, Delete):
        self.deleted_batches.append((Bucket, Delete))
        if self.delete_errors:
            return {"Errors": self.delete_errors}
        return {"Deleted": Delete["Objects"]}

    def copy_object(self, **kwargs):
        self.copies.append(kwargs)

    def download_file(self, bucket, key, local_path, Callback=None):
        if Callback:
            Callback(5)
        self.downloads.append((bucket, key, local_path))
        with open(local_path, "wb") as file:
            file.write(b"data")


class FakePartialDownloadS3Client(FakeObjectS3Client):
    def download_file(self, bucket, key, local_path, Callback=None):
        with open(local_path, "wb") as file:
            file.write(b"partial")
        if Callback:
            Callback(5)
        self.downloads.append((bucket, key, local_path))


def test_collect_upload_files_uses_legacy_copc_target(tmp_path):
    source = tmp_path / "survey.copc.laz"
    source.write_bytes(b"copc")

    assert collect_upload_files("copc", "pointclouds/k/id/p", source_file=str(source)) == [
        (str(source), "pointclouds/k/id/p/source.copc.laz")
    ]


def test_collect_upload_files_sorts_metadata_json_last(tmp_path):
    (tmp_path / "metadata.json").write_text("{}", encoding="utf-8")
    (tmp_path / "cloud.js").write_text("cloud.js = {};", encoding="utf-8")
    nested = tmp_path / "hierarchy"
    nested.mkdir()
    (nested / "a.bin").write_bytes(b"abc")

    files = collect_upload_files("potree", "pointclouds/k/id/p", output_dir=str(tmp_path))

    assert [key for _local, key in files] == [
        "pointclouds/k/id/p/cloud.js",
        "pointclouds/k/id/p/hierarchy/a.bin",
        "pointclouds/k/id/p/metadata.json",
    ]


def test_upload_files_records_only_successfully_uploaded_keys(tmp_path):
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    fake_s3 = FakeS3Client(fail_on_key="prefix/second.bin")
    ledger = UploadedKeyLedger()

    with pytest.raises(RuntimeError):
        upload_files_to_s3(
            fake_s3,
            [(str(first), "prefix/first.bin"), (str(second), "prefix/second.bin")],
            bucket_name="bucket",
            ledger=ledger,
        )

    assert ledger.as_tuple() == ("prefix/first.bin",)
    assert fake_s3.uploads[0]["extra_args"] == {
        "ContentType": "application/octet-stream",
        "CacheControl": S3_CACHE_CONTROL,
    }
    assert S3_CACHE_CONTROL == "public, max-age=31536000, immutable"
    assert "ContentEncoding" not in fake_s3.uploads[0]["extra_args"]


def test_collect_project_object_entries_reads_paginator_pages():
    fake_s3 = FakeObjectS3Client(
        pages=[
            {"Contents": [{"Key": "prefix/a.bin", "Size": 4}, {"Key": "", "Size": 1}]},
            {"Contents": [{"Key": "prefix/b.bin"}]},
        ]
    )

    assert collect_project_object_entries(fake_s3, "prefix", bucket_name="bucket") == [
        {"Key": "prefix/a.bin", "Size": 4},
        {"Key": "prefix/b.bin", "Size": 0},
    ]
    assert fake_s3.paginator.calls == [{"Bucket": "bucket", "Prefix": "prefix"}]


def test_delete_s3_objects_raises_on_partial_errors():
    fake_s3 = FakeObjectS3Client(delete_errors=[{"Key": "bad", "Code": "Denied", "Message": "no"}])

    with pytest.raises(RuntimeError, match="bad"):
        delete_s3_objects(fake_s3, ("bad",), bucket_name="bucket")


def test_copy_project_objects_preserves_relative_paths_and_cache_control():
    fake_s3 = FakeObjectS3Client()

    copied = copy_project_objects(
        fake_s3,
        ("old/root/cloud.js", "old/root/data/a.bin"),
        "old/root",
        "new/root",
        bucket_name="bucket",
    )

    assert copied == ("new/root/cloud.js", "new/root/data/a.bin")
    assert fake_s3.copies[0]["CacheControl"] == S3_CACHE_CONTROL
    assert fake_s3.copies[0]["MetadataDirective"] == "REPLACE"


def test_copy_project_objects_replaces_glb_and_json_metadata_with_explicit_mime_types():
    fake_s3 = FakeObjectS3Client()

    copy_project_objects(
        fake_s3,
        ("old/models/scene.glb", "old/models/model.json"),
        "old",
        "new",
        bucket_name="bucket",
    )

    assert [copy["ContentType"] for copy in fake_s3.copies] == [
        "model/gltf-binary",
        "application/json",
    ]


def test_managed_copy_replaces_model_metadata_with_explicit_mime_type():
    class ManagedCopyClient(FakeObjectS3Client):
        def copy(self, copy_source, bucket, key, ExtraArgs=None, Callback=None):
            self.copies.append({"CopySource": copy_source, "Bucket": bucket, "Key": key, "ExtraArgs": ExtraArgs})
            if Callback:
                Callback(10)

    fake_s3 = ManagedCopyClient()
    copy_project_objects(
        fake_s3,
        ("old/models/scene.glb", "old/models/model.json", "old/models/texture.ktx2"),
        "old",
        "new",
        bucket_name="bucket",
        on_progress=lambda _event: None,
        source_sizes={
            "old/models/scene.glb": 10,
            "old/models/model.json": 10,
            "old/models/texture.ktx2": 10,
        },
    )

    assert [copy["ExtraArgs"]["ContentType"] for copy in fake_s3.copies] == [
        "model/gltf-binary",
        "application/json",
        "image/ktx2",
    ]


def test_build_safe_download_path_removes_traversal_segments(tmp_path):
    path = build_safe_download_path(str(tmp_path), "prefix", "prefix/../safe/cloud.js")

    assert path == str(tmp_path / "safe" / "cloud.js")


def test_download_project_objects_writes_safe_local_paths(tmp_path):
    fake_s3 = FakeObjectS3Client()

    downloaded = download_project_objects(
        fake_s3,
        ({"Key": "prefix/cloud.js", "Size": 5}, {"Key": "prefix/nested/data.bin", "Size": 5}),
        "prefix",
        str(tmp_path),
        bucket_name="bucket",
    )

    assert downloaded == (str(tmp_path / "cloud.js"), str(tmp_path / "nested" / "data.bin"))
    assert (tmp_path / "cloud.js").is_file()
    assert fake_s3.downloads[1][1] == "prefix/nested/data.bin"


def test_download_project_objects_can_cancel_before_next_file(tmp_path):
    fake_s3 = FakeObjectS3Client()

    with pytest.raises(DownloadCancelledError) as exc_info:
        download_project_objects(
            fake_s3,
            ({"Key": "prefix/cloud.js", "Size": 5}, {"Key": "prefix/nested/data.bin", "Size": 5}),
            "prefix",
            str(tmp_path),
            bucket_name="bucket",
            cancel_requested=lambda: len(fake_s3.downloads) >= 1,
        )

    assert exc_info.value.downloaded_paths == (str(tmp_path / "cloud.js"),)
    assert [download[1] for download in fake_s3.downloads] == ["prefix/cloud.js"]


def test_download_project_objects_can_cancel_from_progress_callback(tmp_path):
    fake_s3 = FakeObjectS3Client()
    events = []

    with pytest.raises(DownloadCancelledError) as exc_info:
        download_project_objects(
            fake_s3,
            ({"Key": "prefix/cloud.js", "Size": 5},),
            "prefix",
            str(tmp_path),
            bucket_name="bucket",
            on_progress=events.append,
            cancel_requested=lambda: any(event.kind == "progress" for event in events),
        )

    assert exc_info.value.downloaded_paths == ()
    assert fake_s3.downloads == []
    assert events[-1].kind == "warning"


def test_download_project_objects_removes_active_partial_file_when_cancelled(tmp_path):
    fake_s3 = FakePartialDownloadS3Client()
    events = []

    with pytest.raises(DownloadCancelledError):
        download_project_objects(
            fake_s3,
            ({"Key": "prefix/cloud.js", "Size": 5},),
            "prefix",
            str(tmp_path),
            bucket_name="bucket",
            on_progress=events.append,
            cancel_requested=lambda: any(event.kind == "progress" for event in events),
        )

    assert not (tmp_path / "cloud.js").exists()
    assert fake_s3.downloads == []
