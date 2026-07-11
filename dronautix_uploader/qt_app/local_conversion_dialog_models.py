"""UI-free dialog state for local Potree conversion."""

from __future__ import annotations

from dataclasses import dataclass

from dronautix_uploader.core.local_conversion_service import (
    LocalConversionRequest,
    validate_local_conversion_request,
)


@dataclass(frozen=True)
class LocalConversionDialogState:
    source_file: str = ""
    output_dir: str = ""
    converter_path: str = ""
    overwrite: bool = False


def validate_local_conversion_dialog_state(state: LocalConversionDialogState) -> LocalConversionRequest:
    request = LocalConversionRequest(
        source_file=str(state.source_file or "").strip(),
        output_dir=str(state.output_dir or "").strip(),
        converter_path=str(state.converter_path or "").strip(),
        overwrite=bool(state.overwrite),
    )
    validate_local_conversion_request(request)
    return request


__all__ = [
    "LocalConversionDialogState",
    "validate_local_conversion_dialog_state",
]
