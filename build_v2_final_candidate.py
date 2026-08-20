"""Build the isolated Final-V2 candidate executable without releasing it."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import hashlib

from app_version import APP_EXE_NAME, APP_FILE_VERSION, APP_ID, APP_NAME, APP_PUBLISHER, APP_VERSION
from dronautix_uploader.core.glb_toolchain import validate_glb_toolchain_for_packaging
from tools.check_v2_final_packaging_contract import (
    CANDIDATE_DIST_DIR,
    CANDIDATE_OUTPUT_DIR,
    PRODUCTION_PYINSTALLER_NAME,
    V2_ENTRYPOINT,
    build_final_packaging_contract,
    validate_final_packaging_contract,
    write_candidate_manifest,
)


VERSION_INFO_FILE = "version_info_v2_final_candidate.txt"
INSTALLER_VERSION_FILE = "installer_version_v2_final_candidate.iss"
INNO_SETUP_SCRIPT = "Dronautix_Pointcloud_Uploader_v2_final_candidate.iss"
BUILD_DIR = "build_v2_final_candidate"
DIST_DIR = CANDIDATE_DIST_DIR
OUTPUT_DIR = CANDIDATE_OUTPUT_DIR
ENTRYPOINT = V2_ENTRYPOINT
PYINSTALLER_NAME = PRODUCTION_PYINSTALLER_NAME
BUNDLED_TOOL_DIRECTORIES = (
    os.path.join("bundled_tools", "PotreeConverter"),
    os.path.join("bundled_tools", "GLBToolchain"),
)
GLB_TOOLCHAIN_FILES = (
    os.path.join("bundled_tools", "GLBToolchain", "toolchain-manifest.v1.json"),
    os.path.join("bundled_tools", "GLBToolchain", "toolchain-integrity.v1.json"),
    os.path.join("bundled_tools", "GLBToolchain", "viewer-capabilities.v1.json"),
)
INNO_SETUP_CANDIDATES = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def write_text_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        file.write(content)


def sync_final_candidate_version_file() -> None:
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
          StringStruct('InternalName', '{PYINSTALLER_NAME}'),
          StringStruct('OriginalFilename', '{APP_EXE_NAME}'),
          StringStruct('ProductName', '{APP_NAME}'),
          StringStruct('ProductVersion', '{APP_VERSION}'),
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)"""
    write_text_file(VERSION_INFO_FILE, version_info_content)


def build_installer_version_content() -> str:
    return (
        f'#define AppName "{APP_NAME}"\n'
        f'#define AppVersion "{APP_VERSION}"\n'
        f'#define AppPublisher "{APP_PUBLISHER}"\n'
        f'#define AppExeName "{APP_EXE_NAME}"\n'
        f'#define AppId "{APP_ID}"\n'
    )


def build_inno_setup_script_content() -> str:
    source_exe = f"{DIST_DIR}\\Dronautix_Pointcloud_Uploader.exe"
    return f"""#include "{INSTALLER_VERSION_FILE}"
#define SourceExe "{source_exe}"

[Setup]
AppId={{#AppId}}
AppName={{#AppName}}
AppVersion={{#AppVersion}}
AppPublisher={{#AppPublisher}}
DefaultDirName={{autopf}}\\Dronautix\\Pointcloud Uploader
DefaultGroupName={{#AppName}}
DisableProgramGroupPage=yes
OutputDir={OUTPUT_DIR}
OutputBaseFilename=Dronautix_Pointcloud_Uploader_Setup_{{#AppVersion}}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=icon.ico
UninstallDisplayIcon={{app}}\\{{#AppExeName}}
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
CloseApplications=yes
CloseApplicationsFilter={{#AppExeName}}
RestartApplications=no

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Desktop-Verknuepfung erstellen"; GroupDescription: "Zusaetzliche Aufgaben:"

[Files]
Source: "{{#SourceExe}}"; DestDir: "{{app}}"; Flags: ignoreversion

[Icons]
Name: "{{group}}\\{{#AppName}}"; Filename: "{{app}}\\{{#AppExeName}}"
Name: "{{autodesktop}}\\{{#AppName}}"; Filename: "{{app}}\\{{#AppExeName}}"; Tasks: desktopicon

[Run]
Filename: "{{win}}\\explorer.exe"; Parameters: \"\"\"{{app}}\\{{#AppExeName}}\"\"\"; Description: "{{#AppName}} starten"; Flags: nowait postinstall skipifsilent

[Code]
function GetUninstallString(): string;
var
  uninstallKey: string;
  uninstallString: string;
begin
  uninstallKey := 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\' + '{{#AppId}}_is1';
  uninstallString := '';

  if not RegQueryStringValue(HKLM64, uninstallKey, 'UninstallString', uninstallString) then
    if not RegQueryStringValue(HKLM, uninstallKey, 'UninstallString', uninstallString) then
      RegQueryStringValue(HKCU, uninstallKey, 'UninstallString', uninstallString);

  Result := uninstallString;
end;

function UnInstallOldVersion(): Integer;
var
  uninstallString: string;
  resultCode: Integer;
begin
  Result := 0;
  uninstallString := RemoveQuotes(GetUninstallString());

  if uninstallString <> '' then
  begin
    if Exec(
      uninstallString,
      '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      resultCode
    ) then
      Result := resultCode
    else
      Result := -1;
  end;
end;

procedure KillRunningApp();
var
  resultCode: Integer;
begin
  Exec('cmd.exe', '/c taskkill /IM {{#AppExeName}} /F', '', SW_HIDE, ewWaitUntilTerminated, resultCode);
  Exec('cmd.exe', '/c ping -n 4 127.0.0.1 >nul', '', SW_HIDE, ewWaitUntilTerminated, resultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  uninstallResult: Integer;
begin
  Result := '';
  KillRunningApp();
  uninstallResult := UnInstallOldVersion();

  if (uninstallResult <> 0) and (uninstallResult <> 1) and (uninstallResult <> 3010) then
    Result := 'Vorherige Version konnte nicht deinstalliert werden. Fehlercode: ' + IntToStr(uninstallResult);
end;
"""


def sync_final_candidate_installer_files() -> None:
    write_text_file(INSTALLER_VERSION_FILE, build_installer_version_content())
    write_text_file(INNO_SETUP_SCRIPT, build_inno_setup_script_content())


def calculate_file_sha256(path: str) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def get_candidate_installer_path() -> str:
    return os.path.join(OUTPUT_DIR, f"Dronautix_Pointcloud_Uploader_Setup_{APP_VERSION}.exe")


def find_inno_setup() -> str:
    for candidate in INNO_SETUP_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return ""


def cleanup_previous_final_candidate_build() -> None:
    def remove_readonly(func, path, _excinfo):
        os.chmod(path, stat.S_IWRITE)
        func(path)

    for target in (BUILD_DIR, DIST_DIR, OUTPUT_DIR):
        if os.path.isdir(target):
            shutil.rmtree(target, onerror=remove_readonly)
            print(f"[OK] Alter Final-Kandidaten-Ordner entfernt: {target}")


def validate_required_files() -> None:
    required_files = (
        ENTRYPOINT,
        "icon.ico",
        VERSION_INFO_FILE,
        INSTALLER_VERSION_FILE,
        INNO_SETUP_SCRIPT,
        os.path.join("bundled_tools", "PotreeConverter", "PotreeConverter.exe"),
        os.path.join("bundled_tools", "PotreeConverter", "laszip.dll"),
        *GLB_TOOLCHAIN_FILES,
    )
    missing_files = [file for file in required_files if not os.path.exists(file)]
    if missing_files:
        print("[FEHLER] Folgende Dateien fehlen:")
        for file in missing_files:
            print(f"  - {file}")
        raise SystemExit(1)
    glb_toolchain_issues = validate_glb_toolchain_for_packaging()
    if glb_toolchain_issues:
        print("[FEHLER] Die gebündelte GLB-Toolchain ist nicht produktionsbereit:")
        for issue in glb_toolchain_issues:
            print(f"  - {issue}")
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
        *[
            f"--add-data={os.path.abspath(source)}{data_separator}{source}"
            for source in BUNDLED_TOOL_DIRECTORIES
        ],
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
    print(f"  {APP_NAME} {APP_VERSION} - Final-V2 Candidate EXE Builder")
    print("=" * 70)
    print()
    print("[INFO] Dieser Build nutzt isolierte Kandidatenpfade und schreibt kein latest-release.json.")

    contract = build_final_packaging_contract()
    issues = validate_final_packaging_contract(contract)
    if issues:
        print("[FEHLER] Final-V2 Packaging Contract ist ungueltig:")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    sync_final_candidate_version_file()
    sync_final_candidate_installer_files()
    cleanup_previous_final_candidate_build()
    validate_required_files()

    if not validate_build_dependencies():
        return 1

    command = build_command()
    print("Befehl:", " ".join(command))
    print()
    subprocess.run(command, check=True)
    inno_setup = find_inno_setup()
    installer_sha256 = ""
    if inno_setup:
        print("[OK] Inno Setup gefunden - baue isolierten Final-Kandidaten-Installer...")
        subprocess.run([inno_setup, INNO_SETUP_SCRIPT], check=True)
        installer_path = get_candidate_installer_path()
        if os.path.isfile(installer_path):
            installer_sha256 = calculate_file_sha256(installer_path)
    else:
        print("[WARNUNG] Inno Setup nicht gefunden - Kandidaten-Installer wurde nicht gebaut")
    write_candidate_manifest(build_final_packaging_contract(installer_sha256))
    print()
    print("[ERFOLG] Final-V2 Kandidaten-Build erfolgreich:")
    print(f"  {DIST_DIR}/{APP_EXE_NAME}")
    if installer_sha256:
        print(f"  {get_candidate_installer_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
