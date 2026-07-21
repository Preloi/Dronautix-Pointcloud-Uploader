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


def test_converter_failure_includes_last_output_lines(monkeypatch, capsys):
    class FailedProcess:
        stdout = iter(["loading source\n", "invalid LAS header\n"])
        returncode = 123

        def wait(self):
            return self.returncode

    monkeypatch.setattr(converter_service.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())

    with pytest.raises(RuntimeError, match="(?s)scan.las.*Exit Code: 123.*invalid LAS header"):
        converter_service.run_potree_conversion("scan.las", "PotreeConverter.exe", "output")

    stderr = capsys.readouterr().err
    assert "[SOURCE] scan.las" in stderr
    assert "[POTREE] invalid LAS header" in stderr


def test_converter_uses_ascii_short_path_for_unicode_source(monkeypatch, tmp_path):
    captured_args = []

    class SuccessfulProcess:
        stdout = iter(())
        returncode = 0

        def wait(self):
            return self.returncode

    def fake_popen(args, **kwargs):
        captured_args.extend(args)
        return SuccessfulProcess()

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "metadata.json").write_text('{"encoding":"BROTLI"}', encoding="utf-8")
    monkeypatch.setattr(converter_service, "_windows_short_path", lambda path: "C:\\DATA\\BAUME~1.LAS")
    monkeypatch.setattr(converter_service.subprocess, "Popen", fake_popen)

    converter_service.run_potree_conversion("C:\\Daten\\Bäume.las", "PotreeConverter.exe", str(output_dir))

    assert captured_args[1] == "C:\\DATA\\BAUME~1.LAS"


def test_unicode_source_alias_is_removed_after_use(monkeypatch, tmp_path):
    source = tmp_path / "Bäume.las"
    source.write_bytes(b"LAS")
    staging_dir = tmp_path / "dronautix_potree_source_test"

    def fake_mkdtemp(**kwargs):
        staging_dir.mkdir()
        return str(staging_dir)

    monkeypatch.setattr(converter_service, "_windows_short_path", lambda path: "")
    monkeypatch.setattr(converter_service.tempfile, "mkdtemp", fake_mkdtemp)

    with converter_service._converter_safe_source_path(str(source), str(tmp_path / "output")) as alias:
        assert os.path.exists(alias)
        assert os.stat(alias).st_ino == os.stat(source).st_ino

    assert not staging_dir.exists()


def test_unicode_source_alias_failure_leaves_no_temp_directory(monkeypatch, tmp_path):
    staging_dir = tmp_path / "dronautix_potree_source_test"

    def fake_mkdtemp(**kwargs):
        staging_dir.mkdir()
        return str(staging_dir)

    monkeypatch.setattr(converter_service, "_windows_short_path", lambda path: "")
    monkeypatch.setattr(converter_service.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(converter_service.os, "link", lambda source, alias: (_ for _ in ()).throw(OSError()))

    with pytest.raises(RuntimeError, match="ohne Umlaute"):
        with converter_service._converter_safe_source_path("Bäume.las", str(tmp_path / "output")):
            pass

    assert not staging_dir.exists()


def test_converter_explains_zero_point_bounding_box_failure(monkeypatch, tmp_path):
    class FailedProcess:
        stdout = iter(["#points: 0\n", "ERROR: invalid bounding box\n"])
        returncode = 123

        def wait(self):
            return self.returncode

    monkeypatch.setattr(converter_service.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())

    with pytest.raises(RuntimeError, match="(?s)scan.las.*keine lesbaren Punkte.*gültige XYZ-Grenzen"):
        converter_service.run_potree_conversion("scan.las", "PotreeConverter.exe", str(tmp_path / "output"))


def test_converter_start_failure_contains_paths_and_system_error(monkeypatch, tmp_path):
    def fail_to_start(*args, **kwargs):
        raise OSError("Zugriff verweigert")

    monkeypatch.setattr(converter_service.subprocess, "Popen", fail_to_start)

    with pytest.raises(
        RuntimeError,
        match="(?s)konnte nicht gestartet werden.*scan.las.*PotreeConverter.exe.*Zugriff verweigert",
    ):
        converter_service.run_potree_conversion("scan.las", "PotreeConverter.exe", str(tmp_path / "output"))


def test_converter_success_without_valid_output_is_explained(monkeypatch, tmp_path):
    class SuccessfulProcess:
        stdout = iter(())
        returncode = 0

        def wait(self):
            return self.returncode

    monkeypatch.setattr(converter_service.subprocess, "Popen", lambda *args, **kwargs: SuccessfulProcess())

    with pytest.raises(RuntimeError, match="(?s)scan.las.*kein verwendbares BROTLI-Ergebnis.*Ausgabeordner"):
        converter_service.run_potree_conversion("scan.las", "PotreeConverter.exe", str(tmp_path / "output"))
