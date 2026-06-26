import io
import json
from pathlib import Path

import pytest

from dronautix_uploader.core.cutover_acceptance import (
    DEFAULT_S3_ACCEPTANCE_SCENARIOS,
    REAL_S3_ACCEPTANCE,
    evaluate_acceptance_evidence,
)
from dronautix_uploader.core.constants import (
    S3_CACHE_CONTROL,
    S3_DELETED_CACHE_CONTROL,
    S3_INDEX_CACHE_CONTROL,
)
from dronautix_uploader.core.s3_acceptance_smoke import (
    S3AcceptanceSmokeConfig,
    S3WriteFenceClient,
    merge_s3_smoke_into_acceptance_evidence,
    run_v2_s3_acceptance_smoke,
)


def test_s3_acceptance_smoke_uses_isolated_metadata_keys_and_cleans_up():
    fake_s3 = FakeS3Client()

    result = run_v2_s3_acceptance_smoke(
        s3_client=fake_s3,
        config=S3AcceptanceSmokeConfig(bucket_name="acceptance-bucket", run_id="Test Run"),
    )

    assert result.status == "passed"
    assert result.test_prefix == "v2-cutover-acceptance/test-run/"
    assert result.projects_index_key == "v2-cutover-acceptance/test-run/projects_index.json"
    assert result.deleted_projects_key == "v2-cutover-acceptance/test-run/deleted_projects.json"
    assert tuple(result.scenarios_passed) == tuple(DEFAULT_S3_ACCEPTANCE_SCENARIOS)
    assert result.projects_index_verified is True
    assert result.metadata_verified is True
    assert result.cleanup_verified is True
    assert not any(key == "projects_index.json" for key in fake_s3.put_keys)
    assert not any(key == "deleted_projects.json" for key in fake_s3.put_keys)
    assert not any(key.startswith(result.test_prefix) for key in fake_s3.objects)
    assert not any(key.startswith(result.project_root_prefix) for key in fake_s3.objects)
    assert fake_s3.uploads
    assert all(upload["extra_args"]["CacheControl"] == S3_CACHE_CONTROL for upload in fake_s3.uploads)
    assert all(upload["extra_args"]["ContentType"] for upload in fake_s3.uploads)
    assert fake_s3.copies
    assert all(copy["CacheControl"] == S3_CACHE_CONTROL for copy in fake_s3.copies)
    assert all(copy["MetadataDirective"] == "REPLACE" for copy in fake_s3.copies)
    assert any(put["Key"] == result.projects_index_key for put in fake_s3.puts)
    assert any(put["Key"] == result.deleted_projects_key for put in fake_s3.puts)
    assert all(put["ContentType"] == "application/json" for put in fake_s3.puts)
    assert all(
        put["CacheControl"] == S3_INDEX_CACHE_CONTROL
        for put in fake_s3.puts
        if put["Key"] == result.projects_index_key
    )
    assert all(
        put["CacheControl"] == S3_DELETED_CACHE_CONTROL
        for put in fake_s3.puts
        if put["Key"] == result.deleted_projects_key
    )


def test_s3_acceptance_smoke_gate_can_be_merged_into_acceptance_evidence():
    result = run_v2_s3_acceptance_smoke(
        s3_client=FakeS3Client(),
        config=S3AcceptanceSmokeConfig(bucket_name="acceptance-bucket", run_id="merge"),
    )
    evidence = {
        "schema_version": 1,
        "gates": {},
    }

    merged = merge_s3_smoke_into_acceptance_evidence(evidence, result)
    gates = evaluate_acceptance_evidence(
        merged,
        required_s3_scenarios=DEFAULT_S3_ACCEPTANCE_SCENARIOS,
    )
    s3_gate = next(gate for gate in gates if gate.gate_id == REAL_S3_ACCEPTANCE)

    assert merged["gates"][REAL_S3_ACCEPTANCE]["status"] == "passed"
    assert s3_gate.complete is True


def test_s3_write_fence_rejects_operations_outside_test_prefixes():
    fenced = S3WriteFenceClient(
        FakeS3Client(),
        allowed_prefixes=("pointclouds/v2_acceptance", "v2-cutover-acceptance/run"),
    )

    bad_calls = {
        "get_object": lambda: fenced.get_object(Bucket="bucket", Key="projects_index.json"),
        "put_object": lambda: fenced.put_object(
            Bucket="bucket",
            Key="projects_index.json",
            Body="{}",
        ),
        "upload_file": lambda: fenced.upload_file("missing.bin", "bucket", "projects_index.json"),
        "copy_source": lambda: fenced.copy_object(
            Bucket="bucket",
            CopySource={"Bucket": "bucket", "Key": "projects_index.json"},
            Key="pointclouds/v2_acceptance/copy",
        ),
        "copy_target": lambda: fenced.copy_object(
            Bucket="bucket",
            CopySource={"Bucket": "bucket", "Key": "pointclouds/v2_acceptance/source"},
            Key="projects_index.json",
        ),
        "delete_objects": lambda: fenced.delete_objects(
            Bucket="bucket",
            Delete={"Objects": [{"Key": "deleted_projects.json"}]},
        ),
        "list_objects_v2": lambda: tuple(
            fenced.get_paginator("list_objects_v2").paginate(Bucket="bucket", Prefix="projects_index.json")
        ),
    }

    for call in bad_calls.values():
        with pytest.raises(RuntimeError, match="outside test prefixes"):
            call()


class FakePaginator:
    def __init__(self, client):
        self.client = client

    def paginate(self, **kwargs):
        prefix = str(kwargs.get("Prefix", "") or "")
        contents = [
            {"Key": key, "Size": len(data)}
            for key, data in sorted(self.client.objects.items())
            if key.startswith(prefix)
        ]
        return ({"Contents": contents},) if contents else ({},)


class FakeS3Client:
    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self):
        self.objects = {}
        self.put_keys = []
        self.puts = []
        self.uploads = []
        self.copies = []

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ContentType=None, CacheControl=None):
        self.put_keys.append(Key)
        self.puts.append(
            {
                "Bucket": Bucket,
                "Key": Key,
                "ContentType": ContentType,
                "CacheControl": CacheControl,
            }
        )
        body = Body if isinstance(Body, bytes) else str(Body).encode("utf-8")
        self.objects[Key] = body
        return {"ETag": '"fake"'}

    def upload_file(self, local_path, bucket, key, ExtraArgs=None, Callback=None):
        data = Path(local_path).read_bytes()
        self.uploads.append(
            {
                "local_path": local_path,
                "bucket": bucket,
                "key": key,
                "extra_args": ExtraArgs or {},
            }
        )
        self.objects[key] = data
        if Callback is not None:
            Callback(len(data))

    def copy_object(self, Bucket, CopySource, Key, **kwargs):
        source_key = CopySource["Key"]
        if source_key not in self.objects:
            raise self.exceptions.NoSuchKey(source_key)
        self.copies.append({"Bucket": Bucket, "CopySource": CopySource, "Key": Key, **kwargs})
        self.objects[Key] = self.objects[source_key]
        return {"CopyObjectResult": {"ETag": '"fake-copy"'}}

    def delete_objects(self, Bucket, Delete):
        deleted = []
        for item in Delete.get("Objects", []):
            key = item["Key"]
            self.objects.pop(key, None)
            deleted.append({"Key": key})
        return {"Deleted": deleted}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return FakePaginator(self)
