"""Real-S3 smoke runner for the V2 cutover acceptance gate.

The runner deliberately uses isolated metadata keys so it never writes the
productive ``projects_index.json`` or ``deleted_projects.json`` objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from typing import Any
from uuid import uuid4

from .constants import COPC_OBJECT_NAME
from .cutover_acceptance import DEFAULT_S3_ACCEPTANCE_SCENARIOS, REAL_S3_ACCEPTANCE
from .metadata_service import write_potree_metadata_crs
from .naming_service import sanitize_folder_name
from .project_management_service import ProjectManagementService
from .project_repository import ProjectMetadataRepository
from .s3_service import collect_project_objects, delete_s3_objects
from .upload_workflow_service import NewProjectUploadWorkflowRequest, UploadWorkflowService


@dataclass(frozen=True)
class S3AcceptanceSmokeConfig:
    bucket_name: str
    run_id: str = ""
    test_prefix: str = ""
    customer: str = ""
    cleanup: bool = True


@dataclass(frozen=True)
class S3AcceptanceSmokeResult:
    status: str
    completed_at_utc: str
    test_prefix: str
    project_root_prefix: str
    scenarios_passed: tuple[str, ...]
    projects_index_key: str
    deleted_projects_key: str
    uploaded_keys: tuple[str, ...]
    deleted_keys: tuple[str, ...]
    projects_index_verified: bool
    metadata_verified: bool
    cleanup_verified: bool
    notes: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "passed"


def run_v2_s3_acceptance_smoke(
    *,
    s3_client: Any,
    config: S3AcceptanceSmokeConfig,
    scenario_ids: tuple[str, ...] | list[str] = DEFAULT_S3_ACCEPTANCE_SCENARIOS,
) -> S3AcceptanceSmokeResult:
    """Run the V2 upload/project-management matrix against an isolated S3 index."""

    run_id = _safe_run_id(config.run_id or _timestamp_run_id())
    test_prefix = (config.test_prefix or f"v2-cutover-acceptance/{run_id}/").strip().lstrip("/")
    if not test_prefix.endswith("/"):
        test_prefix = f"{test_prefix}/"
    customer = config.customer or f"V2 Cutover Acceptance {run_id}"
    project_root_prefix = f"pointclouds/{sanitize_folder_name(customer)}"
    projects_index_key = f"{test_prefix}projects_index.json"
    deleted_projects_key = f"{test_prefix}deleted_projects.json"
    fenced_s3 = S3WriteFenceClient(s3_client, allowed_prefixes=(project_root_prefix, test_prefix))
    requested_scenarios = _normalize_scenarios(scenario_ids)
    completed_at_utc = _utc_timestamp()

    repository = ProjectMetadataRepository(
        fenced_s3,
        bucket_name=config.bucket_name,
        projects_index_key=projects_index_key,
        deleted_projects_key=deleted_projects_key,
        timestamp_factory=lambda: completed_at_utc,
    )
    id_factory = _sequential_id_factory()
    upload_service = UploadWorkflowService(
        repository=repository,
        s3_client=fenced_s3,
        id_factory=id_factory,
        timestamp_factory=lambda: completed_at_utc,
        bucket_name=config.bucket_name,
    )
    project_service = ProjectManagementService(
        repository=repository,
        s3_client=fenced_s3,
        id_factory=id_factory,
        timestamp_factory=lambda: completed_at_utc,
        bucket_name=config.bucket_name,
    )

    uploaded_keys: list[str] = []
    deleted_keys: list[str] = []
    scenarios_passed: list[str] = []
    with tempfile.TemporaryDirectory(prefix="v2-s3-acceptance-") as temp_dir:
        workspace = Path(temp_dir)
        context = _run_upload_scenarios(
            workspace,
            upload_service=upload_service,
            customer=customer,
            requested_scenarios=requested_scenarios,
            scenarios_passed=scenarios_passed,
            uploaded_keys=uploaded_keys,
        )
        _run_project_management_scenarios(
            workspace,
            project_service=project_service,
            context=context,
            customer=customer,
            requested_scenarios=requested_scenarios,
            scenarios_passed=scenarios_passed,
            uploaded_keys=uploaded_keys,
            deleted_keys=deleted_keys,
        )

    index_data = repository.load_projects_index()
    current_project_keys = tuple(collect_project_objects(fenced_s3, project_root_prefix, bucket_name=config.bucket_name))
    metadata_verified = _has_metadata_pair(fenced_s3, current_project_keys, bucket_name=config.bucket_name)
    projects_index_verified = bool(index_data.get("projects") or index_data.get("disabled_projects"))

    cleanup_deleted_keys: tuple[str, ...] = ()
    if config.cleanup:
        cleanup_deleted_keys = _cleanup_acceptance_keys(
            fenced_s3,
            bucket_name=config.bucket_name,
            project_root_prefix=project_root_prefix,
            test_prefix=test_prefix,
        )
        deleted_keys.extend(cleanup_deleted_keys)
    cleanup_verified = not (
        collect_project_objects(fenced_s3, project_root_prefix, bucket_name=config.bucket_name)
        or collect_project_objects(fenced_s3, test_prefix, bucket_name=config.bucket_name)
    )

    status = (
        "passed"
        if set(requested_scenarios).issubset(set(scenarios_passed))
        and projects_index_verified
        and metadata_verified
        and (cleanup_verified if config.cleanup else True)
        else "failed"
    )
    return S3AcceptanceSmokeResult(
        status=status,
        completed_at_utc=completed_at_utc,
        test_prefix=test_prefix,
        project_root_prefix=project_root_prefix,
        scenarios_passed=tuple(scenarios_passed),
        projects_index_key=projects_index_key,
        deleted_projects_key=deleted_projects_key,
        uploaded_keys=tuple(uploaded_keys),
        deleted_keys=tuple(deleted_keys),
        projects_index_verified=projects_index_verified,
        metadata_verified=metadata_verified,
        cleanup_verified=cleanup_verified,
        notes=(
            "V2 S3 smoke used isolated metadata keys; productive projects_index.json was not written."
            if status == "passed"
            else "V2 S3 smoke did not satisfy all acceptance checks."
        ),
    )


def s3_smoke_result_to_acceptance_gate(result: S3AcceptanceSmokeResult) -> dict[str, Any]:
    """Convert a successful smoke result into the real_s3_acceptance gate shape."""

    return {
        "status": "passed" if result.passed else "failed",
        "completed_at_utc": result.completed_at_utc,
        "test_prefix": result.test_prefix,
        "scenarios_passed": list(result.scenarios_passed),
        "projects_index_verified": result.projects_index_verified,
        "metadata_verified": result.metadata_verified,
        "cleanup_verified": result.cleanup_verified,
        "notes": result.notes,
    }


def merge_s3_smoke_into_acceptance_evidence(evidence: dict[str, Any], result: S3AcceptanceSmokeResult) -> dict[str, Any]:
    """Return acceptance evidence with the S3 gate replaced by the smoke result."""

    merged = json.loads(json.dumps(evidence or {}, ensure_ascii=False))
    gates = merged.setdefault("gates", {})
    gates[REAL_S3_ACCEPTANCE] = s3_smoke_result_to_acceptance_gate(result)
    return merged


class S3WriteFenceClient:
    """Small S3 wrapper that rejects all access outside acceptance prefixes."""

    def __init__(self, s3_client: Any, *, allowed_prefixes: tuple[str, ...]) -> None:
        self._s3_client = s3_client
        self._allowed_prefixes = tuple(prefix.strip().strip("/") for prefix in allowed_prefixes if prefix.strip())
        self.exceptions = getattr(s3_client, "exceptions", None)

    def get_object(self, **kwargs):
        self._require_allowed_key(str(kwargs.get("Key", "") or ""), operation="get_object")
        return self._s3_client.get_object(**kwargs)

    def put_object(self, **kwargs):
        self._require_allowed_key(str(kwargs.get("Key", "") or ""), operation="put_object")
        return self._s3_client.put_object(**kwargs)

    def upload_file(self, local_path, bucket, key, **kwargs):
        self._require_allowed_key(str(key or ""), operation="upload_file")
        return self._s3_client.upload_file(local_path, bucket, key, **kwargs)

    def copy_object(self, **kwargs):
        copy_source = kwargs.get("CopySource", {})
        source_key = str(copy_source.get("Key", "") or "") if isinstance(copy_source, dict) else ""
        self._require_allowed_key(source_key, operation="copy_object source")
        self._require_allowed_key(str(kwargs.get("Key", "") or ""), operation="copy_object target")
        return self._s3_client.copy_object(**kwargs)

    def delete_objects(self, **kwargs):
        delete = kwargs.get("Delete", {})
        objects = delete.get("Objects", []) if isinstance(delete, dict) else ()
        for item in objects:
            key = str(item.get("Key", "") or "") if isinstance(item, dict) else ""
            self._require_allowed_key(key, operation="delete_objects")
        return self._s3_client.delete_objects(**kwargs)

    def get_paginator(self, name):
        return _S3WriteFencePaginator(
            self._s3_client.get_paginator(name),
            key_checker=self._require_allowed_key,
        )

    def _require_allowed_key(self, key: str, *, operation: str) -> None:
        normalized = str(key or "").strip().strip("/")
        if not normalized:
            raise RuntimeError(f"S3 smoke write fence rejected empty key for {operation}.")
        if any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in self._allowed_prefixes):
            return
        raise RuntimeError(f"S3 smoke write fence rejected {operation} outside test prefixes: {key}")


class _S3WriteFencePaginator:
    def __init__(self, paginator, *, key_checker) -> None:
        self._paginator = paginator
        self._key_checker = key_checker

    def paginate(self, **kwargs):
        self._key_checker(str(kwargs.get("Prefix", "") or ""), operation="list_objects_v2")
        return self._paginator.paginate(**kwargs)


def _run_upload_scenarios(
    workspace: Path,
    *,
    upload_service: UploadWorkflowService,
    customer: str,
    requested_scenarios: tuple[str, ...],
    scenarios_passed: list[str],
    uploaded_keys: list[str],
) -> dict[str, Any]:
    converter_path = _write_file(workspace / "PotreeConverter.exe", b"acceptance-converter")
    output_base_dir = workspace / "converted"
    context: dict[str, Any] = {}

    if "single_potree_upload" in requested_scenarios:
        source = _write_file(workspace / "Single Potree.laz", b"raw")
        result = upload_service.upload_new_project(
            NewProjectUploadWorkflowRequest(
                source_paths=(str(source),),
                kunde=customer,
                projekt="Single Potree",
                converter_path=str(converter_path),
                output_base_dir=str(output_base_dir),
                overwrite=True,
                crs_info_by_source_path={
                    str(source): {"value": "EPSG:25832", "projection": "EPSG:25832", "epsg": "EPSG:25832"}
                },
            ),
            converter_runner=_smoke_converter_runner,
        )
        _record_success("single_potree_upload", result, scenarios_passed, uploaded_keys)
        context["single_potree_upload"] = result

    if "single_copc_upload" in requested_scenarios:
        source = _write_file(workspace / "Single COPC.copc.laz", b"copc")
        result = upload_service.upload_new_project(
            NewProjectUploadWorkflowRequest(
                source_paths=(str(source),),
                kunde=customer,
                projekt="Single COPC",
                crs_info_by_source_path={str(source): {"value": "EPSG:25832", "projection": "EPSG:25832"}},
            )
        )
        _record_success("single_copc_upload", result, scenarios_passed, uploaded_keys)
        context["single_copc_upload"] = result

    if "multi_mix_upload" in requested_scenarios or {"duplicate_project", "multi_replace"} & set(requested_scenarios):
        copc = _write_file(workspace / "Fassade.copc.laz", b"copc")
        potree = _write_potree_fixture(workspace / "Bestand Potree", source_name="Bestand Potree")
        result = upload_service.upload_new_project(
            NewProjectUploadWorkflowRequest(
                source_paths=(str(copc), str(potree)),
                kunde=customer,
                projekt="Multi Mix",
                crs_info_by_source_path={
                    str(copc): {"value": "EPSG:25832", "projection": "EPSG:25832"},
                    str(potree): {"value": "EPSG:25832", "projection": "EPSG:25832"},
                },
            )
        )
        if "multi_mix_upload" in requested_scenarios:
            _record_success("multi_mix_upload", result, scenarios_passed, uploaded_keys)
        else:
            uploaded_keys.extend(result.uploaded_keys)
        context["multi_mix_upload"] = result

    if "vertical_crs_upload" in requested_scenarios:
        source = _write_file(workspace / "Vertical CRS.laz", b"raw")
        result = upload_service.upload_new_project(
            NewProjectUploadWorkflowRequest(
                source_paths=(str(source),),
                kunde=customer,
                projekt="Vertical CRS",
                converter_path=str(converter_path),
                output_base_dir=str(output_base_dir),
                overwrite=True,
                crs_info_by_source_path={
                    str(source): {
                        "value": "EPSG:25832",
                        "projection": "EPSG:25832",
                        "epsg": "EPSG:25832",
                        "vertical_epsg": "EPSG:7837",
                        "vertical_name": "DHHN2016",
                    }
                },
            ),
            converter_runner=_smoke_converter_runner,
        )
        _record_success("vertical_crs_upload", result, scenarios_passed, uploaded_keys)
        context["vertical_crs_upload"] = result

    if "existing_potree_folder_upload" in requested_scenarios or "disabled_link_state" in requested_scenarios:
        potree = _write_potree_fixture(workspace / "Existing Potree", source_name="Existing Potree")
        result = upload_service.upload_new_project(
            NewProjectUploadWorkflowRequest(
                source_paths=(str(potree),),
                kunde=customer,
                projekt="Existing Potree",
                crs_info_by_source_path={str(potree): {"value": "EPSG:4326", "projection": "EPSG:4326"}},
            )
        )
        if "existing_potree_folder_upload" in requested_scenarios:
            _record_success("existing_potree_folder_upload", result, scenarios_passed, uploaded_keys)
        else:
            uploaded_keys.extend(result.uploaded_keys)
        context["existing_potree_folder_upload"] = result

    return context


def _run_project_management_scenarios(
    workspace: Path,
    *,
    project_service: ProjectManagementService,
    context: dict[str, Any],
    customer: str,
    requested_scenarios: tuple[str, ...],
    scenarios_passed: list[str],
    uploaded_keys: list[str],
    deleted_keys: list[str],
) -> None:
    if "duplicate_project" in requested_scenarios or "delete_project" in requested_scenarios:
        source = context["multi_mix_upload"]
        result = project_service.duplicate_project(source.project_id, customer, "Duplicate Clone")
        if "duplicate_project" in requested_scenarios:
            _record_success("duplicate_project", result, scenarios_passed, uploaded_keys)
        else:
            uploaded_keys.extend(result.uploaded_keys)
        context["duplicate_project"] = result

    if "delete_project" in requested_scenarios:
        target = context.get("duplicate_project") or context["multi_mix_upload"]
        result = project_service.delete_project(target.project_id)
        _record_success("delete_project", result, scenarios_passed, uploaded_keys)
        deleted_keys.extend(result.deleted_keys)

    if "rename_project" in requested_scenarios:
        target = context.get("single_copc_upload") or context["multi_mix_upload"]
        result = project_service.rename_project(target.project_id, customer, "Renamed Project")
        _record_success("rename_project", result, scenarios_passed, uploaded_keys)

    if "single_replace" in requested_scenarios:
        target = context["single_potree_upload"]
        source = _write_file(workspace / "Single Replacement.laz", b"replacement")
        converter = _write_file(workspace / "ReplacementConverter.exe", b"converter")
        result = project_service.replace_single_project_pointcloud_from_source(
            target.project_id,
            target.s3_prefix,
            str(source),
            converter_path=str(converter),
            output_base_dir=str(workspace / "replacement-converted"),
            overwrite=True,
            converter_runner=_smoke_converter_runner,
            crs_info={"value": "EPSG:25832", "projection": "EPSG:25832", "epsg": "EPSG:25832"},
        )
        _record_success("single_replace", result, scenarios_passed, uploaded_keys)
        deleted_keys.extend(result.deleted_keys)

    if "multi_replace" in requested_scenarios:
        target = context["multi_mix_upload"]
        copc = _write_file(workspace / "Multi Replacement.copc.laz", b"copc")
        raw = _write_file(workspace / "Multi Replacement Raw.laz", b"raw")
        converter = _write_file(workspace / "MultiReplacementConverter.exe", b"converter")
        result = project_service.replace_project_pointclouds_from_sources(
            target.project_id,
            (str(copc), str(raw)),
            converter_path=str(converter),
            output_base_dir=str(workspace / "multi-replacement-converted"),
            overwrite=True,
            converter_runner=_smoke_converter_runner,
            crs_info_by_source_path={
                str(copc): {"value": "EPSG:25832", "projection": "EPSG:25832", "epsg": "EPSG:25832"},
                str(raw): {"value": "EPSG:4326", "projection": "EPSG:4326", "epsg": "EPSG:4326"},
            },
        )
        _record_success("multi_replace", result, scenarios_passed, uploaded_keys)
        deleted_keys.extend(result.deleted_keys)

    if "disabled_link_state" in requested_scenarios:
        target = context["existing_potree_folder_upload"]
        project_service.set_project_link_state(target.project_id, True)
        project_service.rename_project(target.project_id, customer, "Disabled Renamed")
        replacement = _write_file(workspace / "Disabled Replacement.copc.laz", b"copc")
        result = project_service.replace_single_project_pointcloud_from_source(
            target.project_id,
            target.s3_prefix,
            str(replacement),
            crs_info={"value": "EPSG:4326", "projection": "EPSG:4326", "epsg": "EPSG:4326"},
        )
        _record_success("disabled_link_state", result, scenarios_passed, uploaded_keys)
        deleted_keys.extend(result.deleted_keys)


def _record_success(scenario_id: str, result: Any, scenarios_passed: list[str], uploaded_keys: list[str]) -> None:
    if getattr(result, "status", "") != "success":
        raise RuntimeError(f"S3 smoke scenario failed: {scenario_id} ({getattr(result, 'message', '')})")
    scenarios_passed.append(scenario_id)
    uploaded_keys.extend(tuple(getattr(result, "uploaded_keys", ()) or ()))


def _has_metadata_pair(s3_client: Any, keys: tuple[str, ...], *, bucket_name: str) -> bool:
    metadata_keys = [key for key in keys if key.endswith("/metadata.json")]
    cloudjs_keys = [key for key in keys if key.endswith("/cloud.js")]
    if not metadata_keys or not cloudjs_keys:
        return False
    for key in (*metadata_keys[:1], *cloudjs_keys[:1]):
        try:
            s3_client.get_object(Bucket=bucket_name, Key=key)
        except Exception:
            return False
    return True


def _cleanup_acceptance_keys(
    s3_client: Any,
    *,
    bucket_name: str,
    project_root_prefix: str,
    test_prefix: str,
) -> tuple[str, ...]:
    keys = tuple(
        dict.fromkeys(
            [
                *collect_project_objects(s3_client, project_root_prefix, bucket_name=bucket_name),
                *collect_project_objects(s3_client, test_prefix, bucket_name=bucket_name),
            ]
        )
    )
    if keys:
        delete_s3_objects(s3_client, keys, bucket_name=bucket_name)
    return keys


def _smoke_converter_runner(source_file, converter_path, output_dir, on_progress) -> None:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    _write_potree_files(output_path, source_name=Path(source_file).stem)


def _write_potree_fixture(path: Path, *, source_name: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _write_potree_files(path, source_name=source_name)
    return path


def _write_potree_files(path: Path, *, source_name: str) -> None:
    (path / "cloud.js").write_text(
        'cloud.js = {"spacing": 0.125, "source": ' + json.dumps(source_name, ensure_ascii=False) + "};",
        encoding="utf-8",
    )
    (path / "metadata.json").write_text(
        json.dumps({"spacing": 0.125, "source": source_name, "points": 12345}, ensure_ascii=False),
        encoding="utf-8",
    )
    write_potree_metadata_crs(
        path / "metadata.json",
        {"value": "EPSG:25832", "projection": "EPSG:25832", "epsg": "EPSG:25832"},
    )


def _write_file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _sequential_id_factory():
    counter = {"value": 0}

    def next_id() -> str:
        counter["value"] += 1
        return f"v2s{counter['value']:05d}"

    return next_id


def _normalize_scenarios(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    requested = []
    seen = set()
    for value in values:
        scenario = str(value or "").strip()
        if not scenario or scenario in seen:
            continue
        requested.append(scenario)
        seen.add(scenario)
    unknown = sorted(set(requested) - set(DEFAULT_S3_ACCEPTANCE_SCENARIOS))
    if unknown:
        raise ValueError(f"Unknown S3 acceptance scenario(s): {', '.join(unknown)}")
    return tuple(requested)


def _safe_run_id(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in str(value or "").strip())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned or uuid4().hex[:12]


def _timestamp_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "S3AcceptanceSmokeConfig",
    "S3AcceptanceSmokeResult",
    "S3WriteFenceClient",
    "merge_s3_smoke_into_acceptance_evidence",
    "run_v2_s3_acceptance_smoke",
    "s3_smoke_result_to_acceptance_gate",
]
