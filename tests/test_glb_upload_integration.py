import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from dronautix_uploader.core.contracts import (
    GLBOptimizationResult,
    ModelIndexEntry,
    ModelUploadInput,
    PreparedModelUpload,
)
from dronautix_uploader.core.upload_workflow_service import (
    NewProjectUploadWorkflowRequest,
    UploadWorkflowService,
)


class FakeRepository:
    bucket_name = "test-bucket"

    def __init__(self, save_result=True, events=None):
        self.index_data = {"projects": []}
        self.save_result = save_result
        self.events = events

    def load_projects_index(self):
        return self.index_data

    def save_projects_index(self, _index_data):
        if self.events is not None:
            self.events.append("index")
        return self.save_result


class FakeS3Client:
    def __init__(self, events=None):
        self.uploads = []
        self.deleted = []
        self.objects = {}
        self.heads = []
        self.events = events

    def upload_file(self, local_path, bucket, key, ExtraArgs=None, Callback=None):
        if Callback:
            Callback(os.path.getsize(local_path))
        self.uploads.append((local_path, bucket, key, ExtraArgs))
        content = Path(local_path).read_bytes()
        self.objects[key] = {
            "ContentLength": len(content),
            "Metadata": dict((ExtraArgs or {}).get("Metadata") or {}),
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(content).digest()).decode("ascii"),
            "ChecksumType": "FULL_OBJECT",
        }
        if self.events is not None:
            self.events.append(f"upload:{key}")

    def head_object(self, Bucket, Key, ChecksumMode=None):
        assert Bucket == "test-bucket"
        assert ChecksumMode == "ENABLED"
        self.heads.append(Key)
        if self.events is not None:
            self.events.append(f"head:{Key}")
        return dict(self.objects[Key])

    def delete_objects(self, Bucket, Delete):
        del Bucket
        keys = [entry["Key"] for entry in Delete["Objects"]]
        self.deleted.extend(keys)
        return {"Deleted": Delete["Objects"]}


class FakeGLBService:
    def __init__(self, staging_parent, version="a" * 64):
        self.staging_parent = staging_parent
        self.version = version
        self.calls = []
        self.stage_dir = None

    def prepare_many(self, model_inputs, **kwargs):
        self.calls.append((tuple(model_inputs), kwargs))
        self.stage_dir = Path(kwargs["staging_root"]) / f".glb-upload-{self.staging_parent.name}"
        self.stage_dir.mkdir(parents=True)
        scene = self.stage_dir / "scene.glb"
        manifest = self.stage_dir / "model.json"
        scene.write_bytes(b"glTF")
        manifest.write_text(json.dumps({"entrypoint": "scene.glb"}), encoding="utf-8")
        viewer_root = kwargs["project_viewer_root"]
        s3_root = kwargs["project_s3_prefix"]
        version = self.version
        relative = f"models/halle/versions/{version}"
        model_input = tuple(model_inputs)[0]
        return (
            PreparedModelUpload(
                model_input=model_input,
                name="Halle",
                slug="halle",
                staging_dir=str(self.stage_dir),
                scene_path=str(scene),
                manifest_path=str(manifest),
                original_sha256="f" * 64,
                model_to_project=(1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 10.0, 20.0, 30.0, 1.0),
                bounds_min=(10.0, 20.0, 30.0),
                bounds_max=(11.0, 21.0, 31.0),
                crs_info={
                    "value": "EPSG:25832",
                    "vertical_crs": "EPSG:7837",
                    "vertical_datum": "DHHN2016 height",
                },
                optimization=GLBOptimizationResult(
                    original_sha256="f" * 64,
                    output_sha256="f" * 64,
                    source_size=4,
                    output_size=4,
                ),
                data_version=version,
                index_entry=ModelIndexEntry(
                    id="halle",
                    name="Halle",
                    viewer_path=f"{viewer_root}/{relative}/model.json",
                    s3_path=f"{s3_root}/{relative}",
                    crs="EPSG:25832",
                    vertical_crs="EPSG:7837",
                    vertical_datum="DHHN2016 height",
                ),
            ),
        )


def make_request(copc_path):
    return NewProjectUploadWorkflowRequest(
        source_paths=(str(copc_path),),
        kunde="Kunde",
        projekt="Projekt",
        output_base_dir=str(copc_path.parent),
        crs_info_by_source_path={
            str(copc_path): {
                "value": "EPSG:25832",
                "vertical_crs": "EPSG:7837",
                "vertical_datum": "DHHN2016 height",
            }
        },
        model_inputs=(ModelUploadInput(source_path=str(copc_path.parent / "halle.glb")),),
    )


def test_mixed_upload_uses_multi_shape_orders_model_manifest_last_and_cleans_stage(tmp_path):
    copc = tmp_path / "scan.copc.laz"
    copc.write_bytes(b"copc")
    repository = FakeRepository()
    s3 = FakeS3Client()
    glb = FakeGLBService(tmp_path)
    service = UploadWorkflowService(
        repository=repository,
        s3_client=s3,
        id_factory=lambda: "projectid",
        timestamp_factory=lambda: "2026-08-20T12:00:00",
        glb_service=glb,
    )

    result = service.upload_new_project(make_request(copc))

    assert result.status == "success"
    project = repository.index_data["projects"][0]
    assert project["format"] == "multi"
    assert len(project["pointclouds"]) == 1
    assert project["models"] == [
        {
            "id": "halle",
            "name": "Halle",
            "format": "glb",
            "viewer_path": f"kunde/projectid/projekt/models/halle/versions/{'a' * 64}/model.json",
            "s3_path": f"pointclouds/kunde/projectid/projekt/models/halle/versions/{'a' * 64}",
            "crs": "EPSG:25832",
            "vertical_crs": "EPSG:7837",
            "vertical_datum": "DHHN2016 height",
        }
    ]
    keys = [upload[2] for upload in s3.uploads]
    assert keys[-2:] == [
        project["models"][0]["s3_path"] + "/scene.glb",
        project["models"][0]["s3_path"] + "/model.json",
    ]
    assert s3.uploads[-2][3]["ContentType"] == "model/gltf-binary"
    assert s3.uploads[-1][3]["ContentType"] == "application/json"
    assert [upload[3]["ChecksumAlgorithm"] for upload in s3.uploads[-2:]] == ["SHA256", "SHA256"]
    assert s3.heads == keys[-2:]
    assert glb.calls[0][1]["project_crs_info"]["value"] == "EPSG:25832"
    assert not glb.stage_dir.exists()


def test_mixed_upload_verifies_model_files_before_writing_the_project_index(tmp_path):
    events = []
    copc = tmp_path / "scan.copc.laz"
    copc.write_bytes(b"copc")
    repository = FakeRepository(events=events)
    s3 = FakeS3Client(events=events)
    glb = FakeGLBService(tmp_path)
    service = UploadWorkflowService(
        repository=repository,
        s3_client=s3,
        id_factory=lambda: "projectid",
        glb_service=glb,
    )

    result = service.upload_new_project(make_request(copc))

    assert result.status == "success"
    assert events[-1] == "index"
    assert events[-3:-1] == [f"head:{key}" for key in s3.heads]


def test_mixed_upload_head_verification_failure_rolls_back_current_version_without_touching_old_one(tmp_path):
    class BadHeadS3Client(FakeS3Client):
        def head_object(self, Bucket, Key, ChecksumMode=None):
            head = super().head_object(Bucket, Key, ChecksumMode)
            if Key.endswith("/model.json"):
                head["Metadata"] = {"sha256": "wrong"}
            return head

    copc = tmp_path / "scan.copc.laz"
    copc.write_bytes(b"copc")
    old_key = "pointclouds/old/models/old/versions/old/scene.glb"
    repository = FakeRepository()
    s3 = BadHeadS3Client()
    s3.objects[old_key] = {"ContentLength": 1, "Metadata": {}, "ChecksumSHA256": "old", "ChecksumType": "FULL_OBJECT"}
    glb = FakeGLBService(tmp_path)
    service = UploadWorkflowService(
        repository=repository,
        s3_client=s3,
        id_factory=lambda: "projectid",
        glb_service=glb,
    )

    with pytest.raises(RuntimeError, match="falschen SHA-256"):
        service.upload_new_project(make_request(copc))

    assert repository.index_data == {"projects": []}
    assert s3.deleted == [upload[2] for upload in s3.uploads]
    assert old_key not in s3.deleted
    assert not glb.stage_dir.exists()


def test_mixed_upload_cancellation_during_model_head_verification_rolls_back(tmp_path):
    copc = tmp_path / "scan.copc.laz"
    copc.write_bytes(b"copc")
    repository = FakeRepository()
    s3 = FakeS3Client()
    glb = FakeGLBService(tmp_path)
    service = UploadWorkflowService(
        repository=repository,
        s3_client=s3,
        id_factory=lambda: "projectid",
        glb_service=glb,
    )

    result = service.upload_new_project(
        make_request(copc),
        cancel_requested=lambda: len(s3.heads) >= 1,
    )

    assert result.status == "cancelled"
    assert len(s3.heads) == 1
    assert repository.index_data == {"projects": []}
    assert s3.deleted == [upload[2] for upload in s3.uploads]
    assert not glb.stage_dir.exists()


def test_mixed_upload_index_failure_rolls_back_only_run_ledger_and_cleans_stage(tmp_path):
    copc = tmp_path / "scan.copc.laz"
    copc.write_bytes(b"copc")
    repository = FakeRepository(save_result=False)
    s3 = FakeS3Client()
    glb = FakeGLBService(tmp_path)
    service = UploadWorkflowService(
        repository=repository,
        s3_client=s3,
        id_factory=lambda: "projectid",
        glb_service=glb,
    )

    with pytest.raises(RuntimeError, match="Projekt-Index"):
        service.upload_new_project(make_request(copc))

    assert repository.index_data == {"projects": []}
    assert s3.deleted == [upload[2] for upload in s3.uploads]
    assert not glb.stage_dir.exists()


@pytest.mark.parametrize("invalid_hash", ("", "a" * 63, "g" * 64))
def test_invalid_data_version_fails_before_s3_or_index_change(tmp_path, invalid_hash):
    copc = tmp_path / "scan.copc.laz"
    copc.write_bytes(b"copc")
    repository = FakeRepository()
    s3 = FakeS3Client()
    glb = FakeGLBService(tmp_path, version=invalid_hash)
    service = UploadWorkflowService(
        repository=repository,
        s3_client=s3,
        id_factory=lambda: "projectid",
        glb_service=glb,
    )

    with pytest.raises(ValueError, match="data_version"):
        service.upload_new_project(make_request(copc))

    assert s3.uploads == []
    assert repository.index_data == {"projects": []}
    assert not glb.stage_dir.exists()


def test_mixed_upload_failure_on_model_manifest_rolls_back_pointcloud_and_scene(tmp_path):
    class FailingManifestS3Client(FakeS3Client):
        def upload_file(self, local_path, bucket, key, ExtraArgs=None, Callback=None):
            if key.endswith("/model.json"):
                raise RuntimeError("manifest upload failed")
            super().upload_file(local_path, bucket, key, ExtraArgs=ExtraArgs, Callback=Callback)

    copc = tmp_path / "scan.copc.laz"
    copc.write_bytes(b"copc")
    original_model = tmp_path / "halle.glb"
    original_model.write_bytes(b"user-original")
    repository = FakeRepository()
    s3 = FailingManifestS3Client()
    glb = FakeGLBService(tmp_path)
    service = UploadWorkflowService(
        repository=repository,
        s3_client=s3,
        id_factory=lambda: "projectid",
        glb_service=glb,
    )

    with pytest.raises(RuntimeError, match="manifest upload failed"):
        service.upload_new_project(make_request(copc))

    assert repository.index_data == {"projects": []}
    assert s3.deleted == [upload[2] for upload in s3.uploads]
    assert s3.deleted[-1].endswith("/scene.glb")
    assert original_model.read_bytes() == b"user-original"
    assert not glb.stage_dir.exists()


def test_mixed_upload_cancellation_after_model_scene_rolls_back_and_cleans_stage(tmp_path):
    copc = tmp_path / "scan.copc.laz"
    copc.write_bytes(b"copc")
    original_model = tmp_path / "halle.glb"
    original_model.write_bytes(b"user-original")
    repository = FakeRepository()
    s3 = FakeS3Client()
    glb = FakeGLBService(tmp_path)
    service = UploadWorkflowService(
        repository=repository,
        s3_client=s3,
        id_factory=lambda: "projectid",
        glb_service=glb,
    )

    result = service.upload_new_project(
        make_request(copc),
        cancel_requested=lambda: len(s3.uploads) >= 2,
    )

    assert result.status == "cancelled"
    assert repository.index_data == {"projects": []}
    assert s3.deleted == [upload[2] for upload in s3.uploads]
    assert s3.deleted[-1].endswith("/scene.glb")
    assert original_model.read_bytes() == b"user-original"
    assert not glb.stage_dir.exists()
