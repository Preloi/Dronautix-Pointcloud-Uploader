import os
from pathlib import Path
import struct

import pytest

from dronautix_uploader.core.local_conversion_service import (
    LocalConversionRequest,
    build_local_output_dir,
    run_local_conversion,
    validate_local_conversion_request,
)


def write_valid_potree(output_dir):
    os.makedirs(output_dir, exist_ok=True)
    (output_dir / "metadata.json").write_text(
        '{"version":"2.0","encoding":"BROTLI","points":1,"offset":[0,0,0],"scale":[0.001,0.001,0.001],"hierarchy":{"firstChunkSize":22,"stepSize":4,"depth":0},"attributes":[{"name":"position","type":"int32","numElements":3,"elementSize":4,"size":12}],"boundingBox":{"min":[0,0,0],"max":[1,1,1]}}',
        encoding="utf-8",
    )
    (output_dir / "octree.bin").write_bytes(b"o")
    (output_dir / "hierarchy.bin").write_bytes(struct.pack("<BBIQQ", 1, 0, 1, 0, 1))


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
        write_valid_potree(Path(output_dir))
        if on_progress:
            on_progress(type("Event", (), {"kind": "progress", "percent": 0.5})())

    result = run_local_conversion(
        LocalConversionRequest(str(source), str(output), str(converter), overwrite=True),
        on_progress=events.append,
        converter_runner=fake_runner,
    )

    assert result.output_dir == str(output)
    assert calls[0][:2] == (str(source), str(converter))
    assert calls[0][2] != str(output)
    assert not old_file.exists()
    assert (output / "metadata.json").is_file()
    assert any(getattr(event, "kind", "") == "step" for event in events)
    assert getattr(events[-1], "percent", None) == 1.0


def test_validate_rejects_output_containing_source_or_converter(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    source = output / "cloud.laz"
    converter = tmp_path / "PotreeConverter.exe"
    source.write_bytes(b"laz")
    converter.write_bytes(b"exe")

    with pytest.raises(ValueError, match="Quelldatei"):
        validate_local_conversion_request(LocalConversionRequest(str(source), str(output), str(converter), overwrite=True))

    source = tmp_path / "cloud.laz"
    source.write_bytes(b"laz")
    converter = output / "PotreeConverter.exe"
    converter.write_bytes(b"exe")
    with pytest.raises(ValueError, match="Potree Converter"):
        validate_local_conversion_request(LocalConversionRequest(str(source), str(output), str(converter), overwrite=True))


def test_failed_conversion_preserves_previous_output(tmp_path):
    source = tmp_path / "cloud.laz"
    converter = tmp_path / "PotreeConverter.exe"
    output = tmp_path / "out"
    source.write_bytes(b"laz")
    converter.write_bytes(b"exe")
    output.mkdir()
    original = output / "octree.bin"
    original.write_bytes(b"old")

    def fail_runner(_source, _converter, staging_dir, _progress):
        os.makedirs(staging_dir)
        (Path(staging_dir) / "octree.bin").write_bytes(b"partial")
        raise RuntimeError("converter failed")

    with pytest.raises(RuntimeError, match="converter failed"):
        run_local_conversion(
            LocalConversionRequest(str(source), str(output), str(converter), overwrite=True),
            converter_runner=fail_runner,
        )

    assert original.read_bytes() == b"old"
