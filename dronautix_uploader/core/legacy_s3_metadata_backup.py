"""Backup and restore productive legacy S3 metadata before Golden captures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .constants import (
    BUCKET_NAME,
    S3_DELETED_CACHE_CONTROL,
    S3_DELETED_JSON,
    S3_INDEX_CACHE_CONTROL,
    S3_INDEX_JSON,
)


BACKUP_MANIFEST_NAME = "s3_metadata_backup.json"
LEGACY_METADATA_KEYS = (S3_INDEX_JSON, S3_DELETED_JSON)


@dataclass(frozen=True)
class S3MetadataBackupEntry:
    key: str
    status: str
    path: str = ""
    size_bytes: int = 0


@dataclass(frozen=True)
class S3MetadataBackupResult:
    bucket_name: str
    backup_dir: str
    manifest_path: str
    entries: tuple[S3MetadataBackupEntry, ...]

    @property
    def saved_keys(self) -> tuple[str, ...]:
        return tuple(entry.key for entry in self.entries if entry.status == "saved")

    @property
    def missing_keys(self) -> tuple[str, ...]:
        return tuple(entry.key for entry in self.entries if entry.status == "missing")


@dataclass(frozen=True)
class S3MetadataRestoreResult:
    bucket_name: str
    backup_dir: str
    restored_keys: tuple[str, ...]
    skipped_missing_keys: tuple[str, ...] = ()
    deleted_missing_keys: tuple[str, ...] = ()


def backup_legacy_s3_metadata(
    s3_client: Any,
    backup_dir: str | Path,
    *,
    bucket_name: str = BUCKET_NAME,
    keys: tuple[str, ...] = LEGACY_METADATA_KEYS,
    now_utc: str = "",
) -> S3MetadataBackupResult:
    """Download productive metadata JSON objects into a local backup directory."""

    target_dir = Path(backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    created_at = now_utc or _utc_now_iso()
    entries: list[S3MetadataBackupEntry] = []

    for key in keys:
        _validate_metadata_key(key)
        target_path = target_dir / key
        try:
            response = s3_client.get_object(Bucket=bucket_name, Key=key)
        except Exception as exc:
            if not _is_missing_object_error(s3_client, exc):
                raise
            entries.append(S3MetadataBackupEntry(key=key, status="missing"))
            continue

        payload = _read_response_bytes(response, key)
        _validate_json_object(payload, key)
        target_path.write_bytes(payload)
        entries.append(
            S3MetadataBackupEntry(
                key=key,
                status="saved",
                path=target_path.name,
                size_bytes=len(payload),
            )
        )

    manifest_path = target_dir / BACKUP_MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_at_utc": created_at,
                "bucket_name": bucket_name,
                "objects": [entry.__dict__ for entry in entries],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return S3MetadataBackupResult(
        bucket_name=bucket_name,
        backup_dir=str(target_dir),
        manifest_path=str(manifest_path),
        entries=tuple(entries),
    )


def restore_legacy_s3_metadata(
    s3_client: Any,
    backup_dir: str | Path,
    *,
    bucket_name: str = "",
    restore_missing: bool = False,
) -> S3MetadataRestoreResult:
    """Restore metadata JSON objects from a backup manifest."""

    source_dir = Path(backup_dir)
    manifest = _load_backup_manifest(source_dir)
    target_bucket = bucket_name or str(manifest.get("bucket_name", "") or "").strip() or BUCKET_NAME
    restored: list[str] = []
    skipped_missing: list[str] = []
    deleted_missing: list[str] = []

    objects = manifest.get("objects", [])
    if not isinstance(objects, list):
        raise ValueError("Backup-Manifest objects muss eine Liste sein.")

    for item in objects:
        if not isinstance(item, dict):
            raise ValueError("Backup-Manifest objects enthaelt keinen Objekt-Eintrag.")
        key = str(item.get("key", "") or "").strip()
        status = str(item.get("status", "") or "").strip()
        _validate_metadata_key(key)
        if status == "missing":
            if restore_missing:
                _delete_metadata_key(s3_client, target_bucket, key)
                deleted_missing.append(key)
            else:
                skipped_missing.append(key)
            continue
        if status != "saved":
            raise ValueError(f"Unbekannter Backup-Status fuer {key}: {status}")
        relative_path = str(item.get("path", "") or "").strip() or key
        _validate_metadata_key(relative_path)
        payload_path = source_dir / relative_path
        if not payload_path.is_file():
            raise FileNotFoundError(f"Backup-Datei fehlt: {payload_path}")
        payload = payload_path.read_bytes()
        _validate_json_object(payload, key)
        s3_client.put_object(
            Bucket=target_bucket,
            Key=key,
            Body=payload,
            ContentType="application/json",
            CacheControl=_cache_control_for_key(key),
        )
        restored.append(key)

    return S3MetadataRestoreResult(
        bucket_name=target_bucket,
        backup_dir=str(source_dir),
        restored_keys=tuple(restored),
        skipped_missing_keys=tuple(skipped_missing),
        deleted_missing_keys=tuple(deleted_missing),
    )


def _load_backup_manifest(backup_dir: Path) -> dict[str, Any]:
    manifest_path = backup_dir / BACKUP_MANIFEST_NAME
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Backup-Manifest muss ein JSON-Objekt sein.")
    if int(data.get("schema_version", 0) or 0) != 1:
        raise ValueError("Backup-Manifest schema_version=1 fehlt.")
    return data


def _validate_metadata_key(key: str) -> None:
    if key not in LEGACY_METADATA_KEYS:
        raise ValueError(f"Unerwarteter Legacy-Metadata-Key: {key}")


def _validate_json_object(payload: bytes, key: str) -> None:
    try:
        data = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Backup-Metadaten fuer {key} sind kein gueltiges JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Backup-Metadaten fuer {key} muessen ein JSON-Objekt sein.")


def _read_response_bytes(response: dict[str, Any], key: str) -> bytes:
    body = response.get("Body")
    raw_data = body.read() if hasattr(body, "read") else body
    if isinstance(raw_data, bytes):
        return raw_data
    if isinstance(raw_data, str):
        return raw_data.encode("utf-8")
    raise RuntimeError(f"S3 object {key} has unsupported body type: {type(raw_data).__name__}")


def _is_missing_object_error(s3_client: Any, error: Exception) -> bool:
    no_such_key = getattr(getattr(s3_client, "exceptions", None), "NoSuchKey", None)
    if no_such_key is not None and isinstance(error, no_such_key):
        return True
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", "") or "")
        return code in {"NoSuchKey", "404", "NotFound"}
    return error.__class__.__name__ == "NoSuchKey"


def _delete_metadata_key(s3_client: Any, bucket_name: str, key: str) -> None:
    s3_client.delete_objects(Bucket=bucket_name, Delete={"Objects": [{"Key": key}]})


def _cache_control_for_key(key: str) -> str:
    if key == S3_DELETED_JSON:
        return S3_DELETED_CACHE_CONTROL
    return S3_INDEX_CACHE_CONTROL


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "BACKUP_MANIFEST_NAME",
    "LEGACY_METADATA_KEYS",
    "S3MetadataBackupEntry",
    "S3MetadataBackupResult",
    "S3MetadataRestoreResult",
    "backup_legacy_s3_metadata",
    "restore_legacy_s3_metadata",
]
