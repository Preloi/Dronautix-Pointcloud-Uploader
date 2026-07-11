import importlib
import sys
from dataclasses import dataclass

from dronautix_uploader.adapters.legacy_project_ops import LegacyProjectOpsAdapter, normalize_legacy_sources
from dronautix_uploader.core.contracts import (
    DownloadRequest,
    MultiReplacementRequest,
    ProjectMetadataUpdate,
    ReplacementRequest,
    UploadRequest,
)


@dataclass(frozen=True)
class Result:
    status: str = "success"
    project_id: str = "project"
    download_dir: str = ""


class FakeProjectService:
    def __init__(self):
        self.duplicates = []

    def duplicate_project(self, project_id, new_kunde, new_projekt):
        self.duplicates.append((project_id, new_kunde, new_projekt))
        return Result(project_id=project_id)


class FakeCoreApi:
    def __init__(self):
        self.project_service = FakeProjectService()
        self.uploads = []
        self.replacements = []
        self.multi_replacements = []
        self.downloads = []
        self.renames = []
        self.duplicates = []

    def upload_project(self, request, on_progress=None, converter_runner=None):
        self.uploads.append((request, on_progress, converter_runner))
        return Result(project_id="upload")

    def replace_pointcloud(self, request, on_progress=None, converter_runner=None):
        self.replacements.append((request, on_progress, converter_runner))
        return Result(project_id="replace")

    def replace_pointclouds(self, request, on_progress=None, converter_runner=None):
        self.multi_replacements.append((request, on_progress, converter_runner))
        return Result(project_id="multi")

    def download_project(self, request, on_progress=None, cancel_requested=None):
        self.downloads.append((request, on_progress, cancel_requested))
        return Result(project_id="download", download_dir=request.target_dir)

    def rename_project_metadata(self, request):
        self.renames.append(request)
        return Result(project_id=request.project_id)

    def duplicate_project(self, project_id, new_kunde, new_projekt):
        self.duplicates.append((project_id, new_kunde, new_projekt))
        return Result(project_id="copy")


def test_legacy_project_ops_adapter_imports_without_tk_qt_or_customtkinter():
    module = importlib.import_module("dronautix_uploader.adapters.legacy_project_ops")

    assert module is not None
    assert "tkinter" not in sys.modules
    assert "customtkinter" not in sys.modules
    assert "PySide6" not in sys.modules


def test_normalize_legacy_sources_matches_old_deduping_shape(tmp_path):
    first = tmp_path / "scan.laz"
    repeated = f'"{{{first}}}"'

    assert normalize_legacy_sources([str(first), repeated, "", None]) == (str(first),)


def test_run_process_maps_old_upload_signature_to_upload_request():
    core_api = FakeCoreApi()
    callback_calls = []
    progress_callback = callback_calls.append
    adapter = LegacyProjectOpsAdapter(
        core_api,
        converter_path="converter.exe",
        output_base_dir="C:/out",
        on_progress=progress_callback,
        converter_runner="runner",
    )

    result = adapter.run_process(
        ["a.laz", "b.copc.laz"],
        " Kunde ",
        " Projekt ",
        "access",
        "secret",
        "EPSG:25832",
        "DHHN2016",
        on_success=lambda: callback_calls.append("success"),
        overwrite=True,
    )

    request, on_progress, converter_runner = core_api.uploads[0]
    assert isinstance(request, UploadRequest)
    assert result.project_id == "upload"
    assert request.kunde == "Kunde"
    assert request.projekt == "Projekt"
    assert [source.source_path for source in request.sources] == ["a.laz", "b.copc.laz"]
    assert request.converter_path == "converter.exe"
    assert request.output_base_dir == "C:/out"
    assert request.crs_input == "EPSG:25832"
    assert request.vertical_input == "DHHN2016"
    assert request.overwrite is True
    assert on_progress is progress_callback
    assert converter_runner == "runner"
    assert callback_calls == ["success"]


def test_adapter_uses_core_api_factory_with_legacy_credentials_per_call():
    factory_calls = []
    created_api = FakeCoreApi()

    def factory(access, secret):
        factory_calls.append((access, secret))
        return created_api

    adapter = LegacyProjectOpsAdapter(core_api_factory=factory)

    adapter.run_multi_upload_process(
        ["scan.copc.laz"],
        "Kunde",
        "Projekt",
        " access ",
        " secret ",
    )

    assert factory_calls == [("access", "secret")]
    assert created_api.uploads[0][0].aws_access == "access"
    assert created_api.uploads[0][0].aws_secret == "secret"


def test_replace_processes_map_legacy_inputs_to_replacement_contracts():
    core_api = FakeCoreApi()
    adapter = LegacyProjectOpsAdapter(core_api, converter_path="converter.exe", output_base_dir="C:/out")
    project = {"id": "project", "s3_path": "pointclouds/k/p"}
    target = {"name": "Scan", "s3_path": "pointclouds/k/p/scan"}

    adapter.replace_project_process(
        project,
        "replacement.laz",
        "access",
        "secret",
        crs_input="EPSG:25832",
        target_pointcloud=target,
        overwrite=True,
    )
    adapter.replace_project_with_multi_pointclouds(
        project,
        ({"source": "a.laz", "name": "A", "slug": "a"}, {"path": "b.copc.laz", "name": "B"}),
        "access",
        "secret",
        vertical_input="DHHN2016",
        overwrite=True,
    )

    replacement_request = core_api.replacements[0][0]
    multi_request = core_api.multi_replacements[0][0]
    assert isinstance(replacement_request, ReplacementRequest)
    assert replacement_request.project == project
    assert replacement_request.replacement.source_path == "replacement.laz"
    assert replacement_request.target_pointcloud == target
    assert replacement_request.converter_path == "converter.exe"
    assert replacement_request.output_base_dir == "C:/out"
    assert replacement_request.crs_input == "EPSG:25832"
    assert replacement_request.overwrite is True
    assert isinstance(multi_request, MultiReplacementRequest)
    assert [source.source_path for source in multi_request.replacements] == ["a.laz", "b.copc.laz"]
    assert [source.name for source in multi_request.replacements] == ["A", "B"]
    assert multi_request.vertical_input == "DHHN2016"
    assert multi_request.overwrite is True


def test_duplicate_download_and_rename_map_legacy_inputs_to_core_operations():
    core_api = FakeCoreApi()
    adapter = LegacyProjectOpsAdapter(core_api)
    callback_results = []

    duplicate = adapter.duplicate_project_process(
        {"id": "project"},
        " Kunde ",
        " Projekt Kopie ",
        "access",
        "secret",
        on_success=callback_results.append,
    )
    download = adapter.download_project_data_process(
        {"id": "project", "s3_path": "pointclouds/k/p"},
        " C:/Downloads ",
        "access",
        "secret",
    )
    rename = adapter.rename_project_metadata_process(
        {"id": "project"},
        " Neu ",
        " Name ",
        (" Cloud A ", "Cloud B"),
        "access",
        "secret",
    )

    download_request = core_api.downloads[0][0]
    rename_request = core_api.renames[0]
    assert duplicate.project_id == "copy"
    assert core_api.duplicates == [("project", "Kunde", "Projekt Kopie")]
    assert isinstance(download_request, DownloadRequest)
    assert download_request.target_dir == "C:/Downloads"
    assert download.project_id == "download"
    assert isinstance(rename_request, ProjectMetadataUpdate)
    assert rename_request.project_id == "project"
    assert rename_request.kunde == "Neu"
    assert rename_request.projekt == "Name"
    assert rename_request.pointcloud_names == ("Cloud A", "Cloud B")
    assert rename.project_id == "project"
    assert callback_results == ["https://pointcloud.dronautix.at/index.html?id=copy"]
