import json
import os

import pytest

from dronautix_uploader.core import converter_service
from dronautix_uploader.core.converter_service import (
    build_potree_command,
    parse_potree_percent,
    validate_brotli_output,
)


def test_converter_process_is_started_without_a_window_on_windows(monkeypatch):
    class StartupInfo:
        dwFlags = 0
        wShowWindow = None

    monkeypatch.setattr(converter_service.os, "name", "nt")
    monkeypatch.setattr(converter_service.subprocess, "STARTUPINFO", StartupInfo, raising=False)
    monkeypatch.setattr(converter_service.subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(converter_service.subprocess, "SW_HIDE", 0, raising=False)
    monkeypatch.setattr(converter_service.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    options = converter_service._hidden_window_options()

    assert options["startupinfo"].dwFlags == 1
    assert options["startupinfo"].wShowWindow == 0
    assert options["creationflags"] == 0x08000000


def test_build_potree_command_matches_legacy_flags():
    command = build_potree_command(
        r"C:\data\cloud one.laz",
        r"C:\Program Files\PotreeConverter\PotreeConverter.exe",
        r"C:\out\project",
    )

    assert command.args == (
        r"C:\Program Files\PotreeConverter\PotreeConverter.exe",
        r"C:\data\cloud one.laz",
        "-o",
        r"C:\out\project",
        "--overwrite",
        "--encoding",
        "BROTLI",
    )
    assert command.cwd == os.path.dirname(r"C:\Program Files\PotreeConverter\PotreeConverter.exe")


def test_parse_potree_percent_clamps_and_ignores_non_progress():
    assert parse_potree_percent("indexing 43%") == 0.43
    assert parse_potree_percent("done 100%") == 1.0
    assert parse_potree_percent("no percent here") is None


def test_validate_brotli_output_requires_converter_metadata(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps({"encoding": "BROTLI"}), encoding="utf-8")
    validate_brotli_output(str(tmp_path))

    (tmp_path / "metadata.json").write_text(json.dumps({"encoding": "DEFAULT"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="BROTLI-Encoding"):
        validate_brotli_output(str(tmp_path))
