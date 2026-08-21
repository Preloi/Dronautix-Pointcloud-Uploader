import copy
import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from dronautix_uploader.core.constants import DOMAIN_URL
from dronautix_uploader.core.contracts import ModelUploadInput, OperationCancelledError, ProgressEvent
from dronautix_uploader.core.local_conversion_service import build_local_output_dir
from dronautix_uploader.core.upload_workflow_service import (
    GLB_UPLOAD_STAGING_ROOT_NAME,
    NewProjectUploadWorkflowRequest,
    UploadWorkflowService,
    build_model_pointcloud_spatial_warning,
    cleanup_prepared_glb_staging_dirs,
)


class FakeRepository:
    bucket_name = "test-bucket"

    def __init__(self, index_data=None, save_result=True):
        self.index_data = copy.deepcopy(index_data or {"projects": []})
        self.save_result = save_result
        self.loaded_indexes = 0
        self.saved_indexes = []

    def load_projects_index(self):
        self.loaded_indexes += 1
        return self.index_data

    def save_projects_index(self, index_data):
        self.saved_indexes.append(copy.deepcopy(index_data))
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
        keys = [entry["Key"] for entry in Delete["Objects"]]
        self.deleted.extend(keys)
        return {"Deleted": Delete["Objects"]}


def make_service(repository, s3_client=None, project_id="projectid", timestamp="2026-06-21T12:00:00"):
    return UploadWorkflowService(
        repository=repository,
        s3_client=s3_client or FakeS3Client(),
        id_factory=lambda: project_id,
        timestamp_factory=lambda: timestamp,
    )


def make_converter_runner(events=None, calls=None):
    def fake_runner(source_file, converter_path, output_dir, on_progress):
        if calls is not None:
            calls.append((source_file, converter_path, output_dir, on_progress))
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "cloud.js"), "w", encoding="utf-8") as file:
            file.write("cloud.js = {};")
        with open(os.path.join(output_dir, "metadata.json"), "w", encoding="utf-8") as file:
            file.write("{}")
        if on_progress:
            event = ProgressEvent(kind="log", message="converter progress")
            if events is not None:
                events.append(event)
            on_progress(event)

    return fake_runner


def _write_potree_bounds(directory: Path, minimum, maximum):
    directory.mkdir()
    (directory / "metadata.json").write_text(
        json.dumps({"boundingBox": {"min": list(minimum), "max": list(maximum)}}),
        encoding="utf-8",
    )


def _write_las_bounds(path: Path, minimum, maximum):
    header = bytearray(227)
    header[:4] = b"LASF"
    struct.pack_into(
        "<6d",
        header,
        179,
        maximum[0],
        minimum[0],
        maximum[1],
        minimum[1],
        maximum[2],
        minimum[2],
    )
    path.write_bytes(header)


def test_spatial_warning_lists_far_model_and_distance_for_potree_bounds(tmp_path):
    pointcloud = tmp_path / "pointcloud"
    _write_potree_bounds(pointcloud, (286445.7865, 5395595.6958, 422.9587), (288171.3629, 5397321.2722, 2148.5351))
    sources = (SimpleNamespace(source_path=str(pointcloud)),)
    models = (
        SimpleNamespace(
            name="WA-F-P1_SK219",
            slug="wa_f_p1_sk219",
            bounds_min=(281535.918222, 5401959.547388, 424.993697),
            bounds_max=(281572.504959, 5401993.634319, 452.936716),
        ),
    )

    warning = build_model_pointcloud_spatial_warning(sources, models)

    assert "WA-F-P1_SK219" in warning
    assert "6,7 km" in warning
    assert "vollständig außerhalb" in warning


def test_spatial_warning_accepts_model_overlapping_copc_las_header_bounds(tmp_path):
    pointcloud = tmp_path / "cloud.copc.laz"
    _write_las_bounds(pointcloud, (100.0, 200.0, 300.0), (200.0, 300.0, 400.0))
    sources = (SimpleNamespace(source_path=str(pointcloud)),)
    models = (SimpleNamespace(name="Halle", slug="halle", bounds_min=(150.0, 250.0, 310.0), bounds_max=(160.0, 260.0, 320.0)),)

    assert build_model_pointcloud_spatial_warning(sources, models) == ""


def test_upload_declines_far_model_before_first_s3_upload(tmp_path, monkeypatch):
    from dronautix_uploader.core import upload_workflow_service

    pointcloud = tmp_path / "pointcloud"
    _write_potree_bounds(pointcloud, (100.0, 200.0, 300.0), (200.0, 300.0, 400.0))
    model = tmp_path / "halle.glb"
    model.write_bytes(b"original")
    app_temp_root = tmp_path / "temp"
    monkeypatch.setattr(upload_workflow_service.tempfile, "gettempdir", lambda: str(app_temp_root))

    class FarModelService:
        def prepare_many(self, _model_inputs, **kwargs):
            stage = Path(kwargs["staging_root"]) / ".glb-upload-far"
            stage.mkdir()
            return (
                SimpleNamespace(
                    staging_dir=str(stage),
                    name="Halle",
                    slug="halle",
                    bounds_min=(5000.0, 6000.0, 300.0),
                    bounds_max=(5010.0, 6010.0, 310.0),
                ),
            )

    repository = FakeRepository()
    s3_client = FakeS3Client()
    confirmations = []
    events = []
    result = UploadWorkflowService(
        repository=repository,
        s3_client=s3_client,
        glb_service=FarModelService(),
    ).upload_new_project(
        NewProjectUploadWorkflowRequest(
            source_paths=(str(pointcloud),),
            kunde="Kunde",
            projekt="Projekt",
            crs_info_by_source_path={str(pointcloud): {"value": "EPSG:25833", "vertical_crs": "EPSG:7837"}},
            model_inputs=(ModelUploadInput(source_path=str(model)),),
        ),
        on_progress=events.append,
        confirm_spatial_warning=lambda message: confirmations.append(message) or False,
    )

    assert result.status == "cancelled"
    assert confirmations and "Halle" in confirmations[0]
    assert any(event.kind == "warning" and "außerhalb" in event.message for event in events)
    assert s3_client.uploads == []
    assert repository.loaded_indexes == 0
    assert not list((app_temp_root / GLB_UPLOAD_STAGING_ROOT_NAME).glob(".glb-upload-run-*"))


def test_glb_staging_cleanup_retries_and_never_removes_a_source_outside_its_staging_root(tmp_path, monkeypatch):
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    stage_dir = staging_root / ".glb-upload-work"
    stage_dir.mkdir()
    (stage_dir / "scene.glb").write_bytes(b"staged")
    source_dir = tmp_path / ".glb-upload-source"
    source_dir.mkdir()
    (source_dir / "original.glb").write_bytes(b"original")
    events = []

    from dronautix_uploader.core import upload_workflow_service

    real_rmtree = upload_workflow_service.shutil.rmtree
    calls = []

    def locked_once(path):
        calls.append(path)
        if len(calls) == 1:
            raise OSError("locked")
        real_rmtree(path)

    monkeypatch.setattr(upload_workflow_service.shutil, "rmtree", locked_once)
    cleanup_prepared_glb_staging_dirs(
        (SimpleNamespace(staging_dir=str(stage_dir)), SimpleNamespace(staging_dir=str(source_dir))),
        staging_root=str(staging_root),
        on_progress=events.append,
    )

    assert len(calls) == 2
    assert not stage_dir.exists()
    assert source_dir.exists()
    assert any(event.kind == "log" and "Temporäre Dateien entfernt" in event.message for event in events)
    assert any(event.kind == "warning" and "Unsicheren Temp-Pfad" in event.message for event in events)


def test_glb_staging_uses_app_temp_root_instead_of_output_directory(tmp_path, monkeypatch):
    from dronautix_uploader.core import upload_workflow_service

    class CapturingGlbService:
        staging_root = None

        def prepare_many(self, model_inputs, **kwargs):
            self.staging_root = kwargs["staging_root"]
            return ()

    app_temp_root = tmp_path / "temp"
    output_dir = tmp_path / "user-output"
    source = tmp_path / "source.copc.laz"
    source.write_bytes(b"copc")
    model = tmp_path / "model.glb"
    model.write_bytes(b"original")
    glb_service = CapturingGlbService()
    monkeypatch.setattr(upload_workflow_service.tempfile, "gettempdir", lambda: str(app_temp_root))

    result = UploadWorkflowService(
        repository=FakeRepository(),
        s3_client=FakeS3Client(),
        glb_service=glb_service,
    ).upload_new_project(
        NewProjectUploadWorkflowRequest(
            source_paths=(str(source),),
            kunde="Kunde",
            projekt="Projekt",
            output_base_dir=str(output_dir),
            model_inputs=(ModelUploadInput(source_path=str(model)),),
        ),
        converter_runner=make_converter_runner(),
    )

    assert result.status == "success"
    assert os.path.dirname(glb_service.staging_root) == str(app_temp_root / GLB_UPLOAD_STAGING_ROOT_NAME)
    assert os.path.basename(glb_service.staging_root).startswith(".glb-upload-run-")
    assert not os.path.exists(glb_service.staging_root)
    assert not output_dir.exists()
    assert model.read_bytes() == b"original"


def test_glb_cancellation_cleans_a_leaked_stage_and_preserves_user_original(tmp_path, monkeypatch):
    from dronautix_uploader.core import upload_workflow_service

    class CancellingGlbService:
        stage_dir = None

        def prepare_many(self, _model_inputs, **kwargs):
            self.stage_dir = Path(kwargs["staging_root"]) / ".glb-upload-leaked"
            self.stage_dir.mkdir()
            (self.stage_dir / "partial.glb").write_bytes(b"partial")
            raise OperationCancelledError()

    app_temp_root = tmp_path / "temp"
    source = tmp_path / "source.copc.laz"
    source.write_bytes(b"copc")
    model = tmp_path / "model.glb"
    model.write_bytes(b"original")
    glb_service = CancellingGlbService()
    monkeypatch.setattr(upload_workflow_service.tempfile, "gettempdir", lambda: str(app_temp_root))

    result = UploadWorkflowService(
        repository=FakeRepository(),
        s3_client=FakeS3Client(),
        glb_service=glb_service,
    ).upload_new_project(
        NewProjectUploadWorkflowRequest(
            source_paths=(str(source),),
            kunde="Kunde",
            projekt="Projekt",
            model_inputs=(ModelUploadInput(source_path=str(model)),),
        ),
        converter_runner=make_converter_runner(),
    )

    assert result.status == "cancelled"
    assert glb_service.stage_dir is not None
    assert not glb_service.stage_dir.exists()
    assert not list((app_temp_root / GLB_UPLOAD_STAGING_ROOT_NAME).glob(".glb-upload-run-*"))
    assert model.read_bytes() == b"original"


def test_upload_new_project_single_copc_uploads_without_conversion_and_writes_index(tmp_path):
    source = tmp_path / "source.copc.laz"
    source.write_bytes(b"copc")
    repository = FakeRepository({"projects": [{"id": "old"}]})
    s3_client = FakeS3Client()

    result = make_service(repository, s3_client=s3_client).upload_new_project(
        NewProjectUploadWorkflowRequest(
            source_paths=(str(source),),
            kunde="Kunde",
            projekt="Projekt",
        ),
        converter_runner=make_converter_runner(),
    )

    assert result.status == "success"
    assert result.project_id == "projectid"
    assert result.project_url == f"{DOMAIN_URL}?id=projectid"
    assert [upload[2] for upload in s3_client.uploads] == [
        "pointclouds/kunde/projectid/projekt/source.copc.laz"
    ]
    assert repository.loaded_indexes == 1
    assert repository.index_data["projects"][0]["id"] == "projectid"
    assert repository.index_data["projects"][0]["format"] == "copc"
    assert repository.index_data["projects"][0]["link"] == f"{DOMAIN_URL}?id=projectid"
    assert repository.index_data["projects"][0]["viewer_path"] == "kunde/projectid/projekt/source.copc.laz"
    assert repository.index_data["projects"][0]["s3_path"] == "pointclouds/kunde/projectid/projekt"
    assert repository.saved_indexes[-1]["projects"][0]["id"] == "projectid"
    assert repository.saved_indexes[-1]["projects"][0]["s3_path"] == "pointclouds/kunde/projectid/projekt"


def test_upload_new_project_raw_las_is_prepared_by_converter_and_uploaded_as_potree(tmp_path):
    raw = tmp_path / "Scan.las"
    raw.write_bytes(b"las")
    converter = tmp_path / "PotreeConverter.exe"
    converter.write_bytes(b"exe")
    output_base = tmp_path / "converted"
    calls = []
    repository = FakeRepository()
    s3_client = FakeS3Client()

    result = make_service(repository, s3_client=s3_client).upload_new_project(
        NewProjectUploadWorkflowRequest(
            source_paths=(str(raw),),
            kunde="Raw Kunde",
            projekt="Raw Projekt",
            converter_path=str(converter),
            output_base_dir=str(output_base),
            overwrite=True,
        ),
        converter_runner=make_converter_runner(calls=calls),
    )

    expected_output_dir = build_local_output_dir(str(raw), str(output_base))
    assert calls == [(str(raw), str(converter), expected_output_dir, None)]
    assert result.status == "success"
    assert repository.index_data["projects"][0]["format"] == "potree"
    assert repository.index_data["projects"][0]["s3_path"] == "pointclouds/raw_kunde/projectid/raw_projekt"
    assert [upload[2] for upload in s3_client.uploads] == [
        "pointclouds/raw_kunde/projectid/raw_projekt/cloud.js",
        "pointclouds/raw_kunde/projectid/raw_projekt/metadata.json",
    ]


def test_upload_new_project_multi_mix_builds_multi_metadata_and_pointcloud_list(tmp_path):
    copc = tmp_path / "Scan.copc.laz"
    copc.write_bytes(b"copc")
    potree_dir = tmp_path / "Potree Cloud"
    potree_dir.mkdir()
    (potree_dir / "cloud.js").write_text("cloud.js = {};", encoding="utf-8")
    raw = tmp_path / "Raw.laz"
    raw.write_bytes(b"laz")
    converter = tmp_path / "PotreeConverter.exe"
    converter.write_bytes(b"exe")
    output_base = tmp_path / "converted"
    repository = FakeRepository()
    s3_client = FakeS3Client()

    result = make_service(repository, s3_client=s3_client).upload_new_project(
        NewProjectUploadWorkflowRequest(
            source_paths=(str(copc), str(potree_dir), str(raw)),
            kunde="Mix Kunde",
            projekt="Mix Projekt",
            converter_path=str(converter),
            output_base_dir=str(output_base),
            overwrite=True,
        ),
        converter_runner=make_converter_runner(),
    )

    project = repository.index_data["projects"][0]
    assert result.status == "success"
    assert project["format"] == "multi"
    assert project["viewer_path"] == "mix_kunde/projectid/mix_projekt"
    assert project["s3_path"] == "pointclouds/mix_kunde/projectid/mix_projekt"
    assert project["pointcloud_count"] == 3
    assert [cloud["format"] for cloud in project["pointclouds"]] == ["copc", "potree", "potree"]
    assert [cloud["name"] for cloud in project["pointclouds"]] == ["Scan", "Potree Cloud", "Raw"]
    assert [upload[2] for upload in s3_client.uploads] == [
        "pointclouds/mix_kunde/projectid/mix_projekt/scan/source.copc.laz",
        "pointclouds/mix_kunde/projectid/mix_projekt/potree_cloud/cloud.js",
        "pointclouds/mix_kunde/projectid/mix_projekt/raw/cloud.js",
        "pointclouds/mix_kunde/projectid/mix_projekt/raw/metadata.json",
    ]


def test_upload_new_project_rolls_back_uploaded_keys_when_index_save_fails(tmp_path):
    source = tmp_path / "source.copc.laz"
    source.write_bytes(b"copc")
    original_index = {"projects": [{"id": "old", "projekt": "Old"}]}
    repository = FakeRepository(original_index, save_result=False)
    s3_client = FakeS3Client()

    with pytest.raises(RuntimeError, match="Projekt-Index"):
        make_service(repository, s3_client=s3_client).upload_new_project(
            NewProjectUploadWorkflowRequest(
                source_paths=(str(source),),
                kunde="Kunde",
                projekt="Projekt",
            )
        )

    assert repository.index_data == original_index
    assert s3_client.deleted == ["pointclouds/kunde/projectid/projekt/source.copc.laz"]


def test_upload_new_project_forwards_preparation_and_upload_progress_events(tmp_path):
    raw = tmp_path / "Scan.laz"
    raw.write_bytes(b"laz")
    converter = tmp_path / "PotreeConverter.exe"
    converter.write_bytes(b"exe")
    events = []

    make_service(FakeRepository()).upload_new_project(
        NewProjectUploadWorkflowRequest(
            source_paths=(str(raw),),
            kunde="Kunde",
            projekt="Projekt",
            converter_path=str(converter),
            output_base_dir=str(tmp_path / "converted"),
            overwrite=True,
        ),
        on_progress=events.append,
        converter_runner=make_converter_runner(),
    )

    assert any(event.kind == "step" and event.message == "Bereite Punktwolke vor..." for event in events)
    assert any(event.kind == "log" and event.message == "converter progress" for event in events)
    assert any(event.kind == "log" and event.message.startswith("[UPLOAD]") for event in events)
    assert {event.phase for event in events} >= {"preparation", "conversion", "upload", "index"}
    assert events[-1] == ProgressEvent(
        kind="progress",
        message="Projekt wurde gespeichert.",
        percent=1.0,
        phase="index",
    )


def test_upload_new_project_applies_crs_info_per_source_path_to_metadata(tmp_path):
    copc = tmp_path / "Scan.copc.laz"
    copc.write_bytes(b"copc")
    potree_dir = tmp_path / "Potree Cloud"
    potree_dir.mkdir()
    (potree_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (potree_dir / "cloud.js").write_text("cloud.js = {};", encoding="utf-8")
    repository = FakeRepository()

    make_service(repository).upload_new_project(
        NewProjectUploadWorkflowRequest(
            source_paths=(str(copc), str(potree_dir)),
            kunde="Kunde",
            projekt="Projekt",
            crs_info_by_source_path={
                str(copc): {"value": "EPSG:25832", "horizontal": "EPSG:25832"},
                str(potree_dir): {"value": "EPSG:4326", "horizontal": "EPSG:4326"},
            },
        )
    )

    pointclouds = repository.index_data["projects"][0]["pointclouds"]
    assert pointclouds[0]["crs"] == "EPSG:25832"
    assert pointclouds[0]["crs_info"] == {"value": "EPSG:25832", "horizontal": "EPSG:25832"}
    assert pointclouds[1]["crs"] == "EPSG:4326"
    assert pointclouds[1]["crs_info"] == {"value": "EPSG:4326", "horizontal": "EPSG:4326"}
    assert json.loads((potree_dir / "metadata.json").read_text(encoding="utf-8"))["projection"] == "EPSG:4326"
    cloudjs = json.loads((potree_dir / "cloud.js").read_text(encoding="utf-8").removeprefix("cloud.js = ").rstrip(";"))
    assert cloudjs["projection"] == "EPSG:4326"


def test_upload_new_project_cancel_during_preparation_returns_cancelled_without_uploads(tmp_path):
    source = tmp_path / "source.copc.laz"
    source.write_bytes(b"copc")
    repository = FakeRepository()
    s3_client = FakeS3Client()

    result = make_service(repository, s3_client=s3_client).upload_new_project(
        NewProjectUploadWorkflowRequest(
            source_paths=(str(source),),
            kunde="Kunde",
            projekt="Projekt",
        ),
        converter_runner=make_converter_runner(),
        cancel_requested=lambda: True,
    )

    assert result.status == "cancelled"
    assert s3_client.uploads == []
    assert repository.saved_indexes == []


def test_upload_new_project_cancel_during_upload_rolls_back_uploaded_keys(tmp_path):
    first = tmp_path / "first.copc.laz"
    first.write_bytes(b"copc-1")
    second = tmp_path / "second.copc.laz"
    second.write_bytes(b"copc-2")
    repository = FakeRepository()
    s3_client = FakeS3Client()

    def cancel_after_first_upload():
        return len(s3_client.uploads) >= 1

    result = make_service(repository, s3_client=s3_client).upload_new_project(
        NewProjectUploadWorkflowRequest(
            source_paths=(str(first), str(second)),
            kunde="Kunde",
            projekt="Projekt",
        ),
        converter_runner=make_converter_runner(),
        cancel_requested=cancel_after_first_upload,
    )

    assert result.status == "cancelled"
    assert "entfernt" in result.message
    assert s3_client.deleted == [upload[2] for upload in s3_client.uploads]
    assert repository.saved_indexes == []
