"""UI-free adapters for connecting core services to presentation layers."""

from .legacy_project_ops import LegacyProjectOpsAdapter, normalize_legacy_sources
from .progress import ProgressDispatchError, ProgressDispatcher, ProgressRecorder
from .runtime_services import (
    ConfigLoader,
    CoreServiceApi,
    CredentialLoader,
    ProjectManagementRuntimeConfig,
    create_core_service_api,
    create_project_management_service,
    create_upload_workflow_service,
    load_project_management_runtime_config,
)

__all__ = [
    "ConfigLoader",
    "CoreServiceApi",
    "CredentialLoader",
    "LegacyProjectOpsAdapter",
    "ProgressDispatchError",
    "ProgressDispatcher",
    "ProgressRecorder",
    "ProjectManagementRuntimeConfig",
    "create_core_service_api",
    "create_project_management_service",
    "create_upload_workflow_service",
    "load_project_management_runtime_config",
    "normalize_legacy_sources",
]
