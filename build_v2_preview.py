"""Build the PySide6 V2 preview executable without touching release metadata."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys

from app_version import APP_FILE_VERSION, APP_PUBLISHER, APP_VERSION


PREVIEW_APP_NAME = "Dronautix Pointcloud Uploader V2 Preview"
PREVIEW_EXE_NAME = "Dronautix_Pointcloud_Uploader_v2_preview.exe"
PYINSTALLER_NAME = "Dronautix_Pointcloud_Uploader_v2_preview"
ENTRYPOINT = "Dronautix_Pointcloud_Uploader_v2.py"
VERSION_INFO_FILE = "version_info_v2_preview.txt"
BUILD_DIR = "build_v2_preview"
DIST_DIR = "dist_v2_preview"


def write_text_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        file.write(content)


def sync_preview_version_file() -> None:
    version_parts = tuple(int(part) for part in APP_FILE_VERSION.split("."))
    version_info_content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_parts},
    prodvers={version_parts},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', '{APP_PUBLISHER}'),
          StringStruct('FileDescription', '{PREVIEW_APP_NAME}'),
          StringStruct('FileVersion', '{APP_FILE_VERSION}'),
          StringStruct('InternalName', '{PYINSTALLER_NAME}'),
          StringStruct('OriginalFilename', '{PREVIEW_EXE_NAME}'),
          StringStruct('ProductName', '{PREVIEW_APP_NAME}'),
          StringStruct('ProductVersion', '{APP_VERSION}'),
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)"""
    write_text_file(VERSION_INFO_FILE, version_info_content)


def cleanup_previous_preview_build() -> None:
    def remove_readonly(func, path, _excinfo):
        os.chmod(path, stat.S_IWRITE)
        func(path)

    for target in (BUILD_DIR, DIST_DIR):
        if os.path.isdir(target):
            shutil.rmtree(target, onerror=remove_readonly)
            print(f"[OK] Alter Preview-Build-Ordner entfernt: {target}")


def validate_required_files() -> None:
    required_files = (
        ENTRYPOINT,
        "icon.ico",
        os.path.join("bundled_tools", "PotreeConverter", "PotreeConverter.exe"),
        os.path.join("bundled_tools", "PotreeConverter", "laszip.dll"),
    )
    missing_files = [file for file in required_files if not os.path.exists(file)]
    if not missing_files:
        return
    print("[FEHLER] Folgende Dateien fehlen:")
    for file in missing_files:
        print(f"  - {file}")
    raise SystemExit(1)


def validate_build_dependencies() -> bool:
    missing_packages = []
    for module_name, package_name in (("PyInstaller", "pyinstaller"), ("PySide6", "PySide6")):
        try:
            __import__(module_name)
        except ImportError:
            missing_packages.append(package_name)
    if not missing_packages:
        return True
    print("[FEHLER] Build-Abhaengigkeiten fehlen:")
    for package_name in missing_packages:
        print(f"  - {package_name}")
    print("Installation mit: pip install -r requirements-v2-preview.txt")
    return False


def build_command() -> list[str]:
    data_separator = ";" if sys.platform == "win32" else ":"
    icon_path = os.path.abspath("icon.ico")
    bundled_tools_path = os.path.abspath("bundled_tools")
    version_info_path = os.path.abspath(VERSION_INFO_FILE)
    return [
        sys.executable,
        "-m",
        "PyInstaller",
        f"--name={PYINSTALLER_NAME}",
        "--onefile",
        "--windowed",
        f"--icon={icon_path}",
        f"--version-file={version_info_path}",
        f"--distpath={DIST_DIR}",
        f"--workpath={BUILD_DIR}",
        f"--specpath={BUILD_DIR}",
        f"--add-data={icon_path}{data_separator}.",
        f"--add-data={bundled_tools_path}{data_separator}bundled_tools",
        "--hidden-import=PySide6.QtCore",
        "--hidden-import=PySide6.QtGui",
        "--hidden-import=PySide6.QtWidgets",
        "--hidden-import=keyring",
        "--hidden-import=keyring.backends.Windows",
        "--hidden-import=boto3",
        ENTRYPOINT,
    ]


def main() -> int:
    print("=" * 70)
    print(f"  {PREVIEW_APP_NAME} {APP_VERSION} - Preview EXE Builder")
    print("=" * 70)
    print()
    print("[INFO] Dieser Build schreibt kein latest-release.json und baut keinen produktiven Installer.")

    sync_preview_version_file()
    cleanup_previous_preview_build()
    validate_required_files()

    if not validate_build_dependencies():
        return 1

    command = build_command()
    print("Befehl:", " ".join(command))
    print()
    subprocess.run(command, check=True)
    print()
    print("[ERFOLG] Preview-Build erfolgreich:")
    print(f"  {DIST_DIR}/{PREVIEW_EXE_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
