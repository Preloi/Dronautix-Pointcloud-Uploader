"""UI-free controller for the Qt local conversion flow."""

from __future__ import annotations

from typing import Any

import shutil

from dronautix_uploader.core.contracts import (
    CancelCallback,
    OperationCancelledError,
    ProgressCallback,
    make_cancel_guarded_progress,
)
from dronautix_uploader.core.local_conversion_service import (
    LocalConversionRequest,
    run_local_conversion,
)

from .local_conversion_dialog_models import (
    LocalConversionDialogState,
    validate_local_conversion_dialog_state,
)
from .project_management_actions import CANCELLED_STATUS, ProjectOperationSummary, SUCCESS_STATUS


class LocalConversionController:
    """Route local conversion dialog payloads to the core conversion service."""

    def __init__(self, converter_runner: Any = None) -> None:
        self.converter_runner = converter_runner

    def run_conversion(
        self,
        request_or_state: LocalConversionRequest | LocalConversionDialogState,
        on_progress: ProgressCallback | None = None,
        cancel_requested: CancelCallback | None = None,
    ) -> ProjectOperationSummary:
        if isinstance(request_or_state, LocalConversionDialogState):
            request = validate_local_conversion_dialog_state(request_or_state)
        elif isinstance(request_or_state, LocalConversionRequest):
            request = request_or_state
        else:
            raise ValueError("LocalConversionDialogState oder LocalConversionRequest erforderlich.")

        kwargs: dict[str, Any] = {"on_progress": make_cancel_guarded_progress(on_progress, cancel_requested)}
        if self.converter_runner is not None:
            kwargs["converter_runner"] = self.converter_runner
        try:
            result = run_local_conversion(request, **kwargs)
        except OperationCancelledError:
            # Unvollstaendige Potree-Ausgabe nicht im Zielordner zuruecklassen.
            shutil.rmtree(request.output_dir, ignore_errors=True)
            return ProjectOperationSummary(
                status=CANCELLED_STATUS,
                message="Konvertierung abgebrochen. Unvollständige Ausgabe wurde entfernt.",
            )
        return ProjectOperationSummary(
            status=SUCCESS_STATUS,
            message=f"{result.message} Ziel: {result.output_dir}",
        )


__all__ = ["LocalConversionController"]
