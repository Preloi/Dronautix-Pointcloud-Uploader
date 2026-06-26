import io
import json
from types import SimpleNamespace

import pytest

from dronautix_uploader.core.constants import (
    S3_DELETED_CACHE_CONTROL,
    S3_DELETED_JSON,
    S3_INDEX_CACHE_CONTROL,
    S3_INDEX_JSON,
)
from dronautix_uploader.core.project_repository import ProjectMetadataRepository


class NoSuchKey(Exception):
    pass


class FakeS3Client:
    exceptions = SimpleNamespace(NoSuchKey=NoSuchKey)

    def __init__(self, objects=None):
        self.objects = objects or {}
        self.puts = []

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)


def test_load_projects_index_reads_existing_json_from_s3():
    payload = {"projects": [{"id": "project-1"}], "last_updated": "2026-06-21T12:00:00"}
    fake_s3 = FakeS3Client({S3_INDEX_JSON: json.dumps(payload).encode("utf-8")})
    repository = ProjectMetadataRepository(fake_s3, bucket_name="bucket")

    assert repository.load_projects_index() == payload


def test_load_projects_index_returns_default_for_missing_object():
    repository = ProjectMetadataRepository(FakeS3Client(), bucket_name="bucket")

    first = repository.load_projects_index()
    second = repository.load_projects_index()

    assert first == {"projects": [], "last_updated": None}
    assert second == {"projects": [], "last_updated": None}
    assert first is not second
    assert first["projects"] is not second["projects"]


def test_load_deleted_projects_returns_default_for_missing_object():
    repository = ProjectMetadataRepository(FakeS3Client(), bucket_name="bucket")

    assert repository.load_deleted_projects() == {"deleted_projects": [], "last_updated": None}


def test_load_json_raises_runtime_error_with_key_context_for_invalid_json():
    fake_s3 = FakeS3Client({S3_INDEX_JSON: b"{not-json"})
    repository = ProjectMetadataRepository(fake_s3, bucket_name="bucket")

    with pytest.raises(RuntimeError, match=S3_INDEX_JSON):
        repository.load_projects_index()


def test_save_projects_index_writes_utf8_json_metadata_to_expected_key():
    fake_s3 = FakeS3Client()
    repository = ProjectMetadataRepository(
        fake_s3,
        bucket_name="bucket",
        timestamp_factory=lambda: "2026-06-21T12:00:00",
    )

    repository.save_projects_index(
        {
            "projects": [{"name": "M\u00fcnchen", "id": "p1", "_link_disabled": True, "link_disabled": True}],
            "last_updated": None,
        }
    )

    assert len(fake_s3.puts) == 1
    put = fake_s3.puts[0]
    assert put["Bucket"] == "bucket"
    assert put["Key"] == S3_INDEX_JSON
    assert put["ContentType"] == "application/json"
    assert put["CacheControl"] == S3_INDEX_CACHE_CONTROL
    assert isinstance(put["Body"], str)
    assert "\\u00fc" not in put["Body"]
    saved = json.loads(put["Body"])
    assert saved == {
        "projects": [{"name": "M\u00fcnchen", "id": "p1"}],
        "disabled_projects": [],
        "last_updated": "2026-06-21T12:00:00",
    }


def test_save_deleted_projects_writes_expected_key_and_json_body():
    fake_s3 = FakeS3Client()
    repository = ProjectMetadataRepository(
        fake_s3,
        bucket_name="bucket",
        timestamp_factory=lambda: "2026-06-21T12:00:00",
    )

    repository.save_deleted_projects({"deleted_projects": [{"id": "old"}], "last_updated": None})

    put = fake_s3.puts[0]
    assert put["Key"] == S3_DELETED_JSON
    assert put["ContentType"] == "application/json"
    assert put["CacheControl"] == S3_DELETED_CACHE_CONTROL
    assert json.loads(put["Body"]) == {
        "deleted_projects": [{"id": "old"}],
        "last_updated": "2026-06-21T12:00:00",
    }
