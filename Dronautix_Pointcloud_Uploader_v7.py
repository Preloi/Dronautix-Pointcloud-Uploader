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
import unicodedata
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

# S3 Pfad fÃƒÂ¼r Index-Dateien
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
projects_window_ref = None
settings_window_ref = None
nav_buttons = {}
app_views = {}
current_view_name = "upload"


# --- HILFSFUNKTIONEN ---

def focus_existing_window(window):
    """Bringt ein bestehendes Fenster in den Vordergrund und gibt True zurueck."""
    if window is None:
        return False

    try:
        if not window.winfo_exists():
            return False
        if str(window.state()) == "iconic":
            window.deiconify()
        window.lift()
        window.focus_force()
        return True
    except tk.TclError:
        return False


def widget_exists(widget):
    """Prueft robust, ob ein Tk-Widget noch existiert."""
    try:
        return widget is not None and widget.winfo_exists()
    except tk.TclError:
        return False


def clear_frame(frame):
    """Entfernt alle Kinder eines Containers."""
    if not widget_exists(frame):
        return

    for child in frame.winfo_children():
        child.destroy()


def show_main_view(view_name):
    """Zeigt genau eine Hauptansicht an und aktualisiert die Navigation."""
    global current_view_name

    for name, view in app_views.items():
        if widget_exists(view):
            view.pack_forget()

    view = app_views.get(view_name)
    if widget_exists(view):
        view.pack(fill="both", expand=True)

    current_view_name = view_name

    for name, button in nav_buttons.items():
        if not widget_exists(button):
            continue
        button.configure(fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER)


def ui_log(message, ui=None):
    """Schreibt Log-Ausgaben optional in einen lokalen Dialog statt ins Hauptfenster."""
    log_widget = (ui or {}).get("log")
    if not widget_exists(log_widget):
        log(message)
        return

    def _append():
        if not widget_exists(log_widget):
            return
        log_widget.configure(state="normal")
        log_widget.insert(tk.END, message + "\n")
        log_widget.see(tk.END)
        log_widget.configure(state="disabled")

    root.after(0, _append)


def ui_set_step(text, step, ui=None):
    """Aktualisiert die Schrittanzeige wahlweise lokal im Dialog."""
    step_widget = (ui or {}).get("step")
    if not widget_exists(step_widget):
        if ui:
            detail_widget = (ui or {}).get("progress_detail")
            if widget_exists(detail_widget):
                root.after(0, lambda: detail_widget.configure(text=f"Schritt {step}/5: {text}") if widget_exists(detail_widget) else None)
                return
        root.after(0, lambda: update_step(text, step))
        return

    root.after(0, lambda: step_widget.configure(text=f"Schritt {step}/5: {text}") if widget_exists(step_widget) else None)


def ui_set_progress(value, ui=None):
    """Setzt die Fortschrittsanzeige wahlweise lokal im Dialog."""
    progress_widget = (ui or {}).get("progress_bar")
    if not widget_exists(progress_widget):
        root.after(0, lambda: progress_bar.set(value))
        return

    root.after(0, lambda: progress_widget.set(value) if widget_exists(progress_widget) else None)


def ui_set_detail(text, ui=None):
    """Setzt die Fortschrittsdetails wahlweise lokal im Dialog."""
    detail_widget = (ui or {}).get("progress_detail")
    if not widget_exists(detail_widget):
        root.after(0, lambda: progress_detail.configure(text=text))
        return

    root.after(0, lambda: detail_widget.configure(text=text) if widget_exists(detail_widget) else None)


def ui_reset_progress(ui=None):
    """Setzt die Fortschrittsanzeige des passenden UI-Kontexts zurueck."""
    if not ui:
        root.after(0, reset_progress)
        return

    ui_set_progress(0, ui)
    ui_set_detail("", ui)
    step_widget = ui.get("step")
    if widget_exists(step_widget):
        root.after(0, lambda: step_widget.configure(text=""))
    log_widget = ui.get("log")
    if widget_exists(log_widget):
        def _clear():
            if not widget_exists(log_widget):
                return
            log_widget.configure(state="normal")
            log_widget.delete("1.0", tk.END)
            log_widget.configure(state="disabled")
        root.after(0, _clear)


def sanitize_folder_name(name):
    """Bereinigt Ordnernamen fuer Dateisystem und S3."""
    name = (name or "").strip().lower()
    name = name.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
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
    """LÃƒÂ¤dt gespeicherte Einstellungen aus AppData"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except:
            return {"first_run": True}
    return {"first_run": True}


def get_app_base_dir():
    """Ermittelt das Basisverzeichnis fÃƒÂ¼r Quellcode und PyInstaller-Builds."""
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
    """PrÃƒÂ¼ft ob der mitgelieferte Converter vollstÃƒÂ¤ndig vorhanden ist."""
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
    """LÃƒÂ¤dt das Update-Manifest aus dem Netzwerkverzeichnis."""
    if not os.path.exists(UPDATE_MANIFEST_FILE):
        return None

    try:
        with open(UPDATE_MANIFEST_FILE, "r", encoding="utf-8") as manifest_file:
            return json.load(manifest_file)
    except Exception as e:
        log(f"[UPDATE] Manifest konnte nicht geladen werden: {e}")
        return None


def check_for_available_update():
    """PrÃƒÂ¼ft beim Start, ob im Netzwerkverzeichnis eine neuere Version bereitliegt."""
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
                "Update verfuegbar",
                f"Version {remote_version} ist verfuegbar, aber der Installer wurde nicht gefunden.\n\n"
                f"Erwarteter Pfad:\n{installer_path or UPDATE_SHARE_DIR}"
            )
            return

        install_now = messagebox.askyesno(
            "Update verfuegbar",
            f"Es ist eine neue Version verfuegbar.\n\n"
            f"Installierte Version: {APP_VERSION}\n"
            f"Verfuegbare Version: {remote_version}\n\n"
            f"Soll das Update jetzt installiert werden?"
        )

        log(f"[UPDATE] Neue Version verfuegbar: {remote_version} ({installer_path})")

        if not install_now:
            log("[UPDATE] Benutzer hat das Update verschoben")
            return

        try:
            subprocess.Popen([installer_path, "/CLOSEAPPLICATIONS"], shell=False)
            log(f"[UPDATE] Installer gestartet: {installer_path}")
            root.after(200, root.destroy)
        except Exception as install_error:
            log(f"[UPDATE] Installer konnte nicht gestartet werden: {install_error}")
            messagebox.showerror(
                "Update fehlgeschlagen",
                f"Der Installer konnte nicht gestartet werden:\n{installer_path}\n\n{install_error}"
            )
    except Exception as e:
        log(f"[UPDATE] Update-Pruefung fehlgeschlagen: {e}")


def validate_file(filepath):
    """Prueft, ob die Datei eine gueltige LAS/LAZ- oder COPC-Datei ist."""
    if not os.path.exists(filepath):
        return False, "Datei existiert nicht"
    filename = os.path.basename(filepath).lower()
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in ['.laz', '.las']:
        return False, "Nur .copc.laz, .laz und .las Dateien werden unterstuetzt"
    if filename.endswith('.copc.laz'):
        return True, "COPC"
    return True, "OK"


def detect_input_format(filepath):
    """Ermittelt ob eine Datei direkt als COPC hochgeladen werden kann."""
    filename = os.path.basename(filepath).lower()
    return "copc" if filename.endswith(".copc.laz") else "potree"


def validate_replacement_file(filepath):
    """Prueft ob eine Datei fuer den Projektaustausch geeignet ist."""
    valid, message = validate_file(filepath)
    if not valid:
        return False, message

    if os.path.basename(filepath).lower().endswith(".copc.laz"):
        return False, "Fuer den Projektaustausch sind nur klassische .las oder .laz Dateien erlaubt"

    return True, "OK"


def cleanup_local_files(output_path):
    """LÃƒÂ¶scht die lokalen konvertierten Dateien nach erfolgreichem Upload"""
    try:
        if os.path.exists(output_path):
            shutil.rmtree(output_path)
            log(f"[CLEANUP] Ã¢Å“â€œ TemporÃƒÂ¤re Dateien gelÃƒÂ¶scht: {output_path}")
            return True
    except Exception as e:
        log(f"[WARNUNG] Cleanup fehlgeschlagen: {e}")
        return False
    return False


def format_bytes(bytes_size):
    """Formatiert Bytes zu lesbarer GrÃƒÂ¶ÃƒÅ¸e"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f} TB"


def get_total_size(files_list):
    """Berechnet die GesamtgrÃƒÂ¶ÃƒÅ¸e aller Dateien"""
    total = 0
    for file_path in files_list:
        if os.path.exists(file_path):
            total += os.path.getsize(file_path)
    return total


def parse_iso_datetime(value):
    """Parst ISO-Zeitstempel robust und liefert None bei ungÃƒÂ¼ltigen Werten."""
    if not value:
        return None

    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def prune_deleted_projects(deleted_data):
    """Entfernt LÃƒÂ¶schhinweise, die ÃƒÂ¤lter als die definierte Aufbewahrungszeit sind."""
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
    """LÃƒÂ¤dt den bestehenden Projekt-Index von S3"""
    try:
        log(f"[INDEX] Versuche Index zu laden: s3://{BUCKET_NAME}/{S3_INDEX_JSON}")
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=S3_INDEX_JSON)
        data = json.loads(response['Body'].read().decode('utf-8'))
        project_count = len(data.get('projects', []))
        log(f"[INDEX] Ã¢Å“â€œ Bestehender Index geladen ({project_count} Projekte)")
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
    """LÃƒÂ¤dt die Liste der gelÃƒÂ¶schten Projekte von S3"""
    try:
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=S3_DELETED_JSON)
        data = json.loads(response['Body'].read().decode('utf-8'))
        data = prune_deleted_projects(data)
        log(f"[GELÃƒâ€“SCHT] Liste geladen ({len(data.get('deleted_projects', []))} EintrÃƒÂ¤ge)")
        return data
    except s3_client.exceptions.NoSuchKey:
        log("[GELÃƒâ€“SCHT] Liste existiert noch nicht - erstelle neue")
        return {"deleted_projects": [], "last_updated": None}
    except Exception as e:
        log(f"[GELÃƒâ€“SCHT] WARNUNG: Liste konnte nicht geladen werden: {e}")
        return {"deleted_projects": [], "last_updated": None}


def save_deleted_projects(s3_client, deleted_data):
    """Speichert die Liste der gelÃƒÂ¶schten Projekte auf S3"""
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
        log(f"[GELÃƒâ€“SCHT] Liste gespeichert ({len(deleted_data['deleted_projects'])} EintrÃƒÂ¤ge)")
        return True
    except Exception as e:
        log(f"[FEHLER] GelÃƒÂ¶scht-Liste konnte nicht gespeichert werden: {e}")
        return False


def build_deleted_project_entry(project_info, s3_path):
    """Erstellt einen standardisierten Eintrag fÃƒÂ¼r deleted_projects.json"""
    return {
        "id": project_info.get("id", ""),
        "kunde": project_info.get("kunde", ""),
        "projekt": project_info.get("projekt", ""),
        "s3_path": s3_path,
        "deleted_at": datetime.now().isoformat(),
        "original_link": project_info.get("link", "")
    }


def upsert_deleted_project(deleted_data, deleted_entry):
    """Aktualisiert einen bestehenden Deleted-Eintrag oder fÃƒÂ¼gt ihn vorne ein."""
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
    """Entfernt ein Projekt aus dem Index und gibt True zurÃƒÂ¼ck, wenn sich der Index geÃƒÂ¤ndert hat."""
    original_count = len(index_data.get("projects", []))
    index_data["projects"] = [
        project for project in index_data.get("projects", [])
        if project.get("id") != project_id
    ]
    return len(index_data["projects"]) != original_count


def collect_project_objects(s3_client, s3_path):
    """Sammelt alle S3-Objekte unter einem ProjektprÃƒÂ¤fix."""
    paginator = s3_client.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=BUCKET_NAME, Prefix=s3_path)

    object_keys = []
    for page in pages:
        for obj in page.get('Contents', []):
            object_keys.append(obj['Key'])

    return object_keys


def delete_s3_objects(s3_client, object_keys):
    """LÃƒÂ¶scht S3-Objekte in Batches und bricht bei partiellen Fehlern ab."""
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
                f"S3 DeleteObjects Fehler fÃƒÂ¼r {first_error.get('Key', 'unbekannt')}: "
                f"{first_error.get('Code', 'Unknown')} - {first_error.get('Message', '')}"
            )

        deleted_count += len(batch_keys)

    return deleted_count


def delete_project_transaction(s3_client, project_info):
    """LÃƒÂ¶scht Projektdaten und aktualisiert die Metadaten so robust wie mÃƒÂ¶glich."""
    s3_path = project_info.get("s3_path", "")
    project_id = project_info.get("id", "")

    if not s3_path:
        return {
            "success": False,
            "partial": False,
            "message": "S3-Pfad nicht gefunden."
        }

    try:
        log(f"[LÃƒâ€“SCHEN] Sammle Dateien unter: {s3_path}")
        object_keys = collect_project_objects(s3_client, s3_path)
        if object_keys:
            log(f"[LÃƒâ€“SCHEN] LÃƒÂ¶sche {len(object_keys)} Dateien aus S3...")
            deleted_count = delete_s3_objects(s3_client, object_keys)
            log(f"[LÃƒâ€“SCHEN] Ã¢Å“â€œ {deleted_count} Dateien gelÃƒÂ¶scht")
        else:
            log(f"[LÃƒâ€“SCHEN] Keine Dateien gefunden unter: {s3_path}")

        metadata_errors = []

        log("[LÃƒâ€“SCHEN] Aktualisiere GelÃƒÂ¶scht-Liste...")
        deleted_data = load_deleted_projects(s3_client)
        deleted_entry = build_deleted_project_entry(project_info, s3_path)
        deleted_data = upsert_deleted_project(deleted_data, deleted_entry)
        if save_deleted_projects(s3_client, deleted_data):
            log("[LÃƒâ€“SCHEN] Ã¢Å“â€œ GelÃƒÂ¶scht-Liste aktualisiert")
        else:
            metadata_errors.append("deleted_projects.json")

        log("[LÃƒâ€“SCHEN] Aktualisiere Projekt-Index...")
        index_data = load_projects_index(s3_client)
        removed_from_index = remove_project_from_index(index_data, project_id)
        if removed_from_index:
            if save_projects_index(s3_client, index_data):
                log("[LÃƒâ€“SCHEN] Ã¢Å“â€œ Projekt-Index aktualisiert")
            else:
                metadata_errors.append("projects_index.json")
        else:
            log("[LÃƒâ€“SCHEN] Projekt war bereits nicht mehr im Index")

        if metadata_errors:
            files = ", ".join(metadata_errors)
            return {
                "success": False,
                "partial": True,
                "message": (
                    "Projektdaten wurden in S3 gelÃƒÂ¶scht, aber folgende Metadaten "
                    f"konnten nicht vollstÃƒÂ¤ndig aktualisiert werden: {files}"
                )
            }

        return {
            "success": True,
            "partial": False,
            "message": "Projekt wurde gelÃƒÂ¶scht und alle Metadaten wurden aktualisiert."
        }
    except Exception as e:
        log(f"[FEHLER] LÃƒÂ¶schen fehlgeschlagen: {e}")
        return {
            "success": False,
            "partial": False,
            "message": f"LÃƒÂ¶schen fehlgeschlagen: {e}"
        }


# ZusÃ¤tzliche Upload-/Austausch-Helfer

def create_s3_client(aws_access, aws_secret):
    """Erstellt einen S3 Client mit den konfigurierten Zugangsdaten."""
    return boto3.client(
        's3',
        aws_access_key_id=aws_access,
        aws_secret_access_key=aws_secret,
        region_name=REGION_NAME
    )


def build_project_url(folder_kunde, folder_id, folder_projekt, input_format, kunde, projekt):
    """Erstellt den Viewer-Link fuer ein Projekt."""
    if input_format == "copc":
        path_param = f"{folder_kunde}/{folder_id}/{folder_projekt}/source.copc.laz"
    else:
        path_param = f"{folder_kunde}/{folder_id}/{folder_projekt}"

    display_name = f"{kunde} - {projekt}"
    safe_name = urllib.parse.quote(display_name)
    project_url = f"{DOMAIN_URL}?id={path_param}&name={safe_name}"
    return path_param, project_url


def collect_upload_files(input_format, s3_prefix, source_file=None, output_dir=None):
    """Sammelt alle hochzuladenden Dateien fuer einen Upload."""
    files_to_upload = []

    if input_format == "copc":
        files_to_upload.append((source_file, f"{s3_prefix}/source.copc.laz"))
        return files_to_upload

    for root_dir, dirs, files in os.walk(output_dir):
        for file in files:
            local_path = os.path.join(root_dir, file)
            rel_path = os.path.relpath(local_path, output_dir)
            s3_key = f"{s3_prefix}/{rel_path}".replace("\\", "/")
            files_to_upload.append((local_path, s3_key))

    files_to_upload.sort(
        key=lambda item: (os.path.basename(item[1]).lower() == "metadata.json", item[1].lower())
    )
    return files_to_upload


def upload_files_to_s3(s3_client, files_to_upload, ui=None):
    """Laedt Dateien nach S3 hoch und aktualisiert die passende Fortschrittsanzeige."""
    if not files_to_upload:
        raise RuntimeError("Keine Dateien zum Upload gefunden")

    total_size = get_total_size([file_path for file_path, _ in files_to_upload])
    ui_log(f"[UPLOAD] {len(files_to_upload)} Dateien ({format_bytes(total_size)})", ui)

    uploaded_total = 0

    def update_upload_progress(bytes_uploaded, file_total):
        nonlocal uploaded_total
        progress = (uploaded_total + bytes_uploaded) / total_size
        ui_set_progress(progress, ui)
        ui_set_detail(
            f"{format_bytes(uploaded_total + bytes_uploaded)} / {format_bytes(total_size)}",
            ui
        )

    for idx, (local_path, s3_key) in enumerate(files_to_upload, 1):
        file_size = os.path.getsize(local_path)
        ui_log(f"[{idx}/{len(files_to_upload)}] {os.path.basename(local_path)} ({format_bytes(file_size)})", ui)

        content_type, _ = mimetypes.guess_type(local_path)
        if not content_type:
            content_type = 'application/octet-stream'

        progress_callback = UploadProgress(file_size, update_upload_progress)

        s3_client.upload_file(
            local_path, BUCKET_NAME, s3_key,
            ExtraArgs={
                'ContentType': content_type,
                'CacheControl': 'no-cache, no-store, must-revalidate, max-age=0'
            },
            Callback=progress_callback
        )

        uploaded_total += file_size

    ui_set_progress(1, ui)
    ui_log("[UPLOAD] Alle Dateien hochgeladen", ui)


def run_potree_conversion(laz_file, converter_path, output_dir, ui=None):
    """Fuehrt den Potree Converter aus und schreibt den Fortschritt ins passende Log."""
    os.makedirs(output_dir, exist_ok=True)

    ui_log("[KONVERTIERUNG] Starte Potree Converter...", ui)
    ui_log(f"[CONVERTER] {converter_path}", ui)
    ui_log(f"[OUTPUT] {output_dir}", ui)

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
            ui_log(f"[POTREE] {line}", ui)
            if "%" in line:
                try:
                    percent_str = re.search(r'(\d+)%', line)
                    if percent_str:
                        percent = int(percent_str.group(1))
                        ui_set_progress(percent / 100, ui)
                except Exception:
                    pass

    process.wait()

    if process.returncode != 0:
        raise RuntimeError(f"Potree Konvertierung fehlgeschlagen (Exit Code: {process.returncode})")

    ui_log("[KONVERTIERUNG] Potree Konvertierung abgeschlossen", ui)
    ui_set_progress(1, ui)


# ============================================================
#  UPLOAD-FORTSCHRITT CALLBACK  (Echtzeit pro Chunk)
# ============================================================

class UploadProgress:
    """Callback-Klasse fÃƒÂ¼r boto3 upload_file Ã¢â‚¬â€œ wird bei jedem Chunk aufgerufen."""

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

        # 1. PrÃƒÂ¼fungen
        root.after(0, lambda: update_step("Pruefe Datei...", 1))
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
            log("[FEHLER] Kein Potree Converter verfÃƒÂ¼gbar")
            messagebox.showwarning(
                "Fehler",
                "Mitgelieferter Potree Converter nicht gefunden. "
                "Bitte Build/Projektdateien prÃƒÂ¼fen oder optional einen Override-Pfad konfigurieren!"
            )
            return

        if not is_copc and not output_base_dir:
            log("[FEHLER] Output-Ordner nicht konfiguriert!")
            messagebox.showwarning("Fehler", "Bitte einen Output-Ordner in den Einstellungen angeben!")
            return

        log(f"[DATEI] Datei ist gueltig: {os.path.basename(laz_file)}")
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
            root.after(0, lambda: update_step("Bereite COPC fuer Upload vor...", 2))
            root.after(0, lambda: progress_bar.set(1))
            log("[COPC] Direkter Upload ohne Potree Converter")
        else:
            root.after(0, lambda: update_step("Konvertiere mit Potree...", 2))
            root.after(0, lambda: progress_bar.set(0))

            # TemporÃƒÂ¤rer Output-Ordner: kunde/id/projekt
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

            log("[KONVERTIERUNG] Potree Konvertierung abgeschlossen")
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
            log("[S3] Verbindung hergestellt")
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
        log("[UPLOAD] Alle Dateien hochgeladen")

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
            log("[CSV] Lokale CSV aktualisiert")
        except Exception as e:
            log(f"[WARNUNG] CSV konnte nicht aktualisiert werden: {e}")

        # 5. Cleanup - TemporÃƒÂ¤re Dateien lÃƒÂ¶schen
        root.after(0, lambda: update_step("Raeume auf...", 5))
        if is_copc:
            log("[CLEANUP] Kein lokaler Cleanup nÃƒÂ¶tig fÃƒÂ¼r COPC Upload")
        else:
            log("[CLEANUP] Loesche temporaere Dateien...")
            
            cleanup_success = cleanup_local_files(output_dir)
            if cleanup_success:
                log("[CLEANUP] Temporaerer Ordner erfolgreich geloescht")
            else:
                log("[CLEANUP] Temporaerer Ordner konnte nicht vollstaendig geloescht werden")

        # Fertig
        root.after(0, lambda: update_step("Fertig!", 5))
        root.after(0, lambda: progress_bar.set(1))
        root.after(0, lambda: entry_link.delete(0, tk.END))
        root.after(0, lambda: entry_link.insert(0, project_url))

        log("=" * 50)
        log("UPLOAD ERFOLGREICH ABGESCHLOSSEN")
        log(f"Projekt-Link: {project_url}")
        log("=" * 50)

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
        title="LAS/LAZ/COPC Datei auswaehlen",
        filetypes=[("Point Cloud", "*.copc.laz *.laz *.las"), ("Alle Dateien", "*.*")]
    )
    if file:
        entry_file.delete(0, tk.END)
        entry_file.insert(0, file)
        log(f"[DATEI] Ausgewaehlt: {os.path.basename(file)}")


def extract_dropped_file(event_data):
    """Extrahiert die erste Datei aus einem Drag-and-Drop Event."""
    try:
        file_list = root.tk.splitlist(event_data)
    except tk.TclError:
        file_list = [event_data]

    if not file_list:
        return ""

    return file_list[0].strip('{}')


def drop_file(event):
    file_path = extract_dropped_file(event.data)
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
        log("[TEST] Verbindung erfolgreich!")
        messagebox.showinfo("Erfolg", "AWS Verbindung erfolgreich!")
    except Exception as e:
        log(f"[TEST] Verbindung fehlgeschlagen: {e}")
        messagebox.showerror("Fehler", f"Verbindung fehlgeschlagen:\n{e}")


def start_thread():
    """Startet den Upload-Prozess in einem Thread"""
    laz = entry_file.get().strip()
    kunde = entry_kunde.get().strip()
    projekt = entry_proj.get().strip()

    if not laz:
        messagebox.showwarning("Fehler", "Bitte eine LAZ/LAS Datei auswaehlen!")
        return
    if not kunde or not projekt:
        messagebox.showwarning("Fehler", "Bitte Kunde und Projekt eingeben!")
        return

    config = load_config()
    aws_access = config.get("aws_access", "")
    aws_secret = config.get("aws_secret", "")

    btn_start.configure(state="disabled", text="Laeuft...")
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
            btn_start.configure(state="normal", text="STARTEN - Konvertieren & Upload")

    root.after(100, check_thread)


def update_step(text, step):
    """Aktualisiert die Schritt-Anzeige"""
    progress_step.configure(text=f"Schritt {step}/5: {text}")


def reset_progress():
    """Setzt die Fortschrittsanzeige zurÃƒÂ¼ck"""
    progress_bar.set(0)
    progress_detail.configure(text="")
    progress_step.configure(text="")


def replace_project_process(project_info, replacement_file, aws_access, aws_secret, on_success=None, ui=None):
    """Tauscht die Punktwolkendaten eines bestehenden Projekts aus."""
    config = load_config()
    configured_converter_path = config.get("converter_path", "")
    converter_path = resolve_converter_path(configured_converter_path)
    output_base_dir = config.get("output_base_dir", "")

    project_name = project_info.get("projekt", "")
    project_id = project_info.get("id", "")
    project_link = project_info.get("link", "")
    s3_prefix = project_info.get("s3_path", "")
    viewer_path = project_info.get("viewer_path", "")
    project_format = project_info.get("format", "")

    temp_output_dir = None

    try:
        ui_reset_progress(ui)
        ui_set_step("Pruefe Austauschdatei...", 1, ui)
        ui_set_progress(0, ui)
        ui_set_detail("Pruefe die ausgewaehlte Austauschdatei...", ui)

        valid, message = validate_replacement_file(replacement_file)
        if not valid:
            ui_log(f"[AUSTAUSCH] [FEHLER] {message}", ui)
            root.after(0, lambda msg=message: messagebox.showerror("Fehler", msg))
            return

        if project_format == "copc" or viewer_path.endswith(".copc.laz"):
            message = (
                "Dieses Projekt nutzt derzeit COPC. Der Austausch mit Potree-Konvertierung ist aktuell "
                "nur fuer klassische Potree-Projekte verfuegbar, damit Link und Viewer-Pfad unveraendert bleiben."
            )
            ui_log(f"[AUSTAUSCH] [FEHLER] {message}", ui)
            root.after(0, lambda msg=message: messagebox.showerror("Fehler", msg))
            return

        if not aws_access or not aws_secret:
            ui_log("[AUSTAUSCH] [FEHLER] Bitte AWS Keys in den Einstellungen eingeben!", ui)
            root.after(0, lambda: messagebox.showwarning("Fehler", "Bitte AWS Zugangsdaten in den Einstellungen eingeben!"))
            return

        if not converter_path:
            ui_log("[AUSTAUSCH] [FEHLER] Kein Potree Converter verfuegbar", ui)
            root.after(0, lambda: messagebox.showwarning(
                "Fehler",
                "Mitgelieferter Potree Converter nicht gefunden. Bitte Build/Projektdateien pruefen oder einen Override-Pfad konfigurieren!"
            ))
            return

        if not output_base_dir:
            ui_log("[AUSTAUSCH] [FEHLER] Output-Ordner nicht konfiguriert!", ui)
            root.after(0, lambda: messagebox.showwarning("Fehler", "Bitte einen Output-Ordner in den Einstellungen angeben!"))
            return

        if not s3_prefix or not project_id:
            ui_log("[AUSTAUSCH] [FEHLER] Projektdaten unvollstaendig", ui)
            root.after(0, lambda: messagebox.showerror("Fehler", "Projektdaten sind unvollstaendig."))
            return

        ui_log(f"[AUSTAUSCH] Starte Datenaustausch fuer Projekt '{project_name}' ({project_id})", ui)
        ui_log(f"[AUSTAUSCH] Neue Quelldatei: {os.path.basename(replacement_file)}", ui)
        ui_log(f"[AUSTAUSCH] Ziel bleibt unveraendert: {s3_prefix}", ui)

        ui_set_step("Konvertiere mit Potree...", 2, ui)
        ui_set_progress(0, ui)
        ui_set_detail("Starte den Potree Converter...", ui)

        temp_output_dir = os.path.join(
            output_base_dir,
            "_project_replacements",
            f"{project_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        run_potree_conversion(replacement_file, converter_path, temp_output_dir, ui=ui)

        ui_set_step("Lade Ersatzdaten zu S3 hoch...", 3, ui)
        ui_set_progress(0, ui)
        ui_set_detail("Verbinde mit dem Projekt-Storage...", ui)

        try:
            s3_client = create_s3_client(aws_access, aws_secret)
            ui_log("[AUSTAUSCH] [S3] Verbindung hergestellt", ui)
            log("[AUSTAUSCH] [S3] Verbindung hergestellt")
        except Exception as e:
            ui_log(f"[AUSTAUSCH] [FEHLER] S3 Verbindung fehlgeschlagen: {e}", ui)
            log(f"[AUSTAUSCH] [FEHLER] S3 Verbindung fehlgeschlagen: {e}")
            root.after(0, lambda err=e: messagebox.showerror("Fehler", f"AWS Verbindung fehlgeschlagen:\n{err}"))
            return

        existing_keys = collect_project_objects(s3_client, s3_prefix)
        files_to_upload = collect_upload_files("potree", s3_prefix, output_dir=temp_output_dir)

        if not files_to_upload:
            ui_log("[AUSTAUSCH] [FEHLER] Keine konvertierten Dateien zum Upload gefunden!", ui)
            log("[AUSTAUSCH] [FEHLER] Keine konvertierten Dateien zum Upload gefunden!")
            root.after(0, lambda: messagebox.showerror("Fehler", "Keine konvertierten Dateien zum Upload gefunden!"))
            return

        upload_files_to_s3(s3_client, files_to_upload, ui=ui)

        ui_set_step("Bereinige alte Projektdateien...", 4, ui)
        ui_set_detail("Vergleiche neue und bestehende Projektdateien...", ui)
        replacement_keys = {s3_key for _, s3_key in files_to_upload}
        obsolete_keys = [key for key in existing_keys if key not in replacement_keys]

        if obsolete_keys:
            ui_log(f"[AUSTAUSCH] Entferne {len(obsolete_keys)} alte Dateien aus dem bestehenden Projekt...", ui)
            ui_set_detail(f"Entferne {len(obsolete_keys)} veraltete Dateien...", ui)
            log(f"[AUSTAUSCH] Entferne {len(obsolete_keys)} alte Dateien aus dem bestehenden Projekt...")
            deleted_count = delete_s3_objects(s3_client, obsolete_keys)
            ui_log(f"[AUSTAUSCH] {deleted_count} veraltete Dateien geloescht", ui)
            log(f"[AUSTAUSCH] {deleted_count} veraltete Dateien geloescht")
        else:
            ui_log("[AUSTAUSCH] Keine veralteten Dateien zum Loeschen gefunden", ui)
            log("[AUSTAUSCH] Keine veralteten Dateien zum Loeschen gefunden")

        ui_set_step("Raeume auf...", 5, ui)
        ui_set_detail("Entferne temporaere Konvertierungsdaten...", ui)
        cleanup_local_files(temp_output_dir)

        ui_set_progress(1, ui)
        ui_set_step("Austausch abgeschlossen", 5, ui)
        ui_set_detail("Projektname, Projekt-ID und Link bleiben unveraendert.", ui)
        ui_log("=" * 50, ui)
        ui_log("PROJEKTDATEN ERFOLGREICH AUSGETAUSCHT", ui)
        ui_log(f"Projekt: {project_name} ({project_id})", ui)
        ui_log(f"Link unveraendert: {project_link}", ui)
        ui_log("=" * 50, ui)

        log("=" * 50)
        log("PROJEKTDATEN ERFOLGREICH AUSGETAUSCHT")
        log(f"Projekt: {project_name} ({project_id})")
        log(f"Link unveraendert: {project_link}")
        log("=" * 50)

        root.after(0, lambda: root.clipboard_clear())
        root.after(0, lambda: root.clipboard_append(project_link))
        root.after(0, lambda: messagebox.showinfo(
            "Erfolg",
            f"Die Punktwolkendaten von '{project_name}' wurden ersetzt.\n\nProjektname, Projekt-ID und Link bleiben unveraendert."
        ))

        if on_success:
            root.after(0, on_success)

    except Exception as e:
        ui_log(f"[AUSTAUSCH] [FEHLER] {e}", ui)
        log(f"[AUSTAUSCH] [FEHLER] {e}")
        import traceback
        ui_log(traceback.format_exc(), ui)
        log(traceback.format_exc())
        root.after(0, lambda err=e: messagebox.showerror("Fehler", f"Austausch fehlgeschlagen:\n{err}"))
    finally:
        if temp_output_dir and os.path.exists(temp_output_dir):
            cleanup_local_files(temp_output_dir)


def open_projects_window():
    global projects_window_ref
    """Ãƒâ€“ffnet das ProjektÃƒÂ¼bersicht-Fenster mit verbesserter Darstellung"""
    config = load_config()
    aws_access = config.get("aws_access", "")
    aws_secret = config.get("aws_secret", "")
    
    if not aws_access or not aws_secret:
        messagebox.showwarning("Fehler", "Bitte AWS Zugangsdaten in den Einstellungen eingeben!")
        return

    if focus_existing_window(projects_window_ref):
        return

    proj_window = ctk.CTkToplevel(root)
    projects_window_ref = proj_window
    proj_window.title("Projekt-Uebersicht")
    proj_window.geometry("1100x750")

    def close_projects_window():
        global projects_window_ref
        projects_window_ref = None
        replace_window = getattr(proj_window, "_replace_window", None)
        if replace_window is not None:
            try:
                if replace_window.winfo_exists():
                    replace_window.destroy()
            except tk.TclError:
                pass
            proj_window._replace_window = None
        proj_window.destroy()

    proj_window.protocol("WM_DELETE_WINDOW", close_projects_window)

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
        text="Zur Projektuebersicht",
        font=ctk.CTkFont(size=18, weight="bold")
    ).pack(side="left", padx=20, pady=16)

    btn_refresh = ctk.CTkButton(
        header, text="Aktualisieren",
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
        text="Ã°Å¸â€Â",
        font=ctk.CTkFont(size=14)
    ).pack(side="left", padx=(0, 8))

    # Kunden-Dropdown
    ctk.CTkLabel(
        filter_inner,
        text="Kunde:",
        font=ctk.CTkFont(size=11)
    ).pack(side="left", padx=(0, 4))

    customer_filter = ctk.CTkComboBox(
        filter_inner,
        values=["Alle Kunden"],
        width=180,
        font=ctk.CTkFont(size=11),
        state="readonly",
        command=lambda val: apply_filter()
    )
    customer_filter.set("Alle Kunden")
    customer_filter.pack(side="left", padx=(0, 12))

    # Such-Eingabefeld
    search_entry = ctk.CTkEntry(
        filter_inner,
        placeholder_text="Projekt suchen...",
        font=ctk.CTkFont(size=11),
        width=250
    )
    search_entry.pack(side="left", padx=(0, 8))

    def apply_filter():
        load_projects(customer_filter.get(), search_entry.get().strip())

    def on_search_key(event):
        apply_filter()

    search_entry.bind('<KeyRelease>', on_search_key)

    ctk.CTkButton(
        filter_inner,
        text="Zuruecksetzen",
        font=ctk.CTkFont(size=11),
        width=120,
        height=28,
        fg_color="transparent",
        border_width=1,
        command=lambda: (customer_filter.set("Alle Kunden"), search_entry.delete(0, tk.END), load_projects())
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
    tree.column("url", width=520)  # Breiter fÃƒÂ¼r Links

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
    projects_by_id = {}

    def get_selected_project():
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("Info", "Bitte ein Projekt auswaehlen!")
            return None

        item = tree.item(selected[0])
        project_id = item['values'][0]
        if not project_id:
            messagebox.showinfo("Info", "Bitte ein gueltiges Projekt auswaehlen!")
            return None

        project_info = projects_by_id.get(project_id)
        if not project_info:
            messagebox.showerror("Fehler", "Projekt konnte im aktuellen Index nicht gefunden werden!")
            return None

        return project_info

    def open_in_browser():
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("Info", "Bitte ein Projekt auswaehlen!")
            return
        item = tree.item(selected[0])
        url = item['values'][4]  # URL ist jetzt in Spalte 4
        webbrowser.open(url)

    def copy_link():
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("Info", "Bitte ein Projekt auswaehlen!")
            return
        item = tree.item(selected[0])
        url = item['values'][4]
        proj_window.clipboard_clear()
        proj_window.clipboard_append(url)
        messagebox.showinfo("Kopiert", "Link in Zwischenablage kopiert!")

    def delete_project():
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("Info", "Bitte ein Projekt auswaehlen!")
            return

        item = tree.item(selected[0])
        projekt_name = item['values'][2]

        result = messagebox.askyesno(
            "Loeschen bestaetigen",
            f"Projekt '{projekt_name}' wirklich lÃƒÂ¶schen?\n\n"
            "Dies lÃƒÂ¶scht alle Dateien aus dem S3 Storage!"
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
                messagebox.showinfo("Erfolg", "Projekt wurde geloescht und Link deaktiviert!")
                load_projects()
            elif delete_result.get("partial"):
                messagebox.showwarning("Teilweise geloescht", delete_result["message"])
                load_projects()
            else:
                messagebox.showerror("Fehler", delete_result["message"])

        except Exception as e:
            messagebox.showerror("Fehler", f"Fehler beim LÃƒÂ¶schen:\n{e}")

    ctk.CTkButton(
        btn_frame, text="Im Browser oeffnen",
        fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
        font=ctk.CTkFont(size=12), height=36,
        command=open_in_browser
    ).pack(side="left", padx=(0, 8))

    ctk.CTkButton(
        btn_frame, text="Link kopieren",
        fg_color=COLOR_PURPLE, hover_color=COLOR_PURPLE_HOVER,
        font=ctk.CTkFont(size=12), height=36,
        command=copy_link
    ).pack(side="left", padx=(0, 8))

    ctk.CTkButton(
        btn_frame, text="Loeschen",
        fg_color=COLOR_DANGER, hover_color=COLOR_DANGER_HOVER,
        font=ctk.CTkFont(size=12), height=36,
        command=delete_project
    ).pack(side="left")

    def open_replace_dialog():
        existing_replace_window = getattr(proj_window, "_replace_window", None)
        if focus_existing_window(existing_replace_window):
            return

        selected = tree.selection()
        if not selected:
            messagebox.showinfo("Info", "Bitte ein Projekt auswaehlen!")
            return

        item = tree.item(selected[0])
        projekt_id = item['values'][0]

        if not projekt_id:
            messagebox.showinfo("Info", "Bitte ein gueltiges Projekt auswaehlen!")
            return

        try:
            s3_client = create_s3_client(aws_access, aws_secret)
            index_data = load_projects_index(s3_client)
        except Exception as e:
            messagebox.showerror("Fehler", f"Projektdaten konnten nicht geladen werden:\n{e}")
            return

        project_info = None
        for proj in index_data.get("projects", []):
            if proj.get("id") == projekt_id:
                project_info = proj
                break

        if not project_info:
            messagebox.showerror("Fehler", "Projekt nicht im Index gefunden!")
            return

        replace_window = ctk.CTkToplevel(proj_window)
        proj_window._replace_window = replace_window
        replace_window.title("Punktwolkendaten austauschen")
        replace_window.geometry("820x640")
        replace_window.minsize(760, 560)
        replace_window.transient(proj_window)
        replace_window.lift()
        replace_window.focus_force()
        replace_window.grab_set()

        def close_replace_window():
            if getattr(proj_window, "_replace_window", None) is replace_window:
                proj_window._replace_window = None
            try:
                replace_window.grab_release()
            except tk.TclError:
                pass
            replace_window.destroy()

        replace_window.protocol("WM_DELETE_WINDOW", close_replace_window)

        content_frame = ctk.CTkScrollableFrame(replace_window, fg_color="transparent")
        content_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        header_replace = ctk.CTkFrame(content_frame, fg_color=COLOR_CARD, corner_radius=0)
        header_replace.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            header_replace,
            text="Punktwolkendaten austauschen",
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w", padx=20, pady=(16, 4))

        ctk.CTkLabel(
            header_replace,
            text="Es werden nur die Punktwolkendaten ersetzt. Projektname, Projekt-ID und Link bleiben unveraendert.",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_TEXT_DIM
        ).pack(anchor="w", padx=20, pady=(0, 16))

        info_card = ctk.CTkFrame(content_frame, corner_radius=12)
        info_card.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            info_card,
            text=f"Kunde: {project_info.get('kunde', '')}",
            font=ctk.CTkFont(size=12)
        ).pack(anchor="w", padx=16, pady=(14, 4))

        ctk.CTkLabel(
            info_card,
            text=f"Projekt: {project_info.get('projekt', '')}",
            font=ctk.CTkFont(size=12)
        ).pack(anchor="w", padx=16, pady=4)

        ctk.CTkLabel(
            info_card,
            text=f"Projekt-ID: {project_info.get('id', '')}",
            font=ctk.CTkFont(size=12)
        ).pack(anchor="w", padx=16, pady=4)

        ctk.CTkLabel(
            info_card,
            text=project_info.get("link", ""),
            font=ctk.CTkFont(size=10),
            text_color=COLOR_TEXT_DIM,
            wraplength=660,
            justify="left"
        ).pack(anchor="w", padx=16, pady=(4, 14))

        upload_card = ctk.CTkFrame(content_frame, corner_radius=12)
        upload_card.pack(fill="both", expand=True, pady=(0, 12))

        replacement_entry = ctk.CTkEntry(
            upload_card,
            font=ctk.CTkFont(family="Consolas", size=11),
            height=34,
            placeholder_text="Neue LAS oder LAZ Datei fuer dieses Projekt auswaehlen"
        )
        replacement_entry.pack(fill="x", padx=16, pady=(16, 10))

        def select_replacement_file():
            file_path = filedialog.askopenfilename(
                title="Neue LAS/LAZ Datei fuer den Projektaustausch waehlen",
                filetypes=[("LAS/LAZ", "*.laz *.las"), ("Alle Dateien", "*.*")]
            )
            if file_path:
                set_replacement_file(file_path)

        ctk.CTkButton(
            upload_card,
            text="LAS/LAZ Datei waehlen",
            command=select_replacement_file,
            fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER,
            height=34
        ).pack(anchor="w", padx=16, pady=(0, 10))

        drop_frame_replace = ctk.CTkFrame(
            upload_card,
            fg_color="#1e1e2e",
            corner_radius=8,
            border_width=1,
            border_color="#334155"
        )
        drop_frame_replace.pack(fill="x", padx=16, pady=(0, 12))
        drop_frame_replace.configure(height=180)
        drop_frame_replace.pack_propagate(False)

        drop_label_replace = tk.Label(
            drop_frame_replace,
            text="Datei hier hineinziehen\n\n(nur .las oder .laz)",
            bg="#1e1e2e",
            fg="#cbd5e1",
            padx=20,
            pady=40,
            font=("Segoe UI", 13, "bold"),
            justify="center"
        )
        drop_label_replace.pack(fill="both", expand=True)
        drop_label_replace.drop_target_register(DND_FILES)
        drop_frame_replace.drop_target_register(DND_FILES)

        def set_replacement_file(file_path):
            replacement_entry.delete(0, tk.END)
            replacement_entry.insert(0, file_path)
            drop_label_replace.configure(
                text=f"Datei erkannt\n\n{os.path.basename(file_path)}\n\nAustausch unten manuell per Button starten"
            )
            drop_frame_replace.configure(border_color=COLOR_SUCCESS)

        def handle_replacement_drop(event):
            file_path = extract_dropped_file(event.data)
            if os.path.isfile(file_path):
                set_replacement_file(file_path)
                valid, message = validate_replacement_file(file_path)
                if not valid:
                    messagebox.showerror("Fehler", message)
                    return

        drop_label_replace.dnd_bind('<<Drop>>', handle_replacement_drop)
        drop_frame_replace.dnd_bind('<<Drop>>', handle_replacement_drop)

        status_card = ctk.CTkFrame(content_frame, corner_radius=12)
        status_card.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            status_card,
            text="Status",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", padx=16, pady=(16, 6))

        replace_progress_bar = ctk.CTkProgressBar(status_card, height=10, corner_radius=5)
        replace_progress_bar.pack(fill="x", padx=16, pady=(0, 8))
        replace_progress_bar.set(0)

        replace_progress_detail = ctk.CTkLabel(
            status_card,
            text="Noch kein Austausch gestartet.",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_TEXT_DIM
        )
        replace_progress_detail.pack(anchor="w", padx=16, pady=(0, 10))

        dialog_ui = {
            "progress_bar": replace_progress_bar,
            "progress_detail": replace_progress_detail
        }

        button_row = ctk.CTkFrame(replace_window, fg_color=COLOR_CARD, corner_radius=10)
        button_row.pack(fill="x", padx=16, pady=(0, 16))

        def start_replacement(skip_confirm=False):
            replacement_file = replacement_entry.get().strip()
            if not replacement_file:
                messagebox.showwarning("Fehler", "Bitte eine LAS- oder LAZ-Datei auswaehlen!")
                return

            valid, message = validate_replacement_file(replacement_file)
            if not valid:
                messagebox.showerror("Fehler", message)
                return

            if not skip_confirm:
                if not messagebox.askyesno(
                    "Austausch bestaetigen",
                    f"Die Punktwolkendaten von '{project_info.get('projekt', '')}' werden ersetzt.\n\n"
                    "Projektname, Projekt-ID und Link bleiben unveraendert.\n\n"
                    "Moechten Sie fortfahren?"
                ):
                    return

            btn_replace.configure(state="disabled", text="Austausch laeuft...")
            btn_cancel.configure(state="disabled")
            replace_window.protocol("WM_DELETE_WINDOW", lambda: None)

            thread = threading.Thread(
                target=replace_project_process,
                args=(
                    project_info,
                    replacement_file,
                    aws_access,
                    aws_secret,
                    load_projects
                ),
                kwargs={"ui": dialog_ui},
                daemon=True
            )
            thread.start()

            def check_thread():
                if thread.is_alive():
                    root.after(100, check_thread)
                    return

                if replace_window.winfo_exists():
                    replace_window.protocol("WM_DELETE_WINDOW", close_replace_window)
                    btn_replace.configure(state="normal", text="Punktwolke austauschen")
                    btn_cancel.configure(state="normal")

            root.after(100, check_thread)

        btn_replace = ctk.CTkButton(
            button_row,
            text="Punktwolke austauschen",
            fg_color="#f59e0b",
            hover_color="#d97706",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=38,
            command=start_replacement
        )
        btn_replace.pack(side="left")

        btn_cancel = ctk.CTkButton(
            button_row,
            text="Schliessen",
            fg_color="transparent",
            border_width=1,
            height=38,
            command=close_replace_window
        )
        btn_cancel.pack(side="right")

    ctk.CTkButton(
        btn_frame,
        text="Punktwolke austauschen",
        fg_color="#f59e0b",
        hover_color="#d97706",
        font=ctk.CTkFont(size=12),
        height=36,
        command=open_replace_dialog
    ).pack(side="left", padx=(0, 8))

    def load_projects(selected_customer="Alle Kunden", search_term=""):
        """LÃƒÂ¤dt Projekte von S3 und wendet Filter an"""
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

            # Kunden-Dropdown befÃƒÂ¼llen
            unique_customers = sorted(set(p.get("kunde", "") for p in projects if p.get("kunde", "")))
            customer_filter.configure(values=["Alle Kunden"] + unique_customers)

            # Sortiere nach Datum (neueste zuerst)
            projects.sort(key=lambda x: x.get("datum", ""), reverse=True)

            # Filter anwenden
            filtered_projects = []
            search_lower = search_term.lower()

            for proj in projects:
                kunde = proj.get("kunde", "")
                projekt = proj.get("projekt", "").lower()

                # Kunden-Filter
                if selected_customer != "Alle Kunden" and kunde != selected_customer:
                    continue

                # Text-Suche im Projektnamen
                if search_term and search_lower not in projekt:
                    continue

                filtered_projects.append(proj)

            if not filtered_projects:
                tree.insert("", "end", values=("", "", "Keine passenden Projekte gefunden", "", ""))
                return

            for proj in filtered_projects:
                # Formatiere Datum fÃƒÂ¼r bessere Lesbarkeit
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
    global settings_window_ref
    """Ãƒâ€“ffnet das Einstellungs-Fenster"""
    if focus_existing_window(settings_window_ref):
        if first_run:
            try:
                settings_window_ref.transient(root)
                settings_window_ref.grab_set()
            except tk.TclError:
                pass
        return

    settings_window = ctk.CTkToplevel(root)
    settings_window_ref = settings_window
    settings_window.title("Einstellungen")
    settings_window.geometry("600x500")

    def close_settings_window():
        global settings_window_ref
        settings_window_ref = None
        try:
            settings_window.grab_release()
        except tk.TclError:
            pass
        settings_window.destroy()

    settings_window.protocol("WM_DELETE_WINDOW", close_settings_window)
    
    if first_run:
        # Bei erstem Start Modal machen
        settings_window.transient(root)
        settings_window.grab_set()

    # Header
    header = ctk.CTkFrame(settings_window, fg_color=COLOR_CARD, corner_radius=0)
    header.pack(fill="x", pady=(0, 16))

    ctk.CTkLabel(
        header,
        text="Einstellungen",
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

    # Hauptframe fÃƒÂ¼r Einstellungen
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
        placeholder_text="Geheimer SchlÃƒÂ¼ssel",
        font=ctk.CTkFont(size=11),
        show="Ã¢â‚¬Â¢",
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
            log("[TEST] Ã¢Å“â€œ AWS S3 Verbindung hergestellt")
            messagebox.showinfo("Erfolg", "AWS S3 Verbindung hergestellt")
        except Exception as e:
            log(f"[TEST] Verbindung fehlgeschlagen: {e}")
            messagebox.showerror("Fehler", f"Verbindung fehlgeschlagen:\n{e}")
    
    ctk.CTkButton(
        aws_card,
        text="Ã°Å¸â€â€”  Verbindung testen",
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
        text="Die App bringt PotreeConverter.exe und laszip.dll mit. FÃƒÂ¼r klassische LAS/LAZ Uploads ist keine externe Installation mehr nÃƒÂ¶tig.",
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
            title="PotreeConverter.exe wÃƒÂ¤hlen",
            filetypes=[("Executable", "*.exe"), ("Alle Dateien", "*.*")]
        )
        if file:
            entry_converter.delete(0, tk.END)
            entry_converter.insert(0, file)

    ctk.CTkButton(
        converter_frame,
        text="Ã°Å¸â€œÂ",
        width=40,
        command=browse_converter
    ).pack(side="right")

    # Output Ordner
    ctk.CTkLabel(
        converter_card,
        text="TemporÃƒÂ¤rer Output-Ordner:",
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
        folder = filedialog.askdirectory(title="Output-Ordner wÃƒÂ¤hlen")
        if folder:
            entry_output.delete(0, tk.END)
            entry_output.insert(0, folder)

    ctk.CTkButton(
        output_frame,
        text="Ã°Å¸â€œÂ",
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
            messagebox.showwarning("Fehler", "Bitte einen gÃƒÂ¼ltigen Pfad zum Potree Converter angeben!")
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
            log("[CONFIG] Ã¢Å“â€œ Einstellungen gespeichert")
            close_settings_window()
        else:
            messagebox.showerror("Fehler", "Einstellungen konnten nicht gespeichert werden!")

    btn_save = ctk.CTkButton(
        settings_window,
        text="Einstellungen speichern",
        font=ctk.CTkFont(size=14, weight="bold"),
        fg_color=COLOR_SUCCESS,
        hover_color=COLOR_SUCCESS_HOVER,
        height=44,
        command=save_settings
    )
    btn_save.pack(fill="x", padx=16, pady=(0, 16))

    if first_run:
        settings_window.protocol("WM_DELETE_WINDOW", lambda: None)


def show_projects_view():
    """Rendert die Projektuebersicht als Hauptansicht im Hauptfenster."""
    config = load_config()
    aws_access = config.get("aws_access", "")
    aws_secret = config.get("aws_secret", "")

    if not aws_access or not aws_secret:
        messagebox.showwarning("Fehler", "Bitte AWS Zugangsdaten in den Einstellungen eingeben!")
        show_settings_view(first_run=False)
        return

    clear_frame(projects_page)
    show_main_view("projects")

    header = ctk.CTkFrame(projects_page, fg_color=COLOR_CARD, corner_radius=0)
    header.pack(fill="x", pady=(0, 8))

    ctk.CTkLabel(
        header,
        text="Projektuebersicht",
        font=ctk.CTkFont(size=22, weight="bold")
    ).pack(side="left", padx=20, pady=16)

    ctk.CTkLabel(
        header,
        text="Bestehende Projekte oeffnen, loeschen oder austauschen",
        font=ctk.CTkFont(size=12),
        text_color=COLOR_TEXT_DIM
    ).pack(side="left", padx=(0, 20), pady=(18, 12))

    filter_frame = ctk.CTkFrame(projects_page, fg_color=COLOR_CARD, corner_radius=12)
    filter_frame.pack(fill="x", padx=16, pady=(0, 8))

    filter_inner = ctk.CTkFrame(filter_frame, fg_color="transparent")
    filter_inner.pack(fill="x", padx=16, pady=12)

    ctk.CTkLabel(
        filter_inner,
        text="Filter",
        font=ctk.CTkFont(size=13, weight="bold")
    ).pack(side="left", padx=(0, 12))

    ctk.CTkLabel(
        filter_inner,
        text="Kunde:",
        font=ctk.CTkFont(size=12)
    ).pack(side="left", padx=(0, 4))

    customer_filter = ctk.CTkComboBox(
        filter_inner,
        values=["Alle Kunden"],
        width=180,
        font=ctk.CTkFont(size=12),
        state="readonly",
        command=lambda _value: apply_filter()
    )
    customer_filter.set("Alle Kunden")
    customer_filter.pack(side="left", padx=(0, 12))

    search_entry = ctk.CTkEntry(
        filter_inner,
        placeholder_text="Projekt suchen...",
        font=ctk.CTkFont(size=12),
        width=260
    )
    search_entry.pack(side="left", padx=(0, 8))

    def apply_filter():
        load_projects(customer_filter.get(), search_entry.get().strip())

    search_entry.bind("<KeyRelease>", lambda _event: apply_filter())

    ctk.CTkButton(
        filter_inner,
        text="Zuruecksetzen",
        font=ctk.CTkFont(size=12),
        width=120,
        height=30,
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        command=lambda: (customer_filter.set("Alle Kunden"), search_entry.delete(0, tk.END), load_projects())
    ).pack(side="left", padx=(0, 8))

    ctk.CTkButton(
        filter_inner,
        text="Aktualisieren",
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        font=ctk.CTkFont(size=12),
        width=130,
        height=30,
        command=lambda: load_projects()
    ).pack(side="right")

    table_frame = ctk.CTkFrame(projects_page, fg_color="transparent")
    table_frame.pack(fill="both", expand=True, padx=16, pady=(0, 12))

    scroll_y = ctk.CTkScrollbar(table_frame, orientation="vertical")
    scroll_y.pack(side="right", fill="y")

    scroll_x = ctk.CTkScrollbar(table_frame, orientation="horizontal")
    scroll_x.pack(side="bottom", fill="x")

    tree = ttk.Treeview(
        table_frame,
        columns=("id", "kunde", "projekt", "datum", "url"),
        show="headings",
        yscrollcommand=scroll_y.set,
        xscrollcommand=scroll_x.set,
        height=20
    )
    tree.heading("id", text="ID")
    tree.heading("kunde", text="Kunde")
    tree.heading("projekt", text="Projekt")
    tree.heading("datum", text="Datum")
    tree.heading("url", text="Web-Link")
    tree.column("id", width=90, anchor="center")
    tree.column("kunde", width=180)
    tree.column("projekt", width=220)
    tree.column("datum", width=150, anchor="center")
    tree.column("url", width=560)
    tree.pack(fill="both", expand=True)

    scroll_y.configure(command=tree.yview)
    scroll_x.configure(command=tree.xview)

    style = ttk.Style()
    style.theme_use("clam")
    style.configure(
        "Treeview",
        background=COLOR_CARD,
        foreground="#e2e8f0",
        fieldbackground=COLOR_CARD,
        borderwidth=0,
        font=("Segoe UI", 10)
    )
    style.configure(
        "Treeview.Heading",
        background=COLOR_SURFACE,
        foreground="#e2e8f0",
        font=("Segoe UI", 10, "bold")
    )
    style.map("Treeview", background=[("selected", COLOR_ACCENT)])

    btn_frame = ctk.CTkFrame(projects_page, fg_color="transparent")
    btn_frame.pack(fill="x", padx=16, pady=(0, 16))
    projects_by_id = {}

    def get_selected_project():
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("Info", "Bitte ein Projekt auswaehlen!")
            return None

        item = tree.item(selected[0])
        project_id = item["values"][0]
        if not project_id:
            messagebox.showinfo("Info", "Bitte ein gueltiges Projekt auswaehlen!")
            return None

        project_info = projects_by_id.get(project_id)
        if not project_info:
            messagebox.showerror("Fehler", "Projekt konnte im aktuellen Index nicht gefunden werden!")
            return None

        return project_info

    def open_in_browser():
        project_info = get_selected_project()
        if project_info:
            webbrowser.open(project_info.get("link", ""))

    def copy_link():
        project_info = get_selected_project()
        if not project_info:
            return
        root.clipboard_clear()
        root.clipboard_append(project_info.get("link", ""))
        messagebox.showinfo("Kopiert", "Link in die Zwischenablage kopiert!")

    def delete_project():
        project_info = get_selected_project()
        if not project_info:
            return

        projekt_name = project_info.get("projekt", "")
        result = messagebox.askyesno(
            "Loeschen bestaetigen",
            f"Projekt '{projekt_name}' wirklich loeschen?\n\nDies loescht alle Dateien aus dem S3 Storage!"
        )
        if not result:
            return

        try:
            s3_client = create_s3_client(aws_access, aws_secret)
            delete_result = delete_project_transaction(s3_client, project_info)

            if delete_result["success"]:
                messagebox.showinfo("Erfolg", "Projekt wurde geloescht und der Link deaktiviert!")
                load_projects()
            elif delete_result.get("partial"):
                messagebox.showwarning("Teilweise geloescht", delete_result["message"])
                load_projects()
            else:
                messagebox.showerror("Fehler", delete_result["message"])
        except Exception as e:
            messagebox.showerror("Fehler", f"Fehler beim Loeschen:\n{e}")

    def open_replace_dialog():
        existing_replace_window = getattr(projects_page, "_replace_window", None)
        if focus_existing_window(existing_replace_window):
            return

        project_info = get_selected_project()
        if not project_info:
            return

        try:
            s3_client = create_s3_client(aws_access, aws_secret)
            index_data = load_projects_index(s3_client)
        except Exception as e:
            messagebox.showerror("Fehler", f"Projektdaten konnten nicht geladen werden:\n{e}")
            return

        current_project = None
        for proj in index_data.get("projects", []):
            if proj.get("id") == project_info.get("id"):
                current_project = proj
                break

        if not current_project:
            messagebox.showerror("Fehler", "Projekt nicht im Index gefunden!")
            return

        replace_window = ctk.CTkToplevel(root)
        projects_page._replace_window = replace_window
        replace_window.title("Punktwolkendaten austauschen")
        replace_window.geometry("980x780")
        replace_window.minsize(920, 720)
        replace_window.transient(root)
        replace_window.lift()
        replace_window.focus_force()
        replace_window.grab_set()

        def close_replace_window():
            if getattr(projects_page, "_replace_window", None) is replace_window:
                projects_page._replace_window = None
            try:
                replace_window.grab_release()
            except tk.TclError:
                pass
            replace_window.destroy()

        replace_window.protocol("WM_DELETE_WINDOW", close_replace_window)

        content_frame = ctk.CTkScrollableFrame(replace_window, fg_color="transparent")
        content_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        header_replace = ctk.CTkFrame(content_frame, fg_color=COLOR_CARD, corner_radius=0)
        header_replace.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            header_replace,
            text="Punktwolkendaten austauschen",
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w", padx=20, pady=(16, 4))

        ctk.CTkLabel(
            header_replace,
            text="Nur die Punktwolkendaten werden ersetzt. Projektname, Projekt-ID und Link bleiben unveraendert.",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_TEXT_DIM
        ).pack(anchor="w", padx=20, pady=(0, 16))

        info_card = ctk.CTkFrame(content_frame, corner_radius=12)
        info_card.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(info_card, text=f"Kunde: {current_project.get('kunde', '')}", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=16, pady=(14, 4))
        ctk.CTkLabel(info_card, text=f"Projekt: {current_project.get('projekt', '')}", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=16, pady=4)
        ctk.CTkLabel(info_card, text=f"Projekt-ID: {current_project.get('id', '')}", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=16, pady=4)
        ctk.CTkLabel(
            info_card,
            text=current_project.get("link", ""),
            font=ctk.CTkFont(size=10),
            text_color=COLOR_TEXT_DIM,
            wraplength=820,
            justify="left"
        ).pack(anchor="w", padx=16, pady=(4, 14))

        upload_card = ctk.CTkFrame(content_frame, corner_radius=12)
        upload_card.pack(fill="both", expand=True, pady=(0, 12))

        replacement_entry = ctk.CTkEntry(
            upload_card,
            font=ctk.CTkFont(family="Consolas", size=11),
            height=34,
            placeholder_text="Neue LAS- oder LAZ-Datei fuer dieses Projekt auswaehlen"
        )
        replacement_entry.pack(fill="x", padx=16, pady=(16, 10))

        def set_replacement_file(file_path):
            replacement_entry.delete(0, tk.END)
            replacement_entry.insert(0, file_path)
            drop_label_replace.configure(
                text=f"Datei erkannt\n\n{os.path.basename(file_path)}\n\nAustausch unten manuell per Button starten"
            )
            drop_frame_replace.configure(border_color=COLOR_SUCCESS)

        def select_replacement_file():
            file_path = filedialog.askopenfilename(
                title="Neue LAS- oder LAZ-Datei fuer den Projektaustausch waehlen",
                filetypes=[("LAS/LAZ", "*.laz *.las"), ("Alle Dateien", "*.*")]
            )
            if file_path:
                set_replacement_file(file_path)

        ctk.CTkButton(
            upload_card,
            text="LAS/LAZ-Datei waehlen",
            command=select_replacement_file,
            fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER,
            height=34
        ).pack(anchor="w", padx=16, pady=(0, 10))

        drop_frame_replace = ctk.CTkFrame(
            upload_card,
            fg_color="#1e1e2e",
            corner_radius=8,
            border_width=1,
            border_color="#334155"
        )
        drop_frame_replace.pack(fill="x", padx=16, pady=(0, 12))
        drop_frame_replace.configure(height=220)
        drop_frame_replace.pack_propagate(False)

        drop_label_replace = tk.Label(
            drop_frame_replace,
            text="Datei hier hineinziehen\n\n(nur .las oder .laz)",
            bg="#1e1e2e",
            fg="#cbd5e1",
            padx=20,
            pady=40,
            font=("Segoe UI", 13, "bold"),
            justify="center"
        )
        drop_label_replace.pack(fill="both", expand=True)
        drop_label_replace.drop_target_register(DND_FILES)
        drop_frame_replace.drop_target_register(DND_FILES)

        def handle_replacement_drop(event):
            file_path = extract_dropped_file(event.data)
            if os.path.isfile(file_path):
                set_replacement_file(file_path)
                valid, message = validate_replacement_file(file_path)
                if not valid:
                    messagebox.showerror("Fehler", message)

        drop_label_replace.dnd_bind("<<Drop>>", handle_replacement_drop)
        drop_frame_replace.dnd_bind("<<Drop>>", handle_replacement_drop)

        status_card = ctk.CTkFrame(content_frame, corner_radius=12)
        status_card.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            status_card,
            text="Status",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", padx=16, pady=(16, 6))

        replace_progress_bar = ctk.CTkProgressBar(status_card, height=10, corner_radius=5)
        replace_progress_bar.pack(fill="x", padx=16, pady=(0, 8))
        replace_progress_bar.set(0)

        replace_progress_detail = ctk.CTkLabel(
            status_card,
            text="Noch kein Austausch gestartet.",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_TEXT_DIM
        )
        replace_progress_detail.pack(anchor="w", padx=16, pady=(0, 10))

        dialog_ui = {
            "progress_bar": replace_progress_bar,
            "progress_detail": replace_progress_detail
        }

        button_row = ctk.CTkFrame(replace_window, fg_color=COLOR_CARD, corner_radius=10)
        button_row.pack(fill="x", padx=16, pady=(0, 16))

        def start_replacement():
            replacement_file = replacement_entry.get().strip()
            if not replacement_file:
                messagebox.showwarning("Fehler", "Bitte eine LAS- oder LAZ-Datei auswaehlen!")
                return

            valid, message = validate_replacement_file(replacement_file)
            if not valid:
                messagebox.showerror("Fehler", message)
                return

            if not messagebox.askyesno(
                "Austausch bestaetigen",
                f"Die Punktwolkendaten von '{current_project.get('projekt', '')}' werden ersetzt.\n\n"
                "Projektname, Projekt-ID und Link bleiben unveraendert.\n\n"
                "Moechten Sie fortfahren?"
            ):
                return

            btn_replace.configure(state="disabled", text="Austausch laeuft...")
            btn_cancel.configure(state="disabled")
            replace_window.protocol("WM_DELETE_WINDOW", lambda: None)

            thread = threading.Thread(
                target=replace_project_process,
                args=(current_project, replacement_file, aws_access, aws_secret, load_projects),
                kwargs={"ui": dialog_ui},
                daemon=True
            )
            thread.start()

            def check_thread():
                if thread.is_alive():
                    root.after(100, check_thread)
                    return

                if replace_window.winfo_exists():
                    replace_window.protocol("WM_DELETE_WINDOW", close_replace_window)
                    btn_replace.configure(state="normal", text="Punktwolke austauschen")
                    btn_cancel.configure(state="normal")

            root.after(100, check_thread)

        btn_replace = ctk.CTkButton(
            button_row,
            text="Punktwolke austauschen",
            fg_color=COLOR_SUCCESS,
            hover_color=COLOR_SUCCESS_HOVER,
            font=ctk.CTkFont(size=12, weight="bold"),
            height=38,
            command=start_replacement
        )
        btn_replace.pack(side="left")

        btn_cancel = ctk.CTkButton(
            button_row,
            text="Schliessen",
            fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER,
            height=38,
            command=close_replace_window
        )
        btn_cancel.pack(side="right")

    ctk.CTkButton(
        btn_frame,
        text="Im Browser oeffnen",
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        font=ctk.CTkFont(size=13),
        height=36,
        command=open_in_browser
    ).pack(side="left", padx=(0, 8))

    ctk.CTkButton(
        btn_frame,
        text="Link kopieren",
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        font=ctk.CTkFont(size=13),
        height=36,
        command=copy_link
    ).pack(side="left", padx=(0, 8))

    ctk.CTkButton(
        btn_frame,
        text="Loeschen",
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        font=ctk.CTkFont(size=13),
        height=36,
        command=delete_project
    ).pack(side="left", padx=(0, 8))

    ctk.CTkButton(
        btn_frame,
        text="Punktwolke austauschen",
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        font=ctk.CTkFont(size=13),
        height=36,
        command=open_replace_dialog
    ).pack(side="left", padx=(0, 8))

    def load_projects(selected_customer="Alle Kunden", search_term=""):
        """Laedt Projekte von S3 und wendet Filter an."""
        projects_by_id.clear()
        for item in tree.get_children():
            tree.delete(item)

        try:
            s3_client = create_s3_client(aws_access, aws_secret)
            index_data = load_projects_index(s3_client)
            projects = index_data.get("projects", [])

            if not projects:
                tree.insert("", "end", values=("", "", "Keine Projekte gefunden", "", ""))
                return

            unique_customers = sorted(set(p.get("kunde", "") for p in projects if p.get("kunde", "")))
            customer_filter.configure(values=["Alle Kunden"] + unique_customers)

            projects.sort(key=lambda x: x.get("datum", ""), reverse=True)
            filtered_projects = []
            search_lower = search_term.lower()

            for proj in projects:
                kunde = proj.get("kunde", "")
                projekt = proj.get("projekt", "").lower()

                if selected_customer != "Alle Kunden" and kunde != selected_customer:
                    continue
                if search_term and search_lower not in projekt:
                    continue

                filtered_projects.append(proj)

            if not filtered_projects:
                tree.insert("", "end", values=("", "", "Keine passenden Projekte gefunden", "", ""))
                return

            for proj in filtered_projects:
                datum_str = proj.get("datum", "")
                if datum_str and "T" in datum_str:
                    try:
                        dt = datetime.fromisoformat(datum_str)
                        datum_str = dt.strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        pass

                project_id = proj.get("id", "")
                projects_by_id[project_id] = proj
                tree.insert("", "end", values=(
                    project_id,
                    proj.get("kunde", ""),
                    proj.get("projekt", ""),
                    datum_str,
                    proj.get("link", "")
                ))
        except Exception as e:
            messagebox.showerror("Fehler", f"Laden fehlgeschlagen:\n{e}")

    load_projects()


def show_settings_view(first_run=False):
    """Rendert die Einstellungen als Hauptansicht im Hauptfenster."""
    clear_frame(settings_page)
    show_main_view("settings")

    header = ctk.CTkFrame(settings_page, fg_color=COLOR_CARD, corner_radius=0)
    header.pack(fill="x", pady=(0, 16))

    ctk.CTkLabel(
        header,
        text="Einstellungen",
        font=ctk.CTkFont(size=22, weight="bold")
    ).pack(side="left", padx=20, pady=16)

    ctk.CTkLabel(
        header,
        text="AWS Zugang, Converter und Output-Ordner",
        font=ctk.CTkFont(size=12),
        text_color=COLOR_TEXT_DIM
    ).pack(side="left", padx=(0, 20), pady=(18, 12))

    settings_scroll = ctk.CTkScrollableFrame(settings_page, fg_color="transparent")
    settings_scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    if first_run:
        ctk.CTkLabel(
            settings_scroll,
            text="Willkommen beim Dronautix Pointcloud Uploader.\nBitte konfigurieren Sie die Anwendung vor dem ersten Gebrauch.",
            font=ctk.CTkFont(size=13),
            text_color=COLOR_TEXT_DIM,
            wraplength=760,
            justify="left"
        ).pack(anchor="w", pady=(0, 16))

    aws_card = ctk.CTkFrame(settings_scroll, corner_radius=12)
    aws_card.pack(fill="x", pady=(0, 12))

    ctk.CTkLabel(
        aws_card,
        text="AWS Zugangsdaten",
        font=ctk.CTkFont(size=15, weight="bold")
    ).pack(anchor="w", padx=16, pady=(14, 8))

    ctk.CTkLabel(
        aws_card,
        text="AWS Access Key:",
        font=ctk.CTkFont(size=12),
        text_color=COLOR_TEXT_DIM
    ).pack(anchor="w", padx=16, pady=(4, 2))

    entry_aws_access = ctk.CTkEntry(
        aws_card,
        placeholder_text="AKIA...",
        font=ctk.CTkFont(size=12),
        height=32
    )
    entry_aws_access.pack(fill="x", padx=16, pady=(0, 8))

    ctk.CTkLabel(
        aws_card,
        text="AWS Secret Key:",
        font=ctk.CTkFont(size=12),
        text_color=COLOR_TEXT_DIM
    ).pack(anchor="w", padx=16, pady=(4, 2))

    entry_aws_secret = ctk.CTkEntry(
        aws_card,
        placeholder_text="Geheimer Schluessel",
        font=ctk.CTkFont(size=12),
        show="*",
        height=32
    )
    entry_aws_secret.pack(fill="x", padx=16, pady=(0, 8))

    def test_aws_in_settings():
        access = entry_aws_access.get().strip()
        secret = entry_aws_secret.get().strip()

        if not access or not secret:
            messagebox.showwarning("Fehler", "Bitte beide AWS Zugangsdaten eingeben!")
            return

        try:
            log("[TEST] Teste AWS Verbindung...")
            s3 = boto3.client(
                "s3",
                aws_access_key_id=access,
                aws_secret_access_key=secret,
                region_name=REGION_NAME
            )
            s3.head_bucket(Bucket=BUCKET_NAME)
            log("[TEST] AWS S3 Verbindung hergestellt")
            messagebox.showinfo("Erfolg", "AWS S3 Verbindung hergestellt")
        except Exception as e:
            log(f"[TEST] Verbindung fehlgeschlagen: {e}")
            messagebox.showerror("Fehler", f"Verbindung fehlgeschlagen:\n{e}")

    ctk.CTkButton(
        aws_card,
        text="Verbindung testen",
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        font=ctk.CTkFont(size=13),
        height=36,
        width=190,
        command=test_aws_in_settings
    ).pack(anchor="w", padx=16, pady=(0, 14))

    converter_card = ctk.CTkFrame(settings_scroll, corner_radius=12)
    converter_card.pack(fill="x", pady=(0, 12))

    ctk.CTkLabel(
        converter_card,
        text="Potree Converter",
        font=ctk.CTkFont(size=15, weight="bold")
    ).pack(anchor="w", padx=16, pady=(14, 8))

    ctk.CTkLabel(
        converter_card,
        text="Die App bringt PotreeConverter.exe und laszip.dll mit. Fuer klassische LAS/LAZ Uploads ist keine externe Installation noetig.",
        font=ctk.CTkFont(size=11),
        text_color=COLOR_TEXT_DIM,
        wraplength=760,
        justify="left"
    ).pack(anchor="w", padx=16, pady=(0, 8))

    bundled_converter_status = (
        f"Mitgeliefert: {get_bundled_converter_path()}"
        if is_converter_bundle_available()
        else "Mitgelieferter Converter aktuell nicht gefunden"
    )
    ctk.CTkLabel(
        converter_card,
        text=bundled_converter_status,
        font=ctk.CTkFont(size=11),
        text_color=COLOR_TEXT_DIM,
        wraplength=760,
        justify="left"
    ).pack(anchor="w", padx=16, pady=(0, 8))

    ctk.CTkLabel(
        converter_card,
        text="Optionaler Override-Pfad zur PotreeConverter.exe:",
        font=ctk.CTkFont(size=12),
        text_color=COLOR_TEXT_DIM
    ).pack(anchor="w", padx=16, pady=(4, 2))

    converter_frame = ctk.CTkFrame(converter_card, fg_color="transparent")
    converter_frame.pack(fill="x", padx=16, pady=(0, 8))

    entry_converter = ctk.CTkEntry(
        converter_frame,
        placeholder_text="Leer lassen, um den integrierten Converter zu verwenden",
        font=ctk.CTkFont(family="Consolas", size=11),
        height=32
    )
    entry_converter.pack(side="left", fill="x", expand=True, padx=(0, 8))

    def browse_converter():
        file = filedialog.askopenfilename(
            title="PotreeConverter.exe waehlen",
            filetypes=[("Executable", "*.exe"), ("Alle Dateien", "*.*")]
        )
        if file:
            entry_converter.delete(0, tk.END)
            entry_converter.insert(0, file)

    ctk.CTkButton(
        converter_frame,
        text="Durchsuchen",
        width=120,
        font=ctk.CTkFont(size=12),
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        command=browse_converter
    ).pack(side="right")

    ctk.CTkLabel(
        converter_card,
        text="Temporarer Output-Ordner:",
        font=ctk.CTkFont(size=12),
        text_color=COLOR_TEXT_DIM
    ).pack(anchor="w", padx=16, pady=(4, 2))

    output_frame = ctk.CTkFrame(converter_card, fg_color="transparent")
    output_frame.pack(fill="x", padx=16, pady=(0, 14))

    entry_output = ctk.CTkEntry(
        output_frame,
        placeholder_text="C:\\...\\Potree_Output",
        font=ctk.CTkFont(family="Consolas", size=11),
        height=32
    )
    entry_output.pack(side="left", fill="x", expand=True, padx=(0, 8))

    def browse_output():
        folder = filedialog.askdirectory(title="Output-Ordner waehlen")
        if folder:
            entry_output.delete(0, tk.END)
            entry_output.insert(0, folder)

    ctk.CTkButton(
        output_frame,
        text="Durchsuchen",
        width=120,
        font=ctk.CTkFont(size=12),
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        command=browse_output
    ).pack(side="right")

    config = load_config()
    if config.get("aws_access"):
        entry_aws_access.insert(0, config["aws_access"])
    if config.get("aws_secret"):
        entry_aws_secret.insert(0, config["aws_secret"])
    if config.get("converter_path"):
        entry_converter.insert(0, config["converter_path"])
    if config.get("output_base_dir"):
        entry_output.insert(0, config["output_base_dir"])

    def save_settings():
        aws_access = entry_aws_access.get().strip()
        aws_secret = entry_aws_secret.get().strip()
        converter_path = entry_converter.get().strip()
        output_dir = entry_output.get().strip()

        if not aws_access or not aws_secret:
            messagebox.showwarning("Fehler", "Bitte AWS Zugangsdaten eingeben!")
            return

        if converter_path and not os.path.exists(converter_path):
            messagebox.showwarning("Fehler", "Bitte einen gueltigen Pfad zum Potree Converter angeben!")
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
            log("[CONFIG] Einstellungen gespeichert")
            if first_run:
                show_main_view("upload")
        else:
            messagebox.showerror("Fehler", "Einstellungen konnten nicht gespeichert werden!")

    ctk.CTkButton(
        settings_scroll,
        text="Einstellungen speichern",
        font=ctk.CTkFont(size=15, weight="bold"),
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        height=44,
        width=240,
        command=save_settings
    ).pack(anchor="w", pady=(0, 16))


# ============================================================
#  HAUPTFENSTER
# ============================================================

root = TkinterDnD.Tk()
root.title(f"{APP_NAME} {APP_VERSION}")
root.geometry("1240x900")

# Icon (optional)
try:
    root.iconbitmap(get_bundled_resource_path("icon.ico"))
except:
    pass

# Statusleiste
status_bar = ctk.CTkFrame(root, fg_color=COLOR_CARD, corner_radius=0, height=28)
status_bar.pack(side="bottom", fill="x")
status_bar.pack_propagate(False)

ctk.CTkLabel(
    status_bar,
    text=f"Version {APP_VERSION}",
    font=ctk.CTkFont(size=11),
    text_color=COLOR_TEXT_DIM
).pack(side="right", padx=12, pady=4)

# App-Layout
app_shell = ctk.CTkFrame(root, fg_color=COLOR_SURFACE, corner_radius=0)
app_shell.pack(fill="both", expand=True)

sidebar = ctk.CTkFrame(app_shell, width=260, fg_color=COLOR_CARD, corner_radius=0)
sidebar.pack(side="left", fill="y")
sidebar.pack_propagate(False)

ctk.CTkLabel(
    sidebar,
    text=f"Version {APP_VERSION}",
    font=ctk.CTkFont(size=12),
    text_color=COLOR_TEXT_DIM
).pack(anchor="w", padx=20, pady=(24, 20))

content_area = ctk.CTkFrame(app_shell, fg_color=COLOR_SURFACE, corner_radius=0)
content_area.pack(side="left", fill="both", expand=True)

main_scroll = ctk.CTkScrollableFrame(content_area, fg_color=COLOR_SURFACE, corner_radius=0)
projects_page = ctk.CTkFrame(content_area, fg_color=COLOR_SURFACE, corner_radius=0)
settings_page = ctk.CTkFrame(content_area, fg_color=COLOR_SURFACE, corner_radius=0)

app_views.update({
    "upload": main_scroll,
    "projects": projects_page,
    "settings": settings_page,
})

nav_buttons["upload"] = ctk.CTkButton(
    sidebar,
    text="Upload",
    anchor="w",
    height=46,
    font=ctk.CTkFont(size=13, weight="bold"),
    fg_color=COLOR_ACCENT,
    hover_color=COLOR_ACCENT_HOVER,
    command=lambda: show_main_view("upload")
)
nav_buttons["upload"].pack(fill="x", padx=12, pady=(0, 8))

nav_buttons["projects"] = ctk.CTkButton(
    sidebar,
    text="Projektübersicht",
    anchor="w",
    height=46,
    font=ctk.CTkFont(size=13, weight="bold"),
    fg_color=COLOR_ACCENT,
    hover_color=COLOR_ACCENT_HOVER,
    command=show_projects_view
)
nav_buttons["projects"].pack(fill="x", padx=12, pady=(0, 8))

nav_buttons["settings"] = ctk.CTkButton(
    sidebar,
    text="Einstellungen",
    anchor="w",
    height=46,
    font=ctk.CTkFont(size=13, weight="bold"),
    fg_color=COLOR_ACCENT,
    hover_color=COLOR_ACCENT_HOVER,
    command=lambda: show_settings_view(first_run=False)
)
nav_buttons["settings"].pack(fill="x", padx=12, pady=(0, 8))

show_main_view("upload")

# ============================================================
#  HEADER MIT MENÃƒÅ“
# ============================================================

header = ctk.CTkFrame(main_scroll, fg_color=COLOR_CARD, corner_radius=0)
header.pack(fill="x", pady=(0, 4))

header_inner = ctk.CTkFrame(header, fg_color="transparent")
header_inner.pack(fill="x", padx=24, pady=16)

ctk.CTkLabel(
    header_inner,
    text=f"{APP_NAME}",
    font=ctk.CTkFont(size=20, weight="bold")
).pack(side="left")

ctk.CTkLabel(
    header_inner,
    text=f"v{APP_VERSION}",
    font=ctk.CTkFont(size=12),
    text_color=COLOR_TEXT_DIM
).pack(side="left", padx=(10, 0), pady=(4, 0))

ctk.CTkLabel(
    header_inner,
    text="Upload und Verwaltung im selben Fenster",
    font=ctk.CTkFont(size=11),
    text_color=COLOR_TEXT_DIM
).pack(side="right", pady=(4, 0))

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
    card_data, text="LAZ / LAS Datei waehlen...",
    fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
    text_color="#e2e8f0",
    font=ctk.CTkFont(size=12),
    height=36, width=220, command=select_file
).pack(anchor="w", padx=16, pady=(0, 6))

entry_file = ctk.CTkEntry(card_data, font=ctk.CTkFont(family="Consolas", size=11),
                           placeholder_text="Dateipfad...")
entry_file.pack(fill="x", padx=16, pady=(0, 8))

# Drag & Drop Zone
drop_frame = ctk.CTkFrame(card_data, fg_color="#1e1e2e", corner_radius=8,
                           border_width=2, border_color=COLOR_ACCENT)
drop_frame.pack(fill="x", padx=16, pady=(0, 10))
drop_frame.configure(height=220)
drop_frame.pack_propagate(False)

lbl_drop = tk.Label(
    drop_frame, text="Datei hier hineinziehen (Drag & Drop)",
    bg="#1e1e2e", fg="#64748b",
    font=("Segoe UI", 13, "bold"),
    pady=40,
    justify="center"
)
lbl_drop.pack(fill="both", expand=True)

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
    text="STARTEN - Konvertieren & Upload",
    font=ctk.CTkFont(size=15, weight="bold"),
    fg_color=COLOR_SUCCESS, hover_color=COLOR_SUCCESS_HOVER,
    height=50, width=320, corner_radius=12,
    command=start_thread
)
btn_start.pack(anchor="w", padx=16, pady=(12, 0))

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
#  5. ERGEBNIS / LINK
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
    card_result, text="Link kopieren",
    fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
    text_color="#e2e8f0",
    font=ctk.CTkFont(size=12), height=34, width=180,
    command=lambda: (root.clipboard_clear(), root.clipboard_append(entry_link.get()), log("Link kopiert!"))
).pack(anchor="w", padx=16, pady=(0, 14))

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

log(f"===  {APP_NAME} v{APP_VERSION}  ===")
log(f"Konfiguration gespeichert in: {APPDATA_DIR}")

# PrÃƒÂ¼fe ob erste AusfÃƒÂ¼hrung
config = load_config()
if config.get("first_run", True):
    log("[INFO] Erste Ausfuehrung erkannt - oeffne Einstellungen...")
    root.after(500, lambda: show_settings_view(first_run=True))
else:
    log("[OK] Konfiguration geladen")
    log("Bereit. Waehle eine .laz oder .las Datei aus.")
    log("Nutze die Navigation links fuer Upload, Projekte und Einstellungen.")

root.after(1000, check_for_available_update)

try:
    root.mainloop()
except Exception as e:
    import traceback
    traceback.print_exc()
    input("Druecke Enter zum Beenden...")


