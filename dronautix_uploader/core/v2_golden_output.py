"""Deterministic V2 output generation for Golden comparison staging."""

from __future__ import annotations

import io
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import BUCKET_NAME, COPC_OBJECT_NAME, S3_DELETED_JSON, S3_DISABLED_PROJECTS_KEY, S3_INDEX_JSON
from .golden_capture import load_golden_manifest
from .project_management_service import ProjectManagementService
from .project_repository import ProjectMetadataRepository
from .upload_workflow_service import NewProjectUploadWorkflowRequest, UploadWorkflowService


SUPPORTED_V2_UPLOAD_SCENARIOS = (
    "single_potree_upload",
    "single_copc_upload",
    "multi_mix_upload",
    "vertical_crs_upload",
    "existing_potree_folder_upload",
)

SUPPORTED_V2_PROJECT_MANAGEMENT_SCENARIOS = (
    "duplicate_project",
    "delete_project",
    "rename_project",
    "single_replace",
    "multi_replace",
    "disabled_link_state",
)

SUPPORTED_V2_GOLDEN_SCENARIOS = SUPPORTED_V2_UPLOAD_SCENARIOS + SUPPORTED_V2_PROJECT_MANAGEMENT_SCENARIOS
SIDE_EFFECTS_JSON = "side_effects.json"

FIXED_PROJECT_TIMESTAMP = "2026-06-21T12:00:00"
FIXED_INDEX_TIMESTAMP = "2026-06-21T12:30:00"


@dataclass(frozen=True)
class V2GoldenOutputScenarioResult:
    scenario_id: str
    output_dir: Path
    generated_files: tuple[Path, ...]
    uploaded_keys: tuple[str, ...]
    side_effects_path: Path | None = None


def generate_v2_golden_outputs(
    manifest_path: str | Path,
    *,
    output_root: str | Path,
    scenario_id: str | None = None,
    overwrite: bool = False,
) -> tuple[V2GoldenOutputScenarioResult, ...]:
    """Generate raw V2 output files for supported Golden scenarios."""

    manifest_path = Path(manifest_path)
    output_root = Path(output_root)
    manifest = load_golden_manifest(manifest_path)
    scenarios = _selected_manifest_scenarios(manifest, scenario_id)
    return tuple(
        _generate_scenario(scenario, output_root=output_root, overwrite=overwrite)
        for scenario in scenarios
    )


def _generate_scenario(
    scenario: dict[str, Any],
    *,
    output_root: Path,
    overwrite: bool,
) -> V2GoldenOutputScenarioResult:
    scenario_id = str(scenario.get("id", "") or "").strip()
    if scenario_id in SUPPORTED_V2_UPLOAD_SCENARIOS:
        return _generate_upload_scenario(scenario, output_root=output_root, overwrite=overwrite)
    if scenario_id in SUPPORTED_V2_PROJECT_MANAGEMENT_SCENARIOS:
        return _generate_project_management_scenario(scenario, output_root=output_root, overwrite=overwrite)
    raise ValueError(f"V2 output generation is not implemented for scenario: {scenario_id}")


def _generate_upload_scenario(
    scenario: dict[str, Any],
    *,
    output_root: Path,
    overwrite: bool,
) -> V2GoldenOutputScenarioResult:
    scenario_id = str(scenario.get("id", "") or "").strip()
    if scenario_id not in SUPPORTED_V2_UPLOAD_SCENARIOS:
        raise ValueError(f"V2 output generation is not implemented for scenario: {scenario_id}")

    required_files = tuple(str(file_name) for file_name in scenario.get("required_files", ()) if str(file_name))
    output_dir = output_root / scenario_id
    work_dir = output_root / "_work" / scenario_id
    _prepare_output_dir(output_dir, required_files, overwrite=overwrite)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    fake_s3 = _GoldenFakeS3Client()
    repository = ProjectMetadataRepository(
        fake_s3,
        bucket_name=BUCKET_NAME,
        timestamp_factory=lambda: FIXED_INDEX_TIMESTAMP,
    )
    spec = _build_upload_scenario_spec(scenario_id, work_dir)
    service = UploadWorkflowService(
        repository=repository,
        s3_client=fake_s3,
        id_factory=lambda: spec["project_id"],
        timestamp_factory=lambda: FIXED_PROJECT_TIMESTAMP,
        bucket_name=BUCKET_NAME,
    )
    service.upload_new_project(
        NewProjectUploadWorkflowRequest(
            source_paths=tuple(spec["source_paths"]),
            kunde=str(spec["kunde"]),
            projekt=str(spec["projekt"]),
            converter_path=str(spec.get("converter_path", "")),
            output_base_dir=str(spec.get("output_base_dir", "")),
            overwrite=True,
            crs_info_by_source_path=dict(spec.get("crs_info_by_source_path") or {}),
        ),
        converter_runner=_fake_converter_runner,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_files = []
    for file_name in required_files:
        if Path(file_name).name != file_name:
            raise ValueError(f"Unsafe Golden output file name: {file_name}")
        text = _required_output_text(fake_s3, file_name)
        target_path = output_dir / file_name
        target_path.write_text(text, encoding="utf-8", newline="\n")
        generated_files.append(target_path)
    side_effects_path = _write_side_effects(output_dir, scenario_id, fake_s3)

    shutil.rmtree(work_dir, ignore_errors=True)
    try:
        work_dir.parent.rmdir()
    except OSError:
        pass

    return V2GoldenOutputScenarioResult(
        scenario_id=scenario_id,
        output_dir=output_dir,
        generated_files=tuple(generated_files),
        uploaded_keys=tuple(record.key for record in fake_s3.uploads),
        side_effects_path=side_effects_path,
    )


def _generate_project_management_scenario(
    scenario: dict[str, Any],
    *,
    output_root: Path,
    overwrite: bool,
) -> V2GoldenOutputScenarioResult:
    scenario_id = str(scenario.get("id", "") or "").strip()
    if scenario_id not in SUPPORTED_V2_PROJECT_MANAGEMENT_SCENARIOS:
        raise ValueError(f"V2 output generation is not implemented for scenario: {scenario_id}")

    required_files = tuple(str(file_name) for file_name in scenario.get("required_files", ()) if str(file_name))
    output_dir = output_root / scenario_id
    work_dir = output_root / "_work" / scenario_id
    _prepare_output_dir(output_dir, required_files, overwrite=overwrite)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    fake_s3 = _GoldenFakeS3Client()
    repository = ProjectMetadataRepository(
        fake_s3,
        bucket_name=BUCKET_NAME,
        timestamp_factory=lambda: FIXED_INDEX_TIMESTAMP,
    )
    service = ProjectManagementService(
        repository=repository,
        s3_client=fake_s3,
        id_factory=lambda: "abc12d01",
        timestamp_factory=lambda: FIXED_PROJECT_TIMESTAMP,
        bucket_name=BUCKET_NAME,
    )
    _run_project_management_scenario(scenario_id, work_dir, fake_s3, service)

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_files = []
    for file_name in required_files:
        if Path(file_name).name != file_name:
            raise ValueError(f"Unsafe Golden output file name: {file_name}")
        text = _required_output_text(fake_s3, file_name)
        target_path = output_dir / file_name
        target_path.write_text(text, encoding="utf-8", newline="\n")
        generated_files.append(target_path)
    side_effects_path = _write_side_effects(output_dir, scenario_id, fake_s3)

    shutil.rmtree(work_dir, ignore_errors=True)
    try:
        work_dir.parent.rmdir()
    except OSError:
        pass

    copied_keys = tuple(record.key for record in fake_s3.copies)
    uploaded_keys = tuple(record.key for record in fake_s3.uploads) + copied_keys
    return V2GoldenOutputScenarioResult(
        scenario_id=scenario_id,
        output_dir=output_dir,
        generated_files=tuple(generated_files),
        uploaded_keys=uploaded_keys,
        side_effects_path=side_effects_path,
    )


@dataclass(frozen=True)
class _UploadedObject:
    local_path: str
    key: str
    extra_args: dict[str, Any] | None


@dataclass(frozen=True)
class _CopiedObject:
    source_key: str
    key: str


class _NoSuchKey(Exception):
    pass


class _FakeS3Exceptions:
    NoSuchKey = _NoSuchKey


class _GoldenFakeS3Client:
    exceptions = _FakeS3Exceptions

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: list[_UploadedObject] = []
        self.copies: list[_CopiedObject] = []
        self.events: list[dict[str, Any]] = []
        self._sequence = 0

    def get_object(self, Bucket, Key):
        self._record_event("get_object", key=str(Key), found=Key in self.objects)
        if Key not in self.objects:
            raise _NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ContentType=None, CacheControl=None):
        if isinstance(Body, bytes):
            body = Body
        else:
            body = str(Body).encode("utf-8")
        self.objects[Key] = body
        self._record_event(
            "put_object",
            key=str(Key),
            content_type=ContentType,
            cache_control=CacheControl,
            body_size=len(body),
        )
        return {"ETag": '"fake"'}

    def upload_file(self, local_path, bucket, key, ExtraArgs=None, Callback=None):
        data = Path(local_path).read_bytes()
        self.objects[key] = data
        self.uploads.append(_UploadedObject(str(local_path), str(key), dict(ExtraArgs or {})))
        self._record_event(
            "upload_file",
            key=str(key),
            local_name=Path(local_path).name,
            size=len(data),
            extra_args=dict(ExtraArgs or {}),
        )
        if Callback:
            Callback(len(data))

    def get_paginator(self, name):
        if name != "list_objects_v2":
            raise ValueError(f"Unsupported fake paginator: {name}")
        return _GoldenFakePaginator(self)

    def copy_object(self, Bucket, CopySource, Key, **_kwargs):
        source_key = str(CopySource.get("Key", "")) if isinstance(CopySource, dict) else ""
        if source_key not in self.objects:
            raise _NoSuchKey(source_key)
        self.objects[str(Key)] = self.objects[source_key]
        self.copies.append(_CopiedObject(source_key=source_key, key=str(Key)))
        self._record_event(
            "copy_object",
            source_key=source_key,
            key=str(Key),
            cache_control=_kwargs.get("CacheControl"),
            metadata_directive=_kwargs.get("MetadataDirective"),
        )
        return {"CopyObjectResult": {"ETag": '"fake-copy"'}}

    def delete_objects(self, Bucket, Delete):
        deleted = []
        keys = []
        for item in Delete.get("Objects", []):
            key = str(item.get("Key", ""))
            self.objects.pop(key, None)
            deleted.append({"Key": key})
            keys.append(key)
        self._record_event("delete_objects", keys=keys)
        return {"Deleted": deleted}

    def _record_event(self, event_type: str, **payload) -> None:
        self._sequence += 1
        self.events.append({"sequence": self._sequence, "type": event_type, **payload})


class _GoldenFakePaginator:
    def __init__(self, client: _GoldenFakeS3Client) -> None:
        self.client = client

    def paginate(self, **kwargs):
        prefix = str(kwargs.get("Prefix", "") or "")
        contents = [
            {"Key": key, "Size": len(data)}
            for key, data in sorted(self.client.objects.items())
            if key.startswith(prefix)
        ]
        self.client._record_event("list_objects_v2", prefix=prefix, keys=[str(item["Key"]) for item in contents])
        return ({"Contents": contents},) if contents else ({},)


def _selected_manifest_scenarios(manifest: dict[str, Any], scenario_id: str | None) -> tuple[dict[str, Any], ...]:
    scenarios = [scenario for scenario in manifest.get("scenarios", []) if isinstance(scenario, dict)]
    if scenario_id is None:
        return tuple(
            scenario
            for scenario in scenarios
            if str(scenario.get("id", "") or "").strip() in SUPPORTED_V2_GOLDEN_SCENARIOS
        )
    selected = tuple(
        scenario
        for scenario in scenarios
        if str(scenario.get("id", "") or "").strip() == scenario_id
    )
    if not selected:
        raise ValueError(f"Unknown Golden scenario: {scenario_id}")
    return selected


def _prepare_output_dir(output_dir: Path, required_files: tuple[str, ...], *, overwrite: bool) -> None:
    protected_files = (*required_files, SIDE_EFFECTS_JSON)
    existing_targets = [output_dir / file_name for file_name in protected_files if (output_dir / file_name).exists()]
    if existing_targets and not overwrite:
        existing_list = ", ".join(str(path) for path in existing_targets)
        raise FileExistsError(f"V2 Golden output already exists: {existing_list}")
    if output_dir.exists() and overwrite:
        shutil.rmtree(output_dir)


def _required_output_text(fake_s3: _GoldenFakeS3Client, file_name: str) -> str:
    if file_name in {S3_INDEX_JSON, S3_DELETED_JSON}:
        return fake_s3.objects[file_name].decode("utf-8")

    for upload in fake_s3.uploads:
        if os.path.basename(upload.key) == file_name:
            return fake_s3.objects[upload.key].decode("utf-8")
    raise FileNotFoundError(f"V2 scenario did not upload required file: {file_name}")


def _write_side_effects(output_dir: Path, scenario_id: str, fake_s3: _GoldenFakeS3Client) -> Path:
    path = output_dir / SIDE_EFFECTS_JSON
    events = tuple(_side_effect_event(scenario_id, event) for event in fake_s3.events)
    summary = _side_effect_summary(events, fake_s3)
    report = {
        "schema_version": 1,
        "scenario_id": scenario_id,
        "event_count": len(events),
        "events": events,
        "summary": summary,
        "uploaded_keys": summary["uploaded_keys"],
        "copied_keys": summary["copied_keys"],
        "deleted_keys": summary["deleted_keys"],
        "put_object_keys": summary["put_object_keys"],
    }
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return path


def _side_effect_event(scenario_id: str, event: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("type", ""))
    stable_event = {
        key: value
        for key, value in event.items()
        if key not in {"body_size", "size"}
    }
    stable_event["purpose"] = _side_effect_purpose(scenario_id, event)
    if event_type in {"get_object", "put_object", "copy_object", "delete_objects"}:
        stable_event.setdefault("bucket", BUCKET_NAME)
    return stable_event


def _side_effect_purpose(scenario_id: str, event: dict[str, Any]) -> str:
    event_type = str(event.get("type", ""))
    key = str(event.get("key", "") or "")
    prefix = str(event.get("prefix", "") or "")

    if event_type == "put_object":
        if key == S3_INDEX_JSON:
            return "save_projects_index"
        if key == S3_DELETED_JSON:
            return "save_deleted_projects"
        return "save_object"
    if event_type == "upload_file":
        if scenario_id in {"single_replace", "multi_replace", "disabled_link_state"}:
            return "replacement_upload"
        return "new_project_upload"
    if event_type == "copy_object":
        return "duplicate_copy"
    if event_type == "delete_objects":
        if scenario_id == "delete_project":
            return "project_delete"
        if scenario_id in {"single_replace", "multi_replace"}:
            return "orphan_cleanup"
        return "delete_objects"
    if event_type == "list_objects_v2":
        if scenario_id == "delete_project":
            return "project_delete_scan"
        if scenario_id in {"single_replace", "multi_replace"} or "replace" in prefix:
            return "orphan_cleanup_scan"
        if scenario_id == "duplicate_project":
            return "duplicate_source_scan"
        return "list_objects"
    if event_type == "get_object":
        if key == S3_INDEX_JSON:
            return "read_projects_index"
        if key == S3_DELETED_JSON:
            return "read_deleted_projects"
        return "read_object"
    return event_type or "unknown"


def _side_effect_summary(events: tuple[dict[str, Any], ...], fake_s3: _GoldenFakeS3Client) -> dict[str, list[str]]:
    return {
        "uploaded_keys": [record.key for record in fake_s3.uploads],
        "copied_keys": [record.key for record in fake_s3.copies],
        "deleted_keys": [
            key
            for event in events
            if event.get("type") == "delete_objects"
            for key in event.get("keys", [])
        ],
        "put_object_keys": [str(event["key"]) for event in events if event.get("type") == "put_object"],
    }


def _build_upload_scenario_spec(scenario_id: str, work_dir: Path) -> dict[str, Any]:
    converter_path = _write_file(work_dir / "PotreeConverter.exe", b"converter")
    output_base_dir = work_dir / "converted"

    if scenario_id == "single_copc_upload":
        source = _write_file(work_dir / "source.copc.laz", b"copc")
        return {
            "project_id": "abc123ef",
            "kunde": "Golden Kunde",
            "projekt": "Single COPC",
            "source_paths": (str(source),),
            "crs_info_by_source_path": {
                str(source): {"value": "EPSG:25832", "projection": "EPSG:25832"},
            },
        }

    if scenario_id == "single_potree_upload":
        source = _write_file(work_dir / "Single Potree.laz", b"raw")
        return {
            "project_id": "abc123e1",
            "kunde": "Golden Kunde",
            "projekt": "Single Potree",
            "source_paths": (str(source),),
            "converter_path": str(converter_path),
            "output_base_dir": str(output_base_dir),
            "crs_info_by_source_path": {
                str(source): {"value": "EPSG:25832", "projection": "EPSG:25832", "epsg": "EPSG:25832"},
            },
        }

    if scenario_id == "multi_mix_upload":
        copc = _write_file(work_dir / "Fassade.copc.laz", b"copc")
        potree = _write_potree_fixture(work_dir / "Bestand Potree")
        return {
            "project_id": "abc123e2",
            "kunde": "Golden Kunde",
            "projekt": "Multi Mix",
            "source_paths": (str(copc), str(potree)),
            "crs_info_by_source_path": {
                str(copc): {"value": "EPSG:25832", "projection": "EPSG:25832"},
                str(potree): {"value": "EPSG:25832", "projection": "EPSG:25832"},
            },
        }

    if scenario_id == "vertical_crs_upload":
        source = _write_file(work_dir / "Vertical CRS.laz", b"raw")
        return {
            "project_id": "abc123e3",
            "kunde": "Golden Kunde",
            "projekt": "Vertical CRS",
            "source_paths": (str(source),),
            "converter_path": str(converter_path),
            "output_base_dir": str(output_base_dir),
            "crs_info_by_source_path": {
                str(source): {
                    "value": "EPSG:25832",
                    "projection": "EPSG:25832",
                    "epsg": "EPSG:25832",
                    "vertical_epsg": "EPSG:7837",
                    "vertical_name": "DHHN2016",
                },
            },
        }

    if scenario_id == "existing_potree_folder_upload":
        potree = _write_potree_fixture(work_dir / "Existing Potree")
        return {
            "project_id": "abc123e4",
            "kunde": "Golden Kunde",
            "projekt": "Existing Potree",
            "source_paths": (str(potree),),
            "crs_info_by_source_path": {
                str(potree): {"value": "EPSG:4326", "projection": "EPSG:4326", "epsg": "EPSG:4326"},
            },
        }

    raise ValueError(f"Unsupported V2 upload scenario: {scenario_id}")


def _run_project_management_scenario(
    scenario_id: str,
    work_dir: Path,
    fake_s3: _GoldenFakeS3Client,
    service: ProjectManagementService,
) -> None:
    if scenario_id == "duplicate_project":
        _seed_duplicate_project(fake_s3)
        service.duplicate_project("dup-source", "Golden Kunde", "Duplicate Clone")
        return

    if scenario_id == "delete_project":
        _seed_delete_project(fake_s3)
        service.delete_project("delete-target")
        return

    if scenario_id == "rename_project":
        _seed_rename_project(fake_s3)
        service.rename_project("rename-target", "Neue Kunde", "Neues Projekt", ("Cloud Neu A", "Cloud Neu B"))
        return

    if scenario_id == "single_replace":
        target_path = _seed_single_replace(fake_s3)
        source = _write_file(work_dir / "Cloud B Replacement.laz", b"raw")
        converter = _write_file(work_dir / "PotreeConverter.exe", b"converter")
        service.replace_single_project_pointcloud_from_source(
            "replace-single",
            target_path,
            str(source),
            converter_path=str(converter),
            output_base_dir=str(work_dir / "converted"),
            overwrite=True,
            converter_runner=_fake_converter_runner,
            crs_info={"value": "EPSG:25832", "projection": "EPSG:25832", "epsg": "EPSG:25832"},
        )
        return

    if scenario_id == "multi_replace":
        _seed_multi_replace(fake_s3)
        copc = _write_file(work_dir / "Scan.copc.laz", b"copc")
        raw = _write_file(work_dir / "Raw.laz", b"raw")
        converter = _write_file(work_dir / "PotreeConverter.exe", b"converter")
        service.replace_project_pointclouds_from_sources(
            "replace-multi",
            (str(copc), str(raw)),
            converter_path=str(converter),
            output_base_dir=str(work_dir / "converted"),
            overwrite=True,
            converter_runner=_fake_converter_runner,
            crs_info_by_source_path={
                str(copc): {"value": "EPSG:25832", "projection": "EPSG:25832", "epsg": "EPSG:25832"},
                str(raw): {"value": "EPSG:4326", "projection": "EPSG:4326", "epsg": "EPSG:4326"},
            },
        )
        return

    if scenario_id == "disabled_link_state":
        target_path = _seed_disabled_link_state(fake_s3)
        service.set_project_link_state("disable-target", True)
        service.rename_project("disabled-target", "Disabled Kunde Neu", "Disabled Projekt Neu")
        replacement = _write_file(work_dir / "Disabled Replacement.copc.laz", b"copc")
        service.replace_single_project_pointcloud_from_source(
            "disabled-target",
            target_path,
            str(replacement),
            crs_info={"value": "EPSG:4326", "projection": "EPSG:4326", "epsg": "EPSG:4326"},
        )
        return

    raise ValueError(f"Unsupported V2 project management scenario: {scenario_id}")


def _seed_duplicate_project(fake_s3: _GoldenFakeS3Client) -> None:
    source_prefix = "pointclouds/golden/dup_source/original"
    viewer_root = "golden/dup_source/original"
    _seed_json(
        fake_s3,
        S3_INDEX_JSON,
        {
            "projects": [
                {
                    "datum": "2026-06-20T09:00:00",
                    "kunde": "Bestehend",
                    "id": "existing-active",
                    "projekt": "Aktiv",
                    "format": "potree",
                    "link": "https://pointcloud.dronautix.at/index.html?id=existing-active",
                    "viewer_path": "bestehend/existing-active/aktiv",
                    "s3_path": "pointclouds/bestehend/existing-active/aktiv",
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "datum": "2026-06-20T10:00:00",
                    "kunde": "Alt Kunde",
                    "id": "dup-source",
                    "projekt": "Original",
                    "format": "multi",
                    "link": "https://pointcloud.dronautix.at/index.html?id=dup-source",
                    "viewer_path": viewer_root,
                    "s3_path": source_prefix,
                    "disabled_at": "2026-06-20T12:00:00",
                    "pointcloud_count": 2,
                    "pointclouds": [
                        {
                            "name": "Cloud A",
                            "format": "potree",
                            "viewer_path": f"{viewer_root}/cloud_a",
                            "s3_path": f"{source_prefix}/cloud_a",
                            "visible": True,
                        },
                        {
                            "name": "Cloud B",
                            "format": "copc",
                            "viewer_path": f"{viewer_root}/cloud_b/{COPC_OBJECT_NAME}",
                            "s3_path": f"{source_prefix}/cloud_b/{COPC_OBJECT_NAME}",
                            "visible": False,
                        },
                    ],
                }
            ],
            "last_updated": "2026-06-20T12:00:00",
        },
    )
    _seed_s3_object(fake_s3, f"{source_prefix}/cloud_a/cloud.js", 'cloud.js = {"source":"old-a"};')
    _seed_s3_object(fake_s3, f"{source_prefix}/cloud_a/metadata.json", '{"source":"old-a"}')
    _seed_s3_object(fake_s3, f"{source_prefix}/cloud_b/{COPC_OBJECT_NAME}", b"old-copc")


def _seed_delete_project(fake_s3: _GoldenFakeS3Client) -> None:
    target_prefix = "pointclouds/golden/delete_target"
    _seed_json(
        fake_s3,
        S3_INDEX_JSON,
        {
            "projects": [
                {
                    "id": "active-stays",
                    "kunde": "Aktiv",
                    "projekt": "Bleibt",
                    "s3_path": "pointclouds/golden/active_stays",
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "id": "delete-target",
                    "kunde": "Delete Kunde",
                    "projekt": "Delete Projekt",
                    "format": "multi",
                    "link": "https://pointcloud.dronautix.at/index.html?id=delete-target",
                    "viewer_path": "golden/delete_target",
                    "s3_path": target_prefix,
                    "disabled_at": "2026-06-20T12:00:00",
                }
            ],
            "last_updated": "2026-06-20T12:00:00",
        },
    )
    _seed_json(
        fake_s3,
        S3_DELETED_JSON,
        {
            "deleted_projects": [
                {
                    "id": "old-delete",
                    "kunde": "Alt",
                    "projekt": "Archiviert",
                    "s3_path": "pointclouds/golden/old_delete",
                    "deleted_at": "2026-06-19T12:00:00",
                    "original_link": "https://pointcloud.dronautix.at/index.html?id=old-delete",
                }
            ],
            "last_updated": "2026-06-19T12:00:00",
        },
    )
    _seed_s3_object(fake_s3, f"{target_prefix}/cloud.js", 'cloud.js = {"source":"delete"};')
    _seed_s3_object(fake_s3, f"{target_prefix}/metadata.json", '{"source":"delete"}')
    _seed_s3_object(fake_s3, f"{target_prefix}/{COPC_OBJECT_NAME}", b"delete-copc")


def _seed_rename_project(fake_s3: _GoldenFakeS3Client) -> None:
    project_prefix = "pointclouds/golden/rename_target"
    viewer_root = "golden/rename_target"
    _seed_json(
        fake_s3,
        S3_INDEX_JSON,
        {
            "projects": [{"id": "active-stays", "kunde": "Aktiv", "projekt": "Bleibt"}],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "id": "rename-target",
                    "kunde": "Alt Kunde",
                    "projekt": "Alt Projekt",
                    "format": "multi",
                    "link": "https://pointcloud.dronautix.at/index.html?id=rename-target",
                    "viewer_path": viewer_root,
                    "s3_path": project_prefix,
                    "disabled_at": "2026-06-20T12:00:00",
                    "pointcloud_count": 2,
                    "pointclouds": [
                        {
                            "name": "Cloud Alt A",
                            "format": "potree",
                            "viewer_path": f"{viewer_root}/cloud_a",
                            "s3_path": f"{project_prefix}/cloud_a",
                            "visible": True,
                        },
                        {
                            "name": "Cloud Alt B",
                            "format": "copc",
                            "viewer_path": f"{viewer_root}/cloud_b/{COPC_OBJECT_NAME}",
                            "s3_path": f"{project_prefix}/cloud_b/{COPC_OBJECT_NAME}",
                            "visible": False,
                        },
                    ],
                }
            ],
            "last_updated": "2026-06-20T12:00:00",
        },
    )


def _seed_single_replace(fake_s3: _GoldenFakeS3Client) -> str:
    project_prefix = "pointclouds/golden/replace_single"
    viewer_root = "golden/replace_single"
    target_path = f"{project_prefix}/cloud_b"
    _seed_json(
        fake_s3,
        S3_INDEX_JSON,
        {
            "projects": [
                {
                    "id": "replace-single",
                    "kunde": "Replace Kunde",
                    "projekt": "Single Replace",
                    "format": "multi",
                    "link": "https://pointcloud.dronautix.at/index.html?id=replace-single",
                    "viewer_path": viewer_root,
                    "s3_path": project_prefix,
                    "pointcloud_count": 2,
                    "pointclouds": [
                        {
                            "name": "Cloud A",
                            "format": "potree",
                            "viewer_path": f"{viewer_root}/cloud_a",
                            "s3_path": f"{project_prefix}/cloud_a",
                            "visible": False,
                        },
                        {
                            "name": "Cloud B",
                            "format": "potree",
                            "viewer_path": f"{viewer_root}/cloud_b",
                            "s3_path": target_path,
                            "visible": False,
                        },
                    ],
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [],
            "last_updated": "2026-06-20T12:00:00",
        },
    )
    _seed_s3_object(fake_s3, f"{project_prefix}/cloud_a/cloud.js", 'cloud.js = {"source":"keep"};')
    _seed_s3_object(fake_s3, f"{target_path}/cloud.js", 'cloud.js = {"source":"old-target"};')
    _seed_s3_object(fake_s3, f"{target_path}/metadata.json", '{"source":"old-target"}')
    _seed_s3_object(fake_s3, f"{target_path}/hierarchy.bin", b"old-hierarchy")
    return target_path


def _seed_multi_replace(fake_s3: _GoldenFakeS3Client) -> None:
    project_prefix = "pointclouds/golden/replace_multi"
    viewer_root = "golden/replace_multi"
    _seed_json(
        fake_s3,
        S3_INDEX_JSON,
        {
            "projects": [{"id": "active-stays", "kunde": "Aktiv", "projekt": "Bleibt"}],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "id": "replace-multi",
                    "kunde": "Replace Kunde",
                    "projekt": "Multi Replace",
                    "format": "multi",
                    "link": "https://pointcloud.dronautix.at/index.html?id=replace-multi",
                    "viewer_path": viewer_root,
                    "s3_path": project_prefix,
                    "disabled_at": "2026-06-20T12:00:00",
                    "crs": "EPSG:25832",
                    "projection": "EPSG:25832",
                    "crs_info": {"value": "EPSG:25832", "projection": "EPSG:25832"},
                    "pointcloud_count": 2,
                    "pointclouds": [
                        {"name": "Old A", "format": "potree", "s3_path": f"{project_prefix}/old_a"},
                        {
                            "name": "Old B",
                            "format": "copc",
                            "s3_path": f"{project_prefix}/old_b/{COPC_OBJECT_NAME}",
                        },
                    ],
                }
            ],
            "last_updated": "2026-06-20T12:00:00",
        },
    )
    _seed_s3_object(fake_s3, f"{project_prefix}/old_a/cloud.js", 'cloud.js = {"source":"old-a"};')
    _seed_s3_object(fake_s3, f"{project_prefix}/old_a/metadata.json", '{"source":"old-a"}')
    _seed_s3_object(fake_s3, f"{project_prefix}/old_b/{COPC_OBJECT_NAME}", b"old-copc")
    _seed_s3_object(fake_s3, f"{project_prefix}/old_orphan.bin", b"old-orphan")


def _seed_disabled_link_state(fake_s3: _GoldenFakeS3Client) -> str:
    disabled_prefix = "pointclouds/golden/disabled_target"
    disabled_viewer = "golden/disabled_target"
    target_path = f"{disabled_prefix}/{COPC_OBJECT_NAME}"
    _seed_json(
        fake_s3,
        S3_INDEX_JSON,
        {
            "projects": [
                {
                    "id": "disable-target",
                    "kunde": "Disable Kunde",
                    "projekt": "Disable Projekt",
                    "format": "copc",
                    "link": "https://pointcloud.dronautix.at/index.html?id=disable-target",
                    "viewer_path": f"golden/disable_target/{COPC_OBJECT_NAME}",
                    "s3_path": f"pointclouds/golden/disable_target/{COPC_OBJECT_NAME}",
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "id": "disabled-target",
                    "kunde": "Disabled Kunde",
                    "projekt": "Disabled Projekt",
                    "format": "copc",
                    "link": "https://pointcloud.dronautix.at/index.html?id=disabled-target",
                    "viewer_path": f"{disabled_viewer}/{COPC_OBJECT_NAME}",
                    "s3_path": target_path,
                    "disabled_at": "2026-06-20T12:00:00",
                    "crs": "EPSG:25832",
                    "projection": "EPSG:25832",
                    "crs_info": {"value": "EPSG:25832", "projection": "EPSG:25832"},
                }
            ],
            "last_updated": "2026-06-20T12:00:00",
        },
    )
    _seed_json(fake_s3, S3_DELETED_JSON, {"deleted_projects": [], "last_updated": None})
    _seed_s3_object(fake_s3, target_path, b"old-disabled-copc")
    return target_path


def _seed_json(fake_s3: _GoldenFakeS3Client, key: str, data: dict[str, Any]) -> None:
    fake_s3.objects[key] = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")


def _seed_s3_object(fake_s3: _GoldenFakeS3Client, key: str, data: str | bytes) -> None:
    fake_s3.objects[key] = data if isinstance(data, bytes) else data.encode("utf-8")


def _fake_converter_runner(source_file, converter_path, output_dir, on_progress) -> None:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    _write_potree_files(output_path, source_name=Path(source_file).stem)


def _write_potree_fixture(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _write_potree_files(path, source_name=path.name)
    return path


def _write_potree_files(path: Path, *, source_name: str) -> None:
    (path / "cloud.js").write_text(
        'cloud.js = {"spacing": 0.125, "source": ' + json.dumps(source_name, ensure_ascii=False) + "};",
        encoding="utf-8",
    )
    (path / "metadata.json").write_text(
        json.dumps(
            {
                "spacing": 0.125,
                "source": source_name,
                "points": 12345,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


__all__ = [
    "SIDE_EFFECTS_JSON",
    "SUPPORTED_V2_GOLDEN_SCENARIOS",
    "SUPPORTED_V2_PROJECT_MANAGEMENT_SCENARIOS",
    "SUPPORTED_V2_UPLOAD_SCENARIOS",
    "V2GoldenOutputScenarioResult",
    "generate_v2_golden_outputs",
]
