import os

import pytest

from dronautix_uploader.core.local_conversion_service import (
    LocalConversionRequest,
    build_local_output_dir,
    run_local_conversion,
    validate_local_conversion_request,
)


def test_build_local_output_dir_matches_legacy_suffix_and_slug(tmp_path):
    source = tmp_path / "München Scan.laz"

    assert build_local_output_dir(str(source), str(tmp_path / "out")) == str(
        tmp_path / "out" / "muenchen_scan_potree"
    )


def test_validate_local_conversion_request_rejects_unsupported_file(tmp_path):
    source = tmp_path / "cloud.copc.laz"
    converter = tmp_path / "PotreeConverter.exe"
    source.write_bytes(b"copc")
    converter.write_bytes(b"exe")

    with pytest.raises(ValueError, match=".las oder .laz"):
        validate_local_conversion_request(
            LocalConversionRequest(str(source), str(tmp_path / "out"), str(converter))
        )


def test_validate_local_conversion_request_requires_overwrite_for_existing_output(tmp_path):
    source = tmp_path / "cloud.laz"
    converter = tmp_path / "PotreeConverter.exe"
    output = tmp_path / "out"
    source.write_bytes(b"laz")
    converter.write_bytes(b"exe")
    output.mkdir()

    with pytest.raises(FileExistsError):
        validate_local_conversion_request(LocalConversionRequest(str(source), str(output), str(converter)))


def test_run_local_conversion_removes_existing_output_and_uses_runner(tmp_path):
    source = tmp_path / "cloud.laz"
    converter = tmp_path / "PotreeConverter.exe"
    output = tmp_path / "out"
    old_file = output / "old.txt"
    source.write_bytes(b"laz")
    converter.write_bytes(b"exe")
    output.mkdir()
    old_file.write_text("old", encoding="utf-8")
    calls = []
    events = []

    def fake_runner(source_file, converter_path, output_dir, on_progress):
        calls.append((source_file, converter_path, output_dir))
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "metadata.json"), "w", encoding="utf-8") as file:
            file.write("{}")
        if on_progress:
            on_progress(type("Event", (), {"kind": "progress", "percent": 0.5})())

    result = run_local_conversion(
        LocalConversionRequest(str(source), str(output), str(converter), overwrite=True),
        on_progress=events.append,
        converter_runner=fake_runner,
    )

    assert result.output_dir == str(output)
    assert calls == [(str(source), str(converter), str(output))]
    assert not old_file.exists()
    assert (output / "metadata.json").is_file()
    assert any(getattr(event, "kind", "") == "step" for event in events)
    assert getattr(events[-1], "percent", None) == 1.0
