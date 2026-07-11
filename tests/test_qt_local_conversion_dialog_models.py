import pytest

from dronautix_uploader.core.local_conversion_service import LocalConversionRequest
from dronautix_uploader.qt_app.local_conversion_dialog_models import (
    LocalConversionDialogState,
    validate_local_conversion_dialog_state,
)


def test_local_conversion_dialog_state_validates_to_core_request(tmp_path):
    source = tmp_path / "scan.laz"
    converter = tmp_path / "PotreeConverter.exe"
    output = tmp_path / "scan_potree"
    source.write_bytes(b"laz")
    converter.write_bytes(b"exe")

    request = validate_local_conversion_dialog_state(
        LocalConversionDialogState(
            source_file=f" {source} ",
            output_dir=f" {output} ",
            converter_path=f" {converter} ",
            overwrite=True,
        )
    )

    assert request == LocalConversionRequest(
        source_file=str(source),
        output_dir=str(output),
        converter_path=str(converter),
        overwrite=True,
    )


@pytest.mark.parametrize(
    ("state_kwargs", "message"),
    [
        ({"source_file": "", "output_dir": "out", "converter_path": "PotreeConverter.exe"}, "LAS/LAZ"),
        ({"source_file": "scan.copc.laz", "output_dir": "out", "converter_path": "PotreeConverter.exe"}, ".las oder .laz"),
        ({"source_file": "scan.laz", "output_dir": "", "converter_path": "PotreeConverter.exe"}, "Zielordner"),
        ({"source_file": "scan.laz", "output_dir": "out", "converter_path": ""}, "Potree Converter"),
    ],
)
def test_local_conversion_dialog_state_rejects_invalid_inputs(tmp_path, state_kwargs, message):
    if state_kwargs.get("source_file"):
        source = tmp_path / state_kwargs["source_file"]
        source.write_bytes(b"data")
        state_kwargs["source_file"] = str(source)
    if state_kwargs.get("converter_path"):
        converter = tmp_path / state_kwargs["converter_path"]
        converter.write_bytes(b"exe")
        state_kwargs["converter_path"] = str(converter)

    with pytest.raises((ValueError, FileExistsError), match=message):
        validate_local_conversion_dialog_state(LocalConversionDialogState(**state_kwargs))
