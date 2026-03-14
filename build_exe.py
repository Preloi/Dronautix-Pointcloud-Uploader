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

print("=" * 70)
print("  Dronautix Pointcloud Uploader - EXE Builder")
print("=" * 70)
print()

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
    "icon.ico"
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
    "--add-data=icon.ico;.",                  # Icon als Ressource
    "Dronautix_Pointcloud_Uploader_v7.py"
]

# Für Windows muss das add-data Format angepasst werden
if sys.platform == "win32":
    cmd[5] = "--add-data=icon.ico;."
else:
    cmd[5] = "--add-data=icon.ico:."

print("Befehl:", " ".join(cmd))
print()

try:
    result = subprocess.run(cmd, check=True)
    print()
    print("=" * 70)
    print("[ERFOLG] BUILD ERFOLGREICH!")
    print("=" * 70)
    print()
    print("Die .exe Datei findest du in:")
    print("  dist/Dronautix_Pointcloud_Uploader.exe")
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
