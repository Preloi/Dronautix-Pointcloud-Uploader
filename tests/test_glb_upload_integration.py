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

    def __init__(self, save_result=True):
        self.index_data = {"projects": []}
        self.save_result = save_result

    def load_projects_index(self):
        return self.index_data

    def save_projects_index(self, _index_data):
        return self.save_result


class FakeS3Client:
    def __init__(self):
        self.uploads = []
        self.deleted = []

    def upload_file(self, local_path, bucket, key, ExtraArgs=None, Callback=None):
        if Callback:
            Callback(os.path.getsize(local_path))
        self.uploads.append((local_path, bucket, key, ExtraArgs))

    def delete_objects(self, Bucket, Delete):
        del Bucket
        keys = [entry["Key"] for entry in Delete["Objects"]]
        self.deleted.extend(keys)
        return {"Deleted": Delete["Objects"]}


class FakeGLBService:
    def __init__(self, staging_parent):
        self.staging_parent = staging_parent
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
        version = "a" * 64
        relative = "models/halle/version"
        model_input = tuple(model_inputs)[0]
        return (
            PreparedModelUpload(
                model_input=model_input,
                name="Halle",
                slug="halle",
                staging_dir=str(self.stage_dir),
                scene_path=str(scene),
                manifest_path=str(manifest),
                original_sha256=version,
                model_to_project=(1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 10.0, 20.0, 30.0, 1.0),
                bounds_min=(10.0, 20.0, 30.0),
                bounds_max=(11.0, 21.0, 31.0),
                crs_info={"value": "EPSG:25832", "vertical_crs": "DHHN2016"},
                optimization=GLBOptimizationResult(
                    original_sha256=version,
                    output_sha256=version,
                    source_size=4,
                    output_size=4,
                ),
                index_entry=ModelIndexEntry(
                    id="halle",
                    name="Halle",
                    viewer_path=f"{viewer_root}/{relative}/model.json",
                    s3_path=f"{s3_root}/{relative}",
                    crs="EPSG:25832",
                    vertical_crs="DHHN2016",
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
            str(copc_path): {"value": "EPSG:25832", "vertical_crs": "DHHN2016"}
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
            "viewer_path": "kunde/projectid/projekt/models/halle/version/model.json",
            "s3_path": "pointclouds/kunde/projectid/projekt/models/halle/version",
            "crs": "EPSG:25832",
            "vertical_crs": "DHHN2016",
        }
    ]
    keys = [upload[2] for upload in s3.uploads]
    assert keys[-2:] == [
        project["models"][0]["s3_path"] + "/scene.glb",
        project["models"][0]["s3_path"] + "/model.json",
    ]
    assert s3.uploads[-2][3]["ContentType"] == "model/gltf-binary"
    assert s3.uploads[-1][3]["ContentType"] == "application/json"
    assert glb.calls[0][1]["project_crs_info"]["value"] == "EPSG:25832"
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
