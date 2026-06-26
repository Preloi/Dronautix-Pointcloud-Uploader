"""UI-free runtime service factories shared by preview and legacy adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from dronautix_uploader.core.config_service import (
    get_config_locations,
    get_credential_keyring_services,
    load_config_file,
)
from dronautix_uploader.core.constants import BUCKET_NAME, REGION_NAME
from dronautix_uploader.core.project_management_service import ProjectManagementService
from dronautix_uploader.core.project_repository import ProjectMetadataRepository
from dronautix_uploader.core.service_api import CoreServiceApi
from dronautix_uploader.core.upload_workflow_service import UploadWorkflowService


ConfigLoader = Callable[[str | Path], dict[str, Any]]
CredentialLoader = Callable[[str, str], str | None]


@dataclass(frozen=True)
class ProjectManagementRuntimeConfig:
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    region_name: str = REGION_NAME
    bucket_name: str = BUCKET_NAME

    @property
    def missing_fields(self) -> tuple[str, ...]:
        missing = []
        if not self.aws_access_key_id:
            missing.append("aws_access_key_id")
        if not self.aws_secret_access_key:
            missing.append("aws_secret_access_key")
        return tuple(missing)

    @property
    def ready(self) -> bool:
        return not self.missing_fields


def load_project_management_runtime_config(
    config_path: str | Path | None = None,
    *,
    preview: bool = True,
    environ: dict[str, str] | None = None,
    config_loader: ConfigLoader = load_config_file,
    credential_loader: CredentialLoader | None = None,
    use_keyring: bool = True,
) -> ProjectManagementRuntimeConfig:
    """Load runtime config without importing boto3, keyring, Tk, or Qt."""

    path = Path(config_path) if config_path is not None else get_config_locations(preview=preview, environ=environ).current_config
    config = config_loader(path)
    if not isinstance(config, dict):
        config = {}

    access = _first_config_value(
        config,
        "aws_access_key_id",
        "aws_access",
        "aws_access_key",
        "access_key",
    )
    secret = _first_config_value(
        config,
        "aws_secret_access_key",
        "aws_secret",
        "aws_secret_key",
        "secret_key",
    )
    if use_keyring and (not access or not secret):
        loader = credential_loader or _load_keyring_password
        credential_services = get_credential_keyring_services(preview=preview)
        loaded_access, loaded_secret = _load_missing_credentials(
            loader,
            credential_services,
            need_access=not access,
            need_secret=not secret,
        )
        access = access or loaded_access
        secret = secret or loaded_secret

    return ProjectManagementRuntimeConfig(
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name=_first_config_value(config, "region_name", "aws_region", "region") or REGION_NAME,
        bucket_name=_first_config_value(config, "bucket_name", "s3_bucket", "bucket") or BUCKET_NAME,
    )


def create_project_management_service(
    config: ProjectManagementRuntimeConfig,
    *,
    boto3_session_factory: Callable[..., Any] | None = None,
    s3_client: Any | None = None,
) -> ProjectManagementService:
    """Create the project management service from runtime config."""

    if not config.ready and s3_client is None:
        missing = ", ".join(config.missing_fields)
        raise RuntimeError(f"Projektverwaltung kann nicht gestartet werden; fehlende Credentials: {missing}")

    client = s3_client or _create_s3_client(config, boto3_session_factory)
    repository = ProjectMetadataRepository(client, bucket_name=config.bucket_name)
    return ProjectManagementService(
        repository=repository,
        s3_client=client,
        bucket_name=config.bucket_name,
    )


def create_upload_workflow_service(
    config: ProjectManagementRuntimeConfig,
    *,
    boto3_session_factory: Callable[..., Any] | None = None,
    s3_client: Any | None = None,
) -> UploadWorkflowService:
    """Create the upload workflow service from runtime config."""

    if not config.ready and s3_client is None:
        missing = ", ".join(config.missing_fields)
        raise RuntimeError(f"Upload kann nicht gestartet werden; fehlende Credentials: {missing}")

    client = s3_client or _create_s3_client(config, boto3_session_factory)
    repository = ProjectMetadataRepository(client, bucket_name=config.bucket_name)
    return UploadWorkflowService(
        repository=repository,
        s3_client=client,
        bucket_name=config.bucket_name,
    )


def create_core_service_api(
    config: ProjectManagementRuntimeConfig,
    *,
    boto3_session_factory: Callable[..., Any] | None = None,
    s3_client: Any | None = None,
) -> CoreServiceApi:
    """Create the dataclass-based core API with one shared S3 client."""

    if not config.ready and s3_client is None:
        missing = ", ".join(config.missing_fields)
        raise RuntimeError(f"Core-Service-API kann nicht gestartet werden; fehlende Credentials: {missing}")

    client = s3_client or _create_s3_client(config, boto3_session_factory)
    return CoreServiceApi(
        project_service=_create_project_management_service_for_client(config, client),
        upload_service=_create_upload_workflow_service_for_client(config, client),
    )


def create_core_service_api_from_credentials(
    aws_access_key_id: str,
    aws_secret_access_key: str,
    *,
    region_name: str = REGION_NAME,
    bucket_name: str = BUCKET_NAME,
    boto3_session_factory: Callable[..., Any] | None = None,
    s3_client: Any | None = None,
) -> CoreServiceApi:
    """Create a core API for legacy call sites that pass AWS credentials per operation."""

    return create_core_service_api(
        ProjectManagementRuntimeConfig(
            aws_access_key_id=str(aws_access_key_id or "").strip(),
            aws_secret_access_key=str(aws_secret_access_key or "").strip(),
            region_name=str(region_name or REGION_NAME).strip() or REGION_NAME,
            bucket_name=str(bucket_name or BUCKET_NAME).strip() or BUCKET_NAME,
        ),
        boto3_session_factory=boto3_session_factory,
        s3_client=s3_client,
    )


def _create_project_management_service_for_client(
    config: ProjectManagementRuntimeConfig,
    client: Any,
) -> ProjectManagementService:
    repository = ProjectMetadataRepository(client, bucket_name=config.bucket_name)
    return ProjectManagementService(
        repository=repository,
        s3_client=client,
        bucket_name=config.bucket_name,
    )


def _create_upload_workflow_service_for_client(
    config: ProjectManagementRuntimeConfig,
    client: Any,
) -> UploadWorkflowService:
    repository = ProjectMetadataRepository(client, bucket_name=config.bucket_name)
    return UploadWorkflowService(
        repository=repository,
        s3_client=client,
        bucket_name=config.bucket_name,
    )


def _create_s3_client(
    config: ProjectManagementRuntimeConfig,
    boto3_session_factory: Callable[..., Any] | None,
):
    session_factory = boto3_session_factory
    if session_factory is None:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 ist erforderlich, um die Projektverwaltung mit S3 zu verbinden.") from exc
        session_factory = boto3.Session

    session = session_factory(
        aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key,
        region_name=config.region_name,
    )
    return session.client("s3")


def _first_config_value(config: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(config.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _load_keyring_password(service_name: str, username: str) -> str:
    try:
        import keyring
    except ImportError:
        return ""
    try:
        return str(keyring.get_password(service_name, username) or "")
    except Exception:
        return ""


def _load_missing_credentials(
    credential_loader: CredentialLoader,
    service_names: tuple[str, ...],
    *,
    need_access: bool,
    need_secret: bool,
) -> tuple[str, str]:
    if need_access and need_secret:
        for service_name in service_names:
            access = str(credential_loader(service_name, "aws_access") or "").strip()
            secret = str(credential_loader(service_name, "aws_secret") or "").strip()
            if access and secret:
                return access, secret
        return "", ""

    access = _load_first_credential(credential_loader, service_names, "aws_access") if need_access else ""
    secret = _load_first_credential(credential_loader, service_names, "aws_secret") if need_secret else ""
    return access, secret


def _load_first_credential(
    credential_loader: CredentialLoader,
    service_names: tuple[str, ...],
    username: str,
) -> str:
    for service_name in service_names:
        value = str(credential_loader(service_name, username) or "").strip()
        if value:
            return value
    return ""


__all__ = [
    "ConfigLoader",
    "CoreServiceApi",
    "CredentialLoader",
    "ProjectManagementRuntimeConfig",
    "create_core_service_api",
    "create_core_service_api_from_credentials",
    "create_project_management_service",
    "create_upload_workflow_service",
    "load_project_management_runtime_config",
]
