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
from app_version import (
    APP_EXE_NAME,
    APP_FILE_VERSION,
    APP_ID,
    APP_NAME,
    APP_PUBLISHER,
    APP_VERSION,
)

VERSION_INFO_FILE = "version_info.txt"
INSTALLER_VERSION_FILE = "installer_version.iss"
INNO_SETUP_SCRIPT = "Dronautix_Pointcloud_Uploader.iss"
INNO_SETUP_CANDIDATES = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def write_text_file(path, content):
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        file.write(content)


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
print("[OK] Versionsdateien synchronisiert")

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
    "Dronautix_Pointcloud_Uploader_v7.py",
    "icon.ico",
    VERSION_INFO_FILE,
    INSTALLER_VERSION_FILE,
    INNO_SETUP_SCRIPT,
    os.path.join("bundled_tools", "PotreeConverter", "PotreeConverter.exe"),
    os.path.join("bundled_tools", "PotreeConverter", "laszip.dll"),
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
print()

# PyInstaller Befehl
print("Starte PyInstaller...")
print()

cmd = [
    "pyinstaller",
    "--name=Dronautix_Pointcloud_Uploader",
    "--onefile",                              # Eine einzelne .exe Datei
    "--windowed",                             # Kein Konsolen-Fenster (GUI-App)
    "--icon=icon.ico",                        # Icon einbinden
    f"--version-file={VERSION_INFO_FILE}",    # Windows-Dateiversion
    "--add-data=icon.ico;.",                  # Icon als Ressource
    "--add-data=bundled_tools;bundled_tools", # Integrierten PotreeConverter mitnehmen
    "Dronautix_Pointcloud_Uploader_v7.py"
]

# Für Windows muss das add-data Format angepasst werden
if sys.platform == "win32":
    cmd[6] = "--add-data=icon.ico;."
    cmd[7] = "--add-data=bundled_tools;bundled_tools"
else:
    cmd[6] = "--add-data=icon.ico:."
    cmd[7] = "--add-data=bundled_tools:bundled_tools"

print("Befehl:", " ".join(cmd))
print()

try:
    subprocess.run(cmd, check=True)
    inno_setup = find_inno_setup()
    if inno_setup:
        print("[OK] Inno Setup gefunden - baue Setup...")
        subprocess.run([inno_setup, INNO_SETUP_SCRIPT], check=True)
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
