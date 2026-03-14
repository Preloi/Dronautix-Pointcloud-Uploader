import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinterdnd2 import DND_FILES, TkinterDnD
import os
import subprocess
import uuid
import csv
import urllib.parse
from datetime import datetime, timedelta
import re
import boto3
import mimetypes
import threading
import json
import shutil
import webbrowser
import time
import sys
from app_version import APP_NAME, APP_VERSION, APP_FILE_VERSION

# --- KONFIGURATION (PFADE WERDEN IN EINSTELLUNGEN GESETZT) ---
# Neue: Config in AppData speichern
APPDATA_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'DronautixUploader')
CONFIG_FILE = os.path.join(APPDATA_DIR, 'config.json')
CSV_FILE = os.path.join(APPDATA_DIR, 'Projekt_Liste.csv')

# AppData Ordner erstellen falls nicht vorhanden
os.makedirs(APPDATA_DIR, exist_ok=True)

# AWS & DOMAIN
BUCKET_NAME = "potreedronautix"
REGION_NAME = "eu-central-1"
DOMAIN_URL = "https://pointcloud.dronautix.at/index.html"
UPDATE_SHARE_DIR = r"Z:\03 Apps\Pointcloud uploader"
UPDATE_MANIFEST_FILE = os.path.join(UPDATE_SHARE_DIR, "latest-release.json")

# S3 Pfad für Index-Dateien
S3_INDEX_JSON = "projects_index.json"
S3_DELETED_JSON = "deleted_projects.json"
S3_DELETE_BATCH_SIZE = 1000
DELETED_PROJECT_RETENTION_DAYS = 30
BUNDLED_CONVERTER_DIR = os.path.join("bundled_tools", "PotreeConverter")
BUNDLED_CONVERTER_EXE = "PotreeConverter.exe"
BUNDLED_CONVERTER_DLL = "laszip.dll"

# --- CUSTOMTKINTER EINSTELLUNGEN ---
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Farben
COLOR_SUCCESS = "#2ecc71"
COLOR_SUCCESS_HOVER = "#27ae60"
COLOR_DANGER = "#e74c3c"
COLOR_DANGER_HOVER = "#c0392b"
COLOR_ACCENT = "#3b82f6"
COLOR_ACCENT_HOVER = "#2563eb"
COLOR_PURPLE = "#7c3aed"
COLOR_PURPLE_HOVER = "#6d28d9"
COLOR_SURFACE = "#1e1e2e"
COLOR_CARD = "#2a2a3c"
COLOR_TEXT_DIM = "#94a3b8"


# --- HILFSFUNKTIONEN ---

def sanitize_folder_name(name):
    """Bereinigt Ordnernamen für Dateisystem und S3"""
    name = name.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    name = re.sub(r'[^a-z0-9_]', '_', name)
    return re.sub(r'_+', '_', name).strip('_')


def log(message):
    """Schreibt in das Textfeld der GUI (Thread-safe)"""
    def _log():
        txt_log.configure(state="normal")
        txt_log.insert(tk.END, message + "\n")
        txt_log.see(tk.END)
        txt_log.configure(state="disabled")
    root.after(0, _log)


def save_config(aws_access=None, aws_secret=None, converter_path=None, output_dir=None):
    """Speichert die Einstellungen in AppData"""
    config = load_config()
    
    if aws_access is not None:
        config["aws_access"] = aws_access
    if aws_secret is not None:
        config["aws_secret"] = aws_secret
    if converter_path is not None:
        config["converter_path"] = converter_path
    if output_dir is not None:
        config["output_base_dir"] = output_dir
    
    config["first_run"] = False
    
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        log(f"[WARNUNG] Konnte Config nicht speichern: {e}")
        return False


def load_config():
    """Lädt gespeicherte Einstellungen aus AppData"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except:
            return {"first_run": True}
    return {"first_run": True}


def get_app_base_dir():
    """Ermittelt das Basisverzeichnis für Quellcode und PyInstaller-Builds."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def get_bundled_resource_path(*path_parts):
    """Pfad zu mitgelieferten Ressourcen im Quellcode- und EXE-Modus."""
    return os.path.join(get_app_base_dir(), *path_parts)


def get_bundled_converter_dir():
    """Pfad zum mitgelieferten PotreeConverter-Ordner."""
    return os.path.join(get_app_base_dir(), BUNDLED_CONVERTER_DIR)


def get_bundled_converter_path():
    """Pfad zur mitgelieferten PotreeConverter.exe."""
    return os.path.join(get_bundled_converter_dir(), BUNDLED_CONVERTER_EXE)


def is_converter_bundle_available():
    """Prüft ob der mitgelieferte Converter vollständig vorhanden ist."""
    converter_path = get_bundled_converter_path()
    converter_dll = os.path.join(get_bundled_converter_dir(), BUNDLED_CONVERTER_DLL)
    return os.path.exists(converter_path) and os.path.exists(converter_dll)


def resolve_converter_path(configured_path=""):
    """Nutze optional einen Override-Pfad, sonst den mitgelieferten Converter."""
    if configured_path and os.path.exists(configured_path):
        return configured_path

    if is_converter_bundle_available():
        return get_bundled_converter_path()

    return ""


def parse_version_tuple(version_value):
    """Wandelt Versionen wie 1.0.3 in vergleichbare Tupel um."""
    if not version_value:
        return tuple()

    parts = re.findall(r'\d+', str(version_value))
    return tuple(int(part) for part in parts)


def is_remote_version_newer(remote_version, local_version):
    """Vergleicht zwei Versionsstrings numerisch."""
    remote_tuple = parse_version_tuple(remote_version)
    local_tuple = parse_version_tuple(local_version)

    max_length = max(len(remote_tuple), len(local_tuple))
    remote_tuple += (0,) * (max_length - len(remote_tuple))
    local_tuple += (0,) * (max_length - len(local_tuple))

    return remote_tuple > local_tuple


def load_update_manifest():
    """Lädt das Update-Manifest aus dem Netzwerkverzeichnis."""
    if not os.path.exists(UPDATE_MANIFEST_FILE):
        return None

    try:
        with open(UPDATE_MANIFEST_FILE, "r", encoding="utf-8") as manifest_file:
            return json.load(manifest_file)
    except Exception as e:
        log(f"[UPDATE] Manifest konnte nicht geladen werden: {e}")
        return None


def check_for_available_update():
    """Prüft beim Start, ob im Netzwerkverzeichnis eine neuere Version bereitliegt."""
    try:
        manifest = load_update_manifest()
        if not manifest:
            return

        remote_version = manifest.get("version", "").strip()
        if not remote_version or not is_remote_version_newer(remote_version, APP_VERSION):
            return

        installer_name = manifest.get("installer_name", "")
        installer_path = os.path.join(UPDATE_SHARE_DIR, installer_name) if installer_name else ""
        if not installer_path or not os.path.exists(installer_path):
            log(f"[UPDATE] Neue Version {remote_version} gefunden, aber Installer fehlt: {installer_path or UPDATE_SHARE_DIR}")
            messagebox.showwarning(
                "Update verfügbar",
                f"Version {remote_version} ist verfügbar, aber der Installer wurde nicht gefunden.\n\n"
                f"Erwarteter Pfad:\n{installer_path or UPDATE_SHARE_DIR}"
            )
            return

        install_now = messagebox.askyesno(
            "Update verfügbar",
            f"Es ist eine neue Version verfügbar.\n\n"
            f"Installierte Version: {APP_VERSION}\n"
            f"Verfügbare Version: {remote_version}\n\n"
            f"Soll das Update jetzt installiert werden?"
        )

        log(f"[UPDATE] Neue Version verfügbar: {remote_version} ({installer_path})")

        if not install_now:
            log("[UPDATE] Benutzer hat das Update verschoben")
            return

        try:
            subprocess.Popen([installer_path], shell=False)
            log(f"[UPDATE] Installer gestartet: {installer_path}")
            root.after(200, root.destroy)
        except Exception as install_error:
            log(f"[UPDATE] Installer konnte nicht gestartet werden: {install_error}")
            messagebox.showerror(
                "Update fehlgeschlagen",
                f"Der Installer konnte nicht gestartet werden:\n{installer_path}\n\n{install_error}"
            )
    except Exception as e:
        log(f"[UPDATE] Update-Prüfung fehlgeschlagen: {e}")


def validate_file(filepath):
    """Prüft ob die Datei eine gültige LAS/LAZ oder COPC Datei ist"""
    if not os.path.exists(filepath):
        return False, "Datei existiert nicht"
    filename = os.path.basename(filepath).lower()
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in ['.laz', '.las']:
        return False, "Nur .copc.laz, .laz und .las Dateien werden unterstützt"
    if filename.endswith('.copc.laz'):
        return True, "COPC"
    return True, "OK"


def detect_input_format(filepath):
    """Ermittelt ob eine Datei direkt als COPC hochgeladen werden kann."""
    filename = os.path.basename(filepath).lower()
    return "copc" if filename.endswith(".copc.laz") else "potree"


def cleanup_local_files(output_path):
    """Löscht die lokalen konvertierten Dateien nach erfolgreichem Upload"""
    try:
        if os.path.exists(output_path):
            shutil.rmtree(output_path)
            log(f"[CLEANUP] ✓ Temporäre Dateien gelöscht: {output_path}")
            return True
    except Exception as e:
        log(f"[WARNUNG] Cleanup fehlgeschlagen: {e}")
        return False
    return False


def format_bytes(bytes_size):
    """Formatiert Bytes zu lesbarer Größe"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f} TB"


def get_total_size(files_list):
    """Berechnet die Gesamtgröße aller Dateien"""
    total = 0
    for file_path in files_list:
        if os.path.exists(file_path):
            total += os.path.getsize(file_path)
    return total


def parse_iso_datetime(value):
    """Parst ISO-Zeitstempel robust und liefert None bei ungültigen Werten."""
    if not value:
        return None

    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def prune_deleted_projects(deleted_data):
    """Entfernt Löschhinweise, die älter als die definierte Aufbewahrungszeit sind."""
    deleted_projects = deleted_data.get("deleted_projects", [])
    now = datetime.now()
    retention_delta = timedelta(days=DELETED_PROJECT_RETENTION_DAYS)

    active_projects = []
    for project in deleted_projects:
        deleted_at = parse_iso_datetime(project.get("deleted_at"))
        if deleted_at is None:
            continue

        if deleted_at.tzinfo is not None:
            age = datetime.now(deleted_at.tzinfo) - deleted_at
        else:
            age = now - deleted_at

        if age <= retention_delta:
            active_projects.append(project)

    deleted_data["deleted_projects"] = active_projects
    return deleted_data


def load_projects_index(s3_client):
    """Lädt den bestehenden Projekt-Index von S3"""
    try:
        log(f"[INDEX] Versuche Index zu laden: s3://{BUCKET_NAME}/{S3_INDEX_JSON}")
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=S3_INDEX_JSON)
        data = json.loads(response['Body'].read().decode('utf-8'))
        project_count = len(data.get('projects', []))
        log(f"[INDEX] ✓ Bestehender Index geladen ({project_count} Projekte)")
        return data
    except s3_client.exceptions.NoSuchKey:
        log("[INDEX] Index-Datei existiert noch nicht - erstelle neue")
        return {"projects": [], "last_updated": None}
    except Exception as e:
        log(f"[INDEX] WARNUNG: Index konnte nicht geladen werden: {e}")
        return {"projects": [], "last_updated": None}


def save_projects_index(s3_client, index_data):
    """Speichert den Projekt-Index auf S3"""
    try:
        index_data["last_updated"] = datetime.now().isoformat()
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=S3_INDEX_JSON,
            Body=json.dumps(index_data, indent=2, ensure_ascii=False),
            ContentType='application/json',
            CacheControl='no-cache'
        )
        log(f"[INDEX] JSON-Index gespeichert ({len(index_data['projects'])} Projekte)")
        return True
    except Exception as e:
        log(f"[FEHLER] Index konnte nicht gespeichert werden: {e}")
        return False


def load_deleted_projects(s3_client):
    """Lädt die Liste der gelöschten Projekte von S3"""
    try:
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=S3_DELETED_JSON)
        data = json.loads(response['Body'].read().decode('utf-8'))
        data = prune_deleted_projects(data)
        log(f"[GELÖSCHT] Liste geladen ({len(data.get('deleted_projects', []))} Einträge)")
        return data
    except s3_client.exceptions.NoSuchKey:
        log("[GELÖSCHT] Liste existiert noch nicht - erstelle neue")
        return {"deleted_projects": [], "last_updated": None}
    except Exception as e:
        log(f"[GELÖSCHT] WARNUNG: Liste konnte nicht geladen werden: {e}")
        return {"deleted_projects": [], "last_updated": None}


def save_deleted_projects(s3_client, deleted_data):
    """Speichert die Liste der gelöschten Projekte auf S3"""
    try:
        deleted_data = prune_deleted_projects(deleted_data)
        deleted_data["last_updated"] = datetime.now().isoformat()
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=S3_DELETED_JSON,
            Body=json.dumps(deleted_data, indent=2, ensure_ascii=False),
            ContentType='application/json',
            CacheControl='no-cache, no-store, must-revalidate'
        )
        log(f"[GELÖSCHT] Liste gespeichert ({len(deleted_data['deleted_projects'])} Einträge)")
        return True
    except Exception as e:
        log(f"[FEHLER] Gelöscht-Liste konnte nicht gespeichert werden: {e}")
        return False


def build_deleted_project_entry(project_info, s3_path):
    """Erstellt einen standardisierten Eintrag für deleted_projects.json"""
    return {
        "id": project_info.get("id", ""),
        "kunde": project_info.get("kunde", ""),
        "projekt": project_info.get("projekt", ""),
        "s3_path": s3_path,
        "deleted_at": datetime.now().isoformat(),
        "original_link": project_info.get("link", "")
    }


def upsert_deleted_project(deleted_data, deleted_entry):
    """Aktualisiert einen bestehenden Deleted-Eintrag oder fügt ihn vorne ein."""
    deleted_projects = deleted_data.get("deleted_projects", [])
    filtered_projects = [
        proj for proj in deleted_projects
        if proj.get("s3_path") != deleted_entry["s3_path"]
        and proj.get("id") != deleted_entry["id"]
    ]
    filtered_projects.insert(0, deleted_entry)
    deleted_data["deleted_projects"] = filtered_projects
    return deleted_data


def remove_project_from_index(index_data, project_id):
    """Entfernt ein Projekt aus dem Index und gibt True zurück, wenn sich der Index geändert hat."""
    original_count = len(index_data.get("projects", []))
    index_data["projects"] = [
        project for project in index_data.get("projects", [])
        if project.get("id") != project_id
    ]
    return len(index_data["projects"]) != original_count


def collect_project_objects(s3_client, s3_path):
    """Sammelt alle S3-Objekte unter einem Projektpräfix."""
    paginator = s3_client.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=BUCKET_NAME, Prefix=s3_path)

    object_keys = []
    for page in pages:
        for obj in page.get('Contents', []):
            object_keys.append(obj['Key'])

    return object_keys


def delete_s3_objects(s3_client, object_keys):
    """Löscht S3-Objekte in Batches und bricht bei partiellen Fehlern ab."""
    if not object_keys:
        return 0

    deleted_count = 0
    for start_index in range(0, len(object_keys), S3_DELETE_BATCH_SIZE):
        batch_keys = object_keys[start_index:start_index + S3_DELETE_BATCH_SIZE]
        response = s3_client.delete_objects(
            Bucket=BUCKET_NAME,
            Delete={'Objects': [{'Key': key} for key in batch_keys]}
        )

        errors = response.get("Errors", [])
        if errors:
            first_error = errors[0]
            raise RuntimeError(
                f"S3 DeleteObjects Fehler für {first_error.get('Key', 'unbekannt')}: "
                f"{first_error.get('Code', 'Unknown')} - {first_error.get('Message', '')}"
            )

        deleted_count += len(batch_keys)

    return deleted_count


def delete_project_transaction(s3_client, project_info):
    """Löscht Projektdaten und aktualisiert die Metadaten so robust wie möglich."""
    s3_path = project_info.get("s3_path", "")
    project_id = project_info.get("id", "")

    if not s3_path:
        return {
            "success": False,
            "partial": False,
            "message": "S3-Pfad nicht gefunden."
        }

    try:
        log(f"[LÖSCHEN] Sammle Dateien unter: {s3_path}")
        object_keys = collect_project_objects(s3_client, s3_path)
        if object_keys:
            log(f"[LÖSCHEN] Lösche {len(object_keys)} Dateien aus S3...")
            deleted_count = delete_s3_objects(s3_client, object_keys)
            log(f"[LÖSCHEN] ✓ {deleted_count} Dateien gelöscht")
        else:
            log(f"[LÖSCHEN] Keine Dateien gefunden unter: {s3_path}")

        metadata_errors = []

        log("[LÖSCHEN] Aktualisiere Gelöscht-Liste...")
        deleted_data = load_deleted_projects(s3_client)
        deleted_entry = build_deleted_project_entry(project_info, s3_path)
        deleted_data = upsert_deleted_project(deleted_data, deleted_entry)
        if save_deleted_projects(s3_client, deleted_data):
            log("[LÖSCHEN] ✓ Gelöscht-Liste aktualisiert")
        else:
            metadata_errors.append("deleted_projects.json")

        log("[LÖSCHEN] Aktualisiere Projekt-Index...")
        index_data = load_projects_index(s3_client)
        removed_from_index = remove_project_from_index(index_data, project_id)
        if removed_from_index:
            if save_projects_index(s3_client, index_data):
                log("[LÖSCHEN] ✓ Projekt-Index aktualisiert")
            else:
                metadata_errors.append("projects_index.json")
        else:
            log("[LÖSCHEN] Projekt war bereits nicht mehr im Index")

        if metadata_errors:
            files = ", ".join(metadata_errors)
            return {
                "success": False,
                "partial": True,
                "message": (
                    "Projektdaten wurden in S3 gelöscht, aber folgende Metadaten "
                    f"konnten nicht vollständig aktualisiert werden: {files}"
                )
            }

        return {
            "success": True,
            "partial": False,
            "message": "Projekt wurde gelöscht und alle Metadaten wurden aktualisiert."
        }
    except Exception as e:
        log(f"[FEHLER] Löschen fehlgeschlagen: {e}")
        return {
            "success": False,
            "partial": False,
            "message": f"Löschen fehlgeschlagen: {e}"
        }


# ============================================================
#  UPLOAD-FORTSCHRITT CALLBACK  (Echtzeit pro Chunk)
# ============================================================

class UploadProgress:
    """Callback-Klasse für boto3 upload_file – wird bei jedem Chunk aufgerufen."""

    def __init__(self, total_size, on_progress):
        self._total = total_size
        self._uploaded = 0
        self._on_progress = on_progress
        self._lock = threading.Lock()
        self._last_update = 0

    def __call__(self, bytes_amount):
        with self._lock:
            self._uploaded += bytes_amount
            now = time.time()
            if now - self._last_update >= 0.066 or self._uploaded >= self._total:
                self._last_update = now
                self._on_progress(self._uploaded, self._total)


# --- HAUPTPROZESS (THREAD) ---

def run_process(laz_file, kunde, projekt, aws_access, aws_secret):
    config = load_config()
    configured_converter_path = config.get("converter_path", "")
    converter_path = resolve_converter_path(configured_converter_path)
    output_base_dir = config.get("output_base_dir", "")
    
    try:
        # Reset Fortschritt
        root.after(0, reset_progress)

        # 1. Prüfungen
        root.after(0, lambda: update_step("Prüfe Datei...", 1))
        root.after(0, lambda: progress_bar.set(0))

        valid, msg = validate_file(laz_file)
        if not valid:
            log(f"[FEHLER] {msg}")
            messagebox.showerror("Fehler", msg)
            return

        input_format = detect_input_format(laz_file)
        is_copc = input_format == "copc"

        if not aws_access or not aws_secret:
            log("[FEHLER] Bitte AWS Keys in den Einstellungen eingeben!")
            messagebox.showwarning("Fehler", "Bitte AWS Zugangsdaten in den Einstellungen eingeben!")
            return

        if not is_copc and not converter_path:
            log("[FEHLER] Kein Potree Converter verfügbar")
            messagebox.showwarning(
                "Fehler",
                "Mitgelieferter Potree Converter nicht gefunden. "
                "Bitte Build/Projektdateien prüfen oder optional einen Override-Pfad konfigurieren!"
            )
            return

        if not is_copc and not output_base_dir:
            log("[FEHLER] Output-Ordner nicht konfiguriert!")
            messagebox.showwarning("Fehler", "Bitte einen Output-Ordner in den Einstellungen angeben!")
            return

        log(f"[DATEI] ✓ Datei ist gültig: {os.path.basename(laz_file)}")
        log(f"[FORMAT] {'COPC Direkt-Upload' if is_copc else 'LAS/LAZ mit Potree Converter'}")
        log(f"[KUNDE] {kunde}")
        log(f"[PROJEKT] {projekt}")

        # Sanitize Namen und ID generieren
        folder_kunde = sanitize_folder_name(kunde)
        folder_id = uuid.uuid4().hex[:6]  # 6 Zeichen ID
        folder_projekt = sanitize_folder_name(projekt)

        log(f"[ID] {folder_id}")

        output_dir = None

        # 2. Dateien vorbereiten
        if is_copc:
            root.after(0, lambda: update_step("Bereite COPC für Upload vor...", 2))
            root.after(0, lambda: progress_bar.set(1))
            log("[COPC] Direkter Upload ohne Potree Converter")
        else:
            root.after(0, lambda: update_step("Konvertiere mit Potree...", 2))
            root.after(0, lambda: progress_bar.set(0))

            # Temporärer Output-Ordner: kunde/id/projekt
            output_dir = os.path.join(output_base_dir, folder_kunde, folder_id, folder_projekt)
            os.makedirs(output_dir, exist_ok=True)

            log(f"[KONVERTIERUNG] Starte Potree Converter...")
            log(f"[CONVERTER] {converter_path}")
            log(f"[OUTPUT] {output_dir}")

            cmd = [converter_path, laz_file, "-o", output_dir, "--overwrite"]
            
            process = subprocess.Popen(
                cmd,
                cwd=os.path.dirname(converter_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )

            for line in process.stdout:
                line = line.strip()
                if line:
                    log(f"[POTREE] {line}")
                    if "%" in line:
                        try:
                            percent_str = re.search(r'(\d+)%', line)
                            if percent_str:
                                percent = int(percent_str.group(1))
                                root.after(0, lambda p=percent: progress_bar.set(p / 100))
                        except:
                            pass

            process.wait()

            if process.returncode != 0:
                log(f"[FEHLER] Potree Converter fehlgeschlagen (Exit Code: {process.returncode})")
                messagebox.showerror("Fehler", "Potree Konvertierung fehlgeschlagen!")
                return

            log("[KONVERTIERUNG] ✓ Potree Konvertierung abgeschlossen")
            root.after(0, lambda: progress_bar.set(1))

        # 3. S3 Upload
        root.after(0, lambda: update_step("Lade zu S3 hoch...", 3))
        root.after(0, lambda: progress_bar.set(0))

        try:
            s3_client = boto3.client(
                's3',
                aws_access_key_id=aws_access,
                aws_secret_access_key=aws_secret,
                region_name=REGION_NAME
            )
            log("[S3] ✓ Verbindung hergestellt")
        except Exception as e:
            log(f"[FEHLER] S3 Verbindung fehlgeschlagen: {e}")
            messagebox.showerror("Fehler", f"AWS Verbindung fehlgeschlagen:\n{e}")
            return

        # S3 Pfad: pointclouds/{kunde}/{id}/{projekt}
        s3_prefix = f"pointclouds/{folder_kunde}/{folder_id}/{folder_projekt}"
        log(f"[S3] Ziel-Pfad: {s3_prefix}")

        # Sammle alle Dateien
        files_to_upload = []
        if is_copc:
            files_to_upload.append((laz_file, f"{s3_prefix}/source.copc.laz"))
        else:
            for root_dir, dirs, files in os.walk(output_dir):
                for file in files:
                    local_path = os.path.join(root_dir, file)
                    rel_path = os.path.relpath(local_path, output_dir)
                    s3_key = f"{s3_prefix}/{rel_path}".replace("\\", "/")
                    files_to_upload.append((local_path, s3_key))

        if not files_to_upload:
            log("[FEHLER] Keine Dateien zum Upload gefunden!")
            messagebox.showwarning("Fehler", "Keine Dateien zum Upload gefunden!")
            return

        total_size = get_total_size([f[0] for f in files_to_upload])
        log(f"[UPLOAD] {len(files_to_upload)} Dateien ({format_bytes(total_size)})")

        uploaded_total = 0

        def update_upload_progress(bytes_uploaded, file_total):
            nonlocal uploaded_total
            progress = (uploaded_total + bytes_uploaded) / total_size
            root.after(0, lambda: progress_bar.set(progress))
            root.after(0, lambda: progress_detail.configure(
                text=f"{format_bytes(uploaded_total + bytes_uploaded)} / {format_bytes(total_size)}"
            ))

        for idx, (local_path, s3_key) in enumerate(files_to_upload, 1):
            file_size = os.path.getsize(local_path)
            log(f"[{idx}/{len(files_to_upload)}] {os.path.basename(local_path)} ({format_bytes(file_size)})")

            content_type, _ = mimetypes.guess_type(local_path)
            if not content_type:
                content_type = 'application/octet-stream'

            progress_callback = UploadProgress(file_size, update_upload_progress)

            # Cache-Control Header: verhindert Browser-Caching
            s3_client.upload_file(
                local_path, BUCKET_NAME, s3_key,
                ExtraArgs={
                    'ContentType': content_type,
                    'CacheControl': 'no-cache, no-store, must-revalidate, max-age=0'
                },
                Callback=progress_callback
            )

            uploaded_total += file_size

        root.after(0, lambda: progress_bar.set(1))
        log("[UPLOAD] ✓ Alle Dateien hochgeladen")

        # 4. Index aktualisieren
        root.after(0, lambda: update_step("Aktualisiere Index...", 4))
        log("[INDEX] Aktualisiere Projekt-Index...")

        timestamp = datetime.now().isoformat()

        # Link erstellen. COPC-Projekte verlinken direkt auf die Datei,
        # klassische Projekte bleiben bei der bisherigen metadata.json Logik.
        if is_copc:
            path_param = f"{folder_kunde}/{folder_id}/{folder_projekt}/source.copc.laz"
        else:
            path_param = f"{folder_kunde}/{folder_id}/{folder_projekt}"
        display_name = f"{kunde} - {projekt}"
        safe_name = urllib.parse.quote(display_name)
        project_url = f"{DOMAIN_URL}?id={path_param}&name={safe_name}"

        # S3 Index aktualisieren
        index_data = load_projects_index(s3_client)
        new_project = {
            "datum": timestamp,
            "kunde": kunde,
            "id": folder_id,
            "projekt": projekt,
            "format": input_format,
            "link": project_url,
            "viewer_path": path_param,
            "s3_path": s3_prefix
        }
        index_data["projects"].insert(0, new_project)

        save_projects_index(s3_client, index_data)

        # Lokale CSV
        try:
            file_exists = os.path.exists(CSV_FILE)
            datum = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(CSV_FILE, mode='a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter=';')
                if not file_exists:
                    writer.writerow(["ID", "Kunde", "Projekt", "Datum", "Link"])
                writer.writerow([folder_id, kunde, projekt, datum, project_url])
            log("[CSV] ✓ Lokale CSV aktualisiert")
        except Exception as e:
            log(f"[WARNUNG] CSV konnte nicht aktualisiert werden: {e}")

        # 5. Cleanup - Temporäre Dateien löschen
        root.after(0, lambda: update_step("Räume auf...", 5))
        if is_copc:
            log("[CLEANUP] Kein lokaler Cleanup nötig für COPC Upload")
        else:
            log("[CLEANUP] Lösche temporäre Dateien...")
            
            cleanup_success = cleanup_local_files(output_dir)
            if cleanup_success:
                log("[CLEANUP] ✓ Temporärer Ordner erfolgreich gelöscht")
            else:
                log("[CLEANUP] ⚠ Temporärer Ordner konnte nicht vollständig gelöscht werden")

        # Fertig
        root.after(0, lambda: update_step("✓ Fertig!", 5))
        root.after(0, lambda: progress_bar.set(1))
        root.after(0, lambda: entry_link.delete(0, tk.END))
        root.after(0, lambda: entry_link.insert(0, project_url))

        log("═" * 50)
        log("✓ UPLOAD ERFOLGREICH ABGESCHLOSSEN")
        log(f"Projekt-Link: {project_url}")
        log("═" * 50)

        save_config(aws_access=aws_access, aws_secret=aws_secret)

        root.after(0, lambda: messagebox.showinfo(
            "Erfolg",
            f"Upload erfolgreich!\n\nProjekt: {projekt}\n\nLink wurde kopiert."
        ))
        root.after(0, lambda: root.clipboard_clear())
        root.after(0, lambda: root.clipboard_append(project_url))

    except Exception as e:
        log(f"[FEHLER] {str(e)}")
        import traceback
        log(traceback.format_exc())
        root.after(0, lambda: messagebox.showerror("Fehler", f"Unerwarteter Fehler:\n{e}"))


# --- GUI CALLBACKS ---

def select_file():
    file = filedialog.askopenfilename(
        title="LAS/LAZ/COPC Datei auswählen",
        filetypes=[("Point Cloud", "*.copc.laz *.laz *.las"), ("Alle Dateien", "*.*")]
    )
    if file:
        entry_file.delete(0, tk.END)
        entry_file.insert(0, file)
        log(f"[DATEI] Ausgewählt: {os.path.basename(file)}")


def drop_file(event):
    file_path = event.data.strip('{}')
    if os.path.isfile(file_path):
        entry_file.delete(0, tk.END)
        entry_file.insert(0, file_path)
        log(f"[DRAG & DROP] Datei: {os.path.basename(file_path)}")


def test_aws_connection():
    """Testet die AWS Verbindung"""
    config = load_config()
    access = config.get("aws_access", "")
    secret = config.get("aws_secret", "")
    
    if not access or not secret:
        messagebox.showwarning("Fehler", "Bitte AWS Zugangsdaten in den Einstellungen eingeben!")
        return

    try:
        log("[TEST] Teste AWS Verbindung...")
        s3 = boto3.client(
            's3',
            aws_access_key_id=access,
            aws_secret_access_key=secret,
            region_name=REGION_NAME
        )
        s3.head_bucket(Bucket=BUCKET_NAME)
        log("[TEST] ✓ Verbindung erfolgreich!")
        messagebox.showinfo("Erfolg", "AWS Verbindung erfolgreich!")
    except Exception as e:
        log(f"[TEST] ✗ Verbindung fehlgeschlagen: {e}")
        messagebox.showerror("Fehler", f"Verbindung fehlgeschlagen:\n{e}")


def start_thread():
    """Startet den Upload-Prozess in einem Thread"""
    laz = entry_file.get().strip()
    kunde = entry_kunde.get().strip()
    projekt = entry_proj.get().strip()

    if not laz:
        messagebox.showwarning("Fehler", "Bitte eine LAZ/LAS Datei auswählen!")
        return
    if not kunde or not projekt:
        messagebox.showwarning("Fehler", "Bitte Kunde und Projekt eingeben!")
        return

    config = load_config()
    aws_access = config.get("aws_access", "")
    aws_secret = config.get("aws_secret", "")

    btn_start.configure(state="disabled", text="⏳  Läuft...")
    thread = threading.Thread(
        target=run_process,
        args=(laz, kunde, projekt, aws_access, aws_secret),
        daemon=True
    )
    thread.start()

    def check_thread():
        if thread.is_alive():
            root.after(100, check_thread)
        else:
            btn_start.configure(state="normal", text="▶   STARTEN  –  Konvertieren & Upload")

    root.after(100, check_thread)


def update_step(text, step):
    """Aktualisiert die Schritt-Anzeige"""
    progress_step.configure(text=f"Schritt {step}/5: {text}")


def reset_progress():
    """Setzt die Fortschrittsanzeige zurück"""
    progress_bar.set(0)
    progress_detail.configure(text="")
    progress_step.configure(text="")


def open_projects_window():
    """Öffnet das Projektübersicht-Fenster mit verbesserter Darstellung"""
    config = load_config()
    aws_access = config.get("aws_access", "")
    aws_secret = config.get("aws_secret", "")
    
    if not aws_access or not aws_secret:
        messagebox.showwarning("Fehler", "Bitte AWS Zugangsdaten in den Einstellungen eingeben!")
        return

    proj_window = ctk.CTkToplevel(root)
    proj_window.title("Projekt-Übersicht")
    proj_window.geometry("1100x750")

    # Fenster im Vordergrund halten und fokussieren
    proj_window.lift()
    proj_window.focus_force()
    proj_window.attributes('-topmost', True)
    proj_window.after(200, lambda: proj_window.attributes('-topmost', False))

    # Header
    header = ctk.CTkFrame(proj_window, fg_color=COLOR_CARD, corner_radius=0)
    header.pack(fill="x", pady=(0, 8))

    ctk.CTkLabel(
        header,
        text="📊  Projekt-Übersicht",
        font=ctk.CTkFont(size=18, weight="bold")
    ).pack(side="left", padx=20, pady=16)

    btn_refresh = ctk.CTkButton(
        header, text="🔄  Aktualisieren",
        fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
        font=ctk.CTkFont(size=12), height=32, width=140,
        command=lambda: load_projects()
    )
    btn_refresh.pack(side="right", padx=20, pady=12)

    # Filter-Bereich
    filter_frame = ctk.CTkFrame(proj_window, fg_color=COLOR_CARD, corner_radius=8)
    filter_frame.pack(fill="x", padx=16, pady=(0, 8))

    filter_inner = ctk.CTkFrame(filter_frame, fg_color="transparent")
    filter_inner.pack(fill="x", padx=16, pady=12)

    ctk.CTkLabel(
        filter_inner,
        text="🔍",
        font=ctk.CTkFont(size=14)
    ).pack(side="left", padx=(0, 8))

    # Filter Typ Auswahl
    filter_type = ctk.CTkComboBox(
        filter_inner,
        values=["Alle", "Kunde", "Projekt"],
        width=120,
        font=ctk.CTkFont(size=11),
        state="readonly"
    )
    filter_type.set("Alle")
    filter_type.pack(side="left", padx=(0, 8))

    # Such-Eingabefeld
    search_entry = ctk.CTkEntry(
        filter_inner,
        placeholder_text="Suchen...",
        font=ctk.CTkFont(size=11),
        width=250
    )
    search_entry.pack(side="left", padx=(0, 8))

    def apply_filter():
        load_projects(filter_type.get(), search_entry.get().strip())

    def on_search_key(event):
        apply_filter()

    search_entry.bind('<KeyRelease>', on_search_key)

    ctk.CTkButton(
        filter_inner,
        text="Filter anwenden",
        font=ctk.CTkFont(size=11),
        width=120,
        height=28,
        command=apply_filter
    ).pack(side="left", padx=(0, 8))

    ctk.CTkButton(
        filter_inner,
        text="✕ Zurücksetzen",
        font=ctk.CTkFont(size=11),
        width=120,
        height=28,
        fg_color="transparent",
        border_width=1,
        command=lambda: (filter_type.set("Alle"), search_entry.delete(0, tk.END), load_projects())
    ).pack(side="left")

    # Tabelle mit verbesserter Spaltenbreite
    table_frame = ctk.CTkFrame(proj_window, fg_color="transparent")
    table_frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    # Scrollbars
    scroll_y = ctk.CTkScrollbar(table_frame, orientation="vertical")
    scroll_y.pack(side="right", fill="y")

    scroll_x = ctk.CTkScrollbar(table_frame, orientation="horizontal")
    scroll_x.pack(side="bottom", fill="x")

    # Treeview mit optimierten Spaltenbreiten
    tree = ttk.Treeview(
        table_frame,
        columns=("id", "kunde", "projekt", "datum", "url"),
        show="headings",
        yscrollcommand=scroll_y.set,
        xscrollcommand=scroll_x.set,
        height=20
    )

    # Optimierte Spaltenbreiten
    tree.heading("id", text="ID")
    tree.heading("kunde", text="Kunde")
    tree.heading("projekt", text="Projekt")
    tree.heading("datum", text="Datum")
    tree.heading("url", text="Web-Link")

    tree.column("id", width=80, anchor="center")  # Schmaler
    tree.column("kunde", width=150)  # Schmaler
    tree.column("projekt", width=150)  # Schmaler
    tree.column("datum", width=140, anchor="center")  # Schmaler
    tree.column("url", width=520)  # Breiter für Links

    tree.pack(fill="both", expand=True)

    scroll_y.configure(command=tree.yview)
    scroll_x.configure(command=tree.xview)

    # Style
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Treeview",
                    background=COLOR_CARD,
                    foreground="#e2e8f0",
                    fieldbackground=COLOR_CARD,
                    borderwidth=0,
                    font=("Segoe UI", 10))
    style.configure("Treeview.Heading",
                    background=COLOR_SURFACE,
                    foreground="#e2e8f0",
                    font=("Segoe UI", 10, "bold"))
    style.map("Treeview", background=[("selected", COLOR_ACCENT)])

    # Button Frame
    btn_frame = ctk.CTkFrame(proj_window, fg_color="transparent")
    btn_frame.pack(fill="x", padx=16, pady=(0, 8))

    def open_in_browser():
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("Info", "Bitte ein Projekt auswählen!")
            return
        item = tree.item(selected[0])
        url = item['values'][4]  # URL ist jetzt in Spalte 4
        webbrowser.open(url)

    def copy_link():
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("Info", "Bitte ein Projekt auswählen!")
            return
        item = tree.item(selected[0])
        url = item['values'][4]
        proj_window.clipboard_clear()
        proj_window.clipboard_append(url)
        messagebox.showinfo("Kopiert", "Link in Zwischenablage kopiert!")

    def delete_project():
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("Info", "Bitte ein Projekt auswählen!")
            return

        item = tree.item(selected[0])
        projekt_name = item['values'][2]

        result = messagebox.askyesno(
            "Löschen bestätigen",
            f"Projekt '{projekt_name}' wirklich löschen?\n\n"
            "Dies löscht alle Dateien aus dem S3 Storage!"
        )

        if not result:
            return

        try:
            s3_client = boto3.client(
                's3',
                aws_access_key_id=aws_access,
                aws_secret_access_key=aws_secret,
                region_name=REGION_NAME
            )

            # Finde Projekt im Index
            index_data = load_projects_index(s3_client)
            projekt_id = item['values'][0]

            project_to_delete = None
            for proj in index_data["projects"]:
                if proj["id"] == projekt_id:
                    project_to_delete = proj
                    break

            if not project_to_delete:
                messagebox.showerror("Fehler", "Projekt nicht im Index gefunden!")
                return

            delete_result = delete_project_transaction(s3_client, project_to_delete)

            if delete_result["success"]:
                messagebox.showinfo("Erfolg", "Projekt wurde gelöscht und Link deaktiviert!")
                load_projects()
            elif delete_result.get("partial"):
                messagebox.showwarning("Teilweise gelöscht", delete_result["message"])
                load_projects()
            else:
                messagebox.showerror("Fehler", delete_result["message"])

        except Exception as e:
            messagebox.showerror("Fehler", f"Fehler beim Löschen:\n{e}")

    ctk.CTkButton(
        btn_frame, text="🌐  Im Browser öffnen",
        fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
        font=ctk.CTkFont(size=12), height=36,
        command=open_in_browser
    ).pack(side="left", padx=(0, 8))

    ctk.CTkButton(
        btn_frame, text="📋  Link kopieren",
        fg_color=COLOR_PURPLE, hover_color=COLOR_PURPLE_HOVER,
        font=ctk.CTkFont(size=12), height=36,
        command=copy_link
    ).pack(side="left", padx=(0, 8))

    ctk.CTkButton(
        btn_frame, text="🗑  Löschen",
        fg_color=COLOR_DANGER, hover_color=COLOR_DANGER_HOVER,
        font=ctk.CTkFont(size=12), height=36,
        command=delete_project
    ).pack(side="left")

    def load_projects(filter_by="Alle", search_term=""):
        """Lädt Projekte von S3 und wendet Filter an"""
        for item in tree.get_children():
            tree.delete(item)

        try:
            s3_client = boto3.client(
                's3',
                aws_access_key_id=aws_access,
                aws_secret_access_key=aws_secret,
                region_name=REGION_NAME
            )

            index_data = load_projects_index(s3_client)
            projects = index_data.get("projects", [])

            if not projects:
                tree.insert("", "end", values=("", "", "Keine Projekte gefunden", "", ""))
                return

            # Sortiere nach Datum (neueste zuerst)
            projects.sort(key=lambda x: x.get("datum", ""), reverse=True)

            # Filter anwenden
            filtered_projects = []
            search_lower = search_term.lower()
            
            for proj in projects:
                kunde = proj.get("kunde", "").lower()
                projekt = proj.get("projekt", "").lower()
                
                if filter_by == "Alle":
                    # Suche in Kunde ODER Projekt
                    if not search_term or search_lower in kunde or search_lower in projekt:
                        filtered_projects.append(proj)
                elif filter_by == "Kunde":
                    # Suche nur in Kunde
                    if not search_term or search_lower in kunde:
                        filtered_projects.append(proj)
                elif filter_by == "Projekt":
                    # Suche nur in Projekt
                    if not search_term or search_lower in projekt:
                        filtered_projects.append(proj)

            if not filtered_projects:
                tree.insert("", "end", values=("", "", "Keine passenden Projekte gefunden", "", ""))
                return

            for proj in filtered_projects:
                # Formatiere Datum für bessere Lesbarkeit
                datum_str = proj.get("datum", "")
                if datum_str and "T" in datum_str:
                    # ISO Format zu lesbarem Format konvertieren
                    try:
                        dt = datetime.fromisoformat(datum_str)
                        datum_str = dt.strftime("%Y-%m-%d %H:%M")
                    except:
                        pass
                
                tree.insert("", "end", values=(
                    proj.get("id", ""),
                    proj.get("kunde", ""),
                    proj.get("projekt", ""),
                    datum_str,
                    proj.get("link", "")  # Link wird jetzt angezeigt
                ))

        except Exception as e:
            messagebox.showerror("Fehler", f"Laden fehlgeschlagen:\n{e}")

    load_projects()


def open_settings_window(first_run=False):
    """Öffnet das Einstellungs-Fenster"""
    settings_window = ctk.CTkToplevel(root)
    settings_window.title("Einstellungen")
    settings_window.geometry("600x500")
    
    if first_run:
        # Bei erstem Start Modal machen
        settings_window.transient(root)
        settings_window.grab_set()

    # Header
    header = ctk.CTkFrame(settings_window, fg_color=COLOR_CARD, corner_radius=0)
    header.pack(fill="x", pady=(0, 16))

    ctk.CTkLabel(
        header,
        text="⚙️  Einstellungen",
        font=ctk.CTkFont(size=18, weight="bold")
    ).pack(padx=20, pady=16)

    if first_run:
        ctk.CTkLabel(
            settings_window,
            text="Willkommen beim Dronautix Pointcloud Uploader!\n"
                 "Bitte konfigurieren Sie die Anwendung vor dem ersten Gebrauch.",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_DIM,
            wraplength=550
        ).pack(padx=20, pady=(0, 20))

    # Hauptframe für Einstellungen
    main_frame = ctk.CTkScrollableFrame(settings_window, fg_color="transparent")
    main_frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    # AWS Einstellungen
    aws_card = ctk.CTkFrame(main_frame, corner_radius=12)
    aws_card.pack(fill="x", pady=(0, 12))

    ctk.CTkLabel(
        aws_card,
        text="AWS Zugangsdaten",
        font=ctk.CTkFont(size=14, weight="bold")
    ).pack(anchor="w", padx=16, pady=(14, 8))

    # Access Key
    ctk.CTkLabel(
        aws_card,
        text="AWS Access Key:",
        font=ctk.CTkFont(size=11),
        text_color=COLOR_TEXT_DIM
    ).pack(anchor="w", padx=16, pady=(4, 2))
    
    entry_aws_access = ctk.CTkEntry(
        aws_card,
        placeholder_text="AKIA...",
        font=ctk.CTkFont(size=11),
        height=32
    )
    entry_aws_access.pack(fill="x", padx=16, pady=(0, 8))

    # Secret Key
    ctk.CTkLabel(
        aws_card,
        text="AWS Secret Key:",
        font=ctk.CTkFont(size=11),
        text_color=COLOR_TEXT_DIM
    ).pack(anchor="w", padx=16, pady=(4, 2))
    
    entry_aws_secret = ctk.CTkEntry(
        aws_card,
        placeholder_text="Geheimer Schlüssel",
        font=ctk.CTkFont(size=11),
        show="•",
        height=32
    )
    entry_aws_secret.pack(fill="x", padx=16, pady=(0, 8))

    # AWS Test Button
    def test_aws_in_settings():
        access = entry_aws_access.get().strip()
        secret = entry_aws_secret.get().strip()
        
        if not access or not secret:
            messagebox.showwarning("Fehler", "Bitte beide AWS Zugangsdaten eingeben!")
            return
        
        try:
            log("[TEST] Teste AWS Verbindung...")
            s3 = boto3.client(
                's3',
                aws_access_key_id=access,
                aws_secret_access_key=secret,
                region_name=REGION_NAME
            )
            s3.head_bucket(Bucket=BUCKET_NAME)
            log("[TEST] ✓ AWS S3 Verbindung hergestellt")
            messagebox.showinfo("Erfolg", "AWS S3 Verbindung hergestellt")
        except Exception as e:
            log(f"[TEST] ✗ Verbindung fehlgeschlagen: {e}")
            messagebox.showerror("Fehler", f"Verbindung fehlgeschlagen:\n{e}")
    
    ctk.CTkButton(
        aws_card,
        text="🔗  Verbindung testen",
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        font=ctk.CTkFont(size=12),
        height=36,
        command=test_aws_in_settings
    ).pack(fill="x", padx=16, pady=(0, 14))

    # Potree Converter Einstellungen
    converter_card = ctk.CTkFrame(main_frame, corner_radius=12)
    converter_card.pack(fill="x", pady=(0, 12))

    ctk.CTkLabel(
        converter_card,
        text="Integrierter Potree Converter",
        font=ctk.CTkFont(size=14, weight="bold")
    ).pack(anchor="w", padx=16, pady=(14, 8))

    ctk.CTkLabel(
        converter_card,
        text="Die App bringt PotreeConverter.exe und laszip.dll mit. Für klassische LAS/LAZ Uploads ist keine externe Installation mehr nötig.",
        font=ctk.CTkFont(size=10),
        text_color=COLOR_TEXT_DIM,
        wraplength=540
    ).pack(anchor="w", padx=16, pady=(0, 8))

    bundled_converter_status = (
        f"Mitgeliefert: {get_bundled_converter_path()}"
        if is_converter_bundle_available()
        else "Mitgelieferter Converter aktuell nicht gefunden"
    )
    ctk.CTkLabel(
        converter_card,
        text=bundled_converter_status,
        font=ctk.CTkFont(size=10),
        text_color=COLOR_TEXT_DIM,
        wraplength=540
    ).pack(anchor="w", padx=16, pady=(0, 8))

    ctk.CTkLabel(
        converter_card,
        text="Optionaler Override-Pfad zur PotreeConverter.exe:",
        font=ctk.CTkFont(size=11),
        text_color=COLOR_TEXT_DIM
    ).pack(anchor="w", padx=16, pady=(4, 2))
    
    converter_frame = ctk.CTkFrame(converter_card, fg_color="transparent")
    converter_frame.pack(fill="x", padx=16, pady=(0, 8))
    
    entry_converter = ctk.CTkEntry(
        converter_frame,
        placeholder_text="Leer lassen, um den integrierten Converter zu verwenden",
        font=ctk.CTkFont(family="Consolas", size=10),
        height=32
    )
    entry_converter.pack(side="left", fill="x", expand=True, padx=(0, 8))

    def browse_converter():
        file = filedialog.askopenfilename(
            title="PotreeConverter.exe wählen",
            filetypes=[("Executable", "*.exe"), ("Alle Dateien", "*.*")]
        )
        if file:
            entry_converter.delete(0, tk.END)
            entry_converter.insert(0, file)

    ctk.CTkButton(
        converter_frame,
        text="📁",
        width=40,
        command=browse_converter
    ).pack(side="right")

    # Output Ordner
    ctk.CTkLabel(
        converter_card,
        text="Temporärer Output-Ordner:",
        font=ctk.CTkFont(size=11),
        text_color=COLOR_TEXT_DIM
    ).pack(anchor="w", padx=16, pady=(4, 2))
    
    output_frame = ctk.CTkFrame(converter_card, fg_color="transparent")
    output_frame.pack(fill="x", padx=16, pady=(0, 14))
    
    entry_output = ctk.CTkEntry(
        output_frame,
        placeholder_text="C:\\...\\Potree_Output",
        font=ctk.CTkFont(family="Consolas", size=10),
        height=32
    )
    entry_output.pack(side="left", fill="x", expand=True, padx=(0, 8))

    def browse_output():
        folder = filedialog.askdirectory(title="Output-Ordner wählen")
        if folder:
            entry_output.delete(0, tk.END)
            entry_output.insert(0, folder)

    ctk.CTkButton(
        output_frame,
        text="📁",
        width=40,
        command=browse_output
    ).pack(side="right")

    # Lade bestehende Konfiguration
    config = load_config()
    if config.get("aws_access"):
        entry_aws_access.insert(0, config["aws_access"])
    if config.get("aws_secret"):
        entry_aws_secret.insert(0, config["aws_secret"])
    if config.get("converter_path"):
        entry_converter.insert(0, config["converter_path"])
    if config.get("output_base_dir"):
        entry_output.insert(0, config["output_base_dir"])

    # Speichern Button
    def save_settings():
        aws_access = entry_aws_access.get().strip()
        aws_secret = entry_aws_secret.get().strip()
        converter_path = entry_converter.get().strip()
        output_dir = entry_output.get().strip()

        if not aws_access or not aws_secret:
            messagebox.showwarning("Fehler", "Bitte AWS Zugangsdaten eingeben!")
            return

        if converter_path and not os.path.exists(converter_path):
            messagebox.showwarning("Fehler", "Bitte einen gültigen Pfad zum Potree Converter angeben!")
            return

        if not is_converter_bundle_available() and not converter_path:
            messagebox.showwarning(
                "Fehler",
                "Es wurde kein mitgelieferter Potree Converter gefunden und kein Override-Pfad angegeben!"
            )
            return

        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        if save_config(
            aws_access=aws_access,
            aws_secret=aws_secret,
            converter_path=converter_path,
            output_dir=output_dir
        ):
            messagebox.showinfo("Erfolg", "Einstellungen wurden gespeichert!")
            log("[CONFIG] ✓ Einstellungen gespeichert")
            settings_window.destroy()
        else:
            messagebox.showerror("Fehler", "Einstellungen konnten nicht gespeichert werden!")

    btn_save = ctk.CTkButton(
        settings_window,
        text="💾  Einstellungen speichern",
        font=ctk.CTkFont(size=14, weight="bold"),
        fg_color=COLOR_SUCCESS,
        hover_color=COLOR_SUCCESS_HOVER,
        height=44,
        command=save_settings
    )
    btn_save.pack(fill="x", padx=16, pady=(0, 16))

    if first_run:
        settings_window.protocol("WM_DELETE_WINDOW", lambda: None)


# ============================================================
#  HAUPTFENSTER
# ============================================================

root = TkinterDnD.Tk()
root.title(f"{APP_NAME} {APP_VERSION}")
root.geometry("700x900")

# Icon (optional)
try:
    root.iconbitmap(get_bundled_resource_path("icon.ico"))
except:
    pass

# Scrollbarer Hauptbereich
main_scroll = ctk.CTkScrollableFrame(root, fg_color=COLOR_SURFACE, corner_radius=0)
main_scroll.pack(fill="both", expand=True)

# ============================================================
#  HEADER MIT MENÜ
# ============================================================

header = ctk.CTkFrame(main_scroll, fg_color=COLOR_CARD, corner_radius=0)
header.pack(fill="x", pady=(0, 4))

header_inner = ctk.CTkFrame(header, fg_color="transparent")
header_inner.pack(fill="x", padx=24, pady=16)

ctk.CTkLabel(
    header_inner,
    text=f"☁  {APP_NAME}",
    font=ctk.CTkFont(size=20, weight="bold")
).pack(side="left")

ctk.CTkLabel(
    header_inner,
    text=f"v{APP_VERSION}",
    font=ctk.CTkFont(size=12),
    text_color=COLOR_TEXT_DIM
).pack(side="left", padx=(10, 0), pady=(4, 0))

# Einstellungs-Button
ctk.CTkButton(
    header_inner,
    text="⚙️",
    width=40,
    font=ctk.CTkFont(size=16),
    fg_color="transparent",
    hover_color=("#e2e8f0", "#333350"),
    command=lambda: open_settings_window(first_run=False)
).pack(side="right")

# ============================================================
#  2. PROJEKTDATEN
# ============================================================

card_data = ctk.CTkFrame(main_scroll, corner_radius=12)
card_data.pack(fill="x", padx=16, pady=(8, 0))

ctk.CTkLabel(
    card_data, text="Projektdaten",
    font=ctk.CTkFont(size=14, weight="bold")
).pack(anchor="w", padx=16, pady=(14, 8))

# Dateiauswahl
ctk.CTkButton(
    card_data, text="📂  LAZ / LAS Datei wählen...",
    fg_color="transparent", hover_color=("#e2e8f0", "#333350"),
    text_color="#e2e8f0", border_width=1, border_color="#4a4a5e",
    font=ctk.CTkFont(size=12),
    height=36, command=select_file
).pack(fill="x", padx=16, pady=(0, 6))

entry_file = ctk.CTkEntry(card_data, font=ctk.CTkFont(family="Consolas", size=11),
                           placeholder_text="Dateipfad...")
entry_file.pack(fill="x", padx=16, pady=(0, 8))

# Drag & Drop Zone
drop_frame = ctk.CTkFrame(card_data, fg_color="#1e1e2e", corner_radius=8,
                           border_width=2, border_color=COLOR_ACCENT)
drop_frame.pack(fill="x", padx=16, pady=(0, 10))

lbl_drop = tk.Label(
    drop_frame, text="⇧  Datei hier hineinziehen (Drag & Drop)",
    bg="#1e1e2e", fg="#64748b",
    font=("Segoe UI", 11), pady=14
)
lbl_drop.pack(fill="x")

lbl_drop.drop_target_register(DND_FILES)
lbl_drop.dnd_bind('<<Drop>>', drop_file)

# Kunde
row_kunde = ctk.CTkFrame(card_data, fg_color="transparent")
row_kunde.pack(fill="x", padx=16, pady=(0, 6))
ctk.CTkLabel(row_kunde, text="Kunde", width=80, anchor="e",
             font=ctk.CTkFont(size=12), text_color=COLOR_TEXT_DIM).pack(side="left")
entry_kunde = ctk.CTkEntry(row_kunde, width=300, font=ctk.CTkFont(size=12),
                             placeholder_text="z.B. Hella")
entry_kunde.pack(side="left", padx=(10, 0))

# Projekt
row_proj = ctk.CTkFrame(card_data, fg_color="transparent")
row_proj.pack(fill="x", padx=16, pady=(0, 14))
ctk.CTkLabel(row_proj, text="Projekt", width=80, anchor="e",
             font=ctk.CTkFont(size=12), text_color=COLOR_TEXT_DIM).pack(side="left")
entry_proj = ctk.CTkEntry(row_proj, width=300, font=ctk.CTkFont(size=12),
                            placeholder_text="z.B. Halle 1")
entry_proj.pack(side="left", padx=(10, 0))

# ============================================================
#  3. START BUTTON
# ============================================================

btn_start = ctk.CTkButton(
    main_scroll,
    text="▶   STARTEN  –  Konvertieren & Upload",
    font=ctk.CTkFont(size=15, weight="bold"),
    fg_color=COLOR_SUCCESS, hover_color=COLOR_SUCCESS_HOVER,
    height=50, corner_radius=12,
    command=start_thread
)
btn_start.pack(fill="x", padx=16, pady=(12, 0))

# ============================================================
#  4. FORTSCHRITT
# ============================================================

card_progress = ctk.CTkFrame(main_scroll, corner_radius=12)
card_progress.pack(fill="x", padx=16, pady=(8, 0))

progress_step = ctk.CTkLabel(
    card_progress, text="",
    font=ctk.CTkFont(size=12, weight="bold")
)
progress_step.pack(anchor="w", padx=16, pady=(12, 0))

progress_bar = ctk.CTkProgressBar(card_progress, height=10, corner_radius=5)
progress_bar.pack(fill="x", padx=16, pady=(8, 4))
progress_bar.set(0)

progress_detail = ctk.CTkLabel(
    card_progress, text="",
    font=ctk.CTkFont(size=11),
    text_color=COLOR_TEXT_DIM
)
progress_detail.pack(anchor="w", padx=16, pady=(0, 12))

# ============================================================
#  5. PROJEKT-ÜBERSICHT BUTTON
# ============================================================

btn_projects = ctk.CTkButton(
    main_scroll,
    text="📊  Projekt-Übersicht",
    font=ctk.CTkFont(size=14, weight="bold"),
    fg_color=COLOR_PURPLE, hover_color=COLOR_PURPLE_HOVER,
    height=44, corner_radius=12,
    command=open_projects_window
)
btn_projects.pack(fill="x", padx=16, pady=(10, 0))

# ============================================================
#  6. ERGEBNIS / LINK
# ============================================================

card_result = ctk.CTkFrame(main_scroll, corner_radius=12)
card_result.pack(fill="x", padx=16, pady=(8, 0))

ctk.CTkLabel(
    card_result, text="Ergebnis",
    font=ctk.CTkFont(size=14, weight="bold")
).pack(anchor="w", padx=16, pady=(14, 4))

ctk.CTkLabel(
    card_result, text="Projekt-Link:",
    font=ctk.CTkFont(size=11), text_color=COLOR_TEXT_DIM
).pack(anchor="w", padx=16)

entry_link = ctk.CTkEntry(card_result, font=ctk.CTkFont(family="Consolas", size=11),
                           text_color=COLOR_ACCENT)
entry_link.pack(fill="x", padx=16, pady=(4, 8))

ctk.CTkButton(
    card_result, text="📋  Link kopieren",
    fg_color="transparent", hover_color=("#e2e8f0", "#333350"),
    text_color="#e2e8f0", border_width=1, border_color="#4a4a5e",
    font=ctk.CTkFont(size=12), height=34,
    command=lambda: (root.clipboard_clear(), root.clipboard_append(entry_link.get()), log("✓ Link kopiert!"))
).pack(fill="x", padx=16, pady=(0, 14))

# ============================================================
#  7. LOG
# ============================================================

ctk.CTkLabel(
    main_scroll, text="Protokoll",
    font=ctk.CTkFont(size=11),
    text_color=COLOR_TEXT_DIM
).pack(anchor="w", padx=20, pady=(10, 4))

txt_log = ctk.CTkTextbox(
    main_scroll, height=160,
    font=ctk.CTkFont(family="Consolas", size=11),
    fg_color=COLOR_CARD, corner_radius=10,
    state="disabled"
)
txt_log.pack(fill="both", expand=True, padx=16, pady=(0, 16))


# ============================================================
#  INITIALISIERUNG
# ============================================================

log(f"═══  {APP_NAME} v{APP_VERSION}  ═══")
log(f"Konfiguration gespeichert in: {APPDATA_DIR}")

# Prüfe ob erste Ausführung
config = load_config()
if config.get("first_run", True):
    log("[INFO] Erste Ausführung erkannt - öffne Einstellungen...")
    root.after(500, lambda: open_settings_window(first_run=True))
else:
    log("[OK] Konfiguration geladen")
    log("Bereit. Wähle eine .laz oder .las Datei aus.")
    log("Klicke auf 'Projekt-Übersicht' um alle Projekte zu sehen.")

root.after(1000, check_for_available_update)

try:
    root.mainloop()
except Exception as e:
    import traceback
    traceback.print_exc()
    input("Drücke Enter zum Beenden...")
