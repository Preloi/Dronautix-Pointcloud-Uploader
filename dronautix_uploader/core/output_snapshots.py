"""Deterministic viewer-output snapshots (projects_index.json, metadata.json, cloud.js).

Every scenario runs the real upload / project-management services against an
in-memory S3 double and writes the files the web viewer reads, plus
``side_effects.json`` with the S3 calls (keys, content types, cache headers,
order). ``tests/test_output_snapshots.py`` compares them byte-for-byte with
``tests/snapshots``; any change to the viewer contract becomes a visible diff.
Update intentionally with ``python tools/update_output_snapshots.py``.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import BUCKET_NAME, S3_DELETED_JSON, S3_DISABLED_PROJECTS_KEY, S3_INDEX_JSON
from .project_management_service import ProjectManagementService
from .project_repository import ProjectMetadataRepository
from .upload_workflow_service import NewProjectUploadWorkflowRequest, UploadWorkflowService


SUPPORTED_V2_UPLOAD_SCENARIOS = (
    "single_potree_upload",
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
    # Seeded with entries in the compact index schema 2 (index_schema_version: 2).
    "schema2_single_replace",
    "schema2_multi_replace",
    "schema2_add_pointcloud",
    "schema2_link_rename",
)

SUPPORTED_SNAPSHOT_SCENARIOS = SUPPORTED_V2_UPLOAD_SCENARIOS + SUPPORTED_V2_PROJECT_MANAGEMENT_SCENARIOS
SIDE_EFFECTS_JSON = "side_effects.json"

FIXED_PROJECT_TIMESTAMP = "2026-06-21T12:00:00"
FIXED_INDEX_TIMESTAMP = "2026-06-21T12:30:00"


@dataclass(frozen=True)
class SnapshotScenarioResult:
    scenario_id: str
    output_dir: Path
    generated_files: tuple[Path, ...]
    uploaded_keys: tuple[str, ...]
    side_effects_path: Path | None = None


# Files the viewer reads per scenario.
SNAPSHOT_SCENARIOS: dict[str, tuple[str, ...]] = {
    "single_potree_upload": ("projects_index.json", "metadata.json", "cloud.js"),
    "multi_mix_upload": ("projects_index.json", "metadata.json", "cloud.js"),
    "vertical_crs_upload": ("projects_index.json", "metadata.json", "cloud.js"),
    "existing_potree_folder_upload": ("projects_index.json", "metadata.json", "cloud.js"),
    "duplicate_project": ("projects_index.json",),
    "delete_project": ("projects_index.json", "deleted_projects.json"),
    "rename_project": ("projects_index.json",),
    "single_replace": ("projects_index.json", "metadata.json", "cloud.js"),
    "multi_replace": ("projects_index.json", "metadata.json", "cloud.js"),
    "disabled_link_state": ("projects_index.json", "deleted_projects.json"),
    "schema2_single_replace": ("projects_index.json", "metadata.json", "cloud.js"),
    "schema2_multi_replace": ("projects_index.json", "metadata.json", "cloud.js"),
    "schema2_add_pointcloud": ("projects_index.json", "metadata.json", "cloud.js"),
    "schema2_link_rename": ("projects_index.json",),
}

_REPLACEMENT_SCENARIOS = frozenset(
    {"single_replace", "multi_replace", "schema2_single_replace", "schema2_multi_replace"}
)


def snapshot_scenarios(scenario_id: str | None = None) -> tuple[dict[str, Any], ...]:
    if scenario_id is None:
        ids = tuple(SNAPSHOT_SCENARIOS)
    elif scenario_id in SNAPSHOT_SCENARIOS:
        ids = (scenario_id,)
    else:
        raise ValueError(f"Unknown snapshot scenario: {scenario_id}")
    return tuple({"id": sid, "required_files": list(SNAPSHOT_SCENARIOS[sid])} for sid in ids)


def generate_output_snapshots(
    output_root: str | Path,
    *,
    scenario_id: str | None = None,
    overwrite: bool = False,
) -> tuple[SnapshotScenarioResult, ...]:
    """Generate the viewer files (+ side_effects.json) for all or one scenario."""

    output_root = Path(output_root)
    results = tuple(
        _generate_scenario(scenario, output_root=output_root, overwrite=overwrite)
        for scenario in snapshot_scenarios(scenario_id)
    )
    work_root = output_root / "_work"
    if work_root.exists():
        shutil.rmtree(work_root, ignore_errors=True)
    return results


def snapshot_file_names(scenario_id: str) -> tuple[str, ...]:
    return (*SNAPSHOT_SCENARIOS[scenario_id], SIDE_EFFECTS_JSON)


def compare_with_snapshots(generated_root: str | Path, snapshot_root: str | Path) -> list[str]:
    """Return human-readable differences; an empty list means byte-identical."""

    generated_root, snapshot_root = Path(generated_root), Path(snapshot_root)
    problems: list[str] = []
    for scenario_id in SNAPSHOT_SCENARIOS:
        for name in snapshot_file_names(scenario_id):
            generated = generated_root / scenario_id / name
            expected = snapshot_root / scenario_id / name
            if not expected.is_file():
                problems.append(f"{scenario_id}/{name}: Snapshot fehlt")
            elif not generated.is_file():
                problems.append(f"{scenario_id}/{name}: wurde nicht erzeugt")
            elif generated.read_bytes() != expected.read_bytes():
                problems.append(f"{scenario_id}/{name}: weicht vom Snapshot ab")
    return problems


def describe_snapshot_differences(generated_root: str | Path, snapshot_root: str | Path, limit: int = 3) -> str:
    """Short unified diffs of the first differing files, for test failure messages."""

    import difflib

    generated_root, snapshot_root = Path(generated_root), Path(snapshot_root)
    chunks = []
    for problem in compare_with_snapshots(generated_root, snapshot_root)[:limit]:
        relative = problem.split(":", 1)[0]
        expected, generated = snapshot_root / relative, generated_root / relative
        if not (expected.is_file() and generated.is_file()):
            chunks.append(problem)
            continue
        diff = difflib.unified_diff(
            expected.read_bytes().decode("utf-8", "replace").splitlines(),
            generated.read_bytes().decode("utf-8", "replace").splitlines(),
            fromfile=f"snapshot/{relative}",
            tofile=f"generated/{relative}",
            lineterm="",
            n=1,
        )
        lines = list(diff)[:24] or [f"{relative}: nur Bytes/Zeilenenden unterscheiden sich"]
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


def _generate_scenario(
    scenario: dict[str, Any],
    *,
    output_root: Path,
    overwrite: bool,
) -> SnapshotScenarioResult:
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
) -> SnapshotScenarioResult:
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

    fake_s3 = _SnapshotFakeS3Client()
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
            raise ValueError(f"Unsafe snapshot file name: {file_name}")
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

    return SnapshotScenarioResult(
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
) -> SnapshotScenarioResult:
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

    fake_s3 = _SnapshotFakeS3Client()
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
        data_version_factory=lambda: "golden-data",
        bucket_name=BUCKET_NAME,
    )
    _run_project_management_scenario(scenario_id, work_dir, fake_s3, service)

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_files = []
    for file_name in required_files:
        if Path(file_name).name != file_name:
            raise ValueError(f"Unsafe snapshot file name: {file_name}")
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
    return SnapshotScenarioResult(
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


class _SnapshotFakeS3Client:
    exceptions = _FakeS3Exceptions

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: list[_UploadedObject] = []
        self.copies: list[_CopiedObject] = []
        self.events: list[dict[str, Any]] = []
        self._sequence = 0
        self._etag_sequence = 0
        self._etags: dict[str, str] = {}

    def get_object(self, Bucket, Key):
        self._record_event("get_object", key=str(Key), found=Key in self.objects)
        if Key not in self.objects:
            raise _NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key]), "ETag": self._etags.setdefault(Key, '"seed"')}

    def put_object(self, Bucket, Key, Body, ContentType=None, CacheControl=None, **conditions):
        current_etag = self._etags.get(Key, '"seed"' if Key in self.objects else None)
        if conditions.get("IfMatch") is not None and conditions["IfMatch"] != current_etag:
            error = RuntimeError("PreconditionFailed")
            error.response = {"Error": {"Code": "PreconditionFailed"}}
            raise error
        if conditions.get("IfNoneMatch") == "*" and Key in self.objects:
            error = RuntimeError("PreconditionFailed")
            error.response = {"Error": {"Code": "PreconditionFailed"}}
            raise error
        if isinstance(Body, bytes):
            body = Body
        else:
            body = str(Body).encode("utf-8")
        self.objects[Key] = body
        self._etag_sequence += 1
        self._etags[Key] = f'"fake-{self._etag_sequence}"'
        self._record_event(
            "put_object",
            key=str(Key),
            content_type=ContentType,
            cache_control=CacheControl,
            body_size=len(body),
        )
        return {"ETag": self._etags[Key]}

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
        return _SnapshotFakePaginator(self)

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


class _SnapshotFakePaginator:
    def __init__(self, client: _SnapshotFakeS3Client) -> None:
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
            if str(scenario.get("id", "") or "").strip() in SUPPORTED_SNAPSHOT_SCENARIOS
        )
    selected = tuple(
        scenario
        for scenario in scenarios
        if str(scenario.get("id", "") or "").strip() == scenario_id
    )
    if not selected:
        raise ValueError(f"Unknown snapshot scenario: {scenario_id}")
    return selected


def _prepare_output_dir(output_dir: Path, required_files: tuple[str, ...], *, overwrite: bool) -> None:
    protected_files = (*required_files, SIDE_EFFECTS_JSON)
    existing_targets = [output_dir / file_name for file_name in protected_files if (output_dir / file_name).exists()]
    if existing_targets and not overwrite:
        existing_list = ", ".join(str(path) for path in existing_targets)
        raise FileExistsError(f"Snapshot output already exists: {existing_list}")
    if output_dir.exists() and overwrite:
        shutil.rmtree(output_dir)


def _required_output_text(fake_s3: _SnapshotFakeS3Client, file_name: str) -> str:
    if file_name in {S3_INDEX_JSON, S3_DELETED_JSON}:
        return fake_s3.objects[file_name].decode("utf-8")

    for upload in fake_s3.uploads:
        if os.path.basename(upload.key) == file_name:
            return fake_s3.objects[upload.key].decode("utf-8")
    raise FileNotFoundError(f"V2 scenario did not upload required file: {file_name}")


def _write_side_effects(output_dir: Path, scenario_id: str, fake_s3: _SnapshotFakeS3Client) -> Path:
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
        if scenario_id in _REPLACEMENT_SCENARIOS or scenario_id == "disabled_link_state":
            return "replacement_upload"
        if scenario_id == "schema2_add_pointcloud":
            return "pointcloud_add_upload"
        return "new_project_upload"
    if event_type == "copy_object":
        return "duplicate_copy"
    if event_type == "delete_objects":
        if scenario_id == "delete_project":
            return "project_delete"
        if scenario_id in _REPLACEMENT_SCENARIOS:
            return "orphan_cleanup"
        return "delete_objects"
    if event_type == "list_objects_v2":
        if scenario_id == "delete_project":
            return "project_delete_scan"
        if scenario_id in _REPLACEMENT_SCENARIOS or "replace" in prefix:
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


def _side_effect_summary(events: tuple[dict[str, Any], ...], fake_s3: _SnapshotFakeS3Client) -> dict[str, list[str]]:
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
        facade = _write_potree_fixture(work_dir / "Fassade Potree")
        potree = _write_potree_fixture(work_dir / "Bestand Potree")
        return {
            "project_id": "abc123e2",
            "kunde": "Golden Kunde",
            "projekt": "Multi Mix",
            "source_paths": (str(facade), str(potree)),
            "crs_info_by_source_path": {
                str(facade): {"value": "EPSG:25832", "projection": "EPSG:25832"},
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
    fake_s3: _SnapshotFakeS3Client,
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
        potree = _write_potree_fixture(work_dir / "Scan Potree")
        raw = _write_file(work_dir / "Raw.laz", b"raw")
        converter = _write_file(work_dir / "PotreeConverter.exe", b"converter")
        service.replace_project_pointclouds_from_sources(
            "replace-multi",
            (str(potree), str(raw)),
            converter_path=str(converter),
            output_base_dir=str(work_dir / "converted"),
            overwrite=True,
            converter_runner=_fake_converter_runner,
            crs_info_by_source_path={
                str(potree): {"value": "EPSG:25832", "projection": "EPSG:25832", "epsg": "EPSG:25832"},
                str(raw): {"value": "EPSG:4326", "projection": "EPSG:4326", "epsg": "EPSG:4326"},
            },
        )
        return

    if scenario_id == "disabled_link_state":
        target_path = _seed_disabled_link_state(fake_s3)
        service.set_project_link_state("disable-target", True)
        service.rename_project("disabled-target", "Disabled Kunde Neu", "Disabled Projekt Neu")
        replacement = _write_potree_fixture(work_dir / "Disabled Replacement")
        service.replace_single_project_pointcloud_from_source(
            "disabled-target",
            target_path,
            str(replacement),
            crs_info={"value": "EPSG:4326", "projection": "EPSG:4326", "epsg": "EPSG:4326"},
        )
        return

    if scenario_id == "schema2_single_replace":
        target_path = _seed_schema2_single_replace(fake_s3)
        source = _write_file(work_dir / "Schema Replacement.laz", b"raw")
        converter = _write_file(work_dir / "PotreeConverter.exe", b"converter")
        service.replace_single_project_pointcloud_from_source(
            "schema2-single",
            target_path,
            str(source),
            converter_path=str(converter),
            output_base_dir=str(work_dir / "converted"),
            overwrite=True,
            converter_runner=_fake_converter_runner,
            crs_info=dict(_SCHEMA2_CRS_INFO),
        )
        return

    if scenario_id == "schema2_multi_replace":
        _seed_schema2_multi_replace(fake_s3)
        first = _write_potree_fixture(work_dir / "Schema Scan A")
        second = _write_potree_fixture(work_dir / "Schema Scan B")
        service.replace_project_pointclouds_from_sources(
            "schema2-multi",
            (str(first), str(second)),
            crs_info_by_source_path={str(first): dict(_SCHEMA2_CRS_INFO), str(second): dict(_SCHEMA2_CRS_INFO)},
        )
        return

    if scenario_id == "schema2_add_pointcloud":
        _seed_schema2_add_pointcloud(fake_s3)
        addition = _write_potree_fixture(work_dir / "Schema Ergaenzung")
        service.add_project_pointclouds_from_sources(
            "schema2-add",
            (str(addition),),
            crs_info_by_source_path={str(addition): dict(_SCHEMA2_CRS_INFO)},
        )
        return

    if scenario_id == "schema2_link_rename":
        _seed_schema2_link_rename(fake_s3)
        service.set_project_link_state("schema2-link", True)
        service.rename_project("schema2-link", "Link Kunde Neu", "Link Projekt Neu")
        return

    raise ValueError(f"Unsupported V2 project management scenario: {scenario_id}")


def _seed_duplicate_project(fake_s3: _SnapshotFakeS3Client) -> None:
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
                            "format": "potree",
                            "viewer_path": f"{viewer_root}/cloud_b",
                            "s3_path": f"{source_prefix}/cloud_b",
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
    _seed_s3_object(fake_s3, f"{source_prefix}/cloud_b/cloud.js", 'cloud.js = {"source":"old-b"};')
    _seed_s3_object(fake_s3, f"{source_prefix}/cloud_b/metadata.json", '{"source":"old-b"}')


def _seed_delete_project(fake_s3: _SnapshotFakeS3Client) -> None:
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


def _seed_rename_project(fake_s3: _SnapshotFakeS3Client) -> None:
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
                            "format": "potree",
                            "viewer_path": f"{viewer_root}/cloud_b",
                            "s3_path": f"{project_prefix}/cloud_b",
                            "visible": False,
                        },
                    ],
                }
            ],
            "last_updated": "2026-06-20T12:00:00",
        },
    )


def _seed_single_replace(fake_s3: _SnapshotFakeS3Client) -> str:
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


def _seed_multi_replace(fake_s3: _SnapshotFakeS3Client) -> None:
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
                            "format": "potree",
                            "viewer_path": f"{viewer_root}/old_b",
                            "s3_path": f"{project_prefix}/old_b",
                        },
                    ],
                }
            ],
            "last_updated": "2026-06-20T12:00:00",
        },
    )
    _seed_s3_object(fake_s3, f"{project_prefix}/old_a/cloud.js", 'cloud.js = {"source":"old-a"};')
    _seed_s3_object(fake_s3, f"{project_prefix}/old_a/metadata.json", '{"source":"old-a"}')
    _seed_s3_object(fake_s3, f"{project_prefix}/old_b/cloud.js", 'cloud.js = {"source":"old-b"};')
    _seed_s3_object(fake_s3, f"{project_prefix}/old_b/metadata.json", '{"source":"old-b"}')
    _seed_s3_object(fake_s3, f"{project_prefix}/old_orphan.bin", b"old-orphan")


def _seed_disabled_link_state(fake_s3: _SnapshotFakeS3Client) -> str:
    disabled_prefix = "pointclouds/golden/disabled_target"
    disabled_viewer = "golden/disabled_target"
    target_path = disabled_prefix
    _seed_json(
        fake_s3,
        S3_INDEX_JSON,
        {
            "projects": [
                {
                    "id": "disable-target",
                    "kunde": "Disable Kunde",
                    "projekt": "Disable Projekt",
                    "format": "potree",
                    "link": "https://pointcloud.dronautix.at/index.html?id=disable-target",
                    "viewer_path": "golden/disable_target",
                    "s3_path": "pointclouds/golden/disable_target",
                }
            ],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "id": "disabled-target",
                    "kunde": "Disabled Kunde",
                    "projekt": "Disabled Projekt",
                    "format": "potree",
                    "link": "https://pointcloud.dronautix.at/index.html?id=disabled-target",
                    "viewer_path": disabled_viewer,
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
    _seed_s3_object(fake_s3, f"{target_path}/cloud.js", 'cloud.js = {"source":"old-disabled"};')
    _seed_s3_object(fake_s3, f"{target_path}/metadata.json", '{"source":"old-disabled"}')
    return target_path


_SCHEMA2_CRS_INFO = {
    "value": "EPSG:25832",
    "name": "ETRS89 / UTM zone 32N",
    "vertical_epsg": "EPSG:7837",
    "vertical_name": "DHHN2016 height",
    "source": "manual",
}


def _schema2_foreign_entries() -> list[dict[str, Any]]:
    """Neighbours every schema-2 scenario must pass on byte-for-byte unchanged."""

    return [
        {
            "index_schema_version": 2,
            "id": "schema2-bloated",
            "kunde": "Fremd",
            "projekt": "Von altem Uploader aufgebläht",
            "format": "potree",
            "viewer_path": "golden/schema2_bloated",
            "s3_path": "pointclouds/golden/schema2_bloated",
            "crs": "EPSG:25832",
            "projection": "EPSG:25832",
            "epsg": "EPSG:25832",
            "crs_info": {"value": "EPSG:25832", "projection": "EPSG:25832", "epsg": "EPSG:25832"},
        },
        {
            "index_schema_version": "3",
            "id": "schema-unknown",
            "kunde": "Fremd",
            "projekt": "Unbekanntes Schema",
            "format": "potree",
            "viewer_path": "golden/schema_unknown",
            "s3_path": "pointclouds/golden/schema_unknown",
            "crs": "EPSG:4326",
            "projection": "EPSG:4326",
        },
        {
            "id": "legacy-bloated",
            "kunde": "Fremd",
            "projekt": "Altprojekt",
            "format": "potree",
            "viewer_path": "golden/legacy_bloated",
            "s3_path": "pointclouds/golden/legacy_bloated",
            "crs": "EPSG:25832",
            "projection": "EPSG:25832",
            "crs_info": {"value": "EPSG:25832", "projection": "EPSG:25832"},
        },
    ]


def _seed_schema2_single_replace(fake_s3: _SnapshotFakeS3Client) -> str:
    project_prefix = "pointclouds/golden/schema2_single"
    _seed_json(
        fake_s3,
        S3_INDEX_JSON,
        {
            "projects": [
                {
                    "index_schema_version": 2,
                    "datum": "2026-06-20T09:00:00",
                    "kunde": "Schema Kunde",
                    "id": "schema2-single",
                    "projekt": "Kompakt Einzeln",
                    "format": "potree",
                    "link": "https://pointcloud.dronautix.at/index.html?id=schema2-single",
                    "viewer_path": "golden/schema2_single",
                    "s3_path": project_prefix,
                    "name": "Bestand",
                    "crs": "EPSG:25832",
                    "crs_name": "ETRS89 / UTM zone 32N",
                    "vertical_crs": "EPSG:7837",
                    "vertical_name": "DHHN2016 height",
                },
                *_schema2_foreign_entries(),
            ],
            S3_DISABLED_PROJECTS_KEY: [],
            "last_updated": "2026-06-20T12:00:00",
        },
    )
    _seed_s3_object(fake_s3, f"{project_prefix}/cloud.js", 'cloud.js = {"source":"old-single"};')
    _seed_s3_object(fake_s3, f"{project_prefix}/metadata.json", '{"source":"old-single"}')
    _seed_s3_object(fake_s3, f"{project_prefix}/hierarchy.bin", b"old-hierarchy")
    return project_prefix


def _seed_schema2_multi_replace(fake_s3: _SnapshotFakeS3Client) -> None:
    project_prefix = "pointclouds/golden/schema2_multi"
    viewer_root = "golden/schema2_multi"
    bloated_crs = {
        "crs": "EPSG:25832",
        "projection": "EPSG:25832",
        "epsg": "EPSG:25832",
        "crs_info": {"value": "EPSG:25832", "projection": "EPSG:25832", "epsg": "EPSG:25832"},
    }
    _seed_json(
        fake_s3,
        S3_INDEX_JSON,
        {
            "projects": [*_schema2_foreign_entries()],
            S3_DISABLED_PROJECTS_KEY: [
                {
                    "index_schema_version": 2,
                    "datum": "2026-06-20T09:00:00",
                    "kunde": "Schema Kunde",
                    "id": "schema2-multi",
                    "projekt": "Kompakt Mehrfach",
                    "format": "multi",
                    "link": "https://pointcloud.dronautix.at/index.html?id=schema2-multi",
                    "viewer_path": viewer_root,
                    "s3_path": project_prefix,
                    "disabled_at": "2026-06-20T12:00:00",
                    **bloated_crs,
                    "pointcloud_count": 2,
                    "pointclouds": [
                        {
                            "name": "Alt A",
                            "format": "potree",
                            "viewer_path": f"{viewer_root}/alt_a",
                            "s3_path": f"{project_prefix}/alt_a",
                            "visible": True,
                            **bloated_crs,
                        },
                        {
                            "name": "Alt B",
                            "format": "potree",
                            "viewer_path": f"{viewer_root}/alt_b",
                            "s3_path": f"{project_prefix}/alt_b",
                            "visible": False,
                            **bloated_crs,
                        },
                    ],
                }
            ],
            "last_updated": "2026-06-20T12:00:00",
        },
    )
    _seed_s3_object(fake_s3, f"{project_prefix}/alt_a/cloud.js", 'cloud.js = {"source":"alt-a"};')
    _seed_s3_object(fake_s3, f"{project_prefix}/alt_a/metadata.json", '{"source":"alt-a"}')
    _seed_s3_object(fake_s3, f"{project_prefix}/alt_b/cloud.js", 'cloud.js = {"source":"alt-b"};')
    _seed_s3_object(fake_s3, f"{project_prefix}/alt_b/metadata.json", '{"source":"alt-b"}')


def _seed_schema2_add_pointcloud(fake_s3: _SnapshotFakeS3Client) -> None:
    project_prefix = "pointclouds/golden/schema2_add"
    _seed_json(
        fake_s3,
        S3_INDEX_JSON,
        {
            "projects": [
                {
                    "index_schema_version": 2,
                    "datum": "2026-06-20T09:00:00",
                    "kunde": "Schema Kunde",
                    "id": "schema2-add",
                    "projekt": "Kompakt Erweitert",
                    "format": "potree",
                    "link": "https://pointcloud.dronautix.at/index.html?id=schema2-add",
                    "viewer_path": "golden/schema2_add",
                    "s3_path": project_prefix,
                    "name": "Bestand",
                    "crs": "EPSG:25832",
                    "crs_name": "ETRS89 / UTM zone 32N",
                    "vertical_crs": "EPSG:7837",
                    "vertical_name": "DHHN2016 height",
                    # Unknown detail: the block stays and must move with the original cloud.
                    "crs_info": {
                        "value": "EPSG:25832",
                        "name": "ETRS89 / UTM zone 32N",
                        "vertical_crs": "EPSG:7837",
                        "vertical_name": "DHHN2016 height",
                        "custom_vendor_detail": "Messkampagne 7",
                    },
                },
                *_schema2_foreign_entries(),
            ],
            S3_DISABLED_PROJECTS_KEY: [],
            "last_updated": "2026-06-20T12:00:00",
        },
    )
    _seed_s3_object(fake_s3, f"{project_prefix}/cloud.js", 'cloud.js = {"source":"bestand"};')
    _seed_s3_object(fake_s3, f"{project_prefix}/metadata.json", '{"source":"bestand"}')


def _seed_schema2_link_rename(fake_s3: _SnapshotFakeS3Client) -> None:
    _seed_json(
        fake_s3,
        S3_INDEX_JSON,
        {
            "projects": [
                {
                    # Re-inflated by an older uploader: only safe duplicates go.
                    "index_schema_version": 2,
                    "datum": "2026-06-20T09:00:00",
                    "kunde": "Link Kunde",
                    "id": "schema2-link",
                    "projekt": "Link Projekt",
                    "format": "potree",
                    "link": "https://pointcloud.dronautix.at/index.html?id=schema2-link",
                    "viewer_path": "golden/schema2_link",
                    "s3_path": "pointclouds/golden/schema2_link",
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
                        "code": "25832",
                        "name": "ETRS89 / UTM zone 32N",
                        "vertical_epsg": "EPSG:7837",
                        "vertical_name": "DHHN2016 height",
                    },
                },
                *_schema2_foreign_entries(),
            ],
            S3_DISABLED_PROJECTS_KEY: [],
            "last_updated": "2026-06-20T12:00:00",
        },
    )


def _seed_json(fake_s3: _SnapshotFakeS3Client, key: str, data: dict[str, Any]) -> None:
    fake_s3.objects[key] = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")


def _seed_s3_object(fake_s3: _SnapshotFakeS3Client, key: str, data: str | bytes) -> None:
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
    octree = source_name.encode("utf-8") or b"fixture-point"
    (path / "metadata.json").write_text(
        json.dumps(
            {
                "version": "2.0",
                "encoding": "BROTLI",
                "spacing": 0.125,
                "source": source_name,
                "points": 12345,
                "offset": [0, 0, 0],
                "scale": [0.001, 0.001, 0.001],
                "hierarchy": {"firstChunkSize": 22, "stepSize": 4, "depth": 0},
                "attributes": [{
                    "name": "position", "type": "int32", "numElements": 3,
                    "elementSize": 4, "size": 12,
                }],
                "boundingBox": {"min": [0, 0, 0], "max": [1, 1, 1]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (path / "hierarchy.bin").write_bytes(struct.pack("<BBIQQ", 1, 0, 12345, 0, len(octree)))
    (path / "octree.bin").write_bytes(octree)


def _write_file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


__all__ = [
    "SIDE_EFFECTS_JSON",
    "SUPPORTED_SNAPSHOT_SCENARIOS",
    "SUPPORTED_V2_PROJECT_MANAGEMENT_SCENARIOS",
    "SUPPORTED_V2_UPLOAD_SCENARIOS",
    "SnapshotScenarioResult",
    "compare_with_snapshots",
    "describe_snapshot_differences",
    "generate_output_snapshots",
    "snapshot_file_names",
    "snapshot_scenarios",
    "SNAPSHOT_SCENARIOS",
]
