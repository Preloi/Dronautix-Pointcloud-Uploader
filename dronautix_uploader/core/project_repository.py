"""UI-free S3 repository for project metadata JSON objects."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .constants import (
    BUCKET_NAME,
    S3_DELETED_CACHE_CONTROL,
    S3_DELETED_JSON,
    S3_DISABLED_PROJECTS_KEY,
    S3_INDEX_CACHE_CONTROL,
    S3_INDEX_JSON,
)
from .project_index_service import PROJECTS_KEY, strip_project_ui_state

JsonObject = dict[str, Any]

PROJECTS_INDEX_DEFAULT: JsonObject = {"projects": [], "last_updated": None}
DELETED_PROJECTS_DEFAULT: JsonObject = {"deleted_projects": [], "last_updated": None}


class ProjectMetadataConflictError(RuntimeError):
    """Raised when a loaded S3 metadata snapshot is no longer current."""

    def __init__(self, key: str, current_data: JsonObject | None = None) -> None:
        super().__init__(f"S3-Metadatenkonflikt bei {key}; Daten wurden inzwischen geaendert.")
        self.key = key
        self.current_data = current_data
        self.cleanup_deferred_keys: tuple[str, ...] = ()

    def __str__(self) -> str:
        message = super().__str__()
        if self.cleanup_deferred_keys:
            return (
                f"{message} Cleanup fuer {len(self.cleanup_deferred_keys)} hochgeladene S3-Objekte wurde "
                "aus Sicherheitsgruenden ausgelassen, weil der aktuelle Index nicht verifiziert werden konnte."
            )
        return message


class ProjectMetadataWriteUncertainError(ProjectMetadataConflictError):
    """Raised when S3 may have committed a write but did not confirm it."""

    def __init__(self, key: str, current_data: JsonObject | None = None) -> None:
        RuntimeError.__init__(
            self,
            f"S3-Schreibergebnis bei {key} ist unklar; ein sicherer Cleanup ist nur nach Indexpruefung moeglich.",
        )
        self.key = key
        self.current_data = current_data
        self.cleanup_deferred_keys = ()


class _VersionedJson(dict):
    def __init__(self, *args, s3_etag: str | None, s3_exists: bool, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.s3_etag = s3_etag
        self.s3_exists = s3_exists


def _clone_default(default_data: JsonObject) -> JsonObject:
    return copy.deepcopy(default_data)


def _default_timestamp() -> str:
    return datetime.now().isoformat()


def _is_missing_object_error(s3_client: Any, error: Exception) -> bool:
    no_such_key = getattr(getattr(s3_client, "exceptions", None), "NoSuchKey", None)
    if no_such_key is not None and isinstance(error, no_such_key):
        return True

    response = getattr(error, "response", None)
    if isinstance(response, dict):
        error_code = str(response.get("Error", {}).get("Code", ""))
        return error_code in {"NoSuchKey", "404", "NotFound"}

    return error.__class__.__name__ == "NoSuchKey"


def _is_precondition_error(error: Exception) -> bool:
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", "")) in {"PreconditionFailed", "412", "ConditionalRequestConflict"}
    return error.__class__.__name__ in {"PreconditionFailed", "ConditionalRequestConflict"}


def _read_response_body(response: dict[str, Any], key: str) -> str:
    body = response.get("Body")
    raw_data = body.read() if hasattr(body, "read") else body
    if isinstance(raw_data, bytes):
        return raw_data.decode("utf-8")
    if isinstance(raw_data, str):
        return raw_data
    raise RuntimeError(f"S3 object {key} has unsupported JSON body type: {type(raw_data).__name__}")


def prepare_projects_index_for_save(index_data: JsonObject, last_updated: str | None = None) -> JsonObject:
    """Return the persistable project index without transient UI state."""

    persisted_index = copy.deepcopy(index_data) if isinstance(index_data, dict) else {"projects": []}
    if last_updated is not None:
        persisted_index["last_updated"] = last_updated

    for key in (PROJECTS_KEY, S3_DISABLED_PROJECTS_KEY):
        projects = persisted_index.get(key, [])
        if not isinstance(projects, list):
            if key == PROJECTS_KEY:
                persisted_index[key] = []
            continue
        persisted_index[key] = [
            strip_project_ui_state(project) if isinstance(project, dict) else project
            for project in projects
        ]
    return persisted_index


def prepare_deleted_projects_for_save(deleted_data: JsonObject, last_updated: str | None = None) -> JsonObject:
    persisted_deleted = copy.deepcopy(deleted_data) if isinstance(deleted_data, dict) else {"deleted_projects": []}
    if not isinstance(persisted_deleted.get("deleted_projects"), list):
        persisted_deleted["deleted_projects"] = []
    if last_updated is not None:
        persisted_deleted["last_updated"] = last_updated
    return persisted_deleted


@dataclass(frozen=True)
class ProjectMetadataRepository:
    """Load and save project metadata JSON objects via an injected S3 client."""

    s3_client: Any
    bucket_name: str = BUCKET_NAME
    projects_index_key: str = S3_INDEX_JSON
    deleted_projects_key: str = S3_DELETED_JSON
    cache_control: str = S3_INDEX_CACHE_CONTROL
    deleted_cache_control: str = S3_DELETED_CACHE_CONTROL
    timestamp_factory: Callable[[], str] = _default_timestamp

    def load_projects_index(self) -> JsonObject:
        return self.load_json(self.projects_index_key, PROJECTS_INDEX_DEFAULT)

    def save_projects_index(self, index_data: JsonObject) -> None:
        self.save_json(
            self.projects_index_key,
            prepare_projects_index_for_save(index_data, self.timestamp_factory()),
            cache_control=self.cache_control,
            source_snapshot=index_data,
        )

    def load_deleted_projects(self) -> JsonObject:
        return self.load_json(self.deleted_projects_key, DELETED_PROJECTS_DEFAULT)

    def save_deleted_projects(self, deleted_data: JsonObject) -> None:
        self.save_json(
            self.deleted_projects_key,
            prepare_deleted_projects_for_save(deleted_data, self.timestamp_factory()),
            cache_control=self.deleted_cache_control,
            source_snapshot=deleted_data,
        )

    def load_json(self, key: str, default_data: JsonObject) -> JsonObject:
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=key)
        except Exception as error:
            if _is_missing_object_error(self.s3_client, error):
                return _VersionedJson(_clone_default(default_data), s3_etag=None, s3_exists=False)
            raise

        try:
            data = json.loads(_read_response_body(response, key))
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Invalid JSON in S3 object {key}: {error}") from error

        if not isinstance(data, dict):
            raise RuntimeError(f"Invalid JSON in S3 object {key}: expected object at top level")
        etag = str(response.get("ETag", "") or "").strip()
        if not etag:
            raise RuntimeError(f"S3 object {key} liefert keinen ETag fuer sichere Aenderungen.")
        return _VersionedJson(data, s3_etag=etag, s3_exists=True)

    def save_json(
        self,
        key: str,
        data: JsonObject,
        cache_control: str | None = None,
        *,
        source_snapshot: JsonObject | None = None,
    ) -> None:
        snapshot = source_snapshot if source_snapshot is not None else data
        if not isinstance(snapshot, _VersionedJson):
            raise RuntimeError(f"S3 object {key} darf nur aus einem geladenen Snapshot gespeichert werden.")
        body = json.dumps(data, indent=2, ensure_ascii=False)
        condition = {"IfMatch": snapshot.s3_etag} if snapshot.s3_exists else {"IfNoneMatch": "*"}
        try:
            response = self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=body,
                ContentType="application/json",
                CacheControl=cache_control or self.cache_control,
                **condition,
            )
        except Exception as error:
            current_data = None
            try:
                current_data = dict(self.load_json(key, {}))
            except Exception:
                pass
            if _is_precondition_error(error):
                raise ProjectMetadataConflictError(key, current_data) from error
            raise ProjectMetadataWriteUncertainError(key, current_data) from error
        etag = str((response or {}).get("ETag", "") or "").strip()
        if not etag:
            current_data = None
            try:
                current_data = dict(self.load_json(key, {}))
            except Exception:
                pass
            raise ProjectMetadataWriteUncertainError(key, current_data)
        snapshot.s3_etag = etag
        snapshot.s3_exists = True


__all__ = [
    "DELETED_PROJECTS_DEFAULT",
    "PROJECTS_INDEX_DEFAULT",
    "ProjectMetadataConflictError",
    "ProjectMetadataWriteUncertainError",
    "ProjectMetadataRepository",
    "prepare_deleted_projects_for_save",
    "prepare_projects_index_for_save",
]
