"""Runtime service factories for the QtWidgets V2 application."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dronautix_uploader.adapters.runtime_services import (
    ConfigLoader,
    CredentialLoader,
    ProjectManagementRuntimeConfig,
    _load_keyring_password,
    create_core_service_api,
    create_core_service_api_from_credentials,
    create_project_management_service,
    create_upload_workflow_service,
    load_project_management_runtime_config,
)
from dronautix_uploader.core.config_service import is_valid_aws_region
from dronautix_uploader.core.service_api import CoreServiceApi


@dataclass(frozen=True)
class RuntimeControllerBundle:
    project_provider: Any | None = None
    project_controller: Any | None = None
    upload_controller: Any | None = None
    core_api: CoreServiceApi | None = None
    status: str = "Nicht verbunden - AWS-Zugangsdaten in den Einstellungen hinterlegen"

    @property
    def ready(self) -> bool:
        return (
            self.project_provider is not None
            and self.project_controller is not None
            and self.upload_controller is not None
            and self.core_api is not None
        )


def create_runtime_controller_bundle(
    config: ProjectManagementRuntimeConfig,
    *,
    boto3_session_factory=None,
    s3_client: Any | None = None,
) -> RuntimeControllerBundle:
    """Create service-backed Qt controllers for project management and upload."""

    if not config.ready and s3_client is None:
        missing = ", ".join(config.missing_fields)
        return RuntimeControllerBundle(status=f"Nicht verbunden - fehlende Angaben in den Einstellungen: {missing}")
    if s3_client is None and not is_valid_aws_region(config.region_name):
        return RuntimeControllerBundle(
            status=f"Nicht verbunden - AWS Region „{config.region_name}“ ist ungültig. Bitte in den Einstellungen korrigieren."
        )

    try:
        project_service = create_project_management_service(
            config,
            boto3_session_factory=boto3_session_factory,
            s3_client=s3_client,
        )
        client = project_service.s3_client
        upload_service = create_upload_workflow_service(config, s3_client=client)
        core_api = CoreServiceApi(project_service=project_service, upload_service=upload_service)
    except Exception as exc:
        # boto3 wirft u. a. ValueError-Subklassen (InvalidRegionError); ein
        # Konfigurationsfehler darf den App-Start nie verhindern.
        return RuntimeControllerBundle(status=f"Nicht verbunden: {exc}")

    from .project_management_controller import ProjectManagementController
    from .upload_workflow_controller import UploadWorkflowController

    return RuntimeControllerBundle(
        project_provider=project_service,
        project_controller=ProjectManagementController(project_service),
        upload_controller=UploadWorkflowController(upload_service),
        core_api=core_api,
        status="S3-Zugangsdaten geladen - Verbindung wird geprüft",
    )


__all__ = [
    "ConfigLoader",
    "CredentialLoader",
    "ProjectManagementRuntimeConfig",
    "RuntimeControllerBundle",
    "_load_keyring_password",
    "CoreServiceApi",
    "create_core_service_api",
    "create_core_service_api_from_credentials",
    "create_project_management_service",
    "create_runtime_controller_bundle",
    "create_upload_workflow_service",
    "load_project_management_runtime_config",
]
