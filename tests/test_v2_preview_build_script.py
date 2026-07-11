import importlib
from pathlib import Path


def test_v2_preview_build_script_is_import_safe_and_separate_from_release_channel():
    build_v2_preview = importlib.import_module("build_v2_preview")
    build_v2_final_candidate = importlib.import_module("build_v2_final_candidate")

    command = build_v2_preview.build_command()

    assert build_v2_preview.ENTRYPOINT == "Dronautix_Pointcloud_Uploader_v2.py"
    assert build_v2_preview.ENTRYPOINT != build_v2_final_candidate.ENTRYPOINT
    assert build_v2_preview.DIST_DIR == "dist_v2_preview"
    assert build_v2_preview.DIST_DIR != build_v2_final_candidate.DIST_DIR
    assert build_v2_preview.DIST_DIR != "Output"
    assert "Dronautix_Pointcloud_Uploader.py" not in command
    assert command[-1] == "Dronautix_Pointcloud_Uploader_v2.py"
    assert "latest-release.json" not in " ".join(command)


def test_v2_preview_requirements_extend_legacy_requirements():
    requirements = Path("requirements-v2-preview.txt").read_text(encoding="utf-8").splitlines()

    assert "-r requirements.txt" in requirements
    assert "PySide6" in requirements
