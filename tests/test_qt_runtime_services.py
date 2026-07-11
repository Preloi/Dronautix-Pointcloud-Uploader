import importlib
import io
import json
import sys

import pytest

from dronautix_uploader.core.config_service import PREVIEW_KEYRING_SERVICE
from dronautix_uploader.core.constants import KEYRING_SERVICE
from dronautix_uploader.core.project_management_service import ProjectManagementService
from dronautix_uploader.core.project_repository import ProjectMetadataRepository
from dronautix_uploader.core.service_api import CoreServiceApi
from dronautix_uploader.core.upload_workflow_service import UploadWorkflowService


def import_runtime_services_without_optional_modules(monkeypatch):
    for module_name in (
        "dronautix_uploader.qt_app.runtime_services",
        "boto3",
        "keyring",
        "PySide6",
    ):
        sys.modules.pop(module_name, None)

    blocked_modules = {"boto3", "keyring", "PySide6"}
    original_import = __import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.split(".", 1)[0] in blocked_modules:
            raise AssertionError(f"unexpected import of optional dependency {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", guarded_import)
    return importlib.import_module("dronautix_uploader.qt_app.runtime_services")


def test_runtime_services_imports_without_boto3_keyring_or_pyside6(monkeypatch):
    runtime_services = import_runtime_services_without_optional_modules(monkeypatch)

    assert hasattr(runtime_services, "ProjectManagementRuntimeConfig")
    assert hasattr(runtime_services, "RuntimeControllerBundle")
    assert hasattr(runtime_services, "create_runtime_controller_bundle")
    assert hasattr(runtime_services, "load_project_management_runtime_config")
    assert hasattr(runtime_services, "create_project_management_service")
    assert hasattr(runtime_services, "create_upload_workflow_service")


def test_runtime_service_adapter_imports_without_boto3_keyring_pyside_or_tk(monkeypatch):
    for module_name in (
        "dronautix_uploader.adapters.runtime_services",
        "boto3",
        "keyring",
        "PySide6",
        "tkinter",
        "customtkinter",
    ):
        sys.modules.pop(module_name, None)

    blocked_modules = {"boto3", "keyring", "PySide6", "tkinter", "customtkinter"}
    original_import = __import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.split(".", 1)[0] in blocked_modules:
            raise AssertionError(f"unexpected import of optional dependency {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", guarded_import)

    adapter = importlib.import_module("dronautix_uploader.adapters.runtime_services")

    assert hasattr(adapter, "ProjectManagementRuntimeConfig")
    assert hasattr(adapter, "CoreServiceApi")
    assert "CoreServiceApi" in adapter.__all__
    assert hasattr(adapter, "load_project_management_runtime_config")
    assert hasattr(adapter, "create_core_service_api")
    assert hasattr(adapter, "create_core_service_api_from_credentials")
    assert hasattr(adapter, "create_project_management_service")
    assert hasattr(adapter, "create_upload_workflow_service")


def test_qt_runtime_services_reuses_ui_free_adapter_factories(monkeypatch):
    adapter = importlib.import_module("dronautix_uploader.adapters.runtime_services")
    runtime_services = import_runtime_services_without_optional_modules(monkeypatch)

    assert runtime_services.ProjectManagementRuntimeConfig is adapter.ProjectManagementRuntimeConfig
    assert runtime_services.CoreServiceApi is adapter.CoreServiceApi
    assert runtime_services.load_project_management_runtime_config is adapter.load_project_management_runtime_config
    assert runtime_services.create_core_service_api is adapter.create_core_service_api
    assert runtime_services.create_core_service_api_from_credentials is adapter.create_core_service_api_from_credentials
    assert runtime_services.create_project_management_service is adapter.create_project_management_service
    assert runtime_services.create_upload_workflow_service is adapter.create_upload_workflow_service


@pytest.mark.parametrize(
    ("config_data", "expected_access_key", "expected_secret_key"),
    [
        (
            {
                "aws_access_key_id": "AKIA_NEW",
                "aws_secret_access_key": "secret-new",
                "region_name": "eu-central-1",
                "bucket_name": "new-bucket",
            },
            "AKIA_NEW",
            "secret-new",
        ),
        (
            {
                "aws_access": "AKIA_LEGACY",
                "aws_secret": "secret-legacy",
                "region_name": "eu-central-1",
                "bucket_name": "legacy-bucket",
            },
            "AKIA_LEGACY",
            "secret-legacy",
        ),
    ],
)
def test_load_runtime_config_accepts_current_and_legacy_aws_key_aliases(
    monkeypatch,
    config_data,
    expected_access_key,
    expected_secret_key,
):
    runtime_services = import_runtime_services_without_optional_modules(monkeypatch)

    config = runtime_services.load_project_management_runtime_config(
        config_path="ignored.json",
        preview=True,
        environ={"APPDATA": "unused"},
        config_loader=lambda _path: dict(config_data),
    )

    assert config.aws_access_key_id == expected_access_key
    assert config.aws_secret_access_key == expected_secret_key
    assert config.region_name == "eu-central-1"
    assert config.bucket_name == config_data["bucket_name"]
    assert config.ready is True
    assert config.missing_fields == ()


def test_load_runtime_config_reports_missing_credentials(monkeypatch):
    runtime_services = importlib.import_module("dronautix_uploader.qt_app.runtime_services")

    config = runtime_services.load_project_management_runtime_config(
        config_path="ignored.json",
        config_loader=lambda _path: {"region_name": "eu-central-1", "bucket_name": "bucket"},
        credential_loader=lambda service, user: "",
    )

    assert config.ready is False
    assert "aws_access_key_id" in config.missing_fields
    assert "aws_secret_access_key" in config.missing_fields
    assert "bucket_name" not in config.missing_fields
    assert "region_name" not in config.missing_fields


def test_load_runtime_config_prefers_complete_preview_keyring_pair(monkeypatch):
    runtime_services = import_runtime_services_without_optional_modules(monkeypatch)
    calls = []

    def credential_loader(service, user):
        calls.append((service, user))
        credentials = {
            (PREVIEW_KEYRING_SERVICE, "aws_access"): "access-preview",
            (PREVIEW_KEYRING_SERVICE, "aws_secret"): "secret-preview",
            (KEYRING_SERVICE, "aws_access"): "access-legacy",
            (KEYRING_SERVICE, "aws_secret"): "secret-legacy",
        }
        return credentials.get((service, user), "")

    config = runtime_services.load_project_management_runtime_config(
        config_path="ignored.json",
        preview=True,
        config_loader=lambda _path: {"region_name": "eu-central-1", "bucket_name": "bucket"},
        credential_loader=credential_loader,
    )

    assert config.aws_access_key_id == "access-preview"
    assert config.aws_secret_access_key == "secret-preview"
    assert (PREVIEW_KEYRING_SERVICE, "aws_access") in calls
    assert (KEYRING_SERVICE, "aws_secret") not in calls


def test_load_runtime_config_falls_back_to_complete_legacy_pair_instead_of_mixing_keyrings(monkeypatch):
    runtime_services = import_runtime_services_without_optional_modules(monkeypatch)

    def credential_loader(service, user):
        credentials = {
            (PREVIEW_KEYRING_SERVICE, "aws_access"): "access-preview-only",
            (KEYRING_SERVICE, "aws_access"): "access-legacy",
            (KEYRING_SERVICE, "aws_secret"): "secret-legacy",
        }
        return credentials.get((service, user), "")

    config = runtime_services.load_project_management_runtime_config(
        config_path="ignored.json",
        preview=True,
        config_loader=lambda _path: {"region_name": "eu-central-1", "bucket_name": "bucket"},
        credential_loader=credential_loader,
    )

    assert config.aws_access_key_id == "access-legacy"
    assert config.aws_secret_access_key == "secret-legacy"


def test_default_runtime_keyring_loader_reads_keyring_module(monkeypatch):
    runtime_services = importlib.import_module("dronautix_uploader.qt_app.runtime_services")

    class FakeKeyring:
        @staticmethod
        def get_password(service, username):
            return f"{service}:{username}"

    monkeypatch.setitem(sys.modules, "keyring", FakeKeyring())

    assert runtime_services._load_keyring_password("service", "user") == "service:user"


def test_create_project_management_service_rejects_incomplete_config(monkeypatch):
    runtime_services = import_runtime_services_without_optional_modules(monkeypatch)
    config = runtime_services.ProjectManagementRuntimeConfig(
        aws_access_key_id="",
        aws_secret_access_key="secret",
        region_name="eu-central-1",
        bucket_name="bucket",
    )

    with pytest.raises((RuntimeError, ValueError), match="aws_access_key_id"):
        runtime_services.create_project_management_service(
            config,
            boto3_session_factory=lambda **_kwargs: pytest.fail("session must not be created"),
        )


def test_create_runtime_controller_bundle_reports_missing_credentials(monkeypatch):
    runtime_services = import_runtime_services_without_optional_modules(monkeypatch)
    config = runtime_services.ProjectManagementRuntimeConfig(
        aws_access_key_id="",
        aws_secret_access_key="",
        region_name="eu-central-1",
        bucket_name="bucket",
    )

    bundle = runtime_services.create_runtime_controller_bundle(
        config,
        boto3_session_factory=lambda **_kwargs: pytest.fail("session must not be created"),
    )

    assert bundle.ready is False
    assert bundle.project_provider is None
    assert bundle.core_api is None
    assert "fehlende Angaben" in bundle.status


class FakeS3Client:
    def __init__(self):
        self.objects = {
            "projects_index.json": {
                "projects": [{"id": "active", "projekt": "Aktiv", "kunde": "Kunde"}],
                "disabled_projects": [{"id": "disabled", "projekt": "Archiv", "kunde": "Kunde"}],
            }
        }

    def get_object(self, Bucket, Key):
        assert Bucket == "runtime-bucket"
        data = json.dumps(self.objects[Key]).encode("utf-8")
        return {"Body": io.BytesIO(data)}


class FakeSession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.s3_client = FakeS3Client()
        self.client_calls = []

    def client(self, service_name):
        self.client_calls.append(service_name)
        assert service_name == "s3"
        return self.s3_client


def test_create_project_management_service_wires_repository_s3_bucket_region_and_provider(monkeypatch):
    runtime_services = import_runtime_services_without_optional_modules(monkeypatch)
    created_sessions = []

    def fake_session_factory(**kwargs):
        session = FakeSession(**kwargs)
        created_sessions.append(session)
        return session

    config = runtime_services.ProjectManagementRuntimeConfig(
        aws_access_key_id="AKIA_RUNTIME",
        aws_secret_access_key="runtime-secret",
        region_name="eu-central-1",
        bucket_name="runtime-bucket",
    )

    service = runtime_services.create_project_management_service(
        config,
        boto3_session_factory=fake_session_factory,
    )

    assert isinstance(service, ProjectManagementService)
    assert isinstance(service.repository, ProjectMetadataRepository)
    assert service.repository.bucket_name == "runtime-bucket"
    assert service.s3_client is created_sessions[0].s3_client
    assert created_sessions[0].kwargs == {
        "aws_access_key_id": "AKIA_RUNTIME",
        "aws_secret_access_key": "runtime-secret",
        "region_name": "eu-central-1",
    }
    assert created_sessions[0].client_calls == ["s3"]
    assert service.list_projects_for_management() == [
        ({"id": "active", "projekt": "Aktiv", "kunde": "Kunde"}, False),
        ({"id": "disabled", "projekt": "Archiv", "kunde": "Kunde"}, True),
    ]


def test_create_upload_workflow_service_uses_same_runtime_s3_repository(monkeypatch):
    runtime_services = import_runtime_services_without_optional_modules(monkeypatch)
    created_sessions = []

    def fake_session_factory(**kwargs):
        session = FakeSession(**kwargs)
        created_sessions.append(session)
        return session

    config = runtime_services.ProjectManagementRuntimeConfig(
        aws_access_key_id="AKIA_UPLOAD",
        aws_secret_access_key="upload-secret",
        region_name="eu-central-1",
        bucket_name="runtime-bucket",
    )

    service = runtime_services.create_upload_workflow_service(
        config,
        boto3_session_factory=fake_session_factory,
    )

    assert isinstance(service, UploadWorkflowService)
    assert isinstance(service.repository, ProjectMetadataRepository)
    assert service.repository.bucket_name == "runtime-bucket"
    assert service.s3_client is created_sessions[0].s3_client


def test_create_core_service_api_uses_one_shared_runtime_s3_client(monkeypatch):
    runtime_services = import_runtime_services_without_optional_modules(monkeypatch)
    created_sessions = []

    def fake_session_factory(**kwargs):
        session = FakeSession(**kwargs)
        created_sessions.append(session)
        return session

    config = runtime_services.ProjectManagementRuntimeConfig(
        aws_access_key_id="AKIA_CORE",
        aws_secret_access_key="core-secret",
        region_name="eu-central-1",
        bucket_name="runtime-bucket",
    )

    api = runtime_services.create_core_service_api(config, boto3_session_factory=fake_session_factory)

    assert isinstance(api, CoreServiceApi)
    assert isinstance(api.project_service, ProjectManagementService)
    assert isinstance(api.upload_service, UploadWorkflowService)
    assert api.project_service.s3_client is created_sessions[0].s3_client
    assert api.upload_service.s3_client is created_sessions[0].s3_client
    assert created_sessions[0].client_calls == ["s3"]


def test_create_core_service_api_from_credentials_builds_runtime_config(monkeypatch):
    runtime_services = import_runtime_services_without_optional_modules(monkeypatch)
    created_sessions = []

    def fake_session_factory(**kwargs):
        session = FakeSession(**kwargs)
        created_sessions.append(session)
        return session

    api = runtime_services.create_core_service_api_from_credentials(
        "AKIA_LEGACY_CALL",
        "legacy-call-secret",
        region_name="eu-central-1",
        bucket_name="runtime-bucket",
        boto3_session_factory=fake_session_factory,
    )

    assert isinstance(api, CoreServiceApi)
    assert created_sessions[0].kwargs == {
        "aws_access_key_id": "AKIA_LEGACY_CALL",
        "aws_secret_access_key": "legacy-call-secret",
        "region_name": "eu-central-1",
    }


def test_create_runtime_controller_bundle_wires_provider_and_controllers_to_shared_s3(monkeypatch):
    runtime_services = import_runtime_services_without_optional_modules(monkeypatch)
    shared_client = FakeS3Client()
    config = runtime_services.ProjectManagementRuntimeConfig(
        aws_access_key_id="AKIA_RUNTIME",
        aws_secret_access_key="runtime-secret",
        region_name="eu-central-1",
        bucket_name="runtime-bucket",
    )

    bundle = runtime_services.create_runtime_controller_bundle(config, s3_client=shared_client)

    assert bundle.ready is True
    assert bundle.status == "Projektverwaltung mit S3 verbunden"
    assert bundle.project_provider.s3_client is shared_client
    assert bundle.project_controller.service.s3_client is shared_client
    assert bundle.upload_controller.service.s3_client is shared_client
    assert isinstance(bundle.core_api, CoreServiceApi)
    assert bundle.core_api.project_service is bundle.project_controller.service
    assert bundle.core_api.upload_service is bundle.upload_controller.service
