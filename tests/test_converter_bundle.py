from dronautix_uploader.core.converter_bundle import (
    get_bundled_converter_path,
    is_converter_bundle_available,
    resolve_converter_path,
)


def test_converter_bundle_resolves_source_layout(tmp_path):
    converter_dir = tmp_path / "bundled_tools" / "PotreeConverter"
    converter_dir.mkdir(parents=True)
    converter = converter_dir / "PotreeConverter.exe"
    dll = converter_dir / "laszip.dll"
    converter.write_bytes(b"converter")
    dll.write_bytes(b"dll")

    assert is_converter_bundle_available(tmp_path)
    assert get_bundled_converter_path(tmp_path) == converter
    assert resolve_converter_path("", tmp_path) == str(converter)


def test_converter_bundle_prefers_explicit_override(tmp_path):
    override = tmp_path / "CustomPotreeConverter.exe"
    override.write_bytes(b"converter")

    assert resolve_converter_path(str(override), tmp_path) == str(override)


def test_converter_bundle_reports_missing_when_exe_or_dll_is_absent(tmp_path):
    converter_dir = tmp_path / "bundled_tools" / "PotreeConverter"
    converter_dir.mkdir(parents=True)
    (converter_dir / "PotreeConverter.exe").write_bytes(b"converter")

    assert not is_converter_bundle_available(tmp_path)
    assert resolve_converter_path("", tmp_path) == ""
