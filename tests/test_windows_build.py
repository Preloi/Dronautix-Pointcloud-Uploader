import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools.windows_build import build_environment


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DLL search paths")
def test_build_excludes_foreign_dll_directories_without_changing_parent(monkeypatch):
    poison = r"C:\foreign\poppler\bin;C:\foreign\libheif\bin"
    monkeypatch.setenv("PATH", poison)
    env = build_environment()
    assert "foreign" not in env["PATH"]
    assert str(Path(os.environ["SystemRoot"]) / "System32") in env["PATH"].split(os.pathsep)
    assert os.environ["PATH"] == poison


def test_self_test_runs_real_qt_window_in_separate_process(tmp_path):
    report = tmp_path / "startup.json"
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    completed = subprocess.run(
        [sys.executable, "Dronautix_Pointcloud_Uploader_v2_final.py", "--startup-self-test", str(report)],
        env=env, capture_output=True, timeout=30, **options,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    result = json.loads(report.read_text())
    assert result["ok"] and result["pages"] >= 3 and result["qt"]
    assert result["frozen"] is False


def test_failed_startup_blocks_release_installer_and_manifest(monkeypatch):
    import build_exe as builder

    monkeypatch.setattr(builder, "sync_version_files", lambda: None)
    monkeypatch.setattr(builder, "cleanup_previous_build_artifacts", lambda: None)
    monkeypatch.setattr(builder, "check_build_prerequisites", lambda: True)
    calls = []
    monkeypatch.setattr(builder.subprocess, "run", lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(builder, "find_inno_setup", lambda: pytest.fail("Installer reached after failed startup"))
    monkeypatch.setattr(
        builder, "write_release_manifest_after_installer_build", lambda: pytest.fail("Manifest reached after failed startup")
    )

    def fail_startup(_executable):
        raise RuntimeError("QtCore DLL import failed")

    monkeypatch.setattr(builder, "verify_frozen_startup", fail_startup)
    with pytest.raises(RuntimeError, match="QtCore DLL import failed"):
        builder.main()
    assert len(calls) == 1 and "PyInstaller" in calls[0]


def test_production_build_checks_startup_before_installer_or_manifest():
    source = Path("build_exe.py").read_text(encoding="utf-8")
    assert source.index('verify_frozen_startup(os.path.join("dist", APP_EXE_NAME))') < source.index(
        'subprocess.run([inno_setup, INNO_SETUP_SCRIPT], check=True)'
    )
