"""Schema-2 lifecycle through the real ProjectMetadataRepository and a local fake S3.

Every assertion reads the JSON body actually stored in the fake bucket, so the
save boundary (compaction, ETag conditions, rebase) is part of what is tested.
"""

import base64
import copy
import dataclasses
import hashlib
import io
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dronautix_uploader.core.constants import S3_DELETED_JSON, S3_DISABLED_PROJECTS_KEY, S3_INDEX_JSON
from dronautix_uploader.core.contracts import (
    GLBOptimizationResult,
    OperationCancelledError,
    PointcloudSource,
    PreparedModelUpload,
)
from dronautix_uploader.core.glb_optimization_service import GLBOptimizationService, build_model_index_entry
from dronautix_uploader.core.naming_service import build_project_paths
from dronautix_uploader.core.output_snapshots import _write_potree_fixture
from dronautix_uploader.core.project_index_schema import UnsupportedIndexSchemaError
from dronautix_uploader.core.project_management_service import ProjectManagementService
from dronautix_uploader.core.project_operations import (
    PreparedProjectUpload,
    add_project_pointclouds,
    prepare_cloud_uploads,
    upload_new_project,
)
from dronautix_uploader.core.project_repository import ProjectMetadataConflictError, ProjectMetadataRepository
from dronautix_uploader.core.upload_workflow_service import NewProjectUploadWorkflowRequest, UploadWorkflowService

TIMESTAMP = "2026-10-07T12:00:00"
WKT_25832 = (
    'PROJCS["ETRS89 / UTM zone 32N",GEOGCS["ETRS89",AUTHORITY["EPSG","4258"]],'
    'AUTHORITY["EPSG","25832"]]'
)
CRS_INFO = {
    "value": "EPSG:25832",
    "name": "ETRS89 / UTM zone 32N",
    "wkt": WKT_25832,
    "vertical_epsg": "EPSG:7837",
    "vertical_name": "DHHN2016 height",
    "source": "manual",
}
FLAT_CRS = {
    "crs": "EPSG:25832",
    "crs_name": "ETRS89 / UTM zone 32N",
    "vertical_crs": "EPSG:7837",
    "vertical_name": "DHHN2016 height",
}
REMOVED_DUPLICATES = ("projection", "epsg", "vertical_epsg", "vertical_projection", "vertical_datum", "crs_info")
IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
BOUNDS_DOCUMENT = json.dumps({"boundingBox": {"min": [0, 0, 0], "max": [1, 1, 1]}})


@pytest.fixture(autouse=True)
def _private_staging_root(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dronautix_uploader.core.project_management_service.get_glb_upload_staging_root",
        lambda: str(tmp_path / "app-staging"),
    )


class NoSuchKey(Exception):
    pass


class FakeS3:
    """In-memory bucket with ETags, conditional writes and checksum heads."""

    exceptions = SimpleNamespace(NoSuchKey=NoSuchKey)

    def __init__(self):
        self.objects = {}
        self.etags = {}
        self.heads = {}
        self.events = []
        self.before_index_put = None
        self.unreadable_marker = ""
        self.fail_upload_suffix = ""
        self.delete_errors = False
        self._etag_counter = itertools.count(1)

    def store(self, key, data):
        self.objects[key] = data if isinstance(data, bytes) else str(data).encode("utf-8")
        self.etags[key] = f'"etag-{next(self._etag_counter)}"'

    def get_object(self, Bucket, Key, Range=None):
        self.events.append(("get", Key))
        if self.unreadable_marker and self.unreadable_marker in Key:
            raise RuntimeError("simulierter Lesefehler")
        if Key not in self.objects:
            raise NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key]), "ETag": self.etags[Key]}

    def put_object(self, Bucket, Key, Body, IfMatch=None, IfNoneMatch=None, **_headers):
        if Key == S3_INDEX_JSON and self.before_index_put is not None:
            hook, self.before_index_put = self.before_index_put, None
            hook(self)
        if (IfMatch is not None and IfMatch != self.etags.get(Key)) or (IfNoneMatch == "*" and Key in self.objects):
            error = RuntimeError("PreconditionFailed")
            error.response = {"Error": {"Code": "PreconditionFailed"}}
            raise error
        self.store(Key, Body)
        self.events.append(("put", Key))
        return {"ETag": self.etags[Key]}

    def upload_file(self, local_path, bucket, key, ExtraArgs=None, Callback=None):
        if self.fail_upload_suffix and key.endswith(self.fail_upload_suffix):
            raise RuntimeError("simulierter Uploadfehler")
        data = Path(local_path).read_bytes()
        self.store(key, data)
        self.heads[key] = {
            "ContentLength": len(data),
            "Metadata": dict((ExtraArgs or {}).get("Metadata") or {}),
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(data).digest()).decode("ascii"),
            "ChecksumType": "FULL_OBJECT",
        }
        self.events.append(("upload", key))
        if Callback:
            Callback(len(data))

    def head_object(self, Bucket, Key, ChecksumMode=None):
        return dict(self.heads[Key])

    def download_file(self, bucket, key, local_path, Callback=None):
        Path(local_path).write_bytes(self.objects[key])
        if Callback:
            Callback(len(self.objects[key]))

    def get_paginator(self, name):
        assert name == "list_objects_v2"

        def paginate(**kwargs):
            prefix = kwargs.get("Prefix", "")
            return [
                {
                    "Contents": [
                        {"Key": key, "Size": len(data)}
                        for key, data in sorted(self.objects.items())
                        if key.startswith(prefix)
                    ]
                }
            ]

        return SimpleNamespace(paginate=paginate)

    def copy_object(self, Bucket, CopySource, Key, **_headers):
        self.store(Key, self.objects[CopySource["Key"]])
        self.events.append(("copy", Key))

    def delete_objects(self, Bucket, Delete):
        keys = [item["Key"] for item in Delete["Objects"]]
        if self.delete_errors:
            return {"Deleted": [], "Errors": [{"Key": keys[0], "Code": "AccessDenied", "Message": "verweigert"}]}
        for key in keys:
            self.objects.pop(key, None)
        self.events.append(("delete", tuple(keys)))
        return {"Deleted": [{"Key": key} for key in keys]}


class RecordingGLBService:
    """Stands in for GLB preparation and records the project CRS it is given."""

    def __init__(self):
        self.crs_calls = []

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
        cancel_requested=None,
    ):
        self.crs_calls.append(copy.deepcopy(project_crs_info))
        name = model_input.name or Path(model_input.source_path).stem
        slug = model_input.slug or name.casefold().replace(" ", "_")
        if used_slugs is not None:
            used_slugs.add(slug)
        stage = Path(staging_root) / f".glb-upload-{slug}"
        stage.mkdir(parents=True)
        scene = stage / "scene.glb"
        scene.write_bytes(Path(model_input.source_path).read_bytes())
        manifest = stage / "model.json"
        manifest.write_text(json.dumps({"entrypoint": "scene.glb", "crs": project_crs_info["value"]}), encoding="utf-8")
        prepared = PreparedModelUpload(
            model_input=model_input,
            name=name,
            slug=slug,
            staging_dir=str(stage),
            scene_path=str(scene),
            manifest_path=str(manifest),
            original_sha256="0" * 64,
            model_to_project=IDENTITY,
            bounds_min=(0.0, 0.0, 0.0),
            bounds_max=(1.0, 1.0, 1.0),
            crs_info=project_crs_info,
            optimization=GLBOptimizationResult(output_sha256="1" * 64),
            data_version=hashlib.sha256(scene.read_bytes() + manifest.read_bytes()).hexdigest(),
        )
        return dataclasses.replace(
            prepared,
            index_entry=build_model_index_entry(
                prepared,
                project_viewer_root=project_viewer_root,
                project_s3_prefix=project_s3_prefix,
            ),
        )


def repository(s3):
    return ProjectMetadataRepository(s3, bucket_name="bucket", timestamp_factory=lambda: TIMESTAMP)


def management(s3, glb=None, new_project_id="dup00001"):
    versions = itertools.count(1)
    return ProjectManagementService(
        repository=repository(s3),
        s3_client=s3,
        id_factory=lambda: new_project_id,
        timestamp_factory=lambda: TIMESTAMP,
        data_version_factory=lambda: f"v{next(versions)}",
        bucket_name="bucket",
        glb_service=glb,
    )


def uploader(s3, project_id="neu00001"):
    return UploadWorkflowService(
        repository=repository(s3),
        s3_client=s3,
        id_factory=lambda: project_id,
        timestamp_factory=lambda: TIMESTAMP,
        bucket_name="bucket",
    )


def seed_index(s3, projects, disabled=()):
    s3.store(
        S3_INDEX_JSON,
        json.dumps(
            {"projects": list(projects), S3_DISABLED_PROJECTS_KEY: list(disabled), "last_updated": "alt"},
            ensure_ascii=False,
        ),
    )


def stored_index(s3):
    return json.loads(s3.objects[S3_INDEX_JSON].decode("utf-8"))


def stored_project(s3, project_id):
    index = stored_index(s3)
    for section in ("projects", S3_DISABLED_PROJECTS_KEY):
        for entry in index.get(section, []):
            if isinstance(entry, dict) and entry.get("id") == project_id:
                return entry
    return None


def side_effects(s3):
    return [event for event in s3.events if event[0] in {"put", "upload", "copy", "delete"}]


def write_glb(path, content):
    path.write_bytes(content)
    return path


def foreign_entries():
    return [
        {
            "index_schema_version": 2,
            "id": "fremd-schema2",
            "kunde": "Fremd",
            "projekt": "Von altem Uploader aufgebläht",
            "format": "potree",
            "viewer_path": "fremd/a",
            "s3_path": "pointclouds/fremd/a",
            "crs": "EPSG:25832",
            "projection": "EPSG:25832",
            "crs_info": {"value": "EPSG:25832", "projection": "EPSG:25832"},
        },
        {
            "index_schema_version": 2,
            "id": "fremd-widerspruch",
            "kunde": "Fremd",
            "projekt": "Widersprüchlich",
            "format": "potree",
            "viewer_path": "fremd/b",
            "s3_path": "pointclouds/fremd/b",
            "crs": "EPSG:25832",
            "projection": "EPSG:4326",
            "crs_info": {"value": "kein CRS"},
        },
        {
            "index_schema_version": "2",
            "id": "fremd-unbekannt",
            "kunde": "Fremd",
            "projekt": "Unbekanntes Schema",
            "format": "potree",
            "viewer_path": "fremd/c",
            "s3_path": "pointclouds/fremd/c",
            "crs": "EPSG:4326",
            "projection": "EPSG:4326",
        },
        {
            "id": "fremd-alt",
            "kunde": "Fremd",
            "projekt": "Altprojekt",
            "format": "potree",
            "viewer_path": "fremd/d",
            "s3_path": "pointclouds/fremd/d",
            "crs": "EPSG:25832",
            "projection": "EPSG:25832",
            "crs_info": {"value": "EPSG:25832"},
        },
    ]


FOREIGN_IDS = {entry["id"] for entry in foreign_entries()}


def assert_foreign_unchanged(s3):
    index = stored_index(s3)
    entries = [
        entry
        for section in ("projects", S3_DISABLED_PROJECTS_KEY)
        for entry in index.get(section, [])
        if isinstance(entry, dict) and entry.get("id") in FOREIGN_IDS
    ]
    assert json.dumps(entries, ensure_ascii=False) == json.dumps(foreign_entries(), ensure_ascii=False)


def assert_compact(project):
    assert type(project["index_schema_version"]) is int and project["index_schema_version"] == 2
    for entry in (project, *(project.get("pointclouds") or ()), *(project.get("models") or ())):
        assert not set(REMOVED_DUPLICATES) & set(entry), entry


def flat_crs(entry):
    return {key: entry.get(key) for key in FLAT_CRS}


def test_schema2_project_stays_compact_through_its_whole_lifecycle(tmp_path):
    s3 = FakeS3()
    seed_index(s3, foreign_entries())
    first = _write_potree_fixture(tmp_path / "Bestand")

    result = uploader(s3).upload_new_project(
        NewProjectUploadWorkflowRequest(
            source_paths=(str(first),),
            kunde="Kunde",
            projekt="Projekt",
            crs_info_by_source_path={str(first): dict(CRS_INFO)},
        )
    )
    assert result.status == "success"
    project = stored_project(s3, "neu00001")
    assert_compact(project)
    assert list(project)[0] == "index_schema_version"
    assert (project["format"], project["name"], flat_crs(project)) == ("potree", "Bestand", FLAT_CRS)
    # The full definition lives on in the uploaded Potree metadata.
    uploaded = json.loads(s3.objects[f"{project['s3_path']}/metadata.json"])
    assert uploaded["crs_info"]["wkt"] == WKT_25832
    assert uploaded["crs_info"]["source"] == "manual"
    assert uploaded["srs"]["wkt"] == WKT_25832
    assert uploaded["vertical_epsg"] == "EPSG:7837"
    assert_foreign_unchanged(s3)

    glb = RecordingGLBService()
    service = management(s3, glb)
    result = service.add_project_models_from_sources(
        "neu00001",
        (str(write_glb(tmp_path / "Fassade.glb", b"glb-1")),),
        confirm_spatial_warning=lambda _message: True,
        confirm_crs_repair=lambda _message: False,
    )
    assert result.status == "success"
    project = stored_project(s3, "neu00001")
    assert_compact(project)
    assert project["format"] == "potree"
    assert flat_crs(project["models"][0]) == FLAT_CRS
    assert glb.crs_calls[-1]["crs_name"] == "ETRS89 / UTM zone 32N"
    assert glb.crs_calls[-1]["vertical_name"] == "DHHN2016 height"
    models = project["models"]

    second = _write_potree_fixture(tmp_path / "Ergaenzung")
    service.add_project_pointclouds_from_sources(
        "neu00001",
        (str(second),),
        crs_info_by_source_path={str(second): dict(CRS_INFO)},
    )
    project = stored_project(s3, "neu00001")
    assert_compact(project)
    assert project["format"] == "multi"
    assert [cloud["name"] for cloud in project["pointclouds"]] == ["Bestand", "Ergaenzung"]
    assert all(flat_crs(cloud) == FLAT_CRS for cloud in project["pointclouds"])
    assert flat_crs(project) == FLAT_CRS
    assert project["models"] == models

    third = _write_potree_fixture(tmp_path / "Ersatz")
    service.replace_single_project_pointcloud_from_source(
        "neu00001",
        project["pointclouds"][1]["s3_path"],
        str(third),
        crs_info=dict(CRS_INFO),
    )
    project = stored_project(s3, "neu00001")
    assert_compact(project)
    assert [cloud["name"] for cloud in project["pointclouds"]] == ["Bestand", "Ersatz"]
    assert project["models"] == models

    service.remove_project_pointcloud("neu00001", project["pointclouds"][0]["s3_path"])
    project = stored_project(s3, "neu00001")
    assert_compact(project)
    assert [cloud["name"] for cloud in project["pointclouds"]] == ["Ersatz"]
    assert flat_crs(project) == FLAT_CRS

    result = service.replace_single_project_model_from_source(
        "neu00001",
        project["models"][0]["s3_path"],
        str(write_glb(tmp_path / "Fassade neu.glb", b"glb-2")),
        confirm_spatial_warning=lambda _message: True,
        confirm_crs_repair=lambda _message: False,
    )
    assert result.status == "success"
    project = stored_project(s3, "neu00001")
    assert_compact(project)
    assert [(model["id"], model["name"]) for model in project["models"]] == [("fassade", "Fassade neu")]

    service.rename_project("neu00001", "Kunde Neu", "Projekt Neu", ("Wolke",))
    project = stored_project(s3, "neu00001")
    assert_compact(project)
    assert (project["kunde"], project["projekt"], project["pointclouds"][0]["name"]) == ("Kunde Neu", "Projekt Neu", "Wolke")

    service.set_project_link_state("neu00001", True)
    assert [entry.get("id") for entry in stored_index(s3)[S3_DISABLED_PROJECTS_KEY]] == ["neu00001"]
    assert_compact(stored_project(s3, "neu00001"))
    service.set_project_link_state("neu00001", False)
    project = stored_project(s3, "neu00001")
    assert_compact(project)
    assert "disabled_at" not in project

    service.remove_project_model("neu00001", project["models"][0]["s3_path"])
    project = stored_project(s3, "neu00001")
    assert_compact(project)
    assert project["models"] == []
    assert len(project["history"]) == 9
    assert_foreign_unchanged(s3)

    result = service.delete_project("neu00001")
    assert result.status == "success"
    assert stored_project(s3, "neu00001") is None
    assert json.loads(s3.objects[S3_DELETED_JSON])["deleted_projects"][0]["id"] == "neu00001"
    assert_foreign_unchanged(s3)


def legacy_single(**overrides):
    project = {
        "datum": "2026-01-01T00:00:00",
        "kunde": "Kunde",
        "id": "alt00001",
        "projekt": "Projekt",
        "format": "potree",
        "link": "https://pointcloud.dronautix.at/index.html?id=alt00001",
        "viewer_path": "kunde/alt00001/projekt",
        "s3_path": "pointclouds/kunde/alt00001/projekt",
        "name": "Bestand",
        "crs": "EPSG:25832",
        "projection": "EPSG:25832",
        "epsg": "EPSG:25832",
        "crs_name": "ETRS89 / UTM zone 32N",
        "vertical_crs": "EPSG:7837",
        "vertical_epsg": "EPSG:7837",
        "vertical_projection": "EPSG:7837",
        "vertical_name": "DHHN2016 height",
        "vertical_datum": "DHHN2016 height",
        "crs_info": {
            "value": "EPSG:25832",
            "projection": "EPSG:25832",
            "epsg": "EPSG:25832",
            "name": "ETRS89 / UTM zone 32N",
            "vertical_epsg": "EPSG:7837",
            "vertical_name": "DHHN2016 height",
        },
    }
    project.update(overrides)
    return project


def seed_cloud_documents(s3, prefix, metadata=BOUNDS_DOCUMENT):
    s3.store(f"{prefix}/metadata.json", metadata)
    s3.store(f"{prefix}/octree.bin", b"octree")


@pytest.mark.parametrize("schema2", (False, True))
def test_single_cloud_rebuild_keeps_or_withholds_the_marker(tmp_path, schema2):
    project = legacy_single()
    if schema2:
        project = {"index_schema_version": 2, **project}
    s3 = FakeS3()
    seed_index(s3, [project, *foreign_entries()])
    seed_cloud_documents(s3, project["s3_path"])
    service = management(s3, RecordingGLBService())

    service.replace_single_project_pointcloud_from_source(
        "alt00001",
        project["s3_path"],
        str(_write_potree_fixture(tmp_path / "Ersatz")),
        crs_info=dict(CRS_INFO),
    )
    service.add_project_models_from_sources(
        "alt00001",
        (str(write_glb(tmp_path / "Haus.glb", b"glb")),),
        confirm_spatial_warning=lambda _message: True,
        confirm_crs_repair=lambda _message: False,
    )
    rebuilt = stored_project(s3, "alt00001")

    assert rebuilt["name"] == "Ersatz"
    assert flat_crs(rebuilt) == FLAT_CRS
    if schema2:
        assert list(rebuilt)[0] == "index_schema_version"
        assert_compact(rebuilt)
    else:
        assert "index_schema_version" not in rebuilt
        assert rebuilt["projection"] == "EPSG:25832"
        assert rebuilt["vertical_datum"] == "DHHN2016 height"
        assert rebuilt["crs_info"]["wkt"] == WKT_25832
        assert rebuilt["models"][0]["vertical_datum"] == "DHHN2016 height"
    assert_foreign_unchanged(s3)


@pytest.mark.parametrize("version", ("2", 2.0, True, None, 1, 3))
def test_unknown_index_schema_blocks_every_write_before_any_side_effect(tmp_path, version):
    project = {"index_schema_version": version, **legacy_single()}
    s3 = FakeS3()
    seed_index(s3, [project])
    seed_cloud_documents(s3, project["s3_path"])
    fixture = _write_potree_fixture(tmp_path / "Neu")
    service = management(s3, RecordingGLBService())
    objects_before = dict(s3.objects)
    actions = (
        lambda: service.rename_project("alt00001", "K", "P"),
        lambda: service.set_project_link_state("alt00001", True),
        lambda: service.delete_project("alt00001"),
        lambda: service.duplicate_project("alt00001", "K", "Kopie"),
        lambda: service.add_project_pointclouds_from_sources("alt00001", (str(fixture),)),
        lambda: service.replace_single_project_pointcloud_from_source("alt00001", project["s3_path"], str(fixture)),
        lambda: service.replace_project_pointclouds_from_sources("alt00001", (str(fixture),)),
        lambda: service.add_project_models_from_sources("alt00001", (str(write_glb(tmp_path / "H.glb", b"g")),)),
        lambda: service.repair_project_crs_metadata("alt00001", {"value": "EPSG:25832", "vertical_crs": "EPSG:7837"}),
    )

    for action in actions:
        with pytest.raises(UnsupportedIndexSchemaError, match="Indexschema-Version"):
            action()

    assert s3.objects == objects_before
    assert side_effects(s3) == []
    # Reading stays possible.
    assert [project["id"] for project, _disabled in service.list_projects_for_management()] == ["alt00001"]
    download = service.download_project("alt00001", str(tmp_path / "download"))
    assert download.status == "success"


def test_direct_operations_with_prepared_data_are_guarded_too(tmp_path):
    s3 = FakeS3()
    index = {"projects": [{"index_schema_version": "2", **legacy_single()}]}
    prepared = prepare_cloud_uploads(
        (PointcloudSource(str(_write_potree_fixture(tmp_path / "Neu")), name="Neu", input_format="potree"),),
        "kunde/alt00001/projekt/versions/v1",
        "pointclouds/kunde/alt00001/projekt/versions/v1",
    )

    with pytest.raises(UnsupportedIndexSchemaError):
        add_project_pointclouds(
            s3_client=s3,
            index_data=index,
            project_id="alt00001",
            project_viewer_root="kunde/alt00001/projekt",
            project_s3_prefix="pointclouds/kunde/alt00001/projekt",
            prepared_clouds=prepared,
            save_index=lambda _data: pytest.fail("index saved"),
            delete_keys=lambda _keys: pytest.fail("keys deleted"),
        )
    with pytest.raises(UnsupportedIndexSchemaError):
        upload_new_project(
            s3_client=s3,
            index_data={"projects": []},
            prepared_upload=PreparedProjectUpload(
                project_metadata={"index_schema_version": 3, "id": "x", "s3_path": "pointclouds/x"},
                files_to_upload=prepared[0].files_to_upload,
            ),
            save_index=lambda _data: pytest.fail("index saved"),
            delete_keys=lambda _keys: pytest.fail("keys deleted"),
        )
    assert s3.events == []


def test_rebase_keeps_a_concurrent_foreign_edit_and_uses_the_fresh_etag():
    s3 = FakeS3()
    target = {"index_schema_version": 2, **legacy_single()}
    seed_index(s3, [target, *foreign_entries()])

    def foreign_edit(fake):
        index = stored_index(fake)
        index["projects"][1]["projekt"] = "Fremd geändert"
        fake.store(S3_INDEX_JSON, json.dumps(index, ensure_ascii=False))

    s3.before_index_put = foreign_edit
    result = management(s3).rename_project("alt00001", "Kunde", "Neu")

    assert result.status == "success"
    stored = stored_index(s3)
    assert stored["projects"][1] == {**foreign_entries()[0], "projekt": "Fremd geändert"}
    assert stored["projects"][2:] == foreign_entries()[1:]
    renamed = stored_project(s3, "alt00001")
    assert renamed["projekt"] == "Neu"
    assert_compact(renamed)


def test_conflict_on_the_own_target_still_aborts():
    s3 = FakeS3()
    target = {"index_schema_version": 2, **legacy_single()}
    seed_index(s3, [target])

    def own_edit(fake):
        index = stored_index(fake)
        index["projects"][0]["projekt"] = "Anderswo umbenannt"
        fake.store(S3_INDEX_JSON, json.dumps(index, ensure_ascii=False))

    s3.before_index_put = own_edit
    with pytest.raises(ProjectMetadataConflictError):
        management(s3).rename_project("alt00001", "Kunde", "Neu")

    assert stored_project(s3, "alt00001") == {**target, "projekt": "Anderswo umbenannt"}


def test_new_upload_rebases_on_a_foreign_edit(tmp_path):
    s3 = FakeS3()
    seed_index(s3, foreign_entries())
    source = _write_potree_fixture(tmp_path / "Bestand")

    def foreign_edit(fake):
        index = stored_index(fake)
        index["projects"][0]["projekt"] = "Fremd geändert"
        fake.store(S3_INDEX_JSON, json.dumps(index, ensure_ascii=False))

    s3.before_index_put = foreign_edit
    uploader(s3).upload_new_project(
        NewProjectUploadWorkflowRequest(
            source_paths=(str(source),),
            kunde="Kunde",
            projekt="Projekt",
            crs_info_by_source_path={str(source): dict(CRS_INFO)},
        )
    )

    stored = stored_index(s3)["projects"]
    assert stored[0]["id"] == "neu00001"
    assert_compact(stored[0])
    assert stored[1]["projekt"] == "Fremd geändert"


def duplicate_source(**crs_info_updates):
    crs_info = {
        "value": "EPSG:25832",
        "projection": "EPSG:25832",
        "name": "ETRS89 / UTM zone 32N",
        "wkt": WKT_25832,
        "source": "las_header",
    }
    crs_info.update(crs_info_updates)
    return legacy_single(
        id="quelle",
        viewer_path="alt/quelle/projekt",
        s3_path="pointclouds/alt/quelle/projekt",
        crs_info=crs_info,
        vertical_crs=None,
    )


def seed_duplicate(s3, source, document_crs_info=None):
    source = {key: value for key, value in source.items() if value is not None and not key.startswith("vertical")}
    seed_index(s3, [source, *foreign_entries()])
    metadata = {"boundingBox": {"min": [0, 0, 0], "max": [1, 1, 1]}, "source": "Bestand"}
    if document_crs_info is not None:
        metadata["crs_info"] = document_crs_info
    seed_cloud_documents(s3, source["s3_path"], json.dumps(metadata))
    return source


def copy_of(s3):
    paths = build_project_paths("Neu", "Kopie", "dup00001")
    return stored_project(s3, "dup00001"), paths.s3_prefix


def test_duplicate_of_a_legacy_source_with_index_only_wkt_keeps_the_block():
    s3 = FakeS3()
    source = seed_duplicate(s3, duplicate_source())

    result = management(s3).duplicate_project("quelle", "Neu", "Kopie")

    assert result.status == "success"
    duplicate, prefix = copy_of(s3)
    assert list(duplicate)[0] == "index_schema_version"
    assert duplicate["index_schema_version"] == 2
    assert duplicate["crs_info"] == source["crs_info"]
    assert "projection" not in duplicate and "epsg" not in duplicate
    assert f"{prefix}/metadata.json" in s3.objects
    assert stored_project(s3, "quelle") == source
    assert_foreign_unchanged(s3)


def test_duplicate_drops_the_block_when_the_copied_documents_prove_it():
    s3 = FakeS3()
    seed_duplicate(s3, duplicate_source(), {"value": "EPSG:25832", "wkt": WKT_25832, "source": "las_header"})

    management(s3).duplicate_project("quelle", "Neu", "Kopie")

    duplicate, prefix = copy_of(s3)
    assert "crs_info" not in duplicate
    assert duplicate["crs"] == "EPSG:25832"
    assert ("get", f"{prefix}/metadata.json") in s3.events


def test_duplicate_keeps_the_block_when_the_copied_documents_cannot_be_read():
    s3 = FakeS3()
    seed_duplicate(s3, duplicate_source(), {"value": "EPSG:25832", "wkt": WKT_25832, "source": "las_header"})
    s3.unreadable_marker = "dup00001"

    result = management(s3).duplicate_project("quelle", "Neu", "Kopie")

    assert result.status == "success"
    duplicate, _prefix = copy_of(s3)
    assert duplicate["crs_info"]["wkt"] == WKT_25832


def test_cancel_while_reading_duplicate_evidence_stays_a_cancel():
    s3 = FakeS3()
    source = seed_duplicate(s3, duplicate_source(), {"value": "EPSG:25832", "wkt": WKT_25832})
    index_before = s3.objects[S3_INDEX_JSON]

    def cancel_after_copying():
        return sum(1 for event in s3.events if event[0] == "copy") == 2

    with pytest.raises(OperationCancelledError):
        management(s3).duplicate_project("quelle", "Neu", "Kopie", cancel_requested=cancel_after_copying)

    _duplicate, prefix = copy_of(s3)
    assert s3.objects[S3_INDEX_JSON] == index_before
    assert not any(key.startswith(prefix) for key in s3.objects)
    assert stored_project(s3, "quelle") == source


class LastCopiedDocumentS3(FakeS3):
    """Cancels or fails while the copy's last metadata document is being read."""

    def __init__(self, mode):
        super().__init__()
        self.mode = mode
        self.cancelled = False
        self.last_document = f"{build_project_paths('Neu', 'Kopie', 'dup00001').s3_prefix}/cloud.js"

    def get_object(self, Bucket, Key, Range=None):
        response = super().get_object(Bucket, Key, Range)
        if Key != self.last_document:
            return response
        data = response["Body"].read()
        if self.mode == "get":
            self.cancelled = True
            return {**response, "Body": io.BytesIO(data)}

        def read():
            if self.mode == "read":
                self.cancelled = True
            elif self.mode == "read_raises_cancel":
                raise OperationCancelledError("Lesen abgebrochen.")
            else:
                raise RuntimeError("simulierter Lesefehler")
            return data

        return {**response, "Body": SimpleNamespace(read=read)}


def seed_duplicate_with_both_documents(s3):
    document_crs_info = {"value": "EPSG:25832", "source": "auto"}
    source = seed_duplicate(s3, duplicate_source(source="auto"), document_crs_info)
    s3.store(
        f"{source['s3_path']}/cloud.js",
        json.dumps({"boundingBox": {"lx": 0, "ly": 0, "lz": 0, "ux": 1, "uy": 1, "uz": 1}, "crs_info": document_crs_info}),
    )
    return source


@pytest.mark.parametrize("mode", ("get", "read", "read_raises_cancel"))
def test_cancel_during_the_last_copied_document_read_publishes_nothing(mode):
    s3 = LastCopiedDocumentS3(mode)
    source = seed_duplicate_with_both_documents(s3)
    index_before = s3.objects[S3_INDEX_JSON]

    with pytest.raises(OperationCancelledError):
        management(s3).duplicate_project("quelle", "Neu", "Kopie", cancel_requested=lambda: s3.cancelled)

    _duplicate, prefix = copy_of(s3)
    assert ("get", s3.last_document) in s3.events
    assert ("put", S3_INDEX_JSON) not in s3.events
    assert s3.objects[S3_INDEX_JSON] == index_before
    assert not any(key.startswith(f"{prefix}/") for key in s3.objects)
    assert stored_project(s3, "quelle") == source
    assert f"{source['s3_path']}/cloud.js" in s3.objects


def test_an_ordinary_body_read_error_only_withholds_the_evidence():
    s3 = LastCopiedDocumentS3("read_fails")
    seed_duplicate_with_both_documents(s3)

    result = management(s3).duplicate_project("quelle", "Neu", "Kopie", cancel_requested=lambda: s3.cancelled)

    assert result.status == "success"
    duplicate, prefix = copy_of(s3)
    assert duplicate["crs_info"]["wkt"] == WKT_25832
    assert duplicate["crs_info"]["source"] == "auto"
    assert f"{prefix}/cloud.js" in s3.objects


def test_prepared_clouds_without_staging_keep_their_crs_details(tmp_path):
    s3 = FakeS3()
    project = {"index_schema_version": 2, **{k: v for k, v in legacy_single().items() if k not in REMOVED_DUPLICATES}}
    seed_index(s3, [project])
    prepared = prepare_cloud_uploads(
        (
            PointcloudSource(
                str(_write_potree_fixture(tmp_path / "Roh")),
                name="Roh",
                slug="roh",
                input_format="potree",
                crs_info=dict(CRS_INFO),
            ),
        ),
        "tmp/viewer",
        "tmp/s3",
    )

    management(s3).add_project_pointclouds("alt00001", prepared)

    stored = stored_project(s3, "alt00001")
    added = stored["pointclouds"][1]
    assert added["crs_info"]["wkt"] == WKT_25832
    assert added["crs_info"]["source"] == "manual"
    assert "projection" not in added and "vertical_datum" not in added
    assert "crs_info" not in stored["pointclouds"][0]


def single_with_vendor_detail(schema2):
    project = {
        **{key: value for key, value in legacy_single().items() if key not in REMOVED_DUPLICATES},
        "crs_info": {
            "value": "EPSG:25832",
            "name": "ETRS89 / UTM zone 32N",
            "vertical_crs": "EPSG:7837",
            "vertical_name": "DHHN2016 height",
            "custom_vendor_detail": "Messkampagne 7",
        },
    }
    return {"index_schema_version": 2, **project} if schema2 else project


@pytest.mark.parametrize("glb_first", (False, True))
@pytest.mark.parametrize("schema2", (True, False))
def test_structure_change_keeps_details_of_the_original_cloud_only_in_schema2(tmp_path, schema2, glb_first):
    s3 = FakeS3()
    project = single_with_vendor_detail(schema2)
    seed_index(s3, [project])
    seed_cloud_documents(s3, project["s3_path"])
    service = management(s3, RecordingGLBService())
    if glb_first:
        service.add_project_models_from_sources(
            "alt00001",
            (str(write_glb(tmp_path / "Haus.glb", b"glb")),),
            confirm_spatial_warning=lambda _message: True,
            confirm_crs_repair=lambda _message: False,
        )

    addition = _write_potree_fixture(tmp_path / "Ergaenzung")
    service.add_project_pointclouds_from_sources(
        "alt00001",
        (str(addition),),
        crs_info_by_source_path={str(addition): dict(CRS_INFO)},
    )

    stored = stored_project(s3, "alt00001")
    original_cloud = stored["pointclouds"][0]
    assert stored["format"] == "multi"
    assert len(stored.get("models", [])) == (1 if glb_first else 0)
    if schema2:
        assert stored["index_schema_version"] == 2
        assert original_cloud["crs_info"]["custom_vendor_detail"] == "Messkampagne 7"
        assert "crs_info" not in stored["pointclouds"][1]
    else:
        # Protected legacy behaviour: the normalized child drops unknown keys.
        assert "index_schema_version" not in stored
        assert "custom_vendor_detail" not in original_cloud["crs_info"]


@pytest.mark.parametrize("empty", (None, ""))
def test_an_unknown_empty_crs_info_key_moves_with_the_original_cloud(tmp_path, empty):
    s3 = FakeS3()
    project = single_with_vendor_detail(True)
    project["crs_info"] = {
        **{key: value for key, value in project["crs_info"].items() if key != "custom_vendor_detail"},
        "vendor_flag": empty,
    }
    seed_index(s3, [project])
    seed_cloud_documents(s3, project["s3_path"])
    addition = _write_potree_fixture(tmp_path / "Ergaenzung")

    management(s3).add_project_pointclouds_from_sources(
        "alt00001",
        (str(addition),),
        crs_info_by_source_path={str(addition): dict(CRS_INFO)},
    )

    stored = stored_project(s3, "alt00001")
    assert stored["format"] == "multi"
    assert stored["pointclouds"][0]["crs_info"] == project["crs_info"]
    assert "crs_info" not in stored["pointclouds"][1]


@pytest.mark.parametrize("detail", ("nur hier", None, ""))
def test_unattributable_summary_detail_stops_only_the_structure_change(tmp_path, detail):
    s3 = FakeS3()
    clouds = [
        {
            "name": name,
            "format": "potree",
            "viewer_path": f"kunde/multi/projekt/{name.lower()}",
            "s3_path": f"pointclouds/kunde/multi/projekt/{name.lower()}",
            "visible": True,
            **FLAT_CRS,
        }
        for name in ("A", "B")
    ]
    project = {
        "index_schema_version": 2,
        "id": "multi",
        "kunde": "Kunde",
        "projekt": "Projekt",
        "format": "multi",
        "viewer_path": "kunde/multi/projekt",
        "s3_path": "pointclouds/kunde/multi/projekt",
        **FLAT_CRS,
        "crs_info": {"value": "EPSG:25832", "projekt_detail": detail},
        "pointcloud_count": 2,
        "pointclouds": clouds,
    }
    seed_index(s3, [project])
    service = management(s3)
    addition = _write_potree_fixture(tmp_path / "C")

    with pytest.raises(ValueError, match="projekt_detail"):
        service.add_project_pointclouds_from_sources("multi", (str(addition),), crs_info_by_source_path={str(addition): dict(CRS_INFO)})
    with pytest.raises(ValueError, match="projekt_detail"):
        service.remove_project_pointcloud("multi", clouds[1]["s3_path"])
    assert side_effects(s3) == []
    assert stored_project(s3, "multi") == project

    service.rename_project("multi", "Kunde", "Umbenannt")
    renamed = stored_project(s3, "multi")
    assert renamed["projekt"] == "Umbenannt"
    assert renamed["crs_info"] == project["crs_info"]


def later_glb_project(schema2):
    # Project summary without height reference, complete named cloud entry:
    # the existing candidate order must still pick the cloud's names.
    cloud = {
        "name": "A",
        "format": "potree",
        "viewer_path": "kunde/m/projekt/a",
        "s3_path": "pointclouds/kunde/m/projekt/a",
        "visible": True,
        **FLAT_CRS,
    }
    project = {
        "id": "m",
        "kunde": "Kunde",
        "projekt": "Projekt",
        "format": "multi",
        "viewer_path": "kunde/m/projekt",
        "s3_path": "pointclouds/kunde/m/projekt",
        "crs": "EPSG:25832",
        "crs_name": "ETRS89 / UTM zone 32N",
        "pointcloud_count": 1,
        "pointclouds": [cloud],
    }
    if schema2:
        project = {"index_schema_version": 2, **project}
    else:
        project["projection"] = "EPSG:25832"
        project["crs_info"] = {"value": "EPSG:25832", "projection": "EPSG:25832", "name": "ETRS89 / UTM zone 32N"}
        cloud.update(
            {
                "projection": "EPSG:25832",
                "vertical_epsg": "EPSG:7837",
                "vertical_datum": "DHHN2016 height",
                "crs_info": {
                    "value": "EPSG:25832",
                    "name": "ETRS89 / UTM zone 32N",
                    "vertical_epsg": "EPSG:7837",
                    "vertical_name": "DHHN2016 height",
                },
            }
        )
    return project


@pytest.mark.parametrize("schema2", (False, True))
def test_later_glb_gets_the_same_crs_from_a_compact_and_a_legacy_index(tmp_path, schema2):
    s3 = FakeS3()
    seed_index(s3, [later_glb_project(schema2)])
    glb = RecordingGLBService()

    result = management(s3, glb).add_project_models_from_sources(
        "m",
        (str(write_glb(tmp_path / "Haus.glb", b"glb")),),
        confirm_spatial_warning=lambda _message: True,
        confirm_crs_repair=lambda _message: True,
    )

    assert result.status == "success"
    assert glb.crs_calls == [
        {
            "value": "EPSG:25832",
            "projection": "EPSG:25832",
            "epsg": "EPSG:25832",
            "code": "25832",
            "name": "ETRS89 / UTM zone 32N",
            "crs_name": "ETRS89 / UTM zone 32N",
            "vertical_crs": "EPSG:7837",
            "vertical_epsg": "EPSG:7837",
            "vertical_projection": "EPSG:7837",
            "vertical_name": "DHHN2016 height",
            "vertical_datum": "DHHN2016 height",
        }
    ]
    stored = stored_project(s3, "m")
    model = stored["models"][0]
    assert flat_crs(model) == FLAT_CRS
    assert flat_crs(stored) == FLAT_CRS
    if schema2:
        assert_compact(stored)
    else:
        assert model["vertical_datum"] == "DHHN2016 height"
        assert stored["crs_info"]["vertical_crs"] == "EPSG:7837"


class CapturingGLBService:
    """The real GLB preparation; keeps each prepared package for comparison."""

    def __init__(self):
        self.service = GLBOptimizationService()
        self.prepared = []

    def prepare(self, model_input, **kwargs):
        prepared = self.service.prepare(model_input, **kwargs)
        self.prepared.append(prepared)
        return prepared


def test_later_glb_package_is_byte_identical_from_a_compact_and_a_legacy_index(tmp_path):
    from test_glb_optimization_service import native_document, write_glb as write_glb_document

    source = tmp_path / "Haus.glb"
    write_glb_document(source, native_document(asset={"version": "2.0"}))
    runs = {}
    for schema2 in (False, True):
        s3 = FakeS3()
        seed_index(s3, [later_glb_project(schema2)])
        glb = CapturingGLBService()

        result = management(s3, glb).add_project_models_from_sources(
            "m",
            (str(source),),
            confirm_spatial_warning=lambda _message: True,
            confirm_crs_repair=lambda _message: True,
        )

        assert result.status == "success"
        [prepared] = glb.prepared
        stored = stored_project(s3, "m")
        # The repair fills in the summary's missing height reference.
        assert flat_crs(stored) == FLAT_CRS
        [model] = stored["models"]
        package = {
            key[len(model["s3_path"]) + 1 :]: data
            for key, data in s3.objects.items()
            if key.startswith(f"{model['s3_path']}/")
        }
        runs[schema2] = (prepared, model, package)

    (legacy, legacy_model, legacy_package), (compact, compact_model, compact_package) = runs[False], runs[True]
    assert sorted(compact_package) == sorted(legacy_package) == ["model.json", "scene.glb"]
    assert compact_package["model.json"] == legacy_package["model.json"]
    assert compact_package["scene.glb"] == legacy_package["scene.glb"]
    assert compact.model_to_project == legacy.model_to_project
    assert compact.output_sha256 == legacy.output_sha256
    assert compact.package_sha256 == legacy.package_sha256 == compact.data_version == legacy.data_version
    assert (compact.name, compact.slug) == (legacy.name, legacy.slug) == ("Haus", "haus")
    assert compact.index_entry == legacy.index_entry
    for key in ("id", "name", "format", "viewer_path", "s3_path", "data_version"):
        assert compact_model.get(key) == legacy_model.get(key), key
    assert compact_model["s3_path"].endswith(f"/models/haus/versions/{compact.package_sha256}")
    assert compact_model["viewer_path"] == f"kunde/m/projekt/models/haus/versions/{compact.package_sha256}/model.json"
    manifest = json.loads(compact_package["model.json"])
    assert (manifest["crs"], manifest["crs_name"]) == ("EPSG:25832", "ETRS89 / UTM zone 32N")
    assert (manifest["vertical_crs"], manifest["vertical_datum"]) == ("EPSG:7837", "DHHN2016 height")
    assert manifest["model_to_project"] == list(IDENTITY)


def test_crs_repair_keeps_document_details_and_uses_them_as_index_proof():
    s3 = FakeS3()
    project = {
        "index_schema_version": 2,
        **{key: value for key, value in legacy_single().items() if key not in REMOVED_DUPLICATES and not key.startswith("vertical")},
        "crs_info": {"value": "EPSG:25832", "name": "ETRS89 / UTM zone 32N", "wkt": WKT_25832},
    }
    seed_index(s3, [project])
    document = {
        "boundingBox": {"min": [0, 0, 0], "max": [1, 1, 1]},
        "srs": {"authority": "EPSG", "horizontal": "25832", "wkt": WKT_25832},
        "crs_info": {"value": "EPSG:25832", "wkt": WKT_25832, "source": "las_header"},
    }
    seed_cloud_documents(s3, project["s3_path"], json.dumps(document))

    result = management(s3).repair_project_crs_metadata(
        "alt00001",
        {"value": "EPSG:25832", "vertical_crs": "EPSG:7837"},
        confirm_repair=lambda _message: True,
    )

    assert result.status == "success"
    repaired_document = json.loads(s3.objects[f"{project['s3_path']}/metadata.json"])
    assert repaired_document["crs_info"]["wkt"] == WKT_25832
    assert repaired_document["crs_info"]["source"] == "las_header"
    assert repaired_document["srs"]["wkt"] == WKT_25832
    assert repaired_document["vertical_crs"] == "EPSG:7837"
    repaired = stored_project(s3, "alt00001")
    assert_compact(repaired)
    assert (repaired["crs"], repaired["vertical_crs"]) == ("EPSG:25832", "EPSG:7837")


def test_failed_glb_upload_rolls_back_without_writing_the_index(tmp_path):
    s3 = FakeS3()
    project = {"index_schema_version": 2, **legacy_single()}
    seed_index(s3, [project])
    seed_cloud_documents(s3, project["s3_path"])
    index_before = s3.objects[S3_INDEX_JSON]
    s3.fail_upload_suffix = "/model.json"

    with pytest.raises(RuntimeError, match="Uploadfehler"):
        management(s3, RecordingGLBService()).add_project_models_from_sources(
            "alt00001",
            (str(write_glb(tmp_path / "Haus.glb", b"glb")),),
            confirm_spatial_warning=lambda _message: True,
            confirm_crs_repair=lambda _message: False,
        )

    assert s3.objects[S3_INDEX_JSON] == index_before
    assert ("put", S3_INDEX_JSON) not in s3.events
    assert not any("/models/" in key for key in s3.objects)


def test_delete_cleanup_failure_leaves_a_compact_cleanup_entry_that_can_be_retried():
    s3 = FakeS3()
    project = {"index_schema_version": 2, **legacy_single()}
    seed_index(s3, [project, *foreign_entries()])
    seed_cloud_documents(s3, project["s3_path"])
    s3.delete_errors = True

    result = management(s3).delete_project("alt00001")

    assert result.status == "partial"
    pending = stored_project(s3, "alt00001")
    assert pending["cleanup_pending"] is True
    assert_compact(pending)
    assert_foreign_unchanged(s3)

    s3.delete_errors = False
    assert management(s3).delete_project("alt00001").status == "success"
    assert stored_project(s3, "alt00001") is None
    assert_foreign_unchanged(s3)
