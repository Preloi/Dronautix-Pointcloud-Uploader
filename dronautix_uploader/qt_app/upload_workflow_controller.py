"""UI-free controller for the Qt upload workflow."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dronautix_uploader.core.contracts import CancelCallback, ProgressCallback
from dronautix_uploader.core.upload_workflow_service import NewProjectUploadWorkflowRequest

from .project_management_actions import ProjectOperationSummary, summarize_project_operation_result
from .upload_dialog_models import UploadDialogState, validate_upload_dialog_state


class UploadWorkflowController:
    """Route validated upload dialog payloads to the upload workflow service."""

    def __init__(self, service: Any) -> None:
        self.service = service

    def upload_new_project(
        self,
        request_or_state: NewProjectUploadWorkflowRequest | UploadDialogState,
        on_progress: ProgressCallback | None = None,
        cancel_requested: CancelCallback | None = None,
        confirm_spatial_warning: Callable[[str], bool] | None = None,
    ) -> ProjectOperationSummary:
        if isinstance(request_or_state, UploadDialogState):
            request = validate_upload_dialog_state(request_or_state)
        elif isinstance(request_or_state, NewProjectUploadWorkflowRequest):
            request = request_or_state
        else:
            raise ValueError("UploadDialogState oder NewProjectUploadWorkflowRequest erforderlich.")

        result = self.service.upload_new_project(
            request,
            on_progress=on_progress,
            cancel_requested=cancel_requested,
            confirm_spatial_warning=confirm_spatial_warning,
        )
        return summarize_project_operation_result(result)


__all__ = ["UploadWorkflowController"]
