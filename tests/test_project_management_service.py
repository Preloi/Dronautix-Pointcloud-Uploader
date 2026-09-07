import base64
import copy
import hashlib
import io
import json
import struct
from pathlib import Path

import pytest

from dronautix_uploader.core.constants import S3_DISABLED_PROJECTS_KEY
from dronautix_uploader.core.contracts import GLBOptimizationResult, ModelIndexEntry, PreparedModelUpload
from dronautix_uploader.core.project_management_service import (
    ProjectManagementService,
    _prepare_potree_name_updates,
    _put_s3_metadata,
)
from dronautix_uploader.core.project_repository import ProjectMetadataWriteUncertainError


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **_kwargs):
        return self.pages


class FakeS3Client:
    def __init__(self, pages=None):
        self.pages = pages or []
        self.copies = []
        self.deleted = []
        self.downloads = []
        self.uploads = []
        self.objects = {}
        self.read_objects = {}
        self.gets = []
        self.puts = []
        self.etags = {}
        self._etag_counter = 0

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
        self.downloads.append((bucket, key, local_path))
        with open(local_path, "wb") as handle:
            handle.write(b"downloaded")
        if Callback is not None:
            Callback(10)

    def upload_file(self, local_path, bucket, key, ExtraArgs=None, Callback=None):
        content = Path(local_path).read_bytes()
        if Callback is not None:
            Callback(len(content))
        self.uploads.append((bucket, key, ExtraArgs))
        self.objects[key] = {
            "ContentLength": len(content),
            "Metadata": dict((ExtraArgs or {}).get("Metadata") or {}),
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(content).digest()).decode("ascii"),
            "ChecksumType": "FULL_OBJECT",
        }

    def head_object(self, Bucket, Key, ChecksumMode=None):
        assert ChecksumMode == "ENABLED"
        return dict(self.objects[Key])

    def get_object(self, Bucket, Key, Range=None):
        self.gets.append((Bucket, Key, Range))
        data = self.read_objects[Key]
        if Range:
            start, end = (int(part) for part in Range.removeprefix("bytes=").split("-", 1))
            data = data[start : end + 1]
        return {"Body": io.BytesIO(data), "ETag": self.etags.setdefault(Key, '"etag-0"')}

    def put_object(self, Bucket, Key, Body, ContentType=None, **kwargs):
        data = Body.read() if hasattr(Body, "read") else Body
        expected_etag = kwargs.get("IfMatch")
        current_etag = self.etags.setdefault(Key, '"etag-0"')
        if expected_etag is not None and expected_etag != current_etag:
            error = RuntimeError("PreconditionFailed")
            error.response = {"Error": {"Code": "PreconditionFailed"}}
            raise error
        self.puts.append((Bucket, Key, data, ContentType, kwargs))
        self.read_objects[Key] = data
        self._etag_counter += 1
        self.etags[Key] = f'"etag-{self._etag_counter}"'
        return {"ETag": self.etags[Key]}


class FakeRepository:
    bucket_name = "test-bucket"

    def __init__(self, index_data, deleted_data=None):
        self.index_data = copy.deepcopy(index_data)
        self.deleted_data = copy.deepcopy(deleted_data or {"deleted_projects": []})
        self.saved_indexes = []
        self.saved_deleted = []

    def load_projects_index(self):
        return self.index_data

    def save_projects_index(self, index_data):
        self.saved_indexes.append(copy.deepcopy(index_data))

    def load_deleted_projects(self):
        return self.deleted_data

    def save_deleted_projects(self, deleted_data):
        self.saved_deleted.append(copy.deepcopy(deleted_data))


class FailingSaveRepository(FakeRepository):
    def save_projects_index(self, index_data):
        super().save_projects_index(index_data)
        raise RuntimeError("index write denied")


def make_service(repository, s3_client=None, project_id="newid", timestamp="2026-06-21T13:00:00"):
    return ProjectManagementService(
        repository=repository,
        s3_client=s3_client or FakeS3Client(),
        id_factory=lambda: project_id,
        timestamp_factory=lambda: timestamp,
    )


class FakeGLBService:
    def __init__(self):
        self.calls = []

    def prepare(
        self,
        model_input,
        *,
        project_crs_info,
        staging_root,
        project_viewer_root,
        project_s3_prefix,
        used_slugs=None,
        on_progress=None,
    ):
        self.calls.append(
            (
                model_input,
                project_crs_info,
                staging_root,
                project_viewer_root,
                project_s3_prefix,
                on_progress,
            )
        )
        name = model_input.name or Path(model_input.source_path).stem
        slug = model_input.slug or name.casefold().replace(" ", "_")
        if used_slugs is not None:
            base_slug = slug
            suffix = 2
            while slug in used_slugs:
                slug = f"{base_slug}_{suffix}"
                suffix += 1
            used_slugs.add(slug)
        staging = Path(staging_root) / f"prepared-{slug}"
        staging.mkdir(parents=True)
        scene = staging / "scene.glb"
        manifest = staging / "model.json"
        scene.write_bytes(b"new-glb")
        manifest.write_text('{"entrypoint":"scene.glb"}', encoding="utf-8")
        version = "e" * 64
        relative = f"models/{slug}/versions/{version}"
        return PreparedModelUpload(
            model_input=model_input,
            name=name,
            slug=slug,
            staging_dir=str(staging),
            scene_path=str(scene),
            manifest_path=str(manifest),
            original_sha256="f" * 64,
            model_to_project=(1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0),
            bounds_min=(0.0, 0.0, 0.0),
            bounds_max=(1.0, 1.0, 1.0),
            crs_info=project_crs_info,
            optimization=GLBOptimizationResult(output_sha256="1" * 64),
            data_version=version,
            index_entry=ModelIndexEntry(
                id=slug,
                name=name,
                viewer_path=f"{project_viewer_root}/{relative}/model.json",
                s3_path=f"{project_s3_prefix}/{relative}",
                crs=project_crs_info["value"],
                vertical_crs=project_crs_info["vertical_crs"],
            ),
        )


def test_list_projects_for_management_returns_active_and_disabled_with_status():
    repository = FakeRepository(
        {
            "projects": [{"id": "active"}],
            S3_DISABLED_PROJECTS_KEY: [{"id": "disabled"}],
        }
    )

    assert make_service(repository).list_projects_for_management() == [
        ({"id": "active"}, False),
        ({"id": "disabled"}, True),
    ]


def test_replace_single_model_from_source_keeps_model_identity_and_cleans_staging(tmp_path, monkeypatch):
    old_prefix = "pointclouds/kunde/project/projekt/models/fassade/versions/old"
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "project",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "viewer_path": "kunde/project/projekt",
                    "s3_path": "pointclouds/kunde/project/projekt",
                    "crs": "EPSG:25833",
                    "vertical_crs": "EPSG:7837",
                    "models": [
                        {
                            "id": "fassade",
                            "name": "Fassade Bestand",
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
    )
    s3_client = FakeS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": f"{old_prefix}/scene.glb", "Size": 10},
                    {"Key": f"{old_prefix}/model.json", "Size": 10},
                ]
            }
        ]
    )
    glb_service = FakeGLBService()
    staging_root = tmp_path / "app-glb-staging"
    monkeypatch.setattr(
        "dronautix_uploader.core.project_management_service.get_glb_upload_staging_root",
        lambda: str(staging_root),
    )
    source = tmp_path / "replacement.glb"
    source.write_bytes(b"user-original")
    service = ProjectManagementService(
        repository=repository,
        s3_client=s3_client,
        timestamp_factory=lambda: "2026-08-20T13:00:00",
        glb_service=glb_service,
    )

    result = service.replace_single_project_model_from_source(
        "project",
        old_prefix,
        str(source),
        model_json_path="C:/input/model.json",
        confirm_spatial_warning=lambda _warning: True,
    )

    prepared_input, project_crs, _run_root, viewer_root, s3_root, _progress = glb_service.calls[0]
    model = repository.index_data["projects"][0]["models"][0]
    assert result.status == "success"
    assert prepared_input.source_path == str(source)
    assert prepared_input.name == "Fassade Bestand"
    assert prepared_input.slug == "fassade"
    assert prepared_input.model_json_path == "C:/input/model.json"
    assert project_crs["value"] == "EPSG:25833"
    assert viewer_root == "kunde/project/projekt"
    assert s3_root == "pointclouds/kunde/project/projekt"
    assert model["id"] == "fassade"
    assert model["name"] == "Fassade Bestand"
    assert model["s3_path"].endswith(f"/models/fassade/versions/{'e' * 64}")
    assert source.read_bytes() == b"user-original"
    assert staging_root.exists()
    assert tuple(staging_root.iterdir()) == ()
    assert s3_client.deleted == [f"{old_prefix}/scene.glb", f"{old_prefix}/model.json"]


def test_add_project_models_from_sources_uses_unique_ids_and_cleans_staging(tmp_path, monkeypatch):
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "project",
                    "viewer_path": "kunde/project/projekt",
                    "s3_path": "pointclouds/kunde/project/projekt",
                    "crs": "EPSG:25833",
                    "vertical_crs": "EPSG:7837",
                    "models": [
                        {
                            "id": "model",
                            "name": "Bestand",
                            "format": "glb",
                            "viewer_path": "kunde/project/projekt/models/model/versions/old/model.json",
                            "s3_path": "pointclouds/kunde/project/projekt/models/model/versions/old",
                            "crs": "EPSG:25833",
                            "vertical_crs": "EPSG:7837",
                        }
                    ],
                }
            ]
        }
    )
    first = tmp_path / "a" / "model.glb"
    second = tmp_path / "b" / "model.glb"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"first-original")
    second.write_bytes(b"second-original")
    s3_client = FakeS3Client()
    glb_service = FakeGLBService()
    staging_root = tmp_path / "app-glb-staging"
    monkeypatch.setattr(
        "dronautix_uploader.core.project_management_service.get_glb_upload_staging_root",
        lambda: str(staging_root),
    )
    service = ProjectManagementService(
        repository=repository,
        s3_client=s3_client,
        timestamp_factory=lambda: "2026-08-21T10:00:00",
        glb_service=glb_service,
    )

    result = service.add_project_models_from_sources(
        "project",
        (str(first), str(second)),
        confirm_spatial_warning=lambda _warning: True,
    )

    models = repository.index_data["projects"][0]["models"]
    assert result.status == "success"
    assert [model["id"] for model in models] == ["model", "model_2", "model_3"]
    assert len(s3_client.uploads) == 4
    assert first.read_bytes() == b"first-original"
    assert second.read_bytes() == b"second-original"
    assert staging_root.exists()
    assert tuple(staging_root.iterdir()) == ()


def test_remove_project_model_lists_and_deletes_only_selected_model_package():
    target_prefix = "pointclouds/kunde/project/models/fassade/versions/old"
    repository = FakeRepository({
        "projects": [{
            "id": "project",
            "projekt": "Projekt",
            "models": [
                {"id": "fassade", "name": "Fassade", "s3_path": target_prefix},
                {"id": "dach", "name": "Dach", "s3_path": "pointclouds/kunde/project/models/dach/versions/old"},
            ],
        }]
    })
    s3_client = FakeS3Client(pages=[{"Contents": [
        {"Key": f"{target_prefix}/scene.glb", "Size": 10},
        {"Key": f"{target_prefix}/model.json", "Size": 10},
        {"Key": f"{target_prefix}-backup/scene.glb", "Size": 10},
    ]}])

    result = make_service(repository, s3_client=s3_client).remove_project_model("project", target_prefix)

    assert result.status == "success"
    assert [model["id"] for model in repository.index_data["projects"][0]["models"]] == ["dach"]
    assert s3_client.deleted == [f"{target_prefix}/scene.glb", f"{target_prefix}/model.json"]


def test_add_project_models_rejects_distant_model_before_s3_upload(tmp_path, monkeypatch):
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "project",
                    "viewer_path": "kunde/project/projekt",
                    "s3_path": "pointclouds/kunde/project/projekt",
                    "crs": "EPSG:25833",
                    "vertical_crs": "EPSG:7837",
                    "pointclouds": [
                        {
                            "name": "Bestand",
                            "format": "potree",
                            "s3_path": "pointclouds/kunde/project/projekt/bestand",
                            "bounds": {"min": [0, 0, 0], "max": [1, 1, 1]},
                            "crs": "EPSG:25833",
                            "vertical_crs": "EPSG:7837",
                        }
                    ],
                }
            ]
        }
    )
    s3_client = FakeS3Client()
    s3_client.read_objects["pointclouds/kunde/project/projekt/bestand/metadata.json"] = json.dumps(
        {"boundingBox": {"min": [1000, 1000, 0], "max": [1010, 1010, 10]}}
    ).encode("utf-8")
    source = tmp_path / "model.glb"
    source.write_bytes(b"user-original")
    staging_root = tmp_path / "app-glb-staging"
    monkeypatch.setattr(
        "dronautix_uploader.core.project_management_service.get_glb_upload_staging_root",
        lambda: str(staging_root),
    )
    confirmations = []
    result = ProjectManagementService(
        repository=repository,
        s3_client=s3_client,
        glb_service=FakeGLBService(),
    ).add_project_models_from_sources(
        "project",
        (str(source),),
        confirm_spatial_warning=lambda warning: confirmations.append(warning) or False,
    )

    assert result.status == "cancelled"
    assert confirmations and "vollständig außerhalb" in confirmations[0]
    assert s3_client.uploads == []
    assert repository.saved_indexes == []
    assert source.read_bytes() == b"user-original"
    assert staging_root.is_dir()
    assert tuple(staging_root.iterdir()) == ()


def test_replace_project_model_rejects_unsupported_pointcloud_format_without_reads(tmp_path, monkeypatch):
    old_prefix = "pointclouds/kunde/project/projekt/models/fassade/versions/old"
    cloud_root = "pointclouds/kunde/project/projekt"
    cloud_path = f"{cloud_root}/source.copc.laz"
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "project",
                    "viewer_path": "kunde/project/projekt",
                    "s3_path": cloud_root,
                    "format": "copc",
                    "crs": "EPSG:25833",
                    "vertical_crs": "EPSG:7837",
                    "models": [{"id": "fassade", "name": "Fassade", "s3_path": old_prefix}],
                }
            ]
        }
    )
    header = bytearray(227)
    header[:4] = b"LASF"
    struct.pack_into("<6d", header, 179, 1010.0, 1000.0, 1010.0, 1000.0, 10.0, 0.0)
    s3_client = FakeS3Client()
    s3_client.read_objects[cloud_path] = bytes(header)
    source = tmp_path / "replacement.glb"
    source.write_bytes(b"user-original")
    staging_root = tmp_path / "app-glb-staging"
    monkeypatch.setattr(
        "dronautix_uploader.core.project_management_service.get_glb_upload_staging_root",
        lambda: str(staging_root),
    )

    with pytest.raises(ValueError, match="nicht unterstuetztes Punktwolkenformat"):
        ProjectManagementService(
            repository=repository,
            s3_client=s3_client,
            glb_service=FakeGLBService(),
        ).replace_single_project_model_from_source("project", old_prefix, str(source))

    assert s3_client.gets == []
    assert s3_client.uploads == []
    assert repository.saved_indexes == []
    assert source.read_bytes() == b"user-original"
    assert not staging_root.exists()


def test_add_project_models_defaults_to_reject_when_existing_bounds_are_unreadable(tmp_path, monkeypatch):
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "project",
                    "viewer_path": "kunde/project/projekt",
                    "s3_path": "pointclouds/kunde/project/projekt",
                    "crs": "EPSG:25833",
                    "vertical_crs": "EPSG:7837",
                }
            ]
        }
    )
    source = tmp_path / "model.glb"
    source.write_bytes(b"user-original")
    staging_root = tmp_path / "app-glb-staging"
    monkeypatch.setattr(
        "dronautix_uploader.core.project_management_service.get_glb_upload_staging_root",
        lambda: str(staging_root),
    )
    result = ProjectManagementService(
        repository=repository,
        s3_client=FakeS3Client(),
        glb_service=FakeGLBService(),
    ).add_project_models_from_sources("project", (str(source),))

    assert result.status == "cancelled"
    assert "nicht sicher ermittelt" in result.warnings[0]
    assert source.read_bytes() == b"user-original"
    assert tuple(staging_root.iterdir()) == ()


def test_add_project_models_repairs_confirmed_partial_crs_from_potree_donor(tmp_path, monkeypatch):
    project_root = "pointclouds/kunde/project/projekt"
    donor_path = f"{project_root}/mellitzgraben"
    target_path = f"{project_root}/terra-hydron"
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "project",
                    "viewer_path": "kunde/project/projekt",
                    "s3_path": project_root,
                    "pointclouds": [
                        {"name": "Mellitzgraben", "format": "potree", "s3_path": donor_path,
                         "crs": "EPSG:31255", "vertical_crs": "EPSG:5778"},
                        {"name": "Terra Hydron", "format": "potree", "s3_path": target_path},
                    ],
                }
            ]
        }
    )
    s3_client = FakeS3Client()
    s3_client.read_objects[f"{donor_path}/metadata.json"] = json.dumps(
        {"projection": "EPSG:31255", "vertical_crs": "EPSG:5778"}
    ).encode("utf-8")
    s3_client.read_objects[f"{target_path}/metadata.json"] = b'{"projection":""}'
    source = tmp_path / "model.glb"
    source.write_bytes(b"user-original")
    staging_root = tmp_path / "app-glb-staging"
    monkeypatch.setattr(
        "dronautix_uploader.core.project_management_service.get_glb_upload_staging_root",
        lambda: str(staging_root),
    )
    confirmations = []
    result = ProjectManagementService(
        repository=repository,
        s3_client=s3_client,
        glb_service=FakeGLBService(),
    ).add_project_models_from_sources(
        "project",
        (str(source),),
        confirm_spatial_warning=lambda _warning: True,
        confirm_crs_repair=lambda warning: confirmations.append(warning) or True,
    )

    repaired = repository.index_data["projects"][0]
    target = repaired["pointclouds"][1]
    assert result.status == "success"
    assert confirmations == [
        "CRS-Reparatur erforderlich: Mellitzgraben (EPSG:31255, Vertikal EPSG:5778) -> "
        "Projektindex, Terra Hydron. Nur fehlende CRS-Felder werden ergänzt und im Projektverlauf dokumentiert."
    ]
    assert repaired["crs"] == target["crs"] == "EPSG:31255"
    assert repaired["vertical_crs"] == target["vertical_crs"] == "EPSG:5778"
    assert json.loads(s3_client.read_objects[f"{target_path}/metadata.json"])["vertical_crs"] == "EPSG:5778"
    assert s3_client.puts and s3_client.puts[0][1] == f"{target_path}/metadata.json"


def test_add_project_models_rejects_partial_crs_repair_by_default_without_writes(tmp_path, monkeypatch):
    project_root = "pointclouds/kunde/project/projekt"
    donor_path = f"{project_root}/donor"
    target_path = f"{project_root}/target"
    repository = FakeRepository(
        {"projects": [{
            "id": "project", "viewer_path": "kunde/project/projekt", "s3_path": project_root,
            "pointclouds": [
                {"name": "Donor-Cloud", "format": "potree", "s3_path": donor_path,
                 "crs": "EPSG:31255", "vertical_crs": "EPSG:5778"},
                {"name": "Ziel-Cloud", "format": "potree", "s3_path": target_path},
            ],
        }]}
    )
    s3_client = FakeS3Client()
    s3_client.read_objects[f"{donor_path}/metadata.json"] = b'{"projection":"EPSG:31255","vertical_crs":"EPSG:5778"}'
    s3_client.read_objects[f"{target_path}/metadata.json"] = b'{}'
    source = tmp_path / "model.glb"
    source.write_bytes(b"user-original")
    staging_root = tmp_path / "app-glb-staging"
    monkeypatch.setattr(
        "dronautix_uploader.core.project_management_service.get_glb_upload_staging_root",
        lambda: str(staging_root),
    )
    glb_service = FakeGLBService()
    result = ProjectManagementService(repository=repository, s3_client=s3_client, glb_service=glb_service).add_project_models_from_sources(
        "project", (str(source),), confirm_spatial_warning=lambda _warning: True
    )

    assert result.status == "cancelled"
    assert "Donor-Cloud (EPSG:31255, Vertikal EPSG:5778) -> Projektindex, Ziel-Cloud" in result.warnings[0]
    assert glb_service.calls == []
    assert s3_client.uploads == [] and s3_client.puts == [] and repository.saved_indexes == []


def test_manual_crs_repair_backfills_unknown_project_and_potree_metadata_only_after_confirmation():
    cloud_path = "pointclouds/kunde/project/projekt/cloud"
    repository = FakeRepository(
        {"projects": [{
            "id": "project", "viewer_path": "kunde/project/projekt", "s3_path": "pointclouds/kunde/project/projekt",
            "pointclouds": [{"name": "Cloud", "format": "potree", "s3_path": cloud_path}],
        }]}
    )
    s3_client = FakeS3Client()
    original = b'{"name":"unchanged"}'
    s3_client.read_objects[f"{cloud_path}/metadata.json"] = original
    service = ProjectManagementService(repository=repository, s3_client=s3_client)

    cancelled = service.repair_project_crs_metadata(
        "project", {"value": "EPSG:31255", "vertical_crs": "EPSG:5778"}
    )
    assert cancelled.status == "cancelled"
    assert s3_client.read_objects[f"{cloud_path}/metadata.json"] == original
    repaired = service.repair_project_crs_metadata(
        "project",
        {"value": "EPSG:31255", "vertical_crs": "EPSG:5778"},
        confirm_repair=lambda message: "EPSG:31255" in message and "EPSG:5778" in message,
    )

    project = repository.index_data["projects"][0]
    metadata = json.loads(s3_client.read_objects[f"{cloud_path}/metadata.json"])
    assert original != s3_client.read_objects[f"{cloud_path}/metadata.json"]
    assert repaired.status == "success"
    assert project["crs"] == project["pointclouds"][0]["crs"] == "EPSG:31255"
    assert metadata["name"] == "unchanged"
    assert metadata["vertical_crs"] == "EPSG:5778"


def test_failed_model_upload_rolls_back_confirmed_crs_metadata_repair(tmp_path, monkeypatch):
    root = "pointclouds/kunde/project/projekt"
    donor_path, target_path = f"{root}/donor", f"{root}/target"
    repository = FailingSaveRepository(
        {"projects": [{
            "id": "project", "viewer_path": "kunde/project/projekt", "s3_path": root,
            "pointclouds": [
                {"name": "Donor", "format": "potree", "s3_path": donor_path,
                 "crs": "EPSG:31255", "vertical_crs": "EPSG:5778"},
                {"name": "Target", "format": "potree", "s3_path": target_path},
            ],
        }]}
    )
    s3_client = FakeS3Client()
    s3_client.read_objects[f"{donor_path}/metadata.json"] = b'{"projection":"EPSG:31255","vertical_crs":"EPSG:5778"}'
    original_target_metadata = b'{"name":"target"}'
    s3_client.read_objects[f"{target_path}/metadata.json"] = original_target_metadata
    source = tmp_path / "model.glb"
    source.write_bytes(b"user-original")
    staging_root = tmp_path / "app-glb-staging"
    monkeypatch.setattr(
        "dronautix_uploader.core.project_management_service.get_glb_upload_staging_root",
        lambda: str(staging_root),
    )

    with pytest.raises(RuntimeError, match="index write denied"):
        ProjectManagementService(repository=repository, s3_client=s3_client, glb_service=FakeGLBService()).add_project_models_from_sources(
            "project",
            (str(source),),
            confirm_spatial_warning=lambda _warning: True,
            confirm_crs_repair=lambda _warning: True,
        )

    project = repository.index_data["projects"][0]
    assert "crs" not in project and "crs" not in project["pointclouds"][1]
    assert s3_client.read_objects[f"{target_path}/metadata.json"] == original_target_metadata
    assert s3_client.deleted


def test_conflicting_index_and_potree_crs_blocks_model_add_without_writes(tmp_path, monkeypatch):
    cloud_path = "pointclouds/kunde/project/projekt/cloud"
    repository = FakeRepository(
        {"projects": [{
            "id": "project", "viewer_path": "kunde/project/projekt", "s3_path": "pointclouds/kunde/project/projekt",
            "pointclouds": [{"name": "Cloud", "format": "potree", "s3_path": cloud_path,
                             "crs": "EPSG:25833", "vertical_crs": "EPSG:7837"}],
        }]}
    )
    s3_client = FakeS3Client()
    s3_client.read_objects[f"{cloud_path}/metadata.json"] = b'{"projection":"EPSG:31255","vertical_crs":"EPSG:5778"}'
    source = tmp_path / "model.glb"
    source.write_bytes(b"user-original")
    staging_root = tmp_path / "app-glb-staging"
    monkeypatch.setattr(
        "dronautix_uploader.core.project_management_service.get_glb_upload_staging_root",
        lambda: str(staging_root),
    )

    with pytest.raises(ValueError, match="widerspr"):
        ProjectManagementService(repository=repository, s3_client=s3_client, glb_service=FakeGLBService()).add_project_models_from_sources(
            "project", (str(source),)
        )

    assert s3_client.uploads == [] and s3_client.puts == [] and repository.saved_indexes == []
    assert source.read_bytes() == b"user-original"


@pytest.mark.parametrize("payload", [b"{", RuntimeError("AccessDenied")])
def test_unreadable_or_invalid_potree_metadata_blocks_crs_repair_without_writes(tmp_path, monkeypatch, payload):
    cloud_path = "pointclouds/kunde/project/projekt/target"
    repository = FakeRepository(
        {"projects": [{
            "id": "project", "viewer_path": "kunde/project/projekt", "s3_path": "pointclouds/kunde/project/projekt",
            "crs": "EPSG:31255", "vertical_crs": "EPSG:5778",
            "pointclouds": [{"name": "Target", "format": "potree", "s3_path": cloud_path}],
        }]}
    )

    class BrokenReadS3(FakeS3Client):
        def get_object(self, Bucket, Key, Range=None):
            if Key == f"{cloud_path}/metadata.json":
                if isinstance(payload, Exception):
                    raise payload
                return {"Body": io.BytesIO(payload)}
            return super().get_object(Bucket, Key, Range)

    s3_client = BrokenReadS3()
    source = tmp_path / "model.glb"
    source.write_bytes(b"user-original")
    monkeypatch.setattr(
        "dronautix_uploader.core.project_management_service.get_glb_upload_staging_root",
        lambda: str(tmp_path / "app-glb-staging"),
    )
    with pytest.raises((RuntimeError, ValueError), match="Potree-Metadaten"):
        ProjectManagementService(repository=repository, s3_client=s3_client, glb_service=FakeGLBService()).add_project_models_from_sources(
            "project", (str(source),)
        )

    assert s3_client.uploads == [] and s3_client.puts == [] and repository.saved_indexes == []


def test_metadata_put_merges_default_content_type_with_existing_headers():
    s3_client = FakeS3Client()

    _put_s3_metadata(
        s3_client,
        "test-bucket",
        "cloud/metadata.json",
        b"{}",
        {"CacheControl": "max-age=60", "Metadata": {"owner": "test"}},
        expected_etag='"etag-0"',
    )

    _bucket, _key, _body, content_type, kwargs = s3_client.puts[0]
    assert content_type == "application/json"
    assert kwargs == {"CacheControl": "max-age=60", "Metadata": {"owner": "test"}, "IfMatch": '"etag-0"'}


def test_crs_repair_uses_javascript_mime_for_cloud_js_without_existing_header():
    cloud_path = "pointclouds/kunde/project/projekt/cloud"
    repository = FakeRepository(
        {"projects": [{
            "id": "project", "viewer_path": "kunde/project/projekt", "s3_path": "pointclouds/kunde/project/projekt",
            "pointclouds": [{"name": "Cloud", "format": "potree", "s3_path": cloud_path}],
        }]}
    )
    s3_client = FakeS3Client()
    s3_client.read_objects[f"{cloud_path}/cloud.js"] = b"cloud.js = {};"

    result = ProjectManagementService(repository=repository, s3_client=s3_client).repair_project_crs_metadata(
        "project",
        {"value": "EPSG:31255", "vertical_crs": "EPSG:5778"},
        confirm_repair=lambda _message: True,
    )

    assert result.status == "success"
    assert len(s3_client.puts) == 1
    assert s3_client.puts[0][1] == f"{cloud_path}/cloud.js"
    assert s3_client.puts[0][3] == "application/javascript"


def test_uncertain_index_commit_does_not_roll_back_repaired_potree_crs():
    cloud_path = "pointclouds/kunde/project/projekt/cloud"
    metadata_key = f"{cloud_path}/metadata.json"

    class UncertainRepository(FakeRepository):
        def save_projects_index(self, index_data):
            self.saved_indexes.append(copy.deepcopy(index_data))
            raise ProjectMetadataWriteUncertainError("projects_index.json")

    repository = UncertainRepository(
        {"projects": [{
            "id": "project", "viewer_path": "kunde/project/projekt", "s3_path": "pointclouds/kunde/project/projekt",
            "pointclouds": [{"name": "Cloud", "format": "potree", "s3_path": cloud_path}],
        }]}
    )
    s3_client = FakeS3Client()
    s3_client.read_objects[metadata_key] = b'{"name":"Cloud"}'

    with pytest.raises(ProjectMetadataWriteUncertainError):
        ProjectManagementService(repository=repository, s3_client=s3_client).repair_project_crs_metadata(
            "project",
            {"value": "EPSG:31255", "vertical_crs": "EPSG:5778"},
            confirm_repair=lambda _message: True,
        )

    repaired = json.loads(s3_client.read_objects[metadata_key])
    assert repaired["crs"] == "EPSG:31255"
    assert repaired["vertical_crs"] == "EPSG:5778"
    assert len(s3_client.puts) == 1


def test_failed_crs_s3_restore_is_reported_with_original_error_as_cause(tmp_path, monkeypatch):
    root = "pointclouds/kunde/project/projekt"
    donor_path, target_path = f"{root}/donor", f"{root}/target"
    original_target_metadata = b'{"name":"target"}'
    repository = FailingSaveRepository(
        {"projects": [{
            "id": "project", "viewer_path": "kunde/project/projekt", "s3_path": root,
            "pointclouds": [
                {"name": "Donor", "format": "potree", "s3_path": donor_path,
                 "crs": "EPSG:31255", "vertical_crs": "EPSG:5778"},
                {"name": "Target", "format": "potree", "s3_path": target_path},
            ],
        }]}
    )

    class RestoreFailingS3(FakeS3Client):
        def put_object(self, Bucket, Key, Body, ContentType=None, **kwargs):
            data = Body.read() if hasattr(Body, "read") else Body
            if Key == f"{target_path}/metadata.json" and data == original_target_metadata and self.puts:
                raise RuntimeError("restore denied")
            return super().put_object(Bucket, Key, data, ContentType=ContentType, **kwargs)

    s3_client = RestoreFailingS3()
    s3_client.read_objects[f"{donor_path}/metadata.json"] = b'{"projection":"EPSG:31255","vertical_crs":"EPSG:5778"}'
    s3_client.read_objects[f"{target_path}/metadata.json"] = original_target_metadata
    source = tmp_path / "model.glb"
    source.write_bytes(b"user-original")
    monkeypatch.setattr(
        "dronautix_uploader.core.project_management_service.get_glb_upload_staging_root",
        lambda: str(tmp_path / "app-glb-staging"),
    )

    with pytest.raises(RuntimeError, match="CRS-Reparatur-Rollback unvollständig") as error:
        ProjectManagementService(repository=repository, s3_client=s3_client, glb_service=FakeGLBService()).add_project_models_from_sources(
            "project",
            (str(source),),
            confirm_spatial_warning=lambda _warning: True,
            confirm_crs_repair=lambda _warning: True,
        )

    assert isinstance(error.value.__cause__, RuntimeError)
    assert "index write denied" in str(error.value.__cause__)
    assert "S3-Metadaten" in str(error.value)


@pytest.mark.parametrize("failure", [RuntimeError("AccessDenied"), b"not-a-las-header"])
def test_unsupported_pointcloud_format_blocks_sibling_crs_repair_without_writes(tmp_path, monkeypatch, failure):
    root = "pointclouds/kunde/project/projekt"
    copc_path = f"{root}/target/source.copc.laz"
    repository = FakeRepository(
        {"projects": [{
            "id": "project", "viewer_path": "kunde/project/projekt", "s3_path": root,
            "crs": "EPSG:31255", "vertical_crs": "EPSG:5778",
            "pointclouds": [
                {"name": "Donor", "format": "potree", "s3_path": f"{root}/donor",
                 "crs": "EPSG:31255", "vertical_crs": "EPSG:5778"},
                {"name": "Target", "format": "copc", "s3_path": copc_path},
            ],
        }]}
    )

    class BrokenCopcS3(FakeS3Client):
        def get_object(self, Bucket, Key, Range=None):
            if Key == copc_path:
                if isinstance(failure, Exception):
                    raise failure
                return {"Body": io.BytesIO(failure)}
            return super().get_object(Bucket, Key, Range)

    s3_client = BrokenCopcS3()
    source = tmp_path / "model.glb"
    source.write_bytes(b"user-original")
    monkeypatch.setattr(
        "dronautix_uploader.core.project_management_service.get_glb_upload_staging_root",
        lambda: str(tmp_path / "app-glb-staging"),
    )
    with pytest.raises(ValueError, match="nicht unterstuetztes Punktwolkenformat"):
        ProjectManagementService(repository=repository, s3_client=s3_client, glb_service=FakeGLBService()).add_project_models_from_sources(
            "project", (str(source),)
        )

    assert s3_client.uploads == [] and s3_client.puts == [] and repository.saved_indexes == []


def test_index_only_crs_repair_does_not_retry_stale_index_after_save_failure(tmp_path, monkeypatch):
    root = "pointclouds/kunde/project/projekt"
    target_path = f"{root}/target"
    repository = FailingSaveRepository(
        {"projects": [{
            "id": "project", "viewer_path": "kunde/project/projekt", "s3_path": root,
            "crs": "EPSG:31255", "vertical_crs": "EPSG:5778",
            "pointclouds": [{"name": "Target", "format": "potree", "s3_path": target_path}],
        }]}
    )
    s3_client = FakeS3Client()
    s3_client.read_objects[f"{target_path}/metadata.json"] = b'{"projection":"EPSG:31255","vertical_crs":"EPSG:5778"}'
    source = tmp_path / "model.glb"
    source.write_bytes(b"user-original")
    monkeypatch.setattr(
        "dronautix_uploader.core.project_management_service.get_glb_upload_staging_root",
        lambda: str(tmp_path / "app-glb-staging"),
    )
    with pytest.raises(RuntimeError, match="index write denied"):
        ProjectManagementService(repository=repository, s3_client=s3_client, glb_service=FakeGLBService()).add_project_models_from_sources(
            "project",
            (str(source),),
            confirm_spatial_warning=lambda _warning: True,
            confirm_crs_repair=lambda _warning: True,
        )

    assert len(repository.saved_indexes) == 1
    assert s3_client.puts == []
    assert "crs" not in repository.index_data["projects"][0]["pointclouds"][0]


def test_duplicate_cloud_names_repair_only_the_planned_child_index(tmp_path, monkeypatch):
    root = "pointclouds/kunde/project/projekt"
    first_path, second_path, third_path = f"{root}/donor", f"{root}/target-1", f"{root}/target-2"
    complete_target = {"name": "Target", "format": "potree", "s3_path": third_path,
                       "crs": "EPSG:31255", "vertical_crs": "EPSG:5778"}
    repository = FakeRepository(
        {"projects": [{
            "id": "project", "viewer_path": "kunde/project/projekt", "s3_path": root,
            "pointclouds": [
                {"name": "Donor", "format": "potree", "s3_path": first_path,
                 "crs": "EPSG:31255", "vertical_crs": "EPSG:5778"},
                {"name": "Target", "format": "potree", "s3_path": second_path},
                copy.deepcopy(complete_target),
            ],
        }]}
    )
    s3_client = FakeS3Client()
    s3_client.read_objects[f"{second_path}/metadata.json"] = b"{}"
    untouched_third_metadata = b'{"name":"third"}'
    s3_client.read_objects[f"{third_path}/metadata.json"] = untouched_third_metadata
    source = tmp_path / "model.glb"
    source.write_bytes(b"user-original")
    monkeypatch.setattr(
        "dronautix_uploader.core.project_management_service.get_glb_upload_staging_root",
        lambda: str(tmp_path / "app-glb-staging"),
    )
    result = ProjectManagementService(repository=repository, s3_client=s3_client, glb_service=FakeGLBService()).add_project_models_from_sources(
        "project",
        (str(source),),
        confirm_spatial_warning=lambda _warning: True,
        confirm_crs_repair=lambda _warning: True,
    )

    clouds = repository.index_data["projects"][0]["pointclouds"]
    assert result.status == "success"
    assert clouds[1]["crs"] == "EPSG:31255"
    assert clouds[2] == complete_target
    assert s3_client.read_objects[f"{third_path}/metadata.json"] == untouched_third_metadata


def test_rename_project_updates_active_project_without_changing_paths():
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "active",
                    "kunde": "Alt",
                    "projekt": "Altprojekt",
                    "viewer_path": "alt/active/altprojekt",
                    "s3_path": "pointclouds/alt/active/altprojekt",
                    "pointclouds": [{"name": "A"}, {"name": "B"}],
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )

    result = make_service(repository).rename_project("active", "Neu", "Neuprojekt", ("Cloud A", "Cloud B"))
    renamed = repository.index_data["projects"][0]

    assert result.status == "success"
    assert result.project_id == "active"
    assert result.message == "Projekt wurde umbenannt."
    assert renamed["kunde"] == "Neu"
    assert renamed["projekt"] == "Neuprojekt"
    assert renamed["viewer_path"] == "alt/active/altprojekt"
    assert renamed["s3_path"] == "pointclouds/alt/active/altprojekt"
    assert [cloud["name"] for cloud in renamed["pointclouds"]] == ["Cloud A", "Cloud B"]
    assert renamed["history"] == [
        {
            "timestamp": "2026-06-21T13:00:00",
            "message": "Kunde von 'Alt' zu 'Neu' geändert; Projekt von 'Altprojekt' zu 'Neuprojekt' umbenannt; "
            "Punktwolke von 'A' zu 'Cloud A' umbenannt; Punktwolke von 'B' zu 'Cloud B' umbenannt",
        }
    ]
    assert repository.index_data["projects"][0] == renamed
    assert repository.saved_indexes[-1]["projects"][0] == renamed


def test_rename_project_stores_legacy_cloud_name_and_updates_existing_potree_metadata():
    metadata_key = "pointclouds/alt/legacy/altprojekt/metadata.json"
    s3_client = FakeS3Client()
    s3_client.read_objects[metadata_key] = b'{"name":"Altprojekt","points":123}'
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "legacy",
                    "kunde": "Alt",
                    "projekt": "Altprojekt",
                    "format": "potree",
                    "viewer_path": "alt/legacy/altprojekt",
                    "s3_path": "pointclouds/alt/legacy/altprojekt",
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )

    result = make_service(repository, s3_client=s3_client).rename_project(
        "legacy",
        "Neu",
        "Neuprojekt",
        ("Neue Punktwolke",),
    )
    renamed = repository.index_data["projects"][0]

    assert result.status == "success"
    assert renamed["name"] == "Neue Punktwolke"
    assert renamed["format"] == "potree"
    assert renamed["viewer_path"] == "alt/legacy/altprojekt"
    assert renamed["s3_path"] == "pointclouds/alt/legacy/altprojekt"
    assert "pointclouds" not in renamed
    assert json.loads(s3_client.read_objects[metadata_key])["name"] == "Neue Punktwolke"
    assert s3_client.puts[0][4]["CacheControl"] == "no-cache"


def test_rename_project_rolls_back_potree_metadata_when_index_save_fails():
    metadata_key = "pointclouds/kunde/legacy/projekt/metadata.json"
    original_metadata = b'{"name":"Alt","points":123}'
    s3_client = FakeS3Client()
    s3_client.read_objects[metadata_key] = original_metadata

    class FailingRepository(FakeRepository):
        def save_projects_index(self, index_data):
            self.saved_indexes.append(copy.deepcopy(index_data))
            return False

    repository = FailingRepository(
        {
            "projects": [
                {
                    "id": "legacy",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "name": "Alt",
                    "format": "potree",
                    "viewer_path": "kunde/legacy/projekt",
                    "s3_path": "pointclouds/kunde/legacy/projekt",
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )

    with pytest.raises(RuntimeError, match="Projekt-Index"):
        make_service(repository, s3_client=s3_client).rename_project(
            "legacy",
            "Kunde",
            "Projekt",
            ("Neu",),
        )

    assert repository.index_data["projects"][0]["name"] == "Alt"
    assert s3_client.read_objects[metadata_key] == original_metadata
    assert len(s3_client.puts) == 2


def test_concurrent_potree_rename_rejects_stale_metadata_snapshot():
    metadata_key = "pointclouds/kunde/project/cloud/metadata.json"
    project = {
        "projekt": "Alt",
        "pointclouds": [{"name": "Alt", "format": "potree", "s3_path": "pointclouds/kunde/project/cloud"}],
    }
    s3_client = FakeS3Client()
    s3_client.read_objects[metadata_key] = b'{"name":"Alt","points":123}'
    update_a = _prepare_potree_name_updates(s3_client, "test-bucket", project, ("Name A",))[0]
    update_b = _prepare_potree_name_updates(s3_client, "test-bucket", project, ("Name B",))[0]

    _put_s3_metadata(
        s3_client,
        "test-bucket",
        metadata_key,
        update_a["updated"],
        update_a["updated_headers"],
        expected_etag=update_a["original_etag"],
    )
    with pytest.raises(RuntimeError, match="Metadatenkonflikt"):
        _put_s3_metadata(
            s3_client,
            "test-bucket",
            metadata_key,
            update_b["updated"],
            update_b["updated_headers"],
            expected_etag=update_b["original_etag"],
        )

    assert json.loads(s3_client.read_objects[metadata_key])["name"] == "Name A"


def test_uncertain_index_commit_does_not_roll_back_renamed_potree_metadata():
    metadata_key = "pointclouds/kunde/legacy/projekt/metadata.json"
    s3_client = FakeS3Client()
    s3_client.read_objects[metadata_key] = b'{"name":"Alt","points":123}'

    class UncertainRepository(FakeRepository):
        def save_projects_index(self, index_data):
            self.saved_indexes.append(copy.deepcopy(index_data))
            raise ProjectMetadataWriteUncertainError("projects_index.json")

    repository = UncertainRepository(
        {
            "projects": [{
                "id": "legacy", "kunde": "Kunde", "projekt": "Projekt", "name": "Alt", "format": "potree",
                "viewer_path": "kunde/legacy/projekt", "s3_path": "pointclouds/kunde/legacy/projekt",
            }],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )

    with pytest.raises(ProjectMetadataWriteUncertainError):
        make_service(repository, s3_client=s3_client).rename_project(
            "legacy", "Kunde", "Projekt", ("Neu",)
        )

    assert json.loads(s3_client.read_objects[metadata_key])["name"] == "Neu"
    assert len(s3_client.puts) == 1


def test_uncertain_second_metadata_write_rolls_back_prior_known_rename():
    first_path = "pointclouds/kunde/project/first"
    second_path = "pointclouds/kunde/project/second"
    first_key = f"{first_path}/metadata.json"
    second_key = f"{second_path}/metadata.json"

    class SecondWriteUncertainS3(FakeS3Client):
        def put_object(self, Bucket, Key, Body, ContentType=None, **kwargs):
            if Key == second_key:
                raise RuntimeError("AccessDenied")
            return super().put_object(Bucket, Key, Body, ContentType=ContentType, **kwargs)

    s3_client = SecondWriteUncertainS3()
    original_first = b'{"name":"First"}'
    s3_client.read_objects[first_key] = original_first
    s3_client.read_objects[second_key] = b'{"name":"Second"}'
    repository = FakeRepository(
        {"projects": [{
            "id": "project", "kunde": "Kunde", "projekt": "Projekt",
            "viewer_path": "kunde/project/projekt", "s3_path": "pointclouds/kunde/project",
            "pointclouds": [
                {"name": "First", "format": "potree", "s3_path": first_path},
                {"name": "Second", "format": "potree", "s3_path": second_path},
            ],
        }]}
    )

    with pytest.raises(ProjectMetadataWriteUncertainError):
        make_service(repository, s3_client=s3_client).rename_project(
            "project", "Kunde", "Projekt", ("First New", "Second New")
        )

    assert s3_client.read_objects[first_key] == original_first
    assert json.loads(s3_client.read_objects[second_key])["name"] == "Second"
    assert repository.saved_indexes == []


def test_rename_project_updates_disabled_project_and_preserves_disabled_status():
    repository = FakeRepository(
        {
            "projects": [{"id": "active", "kunde": "Aktiv"}],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "id": "disabled",
                    "kunde": "Alt",
                    "projekt": "Altprojekt",
                    "disabled_at": "2026-06-20T12:00:00",
                    "viewer_path": "alt/disabled/altprojekt",
                    "s3_path": "pointclouds/alt/disabled/altprojekt",
                }
            ],
        }
    )

    result = make_service(repository).rename_project("disabled", "Neu", "Neuprojekt")
    renamed = repository.index_data[S3_DISABLED_PROJECTS_KEY][0]

    assert result.status == "success"
    assert result.project_id == "disabled"
    assert repository.index_data["projects"] == [{"id": "active", "kunde": "Aktiv"}]
    assert renamed["disabled_at"] == "2026-06-20T12:00:00"
    assert renamed["viewer_path"] == "alt/disabled/altprojekt"
    assert renamed["s3_path"] == "pointclouds/alt/disabled/altprojekt"


def test_delete_disabled_project_removes_disabled_and_writes_deleted_entry():
    s3_client = FakeS3Client(
        pages=[{"Contents": [{"Key": "pointclouds/old/cloud.js", "Size": 10}]}]
    )
    repository = FakeRepository(
        {
            "projects": [{"id": "active"}],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "id": "disabled",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "s3_path": "pointclouds/old",
                    "link": "https://viewer/?id=disabled",
                }
            ],
        },
        deleted_data={"deleted_projects": [{"id": "other", "s3_path": "pointclouds/other"}]},
    )

    result = make_service(repository, s3_client=s3_client).delete_project("disabled")

    assert result.status == "success"
    assert result.deleted_keys == ("pointclouds/old/cloud.js",)
    assert repository.index_data[S3_DISABLED_PROJECTS_KEY] == []
    assert repository.deleted_data["deleted_projects"][0]["id"] == "disabled"
    assert repository.deleted_data["deleted_projects"][0]["deleted_at"] == "2026-06-21T13:00:00"
    assert repository.saved_indexes[-1][S3_DISABLED_PROJECTS_KEY] == []
    assert repository.saved_deleted[-1]["deleted_projects"][0]["id"] == "disabled"


def test_duplicate_from_multi_project_remaps_paths_and_inserts_active_project():
    s3_client = FakeS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": "pointclouds/alt/sourceid/altprojekt/cloud_a/cloud.js", "Size": 10},
                    {"Key": "pointclouds/alt/sourceid/altprojekt/cloud_a/metadata.json", "Size": 10},
                ]
            }
        ]
    )
    repository = FakeRepository(
        {
            "projects": [{"id": "existing"}],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "id": "sourceid",
                    "kunde": "Alt",
                    "projekt": "Altprojekt",
                    "format": "multi",
                    "link": "https://viewer/?id=sourceid",
                    "viewer_path": "alt/sourceid/altprojekt",
                    "s3_path": "pointclouds/alt/sourceid/altprojekt",
                    "disabled_at": "2026-06-20T12:00:00",
                    "pointclouds": [
                        {
                            "name": "Cloud A",
                            "viewer_path": "alt/sourceid/altprojekt/cloud_a",
                            "s3_path": "pointclouds/alt/sourceid/altprojekt/cloud_a",
                        }
                    ],
                }
            ],
        }
    )

    result = make_service(repository, s3_client=s3_client).duplicate_project("sourceid", "Neu Kunde", "Neu Projekt")

    assert result.status == "success"
    assert result.project_id == "newid"
    assert result.uploaded_keys == (
        "pointclouds/neu_kunde/newid/neu_projekt/cloud_a/cloud.js",
        "pointclouds/neu_kunde/newid/neu_projekt/cloud_a/metadata.json",
    )
    duplicated = repository.index_data["projects"][0]
    assert duplicated["id"] == "newid"
    assert duplicated["viewer_path"] == "neu_kunde/newid/neu_projekt"
    assert duplicated["s3_path"] == "pointclouds/neu_kunde/newid/neu_projekt"
    assert duplicated["pointclouds"][0]["viewer_path"] == "neu_kunde/newid/neu_projekt/cloud_a"
    assert duplicated["pointclouds"][0]["s3_path"] == "pointclouds/neu_kunde/newid/neu_projekt/cloud_a"
    assert "disabled_at" not in duplicated
    assert repository.index_data[S3_DISABLED_PROJECTS_KEY][0]["id"] == "sourceid"
    assert repository.saved_indexes[-1]["projects"][0]["id"] == "newid"


def test_download_project_uses_existing_project_s3_path_without_saving_index(tmp_path):
    s3_client = FakeS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": "pointclouds/kunde/id/projekt/cloud.js", "Size": 10},
                    {"Key": "pointclouds/kunde/id/projekt/metadata.json", "Size": 20},
                ]
            }
        ]
    )
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "id",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "s3_path": "pointclouds/kunde/id/projekt",
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )

    result = make_service(repository, s3_client=s3_client).download_project("id", str(tmp_path))

    assert result.status == "success"
    assert result.download_dir == str(tmp_path / "kunde_projekt_id")
    assert result.downloaded_files == (
        str(tmp_path / "kunde_projekt_id" / "cloud.js"),
        str(tmp_path / "kunde_projekt_id" / "metadata.json"),
    )
    assert s3_client.downloads[0][0] == "test-bucket"
    assert repository.saved_indexes == []


def test_download_project_returns_cancelled_result_with_partial_files(tmp_path):
    s3_client = FakeS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": "pointclouds/kunde/id/projekt/cloud.js", "Size": 10},
                    {"Key": "pointclouds/kunde/id/projekt/metadata.json", "Size": 20},
                ]
            }
        ]
    )
    repository = FakeRepository(
        {
            "projects": [
                {
                    "id": "id",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "s3_path": "pointclouds/kunde/id/projekt",
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )
    cancel_checks = {"count": 0}

    def cancel_after_first_file():
        cancel_checks["count"] += 1
        return cancel_checks["count"] >= 3

    result = make_service(repository, s3_client=s3_client).download_project(
        "id",
        str(tmp_path),
        cancel_requested=cancel_after_first_file,
    )

    assert result.status == "cancelled"
    assert result.message == "Download wurde abgebrochen."
    assert result.download_dir == str(tmp_path / "kunde_projekt_id")
    assert result.downloaded_files == (str(tmp_path / "kunde_projekt_id" / "cloud.js"),)


def test_set_project_link_state_moves_active_project_to_disabled_and_saves_index():
    repository = FakeRepository(
        {
            "projects": [{"id": "active", "kunde": "Kunde", "projekt": "Projekt"}],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )

    result = make_service(repository).set_project_link_state("active", True)

    assert result.status == "success"
    assert result.message == "Projekt-Link wurde deaktiviert."
    assert repository.index_data["projects"] == []
    assert repository.index_data[S3_DISABLED_PROJECTS_KEY] == [
        {
            "id": "active",
            "kunde": "Kunde",
            "projekt": "Projekt",
            "disabled_at": "2026-06-21T13:00:00",
            "history": [
                {
                    "timestamp": "2026-06-21T13:00:00",
                    "message": "Projekt wurde inaktiv geschaltet.",
                }
            ],
        }
    ]
    assert repository.saved_indexes[-1][S3_DISABLED_PROJECTS_KEY][0]["id"] == "active"


def test_set_project_link_state_moves_disabled_project_to_active_and_removes_disabled_at():
    repository = FakeRepository(
        {
            "projects": [],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "id": "disabled",
                    "kunde": "Kunde",
                    "projekt": "Projekt",
                    "disabled_at": "old",
                }
            ],
        }
    )

    result = make_service(repository).set_project_link_state("disabled", False)

    assert result.status == "success"
    assert result.message == "Projekt-Link wurde aktiviert."
    assert repository.index_data["projects"] == [
        {
            "id": "disabled",
            "kunde": "Kunde",
            "projekt": "Projekt",
            "history": [
                {
                    "timestamp": "2026-06-21T13:00:00",
                    "message": "Projekt wurde aktiv geschaltet.",
                }
            ],
        }
    ]
    assert repository.index_data[S3_DISABLED_PROJECTS_KEY] == []


def test_set_project_link_state_noops_when_status_already_matches():
    repository = FakeRepository(
        {
            "projects": [{"id": "active"}],
            S3_DISABLED_PROJECTS_KEY: [],
        }
    )

    result = make_service(repository).set_project_link_state("active", False)

    assert result.status == "success"
    assert result.message == "Projekt-Link ist bereits aktiv."
    assert repository.saved_indexes == []


def test_cleanup_pending_project_blocks_activation_and_replacement_without_s3_writes(tmp_path):
    project = {
        "id": "pending", "s3_path": "pointclouds/pending", "viewer_path": "pending",
        "cleanup_pending": True,
    }
    repository = FakeRepository({"projects": [], S3_DISABLED_PROJECTS_KEY: [project]})
    s3_client = FakeS3Client()
    service = make_service(repository, s3_client=s3_client)

    with pytest.raises(RuntimeError, match="Loeschversuch"):
        service.set_project_link_state("pending", False)
    with pytest.raises(RuntimeError, match="Loeschversuch"):
        service.replace_project_pointclouds("pending", ())

    assert repository.saved_indexes == []
    assert s3_client.uploads == [] and s3_client.deleted == []


@pytest.mark.parametrize("method,args", [
    ("rename_project", ("missing", "Kunde", "Projekt")),
    ("delete_project", ("missing",)),
    ("duplicate_project", ("missing", "Kunde", "Projekt")),
    ("download_project", ("missing", "C:/Downloads")),
    ("set_project_link_state", ("missing", True)),
])
def test_unknown_project_id_raises_value_error(method, args):
    repository = FakeRepository({"projects": [{"id": "known"}], S3_DISABLED_PROJECTS_KEY: []})
    service = make_service(repository)

    with pytest.raises(ValueError, match="missing"):
        getattr(service, method)(*args)


@pytest.fixture
def model_cloud_change(tmp_path):
    from dronautix_uploader.core.contracts import PointcloudSource
    from dronautix_uploader.core.project_operations import prepare_cloud_uploads

    root = "kunde/project/projekt"
    crs = {"value": "EPSG:25832", "vertical_crs": "EPSG:7837"}
    model = {"id": "mesh", "name": "Bestand", "format": "glb", "crs": crs["value"], "vertical_crs": crs["vertical_crs"]}
    project = {
        "id": "project", "format": "potree", "viewer_path": root,
        "s3_path": f"pointclouds/{root}", "models": [model], **crs,
    }
    repository = FakeRepository({"projects": [project], S3_DISABLED_PROJECTS_KEY: []})
    client = FakeS3Client()
    service = make_service(repository, client)
    source = tmp_path / "replacement"
    source.mkdir()
    (source / "octree.bin").write_bytes(b"points")
    (source / "hierarchy.bin").write_bytes(struct.pack("<BBIQQ", 1, 0, 1, 0, 6))

    def run(operation, cloud_crs, *, file_crs=None):
        document = {
            "version": "2.0", "encoding": "BROTLI", "points": 1,
            "offset": [0, 0, 0], "scale": [0.001, 0.001, 0.001],
            "hierarchy": {"firstChunkSize": 22, "stepSize": 4, "depth": 0},
            "attributes": [{"name": "position", "type": "int32", "numElements": 3, "elementSize": 4, "size": 12}],
            "boundingBox": {"min": [0, 0, 0], "max": [1, 1, 1]},
        }
        references = cloud_crs if file_crs is None else file_crs
        if references:
            document.update({"projection": references.get("value", ""), "vertical_crs": references.get("vertical_crs", "")})
        (source / "metadata.json").write_text(json.dumps(document), encoding="utf-8")
        target = f"pointclouds/{root}"
        if operation == "add_sources":
            return service.add_project_pointclouds_from_sources("project", (str(source),), crs_info_by_source_path={str(source): cloud_crs})
        if operation == "replace_all_sources":
            return service.replace_project_pointclouds_from_sources("project", (str(source),), crs_info_by_source_path={str(source): cloud_crs})
        if operation == "replace_single_source":
            return service.replace_single_project_pointcloud_from_source("project", target, str(source), crs_info=cloud_crs)
        prepared = prepare_cloud_uploads(
            (PointcloudSource(str(source), name="Ersatz", slug="ersatz", input_format="potree", crs_info=cloud_crs),), root, target,
        )
        if operation == "add":
            return service.add_project_pointclouds("project", prepared)
        if operation == "replace_all":
            return service.replace_project_pointclouds("project", prepared)
        return service.replace_single_project_pointcloud("project", target, prepared[0])

    return repository, client, run


@pytest.mark.parametrize("operation", ["add", "replace_all", "replace_single", "add_sources", "replace_all_sources", "replace_single_source"])
@pytest.mark.parametrize("cloud_crs", [
    {"value": "EPSG:25833", "vertical_crs": "EPSG:7837"},
    {"value": "EPSG:25832", "vertical_crs": "EPSG:5773"},
    {"value": "EPSG:25832"},
])
def test_pointcloud_change_rejects_model_crs_conflict_before_any_write(model_cloud_change, operation, cloud_crs):
    repository, client, run = model_cloud_change
    before = copy.deepcopy(repository.index_data)
    with pytest.raises(ValueError, match="CRS|Höhenbezug"):
        run(operation, cloud_crs)
    assert repository.index_data == before
    assert repository.saved_indexes == []
    assert client.uploads == client.puts == client.deleted == []


@pytest.mark.parametrize("operation", ["add", "replace_all", "replace_single", "add_sources", "replace_all_sources", "replace_single_source"])
def test_pointcloud_change_keeps_compatible_models(model_cloud_change, operation):
    repository, client, run = model_cloud_change
    models = copy.deepcopy(repository.index_data["projects"][0]["models"])
    result = run(operation, {"value": "urn:ogc:def:crs:EPSG::25832", "vertical_crs": "EPSG:7837"})
    assert result.status == "success"
    assert repository.index_data["projects"][0]["models"] == models
    assert len(repository.saved_indexes) == 1
    assert client.uploads


@pytest.mark.parametrize("operation", ["add", "replace_all", "replace_single"])
def test_pointcloud_change_checks_uploaded_metadata_not_just_index(model_cloud_change, operation):
    repository, client, run = model_cloud_change
    with pytest.raises(ValueError, match="widerspricht"):
        run(operation, {"value": "EPSG:25832", "vertical_crs": "EPSG:7837"},
            file_crs={"value": "EPSG:25833", "vertical_crs": "EPSG:7837"})
    assert repository.saved_indexes == []
    assert client.uploads == client.puts == client.deleted == []


def test_pointcloud_change_rejects_unknown_model_reference(model_cloud_change):
    repository, client, run = model_cloud_change
    repository.index_data["projects"][0]["models"][0].pop("vertical_crs")
    with pytest.raises(ValueError, match="Modell.*fehlt"):
        run("replace_single_source", {"value": "EPSG:25832", "vertical_crs": "EPSG:7837"})
    assert repository.saved_indexes == []
    assert client.uploads == client.puts == client.deleted == []
