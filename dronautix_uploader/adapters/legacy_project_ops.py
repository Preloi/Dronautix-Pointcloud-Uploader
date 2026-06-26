"""Legacy operation-name adapter backed by the V2 core service API."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import os
from typing import Any, Callable

from dronautix_uploader.core.contracts import (
    DownloadRequest,
    MultiReplacementRequest,
    PointcloudSource,
    ProjectMetadataUpdate,
    ReplacementRequest,
    UploadRequest,
)
from dronautix_uploader.core.service_api import CoreServiceApi


@dataclass(frozen=True)
class LegacyProjectOpsAdapter:
    """Expose old process-style operation names without importing Tk globals."""

    core_api: CoreServiceApi | None = None
    core_api_factory: Callable[[str, str], CoreServiceApi] | None = None
    converter_path: str = ""
    output_base_dir: str = ""
    on_progress: Any = None
    converter_runner: Any = None

    def run_process(
        self,
        laz_file,
        kunde,
        projekt,
        aws_access,
        aws_secret,
        crs_input="",
        vertical_input="",
        on_success=None,
        overwrite=False,
    ):
        return self.run_multi_upload_process(
            normalize_legacy_sources(laz_file),
            kunde,
            projekt,
            aws_access,
            aws_secret,
            self.converter_path,
            self.output_base_dir,
            crs_input,
            vertical_input,
            on_success=on_success,
            overwrite=overwrite,
        )

    def run_multi_upload_process(
        self,
        upload_sources,
        kunde,
        projekt,
        aws_access,
        aws_secret,
        converter_path="",
        output_base_dir="",
        crs_input="",
        vertical_input="",
        on_success=None,
        overwrite=False,
    ):
        api = self._core_api(aws_access, aws_secret)
        result = api.upload_project(
            UploadRequest(
                sources=tuple(PointcloudSource(source_path=source) for source in normalize_legacy_sources(upload_sources)),
                kunde=str(kunde or "").strip(),
                projekt=str(projekt or "").strip(),
                aws_access=str(aws_access or "").strip(),
                aws_secret=str(aws_secret or "").strip(),
                converter_path=str(converter_path or self.converter_path or ""),
                output_base_dir=str(output_base_dir or self.output_base_dir or ""),
                crs_input=str(crs_input or ""),
                vertical_input=str(vertical_input or ""),
                overwrite=bool(overwrite),
            ),
            on_progress=self._progress_callback(),
            converter_runner=self.converter_runner,
        )
        _invoke_success(on_success)
        return result

    def replace_project_process(
        self,
        project_info,
        replacement_file,
        aws_access,
        aws_secret,
        on_success=None,
        ui=None,
        crs_input="",
        vertical_input="",
        target_pointcloud=None,
        overwrite=False,
    ):
        api = self._core_api(aws_access, aws_secret)
        result = api.replace_pointcloud(
            ReplacementRequest(
                project=dict(project_info or {}),
                replacement=PointcloudSource(source_path=str(replacement_file or "").strip()),
                aws_access=str(aws_access or "").strip(),
                aws_secret=str(aws_secret or "").strip(),
                target_pointcloud=dict(target_pointcloud) if isinstance(target_pointcloud, dict) else None,
                converter_path=self.converter_path,
                output_base_dir=self.output_base_dir,
                crs_input=str(crs_input or ""),
                vertical_input=str(vertical_input or ""),
                overwrite=bool(overwrite),
            ),
            on_progress=self._progress_callback(ui),
            converter_runner=self.converter_runner,
        )
        _invoke_success(on_success)
        return result

    def replace_project_with_multi_pointclouds(
        self,
        project_info,
        replacement_entries,
        aws_access,
        aws_secret,
        on_success=None,
        ui=None,
        crs_input="",
        vertical_input="",
        overwrite=False,
    ):
        api = self._core_api(aws_access, aws_secret)
        result = api.replace_pointclouds(
            MultiReplacementRequest(
                project=dict(project_info or {}),
                replacements=tuple(_pointcloud_source_from_replacement_entry(entry) for entry in replacement_entries or ()),
                aws_access=str(aws_access or "").strip(),
                aws_secret=str(aws_secret or "").strip(),
                converter_path=self.converter_path,
                output_base_dir=self.output_base_dir,
                crs_input=str(crs_input or ""),
                vertical_input=str(vertical_input or ""),
                overwrite=bool(overwrite),
            ),
            on_progress=self._progress_callback(ui),
            converter_runner=self.converter_runner,
        )
        _invoke_success(on_success)
        return result

    def duplicate_project_process(
        self,
        project_info,
        new_kunde,
        new_projekt,
        aws_access,
        aws_secret,
        on_success=None,
        ui=None,
    ):
        project_id = _project_id(project_info)
        api = self._core_api(aws_access, aws_secret)
        result = api.duplicate_project(
            project_id,
            str(new_kunde or "").strip(),
            str(new_projekt or "").strip(),
        )
        _invoke_success(on_success, _duplicate_project_url(result, new_kunde, new_projekt))
        return result

    def download_project_data_process(
        self,
        project_info,
        target_dir,
        aws_access,
        aws_secret,
        on_success=None,
        on_cancel=None,
        ui=None,
        cancel_event=None,
    ):
        api = self._core_api(aws_access, aws_secret)
        result = api.download_project(
            DownloadRequest(
                project=dict(project_info or {}),
                target_dir=str(target_dir or "").strip(),
                aws_access=str(aws_access or "").strip(),
                aws_secret=str(aws_secret or "").strip(),
            ),
            on_progress=self._progress_callback(ui),
            cancel_requested=(cancel_event.is_set if cancel_event is not None else None),
        )
        if getattr(result, "status", "") == "cancelled":
            _invoke_success(on_cancel)
        else:
            _invoke_success(on_success, str(getattr(result, "download_dir", "") or "").strip())
        return result

    def rename_project_metadata_process(
        self,
        project_info,
        new_kunde,
        new_projekt,
        pointcloud_names,
        aws_access,
        aws_secret,
        on_success=None,
        ui=None,
    ):
        api = self._core_api(aws_access, aws_secret)
        result = api.rename_project_metadata(
            ProjectMetadataUpdate(
                project_id=_project_id(project_info),
                kunde=str(new_kunde or "").strip(),
                projekt=str(new_projekt or "").strip(),
                pointcloud_names=tuple(str(name or "").strip() for name in pointcloud_names or ()),
            )
        )
        _invoke_success(on_success)
        return result

    def _core_api(self, aws_access, aws_secret) -> CoreServiceApi:
        if self.core_api_factory is not None:
            return self.core_api_factory(str(aws_access or "").strip(), str(aws_secret or "").strip())
        if self.core_api is not None:
            return self.core_api
        raise RuntimeError("LegacyProjectOpsAdapter benoetigt core_api oder core_api_factory.")

    def _progress_callback(self, ui=None):
        return _progress_callback_from_ui(ui) or self.on_progress


def normalize_legacy_sources(sources) -> tuple[str, ...]:
    """Match the old upload source normalization without importing main.py."""

    raw_sources = sources if isinstance(sources, (list, tuple)) else (sources,)
    normalized_sources: list[str] = []
    seen_paths: set[str] = set()
    for source in raw_sources:
        path = str(source or "").strip().strip('"').strip("{}")
        if not path:
            continue
        normalized_path = os.path.normpath(path)
        lookup_key = os.path.normcase(os.path.abspath(normalized_path))
        if lookup_key in seen_paths:
            continue
        seen_paths.add(lookup_key)
        normalized_sources.append(normalized_path)
    return tuple(normalized_sources)


def _pointcloud_source_from_replacement_entry(entry) -> PointcloudSource:
    if isinstance(entry, dict):
        return PointcloudSource(
            source_path=str(entry.get("source", "") or entry.get("path", "") or "").strip(),
            name=str(entry.get("name", "") or "").strip(),
            slug=str(entry.get("slug", "") or "").strip(),
            input_format=str(entry.get("format", "") or ""),
            source_type=str(entry.get("source_type", "") or ""),
        )
    return PointcloudSource(source_path=str(entry or "").strip())


def _project_id(project_info) -> str:
    project_id = str((project_info or {}).get("id", "") or (project_info or {}).get("project_id", "") or "").strip()
    if not project_id:
        raise ValueError("Projekt-ID fehlt.")
    return project_id


_NO_PAYLOAD = object()


def _invoke_success(callback, payload=_NO_PAYLOAD) -> None:
    if callback is None:
        return
    if payload is _NO_PAYLOAD:
        callback()
        return
    if _callback_accepts_payload(callback):
        callback(payload)
    else:
        callback()


def _callback_accepts_payload(callback) -> bool:
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return True
    return any(
        parameter.kind == inspect.Parameter.VAR_POSITIONAL
        or parameter.default is inspect.Parameter.empty
        and parameter.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
        for parameter in signature.parameters.values()
    )


def _duplicate_project_url(result, new_kunde, new_projekt) -> str:
    project_url = str(getattr(result, "project_url", "") or "").strip()
    if project_url:
        return project_url
    project_id = str(getattr(result, "project_id", "") or "").strip()
    if not project_id:
        return ""
    from dronautix_uploader.core.naming_service import build_project_paths

    return build_project_paths(str(new_kunde or "").strip(), str(new_projekt or "").strip(), project_id).project_url


def _progress_callback_from_ui(ui):
    if callable(ui):
        return ui
    if isinstance(ui, dict):
        for key in ("on_progress", "progress_callback", "progress", "emit", "log"):
            callback = ui.get(key)
            if callable(callback):
                return callback
        return None
    for attr in ("on_progress", "progress_callback", "emit", "log"):
        callback = getattr(ui, attr, None)
        if callable(callback):
            return callback
    return None


__all__ = [
    "LegacyProjectOpsAdapter",
    "normalize_legacy_sources",
]
