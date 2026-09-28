import importlib
from pathlib import Path

import build_exe


def test_v2_preview_build_script_is_import_safe_and_separate_from_release_channel():
    build_v2_preview = importlib.import_module("build_v2_preview")

    command = build_v2_preview.build_command()

    assert build_v2_preview.ENTRYPOINT == "Dronautix_Pointcloud_Uploader_v2.py"
    assert build_v2_preview.ENTRYPOINT != build_exe.ENTRYPOINT
    assert build_v2_preview.DIST_DIR == "dist_v2_preview"
    assert build_v2_preview.DIST_DIR not in {"dist", "Output"}
    assert "Dronautix_Pointcloud_Uploader.py" not in command
    assert command[-1] == "Dronautix_Pointcloud_Uploader_v2.py"
    assert "latest-release.json" not in " ".join(command)


def test_requirements_contain_only_the_v2_stack():
    requirements = {line.strip() for line in Path("requirements.txt").read_text(encoding="utf-8").splitlines() if line.strip()}

    assert "PySide6" in requirements
    assert not requirements & {"customtkinter", "tkinterdnd2"}
