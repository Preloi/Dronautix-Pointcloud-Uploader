"""UI-free controller for the Qt local conversion flow."""

from __future__ import annotations

from typing import Any

from dronautix_uploader.core.contracts import ProgressCallback
from dronautix_uploader.core.local_conversion_service import (
    LocalConversionRequest,
    run_local_conversion,
)

from .local_conversion_dialog_models import (
    LocalConversionDialogState,
    validate_local_conversion_dialog_state,
)
from .project_management_actions import ProjectOperationSummary, SUCCESS_STATUS


class LocalConversionController:
    """Route local conversion dialog payloads to the core conversion service."""

    def __init__(self, converter_runner: Any = None) -> None:
        self.converter_runner = converter_runner

    def run_conversion(
        self,
        request_or_state: LocalConversionRequest | LocalConversionDialogState,
        on_progress: ProgressCallback | None = None,
    ) -> ProjectOperationSummary:
        if isinstance(request_or_state, LocalConversionDialogState):
            request = validate_local_conversion_dialog_state(request_or_state)
        elif isinstance(request_or_state, LocalConversionRequest):
            request = request_or_state
        else:
            raise ValueError("LocalConversionDialogState oder LocalConversionRequest erforderlich.")

        kwargs: dict[str, Any] = {"on_progress": on_progress}
        if self.converter_runner is not None:
            kwargs["converter_runner"] = self.converter_runner
        result = run_local_conversion(request, **kwargs)
        return ProjectOperationSummary(
            status=SUCCESS_STATUS,
            message=f"{result.message} Ziel: {result.output_dir}",
        )


__all__ = ["LocalConversionController"]
