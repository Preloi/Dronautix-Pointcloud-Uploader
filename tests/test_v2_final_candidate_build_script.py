import importlib
import os

from app_version import APP_EXE_NAME, APP_ID, APP_NAME, APP_VERSION
from tools.check_v2_final_packaging_contract import (
    CANDIDATE_DIST_DIR,
    CANDIDATE_OUTPUT_DIR,
    PRODUCTION_PYINSTALLER_NAME,
    V2_ENTRYPOINT,
    build_final_packaging_contract,
)


def test_v2_final_candidate_build_script_is_import_safe_and_uses_contract_paths():
    build_v2_final_candidate = importlib.import_module("build_v2_final_candidate")

    command = build_v2_final_candidate.build_command()
    contract = build_final_packaging_contract()

    assert build_v2_final_candidate.ENTRYPOINT == V2_ENTRYPOINT
    assert build_v2_final_candidate.PYINSTALLER_NAME == PRODUCTION_PYINSTALLER_NAME
    assert build_v2_final_candidate.DIST_DIR == CANDIDATE_DIST_DIR
    assert build_v2_final_candidate.OUTPUT_DIR == CANDIDATE_OUTPUT_DIR
    assert contract["isolated_paths"]["dist_dir"] == build_v2_final_candidate.DIST_DIR
    assert contract["isolated_paths"]["output_dir"] == build_v2_final_candidate.OUTPUT_DIR
    assert "Dronautix_Pointcloud_Uploader.py" not in command
    assert command[-1] == V2_ENTRYPOINT
    assert "--name=Dronautix_Pointcloud_Uploader" in command
    assert "--distpath=dist_v2_final_candidate" in command
    assert "--workpath=build_v2_final_candidate" in command
    assert "--specpath=build_v2_final_candidate" in command
    assert "latest-release.json" not in " ".join(command)
    assert "Output/" not in " ".join(command)


def test_v2_final_candidate_version_file_uses_production_identity(monkeypatch):
    build_v2_final_candidate = importlib.import_module("build_v2_final_candidate")
    writes = {}

    monkeypatch.setattr(
        build_v2_final_candidate,
        "write_text_file",
        lambda path, content: writes.setdefault(path, content),
    )

    build_v2_final_candidate.sync_final_candidate_version_file()

    content = writes[build_v2_final_candidate.VERSION_INFO_FILE]
    assert f"StringStruct('FileDescription', '{APP_NAME}')" in content
    assert f"StringStruct('OriginalFilename', '{APP_EXE_NAME}')" in content
    assert "Dronautix Pointcloud Uploader V2 Preview" not in content


def test_v2_final_candidate_installer_files_are_isolated_and_keep_production_identity():
    build_v2_final_candidate = importlib.import_module("build_v2_final_candidate")

    defines = build_v2_final_candidate.build_installer_version_content()
    iss = build_v2_final_candidate.build_inno_setup_script_content()

    assert build_v2_final_candidate.INSTALLER_VERSION_FILE == "installer_version_v2_final_candidate.iss"
    assert build_v2_final_candidate.INNO_SETUP_SCRIPT == "Dronautix_Pointcloud_Uploader_v2_final_candidate.iss"
    assert f'#define AppName "{APP_NAME}"' in defines
    assert f'#define AppExeName "{APP_EXE_NAME}"' in defines
    assert f'#define AppId "{APP_ID}"' in defines
    assert '#include "installer_version_v2_final_candidate.iss"' in iss
    assert '#define SourceExe "dist_v2_final_candidate\\Dronautix_Pointcloud_Uploader.exe"' in iss
    assert "OutputDir=Output_v2_final_candidate" in iss
    assert 'Name: "desktopicon"' in iss
    assert "Flags: unchecked" not in iss
    assert 'Filename: "{win}\\explorer.exe"' in iss
    assert 'Parameters: """{app}\\{#AppExeName}"""' in iss
    assert '_MEI*' not in iss
    assert f"OutputBaseFilename=Dronautix_Pointcloud_Uploader_Setup_{{#AppVersion}}" in iss
    assert "OutputDir=Output\n" not in iss
    assert '#include "installer_version.iss"' not in iss
    assert 'SourceExe "dist\\Dronautix_Pointcloud_Uploader.exe"' not in iss
    assert os.path.normpath(build_v2_final_candidate.get_candidate_installer_path()) == os.path.normpath(
        f"Output_v2_final_candidate/Dronautix_Pointcloud_Uploader_Setup_{APP_VERSION}.exe"
    )
