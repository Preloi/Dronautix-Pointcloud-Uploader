"""UI-free S3 upload helpers with explicit rollback accounting."""

from __future__ import annotations

import mimetypes
import os
import re

from .constants import BUCKET_NAME, COPC_OBJECT_NAME, S3_CACHE_CONTROL, S3_DELETE_BATCH_SIZE
from .contracts import (
    CancelCallback,
    OperationCancelledError,
    ProgressCallback,
    ProgressEvent,
    UploadedKeyLedger,
)

UploadFile = tuple[str, str]


class DownloadCancelledError(RuntimeError):
    """Raised when a project download is cancelled by the caller."""

    def __init__(self, downloaded_paths: tuple[str, ...] = ()) -> None:
        super().__init__("Download wurde abgebrochen.")
        self.downloaded_paths = downloaded_paths


def get_total_size(files_list: list[str] | tuple[str, ...]) -> int:
    total = 0
    for file_path in files_list:
        if os.path.exists(file_path):
            total += os.path.getsize(file_path)
    return total


def format_bytes(bytes_size: int) -> str:
    if bytes_size < 1024:
        return f"{bytes_size} B"
    if bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.1f} KB"
    if bytes_size < 1024 * 1024 * 1024:
        return f"{bytes_size / (1024 * 1024):.1f} MB"
    return f"{bytes_size / (1024 * 1024 * 1024):.1f} GB"


def collect_upload_files(
    input_format: str,
    s3_prefix: str,
    source_file: str | None = None,
    output_dir: str | None = None,
) -> list[UploadFile]:
    """Collect local files and S3 keys with legacy ordering and COPC naming."""

    files_to_upload: list[UploadFile] = []
    if input_format == "copc":
        if not source_file:
            return files_to_upload
        files_to_upload.append((source_file, f"{s3_prefix}/{COPC_OBJECT_NAME}"))
        return files_to_upload

    if not output_dir:
        return files_to_upload

    for root_dir, _dirs, files in os.walk(output_dir):
        for file in files:
            local_path = os.path.join(root_dir, file)
            rel_path = os.path.relpath(local_path, output_dir)
            s3_key = f"{s3_prefix}/{rel_path}".replace("\\", "/")
            files_to_upload.append((local_path, s3_key))

    files_to_upload.sort(
        key=lambda item: (os.path.basename(item[1]).lower() == "metadata.json", item[1].lower())
    )
    return files_to_upload


def _emit(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    if callback:
        callback(event)


def _cancel_requested(callback: CancelCallback | None) -> bool:
    if callback is None:
        return False
    try:
        return bool(callback())
    except Exception:
        return False


def upload_files_to_s3(
    s3_client,
    files_to_upload: list[UploadFile] | tuple[UploadFile, ...],
    bucket_name: str = BUCKET_NAME,
    on_progress: ProgressCallback | None = None,
    ledger: UploadedKeyLedger | None = None,
    cancel_requested: CancelCallback | None = None,
) -> UploadedKeyLedger:
    """Upload files and record a key only after upload_file completed."""

    if not files_to_upload:
        raise RuntimeError("Keine Dateien zum Upload gefunden")

    upload_ledger = ledger or UploadedKeyLedger()
    total_size = get_total_size(tuple(file_path for file_path, _ in files_to_upload))
    _emit(
        on_progress,
        ProgressEvent(
            kind="log",
            message=f"[UPLOAD] {len(files_to_upload)} Dateien ({format_bytes(total_size)})",
        ),
    )

    uploaded_total = 0

    for idx, (local_path, s3_key) in enumerate(files_to_upload, 1):
        if _cancel_requested(cancel_requested):
            raise OperationCancelledError("Upload wurde abgebrochen.")
        file_size = os.path.getsize(local_path)
        _emit(
            on_progress,
            ProgressEvent(
                kind="log",
                message=f"[{idx}/{len(files_to_upload)}] {os.path.basename(local_path)} ({format_bytes(file_size)})",
            ),
        )

        content_type, _ = mimetypes.guess_type(local_path)
        if not content_type:
            content_type = "application/octet-stream"

        # boto3 liefert pro Callback-Aufruf das Chunk-Inkrement, nicht die
        # Gesamtsumme; ohne Akkumulation bleibt der Balken bei grossen Dateien
        # scheinbar stehen.
        file_progress = {"bytes": 0, "last_fraction": -1.0}

        def update_upload_progress(bytes_chunk, _state=file_progress, _base=uploaded_total):
            if _cancel_requested(cancel_requested):
                raise OperationCancelledError("Upload wurde abgebrochen.")
            if total_size <= 0:
                return
            _state["bytes"] += int(bytes_chunk or 0)
            current = _base + _state["bytes"]
            fraction = min(current / total_size, 1.0)
            if fraction < 1.0 and fraction - _state["last_fraction"] < 0.002:
                return
            _state["last_fraction"] = fraction
            _emit(
                on_progress,
                ProgressEvent(
                    kind="progress",
                    percent=fraction,
                    detail=f"{format_bytes(current)} / {format_bytes(total_size)}",
                ),
            )

        try:
            s3_client.upload_file(
                local_path,
                bucket_name,
                s3_key,
                ExtraArgs={
                    "ContentType": content_type,
                    "CacheControl": S3_CACHE_CONTROL,
                },
                Callback=update_upload_progress,
            )
        except OperationCancelledError:
            raise
        except Exception:
            # boto3/s3transfer kann Callback-Exceptions einpacken; einen
            # angeforderten Abbruch als solchen normalisieren.
            if _cancel_requested(cancel_requested):
                raise OperationCancelledError("Upload wurde abgebrochen.")
            raise
        upload_ledger.record(s3_key)
        uploaded_total += file_size

    _emit(on_progress, ProgressEvent(kind="progress", percent=1.0))
    _emit(on_progress, ProgressEvent(kind="log", message="[UPLOAD] Alle Dateien hochgeladen"))
    return upload_ledger


def collect_project_object_entries(
    s3_client,
    s3_path: str,
    bucket_name: str = BUCKET_NAME,
) -> list[dict[str, int | str]]:
    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket_name, Prefix=s3_path)
    object_entries: list[dict[str, int | str]] = []
    for page in pages:
        for obj in page.get("Contents", []):
            object_key = obj.get("Key")
            if not object_key:
                continue
            object_entries.append({"Key": object_key, "Size": int(obj.get("Size", 0) or 0)})
    return object_entries


def collect_project_objects(s3_client, s3_path: str, bucket_name: str = BUCKET_NAME) -> list[str]:
    return [
        str(entry["Key"])
        for entry in collect_project_object_entries(s3_client, s3_path, bucket_name=bucket_name)
    ]


def delete_s3_objects(
    s3_client,
    object_keys: list[str] | tuple[str, ...],
    bucket_name: str = BUCKET_NAME,
) -> int:
    if not object_keys:
        return 0

    deleted_count = 0
    for start_index in range(0, len(object_keys), S3_DELETE_BATCH_SIZE):
        batch_keys = list(object_keys[start_index : start_index + S3_DELETE_BATCH_SIZE])
        response = s3_client.delete_objects(
            Bucket=bucket_name,
            Delete={"Objects": [{"Key": key} for key in batch_keys]},
        )
        errors = response.get("Errors", [])
        if errors:
            first_error = errors[0]
            raise RuntimeError(
                f"S3 DeleteObjects Fehler für {first_error.get('Key', 'unbekannt')}: "
                f"{first_error.get('Code', 'Unknown')} - {first_error.get('Message', '')}"
            )
        deleted_count += len(batch_keys)
    return deleted_count


def build_safe_download_path(base_dir: str, s3_prefix: str, object_key: str) -> str:
    relative_path = object_key[len(s3_prefix) :] if object_key.startswith(s3_prefix) else os.path.basename(object_key)
    relative_path = relative_path.lstrip("/\\")
    safe_parts = []
    for path_part in re.split(r"[/\\]+", relative_path):
        if not path_part or path_part in (".", ".."):
            continue
        safe_parts.append(path_part)
    if not safe_parts:
        fallback_name = os.path.basename(object_key.rstrip("/\\"))
        if not fallback_name:
            return ""
        safe_parts.append(fallback_name)
    return os.path.join(base_dir, *safe_parts)


def copy_project_objects(
    s3_client,
    source_keys: list[str] | tuple[str, ...],
    source_prefix: str,
    destination_prefix: str,
    bucket_name: str = BUCKET_NAME,
    on_progress: ProgressCallback | None = None,
    source_sizes: dict[str, int] | None = None,
) -> tuple[str, ...]:
    copied_keys: list[str] = []
    source_prefix = source_prefix.rstrip("/")
    destination_prefix = destination_prefix.rstrip("/")
    total = len(source_keys)
    sizes = dict(source_sizes or {})
    total_bytes = sum(int(sizes.get(key, 0) or 0) for key in source_keys)
    copied_bytes = 0
    throttle = {"last_fraction": -1.0}

    def emit_copy_progress(current_bytes: int, index: int) -> None:
        # Byte-gewichtet: ein Potree-Projekt besteht aus wenigen Dateien, von
        # denen octree.bin fast die gesamte Groesse ausmacht; ein reiner
        # Dateizaehler bliebe dort minutenlang stehen.
        fraction = min(current_bytes / total_bytes, 1.0) if total_bytes > 0 else index / total
        if fraction < 1.0 and fraction - throttle["last_fraction"] < 0.002:
            return
        throttle["last_fraction"] = fraction
        detail = (
            f"{format_bytes(min(current_bytes, total_bytes))} / {format_bytes(total_bytes)}"
            if total_bytes > 0
            else ""
        )
        _emit(
            on_progress,
            ProgressEvent(
                kind="progress",
                percent=fraction,
                message=f"Kopiere Dateien... ({index}/{total})",
                detail=detail,
            ),
        )

    managed_copy = getattr(s3_client, "copy", None)
    for index, source_key in enumerate(source_keys, start=1):
        rel_path = source_key[len(source_prefix) :] if source_key.startswith(source_prefix) else ""
        rel_path = rel_path.lstrip("/")
        destination_key = (
            f"{destination_prefix}/{rel_path}"
            if rel_path
            else f"{destination_prefix}/{os.path.basename(source_key)}"
        )
        file_size = int(sizes.get(source_key, 0) or 0)
        if on_progress is not None and callable(managed_copy) and total_bytes > 0 and file_size > 0:
            # Managed Copy meldet Byte-Chunks auch waehrend einer einzelnen
            # grossen Datei, statt erst nach deren Abschluss.
            file_progress = {"bytes": 0}

            def report_copy_chunk(bytes_chunk, _state=file_progress, _base=copied_bytes, _index=index):
                _state["bytes"] += int(bytes_chunk or 0)
                emit_copy_progress(_base + _state["bytes"], _index)

            managed_copy(
                {"Bucket": bucket_name, "Key": source_key},
                bucket_name,
                destination_key,
                ExtraArgs={"CacheControl": S3_CACHE_CONTROL, "MetadataDirective": "REPLACE"},
                Callback=report_copy_chunk,
            )
        else:
            s3_client.copy_object(
                Bucket=bucket_name,
                CopySource={"Bucket": bucket_name, "Key": source_key},
                Key=destination_key,
                CacheControl=S3_CACHE_CONTROL,
                MetadataDirective="REPLACE",
            )
        copied_keys.append(destination_key)
        copied_bytes += file_size
        emit_copy_progress(copied_bytes, index)
    return tuple(copied_keys)


def download_project_objects(
    s3_client,
    object_entries: list[dict[str, int | str]] | tuple[dict[str, int | str], ...],
    source_s3_path: str,
    download_dir: str,
    bucket_name: str = BUCKET_NAME,
    on_progress: ProgressCallback | None = None,
    cancel_requested: CancelCallback | None = None,
) -> tuple[str, ...]:
    downloaded_paths: list[str] = []
    total_files = len(object_entries)
    total_bytes = sum(int(entry.get("Size", 0) or 0) for entry in object_entries)
    downloaded_bytes = 0
    throttle = {"last_fraction": -1.0}

    for index, entry in enumerate(object_entries, start=1):
        if _cancel_requested(cancel_requested):
            _emit(on_progress, ProgressEvent(kind="warning", message="Download wurde abgebrochen."))
            raise DownloadCancelledError(tuple(downloaded_paths))

        object_key = str(entry.get("Key", ""))
        if not object_key or object_key.endswith("/"):
            continue
        local_path = build_safe_download_path(download_dir, source_s3_path, object_key)
        if not local_path:
            continue
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        def progress_callback(bytes_amount):
            nonlocal downloaded_bytes
            downloaded_bytes += bytes_amount
            if total_bytes > 0:
                fraction = 0.1 + 0.85 * min(downloaded_bytes / total_bytes, 1.0)
                # Gedrosselt emittieren, damit grosse Downloads die GUI nicht
                # mit Tausenden Chunk-Events fluten; Abbruch wird trotzdem
                # bei jedem Chunk geprueft.
                if fraction - throttle["last_fraction"] >= 0.002:
                    throttle["last_fraction"] = fraction
                    _emit(
                        on_progress,
                        ProgressEvent(
                            kind="progress",
                            percent=fraction,
                            detail=f"{format_bytes(min(downloaded_bytes, total_bytes))} / {format_bytes(total_bytes)}",
                        ),
                    )
            if _cancel_requested(cancel_requested):
                _emit(on_progress, ProgressEvent(kind="warning", message="Download wurde abgebrochen."))
                raise DownloadCancelledError(tuple(downloaded_paths))

        _emit(
            on_progress,
            ProgressEvent(
                kind="detail",
                detail=f"Lade Datei {index}/{total_files}: {os.path.basename(local_path)}",
            ),
        )
        try:
            s3_client.download_file(bucket_name, object_key, local_path, Callback=progress_callback)
        except DownloadCancelledError:
            if os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except OSError:
                    pass
            raise
        if total_bytes <= 0 and total_files:
            _emit(
                on_progress,
                ProgressEvent(kind="progress", percent=0.1 + 0.85 * (index / total_files)),
            )
        downloaded_paths.append(local_path)

    _emit(on_progress, ProgressEvent(kind="progress", percent=1.0))
    return tuple(downloaded_paths)
