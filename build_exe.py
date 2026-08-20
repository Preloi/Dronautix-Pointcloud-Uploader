# ==============================================================================
# BUILD SCRIPT - Dronautix Pointcloud Uploader EXE erstellen
# ==============================================================================
#
# VORAUSSETZUNGEN:
# 1. Python 3.8 oder höher installiert
# 2. Alle Dependencies installiert (siehe requirements.txt)
# 3. PyInstaller installiert
#
# VERWENDUNG:
# 1. Öffne eine Kommandozeile/Terminal
# 2. Navigiere zu diesem Ordner
# 3. Führe aus: python build_exe.py
#
# ==============================================================================

import subprocess
import sys
import os
import json
import shutil
import stat
import hashlib
from datetime import datetime
from app_version import (
    APP_EXE_NAME,
    APP_FILE_VERSION,
    APP_ID,
    APP_NAME,
    APP_PUBLISHER,
    APP_VERSION,
)
from dronautix_uploader.core.glb_toolchain import validate_glb_toolchain_for_packaging

VERSION_INFO_FILE = "version_info.txt"
INSTALLER_VERSION_FILE = "installer_version.iss"
LATEST_RELEASE_FILE = "latest-release.json"
INNO_SETUP_SCRIPT = "Dronautix_Pointcloud_Uploader.iss"
INNO_SETUP_CANDIDATES = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]
GITHUB_UPDATE_OWNER = "Preloi"
GITHUB_UPDATE_REPO = "Dronautix-Pointcloud-Uploader"
GITHUB_UPDATE_BRANCH = "master"
ENTRYPOINT = "Dronautix_Pointcloud_Uploader_v2_final.py"
BUNDLED_TOOL_DIRECTORIES = (
    os.path.join("bundled_tools", "PotreeConverter"),
    os.path.join("bundled_tools", "GLBToolchain"),
)
GLB_TOOLCHAIN_FILES = (
    os.path.join("bundled_tools", "GLBToolchain", "toolchain-manifest.v1.json"),
    os.path.join("bundled_tools", "GLBToolchain", "toolchain-integrity.v1.json"),
    os.path.join("bundled_tools", "GLBToolchain", "viewer-capabilities.v1.json"),
)


def write_text_file(path, content):
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        file.write(content)


def write_json_file(path, data):
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
        file.write("\n")


def calculate_file_sha256(path):
    sha256 = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def get_output_installer_path():
    return os.path.join("Output", f"Dronautix_Pointcloud_Uploader_Setup_{APP_VERSION}.exe")


def sync_version_files():
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
          StringStruct('FileDescription', '{APP_NAME}'),
          StringStruct('FileVersion', '{APP_FILE_VERSION}'),
          StringStruct('InternalName', 'Dronautix_Pointcloud_Uploader'),
          StringStruct('OriginalFilename', '{APP_EXE_NAME}'),
          StringStruct('ProductName', '{APP_NAME}'),
          StringStruct('ProductVersion', '{APP_VERSION}'),
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)"""
    installer_version_content = (
        f'#define AppName "{APP_NAME}"\n'
        f'#define AppVersion "{APP_VERSION}"\n'
        f'#define AppPublisher "{APP_PUBLISHER}"\n'
        f'#define AppExeName "{APP_EXE_NAME}"\n'
        f'#define AppId "{APP_ID}"\n'
    )
    write_text_file(VERSION_INFO_FILE, version_info_content)
    write_text_file(INSTALLER_VERSION_FILE, installer_version_content)


def sync_output_manifest():
    output_dir = "Output"
    if not os.path.isdir(output_dir):
        return

    shutil.copyfile(LATEST_RELEASE_FILE, os.path.join(output_dir, LATEST_RELEASE_FILE))


def write_release_manifest_after_installer_build():
    installer_path = get_output_installer_path()
    if not os.path.isfile(installer_path):
        raise FileNotFoundError(f"Finaler Installer fehlt: {installer_path}")

    release_manifest = {
        "version": APP_VERSION,
        "installer_name": f"Dronautix_Pointcloud_Uploader_Setup_{APP_VERSION}.exe",
        "repo_owner": GITHUB_UPDATE_OWNER,
        "repo_name": GITHUB_UPDATE_REPO,
        "manifest_branch": GITHUB_UPDATE_BRANCH,
        "release_tag": f"v{APP_VERSION}",
        "installer_url": (
            f"https://github.com/{GITHUB_UPDATE_OWNER}/{GITHUB_UPDATE_REPO}/"
            f"releases/download/v{APP_VERSION}/Dronautix_Pointcloud_Uploader_Setup_{APP_VERSION}.exe"
        ),
        "published_at": datetime.now().isoformat(timespec="seconds"),
        "installer_sha256": calculate_file_sha256(installer_path),
    }
    write_json_file(LATEST_RELEASE_FILE, release_manifest)
    print("[OK] latest-release.json nach finalem Installer-Build geschrieben")


def cleanup_previous_build_artifacts():
    def remove_readonly(func, path, excinfo):
        os.chmod(path, stat.S_IWRITE)
        func(path)

    for target in ["build", "dist", "Output"]:
        if os.path.isdir(target):
            shutil.rmtree(target, onerror=remove_readonly)
            print(f"[OK] Alter Build-Ordner entfernt: {target}")


def find_inno_setup():
    for candidate in INNO_SETUP_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return ""

print("=" * 70)
print(f"  {APP_NAME} {APP_VERSION} - EXE Builder")
print("=" * 70)
print()

sync_version_files()
cleanup_previous_build_artifacts()
print("[OK] Versionsdateien synchronisiert")
print("[OK] Vorherige Build-Artefakte bereinigt")

# Prüfe ob PyInstaller installiert ist
try:
    import PyInstaller
    print("[OK] PyInstaller ist installiert")
except ImportError:
    print("[FEHLER] PyInstaller nicht gefunden!")
    print()
    print("Installation mit:")
    print("  pip install pyinstaller")
    print()
    sys.exit(1)

# Prüfe ob alle erforderlichen Dateien vorhanden sind
required_files = [
    ENTRYPOINT,
    "icon.ico",
    VERSION_INFO_FILE,
    INSTALLER_VERSION_FILE,
    INNO_SETUP_SCRIPT,
    os.path.join("bundled_tools", "PotreeConverter", "PotreeConverter.exe"),
    os.path.join("bundled_tools", "PotreeConverter", "laszip.dll"),
    *GLB_TOOLCHAIN_FILES,
]

missing_files = []
for file in required_files:
    if not os.path.exists(file):
        missing_files.append(file)

if missing_files:
    print("[FEHLER] Folgende Dateien fehlen:")
    for file in missing_files:
        print(f"  - {file}")
    sys.exit(1)

print("[OK] Alle erforderlichen Dateien gefunden")
glb_toolchain_issues = validate_glb_toolchain_for_packaging()
if glb_toolchain_issues:
    print("[FEHLER] Die gebündelte GLB-Toolchain ist nicht produktionsbereit:")
    for issue in glb_toolchain_issues:
        print(f"  - {issue}")
    sys.exit(1)
print("[OK] Gebündelte GLB-Toolchain ist versiegelt und lokal getestet")
print()

# PyInstaller Befehl
print("Starte PyInstaller...")
print()

data_separator = ";" if sys.platform == "win32" else ":"
cmd = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--name=Dronautix_Pointcloud_Uploader",
    "--onefile",                              # Eine einzelne .exe Datei
    "--windowed",                             # Kein Konsolen-Fenster (GUI-App)
    "--icon=icon.ico",                        # Icon einbinden
    f"--version-file={VERSION_INFO_FILE}",    # Windows-Dateiversion
    f"--add-data=icon.ico{data_separator}.",
    *[
        f"--add-data={source}{data_separator}{source}"
        for source in BUNDLED_TOOL_DIRECTORIES
    ],
    "--hidden-import=keyring",
    "--hidden-import=keyring.backends.Windows",
    "--hidden-import=PySide6.QtCore",
    "--hidden-import=PySide6.QtGui",
    "--hidden-import=PySide6.QtWidgets",
    "--hidden-import=boto3",
    ENTRYPOINT,
]

print("Befehl:", " ".join(cmd))
print()

try:
    subprocess.run(cmd, check=True)
    inno_setup = find_inno_setup()
    if inno_setup:
        print("[OK] Inno Setup gefunden - baue Setup...")
        subprocess.run([inno_setup, INNO_SETUP_SCRIPT], check=True)
        write_release_manifest_after_installer_build()
        sync_output_manifest()
        print("[OK] Update-Manifest synchronisiert")
    else:
        print("[WARNUNG] Inno Setup nicht gefunden - Setup wurde nicht gebaut")
    print()
    print("=" * 70)
    print("[ERFOLG] BUILD ERFOLGREICH!")
    print("=" * 70)
    print()
    print("Die .exe Datei findest du in:")
    print("  dist/Dronautix_Pointcloud_Uploader.exe")
    print("Das Setup findest du in:")
    print(f"  Output/Dronautix_Pointcloud_Uploader_Setup_{APP_VERSION}.exe")
    print()
    print("Du kannst diese Datei nun auf jedem Windows-Computer ausführen,")
    print("ohne dass Python installiert sein muss!")
    print()
except subprocess.CalledProcessError as e:
    print()
    print("=" * 70)
    print("[FEHLER] BUILD FEHLGESCHLAGEN")
    print("=" * 70)
    print()
    print(f"Fehler: {e}")
    sys.exit(1)
