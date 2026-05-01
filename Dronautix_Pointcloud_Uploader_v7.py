import customtkinter as ctk

import tkinter as tk

from tkinter import filedialog, messagebox, ttk

from tkinterdnd2 import DND_FILES, TkinterDnD

import os

import subprocess

import uuid

import csv

import urllib.error

import urllib.parse

import urllib.request

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

UPDATE_REPO_OWNER = "Preloi"

UPDATE_REPO_NAME = "Dronautix-Pointcloud-Uploader"

UPDATE_MANIFEST_BRANCH = "master"

UPDATE_MANIFEST_URL = (

    f"https://raw.githubusercontent.com/{UPDATE_REPO_OWNER}/{UPDATE_REPO_NAME}/"

    f"{UPDATE_MANIFEST_BRANCH}/latest-release.json"

)

UPDATE_DOWNLOAD_DIR = os.path.join(APPDATA_DIR, "updates")



# S3 Pfad fuer Index-Dateien

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

nav_buttons = {}

app_views = {}

current_view_name = "upload"





# --- HILFSFUNKTIONEN ---



def focus_existing_window(window):

    """Bringt ein bestehendes Fenster in den Vordergrund und gibt True zurück."""

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

    """Setzt die Fortschrittsanzeige des passenden UI-Kontexts zurück."""

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

    """Bereinigt Ordnernamen für Dateisystem und S3."""

    name = (name or "").strip().lower()

    name = name.replace("ae", "ae").replace("oe", "oe").replace("ue", "ue").replace("ss", "ss")

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

    """Laedt gespeicherte Einstellungen aus AppData"""

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

    """Prueft ob der mitgelieferte Converter vollstaendig vorhanden ist."""

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

    """Laedt das Update-Manifest direkt aus dem GitHub-Repo."""

    try:

        request = urllib.request.Request(

            UPDATE_MANIFEST_URL,

            headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"}

        )

        with urllib.request.urlopen(request, timeout=15) as response:

            payload = response.read()

            encoding = response.headers.get_content_charset() or "utf-8"

        manifest = json.loads(payload.decode(encoding))

        log(f"[UPDATE] Manifest von GitHub geladen: {UPDATE_MANIFEST_URL}")

        return manifest

    except Exception as e:

        log(f"[UPDATE] Manifest konnte nicht von GitHub geladen werden: {e}")

        return None



def get_update_installer_url(manifest):

    """Ermittelt die Download-URL fuer den Installer aus dem Manifest."""

    installer_url = str(manifest.get("installer_url", "")).strip()

    if installer_url:

        return installer_url



    remote_version = str(manifest.get("version", "")).strip()

    installer_name = str(manifest.get("installer_name", "")).strip()

    repo_owner = str(manifest.get("repo_owner", UPDATE_REPO_OWNER)).strip()

    repo_name = str(manifest.get("repo_name", UPDATE_REPO_NAME)).strip()

    release_tag = str(manifest.get("release_tag", f"v{remote_version}")).strip()



    if not remote_version or not installer_name or not repo_owner or not repo_name or not release_tag:

        return ""



    return (

        f"https://github.com/{repo_owner}/{repo_name}/releases/download/"

        f"{urllib.parse.quote(release_tag, safe='')}/{urllib.parse.quote(installer_name)}"

    )



def download_update_installer(installer_url, installer_name):

    """Laedt den Installer in den lokalen Update-Cache herunter."""

    os.makedirs(UPDATE_DOWNLOAD_DIR, exist_ok=True)

    installer_path = os.path.join(UPDATE_DOWNLOAD_DIR, installer_name)

    temp_path = f"{installer_path}.download"

    request = urllib.request.Request(

        installer_url,

        headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"}

    )



    try:

        with urllib.request.urlopen(request, timeout=120) as response, open(temp_path, "wb") as installer_file:

            shutil.copyfileobj(response, installer_file)

        os.replace(temp_path, installer_path)

        log(f"[UPDATE] Installer heruntergeladen: {installer_path}")

        return installer_path

    except Exception:

        if os.path.exists(temp_path):

            try:

                os.remove(temp_path)

            except OSError:

                pass

        raise





def check_for_available_update():

    """Prueft beim Start, ob im GitHub-Repo eine neuere Version bereitliegt."""

    try:

        manifest = load_update_manifest()

        if not manifest:

            return



        remote_version = manifest.get("version", "").strip()

        if not remote_version or not is_remote_version_newer(remote_version, APP_VERSION):

            return



        installer_name = str(manifest.get("installer_name", "")).strip()

        installer_url = get_update_installer_url(manifest)

        if not installer_name or not installer_url:

            log(f"[UPDATE] Neue Version {remote_version} gefunden, aber keine gueltige Installer-URL im Manifest")

            messagebox.showwarning(

                "Update verfügbar",

                f"Version {remote_version} ist verfügbar, aber die Download-Informationen sind unvollständig."

            )

            return



        install_now = messagebox.askyesno(

            "Update verfügbar",

            f"Es ist eine neue Version verfügbar.\n\n"

            f"Installierte Version: {APP_VERSION}\n"

            f"Verfuegbare Version: {remote_version}\n\n"

            f"Soll das Update jetzt installiert werden?"

        )



        log(f"[UPDATE] Neue Version verfügbar: {remote_version} ({installer_url})")



        if not install_now:

            log("[UPDATE] Benutzer hat das Update verschoben")

            return



        try:

            log(f"[UPDATE] Lade Installer herunter: {installer_url}")

            installer_path = download_update_installer(installer_url, installer_name)

            subprocess.Popen([installer_path, "/CLOSEAPPLICATIONS"], shell=False)

            log(f"[UPDATE] Installer gestartet: {installer_path}")

            root.after(200, root.destroy)

        except Exception as install_error:

            log(f"[UPDATE] Installer konnte nicht heruntergeladen oder gestartet werden: {install_error}")

            messagebox.showerror(

                "Update fehlgeschlagen",

                f"Der Installer konnte nicht heruntergeladen oder gestartet werden:\n{installer_url}\n\n{install_error}"

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

    """Prueft ob eine Datei für den Projektaustausch geeignet ist."""

    valid, message = validate_file(filepath)

    if not valid:

        return False, message



    if os.path.basename(filepath).lower().endswith(".copc.laz"):

        return False, "Für den Projektaustausch sind nur klassische .las oder .laz Dateien erlaubt"



    return True, "OK"





def resolve_replacement_source(source_path):

    """Ordnet eine Austauschquelle einem unterstützten Typ zu."""

    normalized_path = (source_path or "").strip().strip('"')

    if not normalized_path:

        return "", ""

    if os.path.isdir(normalized_path):

        return "potree_dir", normalized_path

    if os.path.isfile(normalized_path):

        if os.path.basename(normalized_path).lower() == "metadata.json":

            return "potree_dir", os.path.dirname(normalized_path)

        return "raw_file", normalized_path

    return "", normalized_path



def validate_potree_output_dir(directory_path):

    """Prueft ob ein vorhandener Ordner wie ein Potree-Projekt aussieht."""

    if not directory_path or not os.path.isdir(directory_path):

        return False, "Bitte einen gueltigen Potree-Ordner auswählen."

    metadata_path = os.path.join(directory_path, "metadata.json")

    if not os.path.isfile(metadata_path):

        return False, (

            "Im gewählten Ordner wurde keine metadata.json gefunden. "

            "Bitte den Hauptordner des konvertierten Potree-Projekts auswählen."

        )

    return True, "OK"



def validate_replacement_source(source_path):

    """Prueft ob eine Austauschquelle als LAS/LAZ oder Potree-Ordner geeignet ist."""

    source_type, normalized_path = resolve_replacement_source(source_path)

    if source_type == "raw_file":

        return validate_replacement_file(normalized_path)

    if source_type == "potree_dir":

        return validate_potree_output_dir(normalized_path)

    return False, "Bitte eine gueltige LAS/LAZ-Datei oder einen Potree-Ordner auswählen."



def cleanup_local_files(output_path):

    """Loescht die lokalen konvertierten Dateien nach erfolgreichem Upload"""

    try:

        if os.path.exists(output_path):

            shutil.rmtree(output_path)

            log(f"[CLEANUP] [OK] Temporaere Dateien gelöscht: {output_path}")

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

    """Berechnet die Gesamtgroesse aller Dateien"""

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

    """Entfernt Loeschhinweise, die älter als die definierte Aufbewahrungszeit sind."""

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

    """Laedt den bestehenden Projekt-Index von S3"""

    try:

        log(f"[INDEX] Versuche Index zu laden: s3://{BUCKET_NAME}/{S3_INDEX_JSON}")

        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=S3_INDEX_JSON)

        data = json.loads(response['Body'].read().decode('utf-8'))

        project_count = len(data.get('projects', []))

        log(f"[INDEX] [OK] Bestehender Index geladen ({project_count} Projekte)")

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

    """Laedt die Liste der gelöschten Projekte von S3"""

    try:

        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=S3_DELETED_JSON)

        data = json.loads(response['Body'].read().decode('utf-8'))

        data = prune_deleted_projects(data)

        log(f"[GELOESCHT] Liste geladen ({len(data.get('deleted_projects', []))} Einträge)")

        return data

    except s3_client.exceptions.NoSuchKey:

        log("[GELOESCHT] Liste existiert noch nicht - erstelle neue")

        return {"deleted_projects": [], "last_updated": None}

    except Exception as e:

        log(f"[GELOESCHT] WARNUNG: Liste konnte nicht geladen werden: {e}")

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

        log(f"[GELOESCHT] Liste gespeichert ({len(deleted_data['deleted_projects'])} Einträge)")

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

    """Aktualisiert einen bestehenden Deleted-Eintrag oder fuegt ihn vorne ein."""

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

    """Entfernt ein Projekt aus dem Index und gibt True zurück, wenn sich der Index geaendert hat."""

    original_count = len(index_data.get("projects", []))

    index_data["projects"] = [

        project for project in index_data.get("projects", [])

        if project.get("id") != project_id

    ]

    return len(index_data["projects"]) != original_count





def collect_project_objects(s3_client, s3_path):

    """Sammelt alle S3-Objekte unter einem Projektpraefix."""

    return [entry["Key"] for entry in collect_project_object_entries(s3_client, s3_path)]




def collect_project_object_entries(s3_client, s3_path):

    """Sammelt S3-Objekte inklusive Groesse unter einem Projektpraefix."""

    paginator = s3_client.get_paginator('list_objects_v2')

    pages = paginator.paginate(Bucket=BUCKET_NAME, Prefix=s3_path)



    object_entries = []

    for page in pages:

        for obj in page.get('Contents', []):

            object_key = obj.get('Key')

            if not object_key:

                continue

            object_entries.append({

                "Key": object_key,

                "Size": int(obj.get("Size", 0) or 0)

            })



    return object_entries




def build_safe_download_path(base_dir, s3_prefix, object_key):

    """Erstellt einen lokalen Zielpfad ohne S3-Pfadbestandteile blind zu uebernehmen."""

    relative_path = object_key[len(s3_prefix):] if object_key.startswith(s3_prefix) else os.path.basename(object_key)

    relative_path = relative_path.lstrip("/\\")

    safe_parts = []

    for path_part in re.split(r'[/\\]+', relative_path):

        if not path_part or path_part in (".", ".."):

            continue

        safe_parts.append(path_part)

    if not safe_parts:

        fallback_name = os.path.basename(object_key.rstrip("/\\"))

        if not fallback_name:

            return ""

        safe_parts.append(fallback_name)

    return os.path.join(base_dir, *safe_parts)





class DownloadCancelledError(Exception):

    """Signalisiert einen bewusst abgebrochenen Projekt-Download."""




def delete_s3_objects(s3_client, object_keys):

    """Loescht S3-Objekte in Batches und bricht bei partiellen Fehlern ab."""

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

    """Loescht Projektdaten und aktualisiert die Metadaten so robust wie moeglich."""

    s3_path = project_info.get("s3_path", "")

    project_id = project_info.get("id", "")



    if not s3_path:

        return {

            "success": False,

            "partial": False,

            "message": "S3-Pfad nicht gefunden."

        }



    try:

        log(f"[LOESCHEN] Sammle Dateien unter: {s3_path}")

        object_keys = collect_project_objects(s3_client, s3_path)

        if object_keys:

            log(f"[LOESCHEN] Loesche {len(object_keys)} Dateien aus S3...")

            deleted_count = delete_s3_objects(s3_client, object_keys)

            log(f"[LOESCHEN] [OK] {deleted_count} Dateien gelöscht")

        else:

            log(f"[LOESCHEN] Keine Dateien gefunden unter: {s3_path}")



        metadata_errors = []



        log("[LOESCHEN] Aktualisiere Gelöscht-Liste...")

        deleted_data = load_deleted_projects(s3_client)

        deleted_entry = build_deleted_project_entry(project_info, s3_path)

        deleted_data = upsert_deleted_project(deleted_data, deleted_entry)

        if save_deleted_projects(s3_client, deleted_data):

            log("[LOESCHEN] [OK] Gelöscht-Liste aktualisiert")

        else:

            metadata_errors.append("deleted_projects.json")



        log("[LOESCHEN] Aktualisiere Projekt-Index...")

        index_data = load_projects_index(s3_client)

        removed_from_index = remove_project_from_index(index_data, project_id)

        if removed_from_index:

            if save_projects_index(s3_client, index_data):

                log("[LOESCHEN] [OK] Projekt-Index aktualisiert")

            else:

                metadata_errors.append("projects_index.json")

        else:

            log("[LOESCHEN] Projekt war bereits nicht mehr im Index")



        if metadata_errors:

            files = ", ".join(metadata_errors)

            return {

                "success": False,

                "partial": True,

                "message": (

                    "Projektdaten wurden in S3 gelöscht, aber folgende Metadaten "

                    f"konnten nicht vollstaendig aktualisiert werden: {files}"

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





# Zusaetzliche Upload-/Austausch-Helfer



def create_s3_client(aws_access, aws_secret):

    """Erstellt einen S3 Client mit den konfigurierten Zugangsdaten."""

    return boto3.client(

        's3',

        aws_access_key_id=aws_access,

        aws_secret_access_key=aws_secret,

        region_name=REGION_NAME

    )





def build_project_url(folder_kunde, folder_id, folder_projekt, input_format, kunde, projekt):

    """Erstellt den Viewer-Link für ein Projekt."""

    if input_format == "copc":

        path_param = f"{folder_kunde}/{folder_id}/{folder_projekt}/source.copc.laz"

    else:

        path_param = f"{folder_kunde}/{folder_id}/{folder_projekt}"



    project_url = f"{DOMAIN_URL}?id={folder_id}"

    return path_param, project_url


def extract_project_identifiers_from_link(project_link):

    """Extrahiert Viewer-ID und Kurz-ID aus einem Projekt-Link."""

    if not project_link:

        return "", ""

    try:

        parsed_url = urllib.parse.urlparse(project_link)

        query_params = urllib.parse.parse_qs(parsed_url.query)

        raw_identifier = query_params.get("id", [""])[0].strip()

        if not raw_identifier:

            return "", ""

        short_identifier = raw_identifier

        if "/" in raw_identifier:

            id_parts = [part for part in raw_identifier.split("/") if part]

            if len(id_parts) >= 2:

                short_identifier = id_parts[1]

        return raw_identifier, short_identifier

    except Exception:

        return "", ""


def find_project_in_index(index_data, project_id="", project_link=""):

    """Findet ein Projekt im Index ueber ID, Link oder Viewer-Pfad."""

    projects = index_data.get("projects", []) if isinstance(index_data, dict) else []
    normalized_project_id = str(project_id).strip()
    raw_link_identifier, short_link_identifier = extract_project_identifiers_from_link(project_link)

    for project in projects:

        indexed_project_id = str(project.get("id", "")).strip()
        indexed_project_link = str(project.get("link", "")).strip()
        indexed_viewer_path = str(project.get("viewer_path", "")).strip()

        if normalized_project_id and indexed_project_id == normalized_project_id:

            return project

        if project_link and indexed_project_link == project_link:

            return project

        if raw_link_identifier and indexed_viewer_path == raw_link_identifier:

            return project

        if short_link_identifier and indexed_project_id == short_link_identifier:

            return project

    return None





def collect_upload_files(input_format, s3_prefix, source_file=None, output_dir=None):

    """Sammelt alle hochzuladenden Dateien für einen Upload."""

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

    """Callback-Klasse für boto3 upload_file - wird bei jedem Chunk aufgerufen."""



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



        # 1. Pruefungen

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

            log("[FEHLER] Kein Potree Converter verfügbar")

            messagebox.showwarning(

                "Fehler",

                "Mitgelieferter Potree Converter nicht gefunden. "

                "Bitte Build/Projektdateien pruefen oder optional einen Override-Pfad konfigurieren!"

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

            root.after(0, lambda: update_step("Bereite COPC für Upload vor...", 2))

            root.after(0, lambda: progress_bar.set(1))

            log("[COPC] Direkter Upload ohne Potree Converter")

        else:

            root.after(0, lambda: update_step("Konvertiere mit Potree...", 2))

            root.after(0, lambda: progress_bar.set(0))



            # Temporaerer Output-Ordner: kunde/id/projekt

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



        # Viewer-Pfad bleibt sprechend im Index, aber der oeffentliche Link nutzt nur die technische Kurz-ID.

        if is_copc:

            path_param = f"{folder_kunde}/{folder_id}/{folder_projekt}/source.copc.laz"

        else:

            path_param = f"{folder_kunde}/{folder_id}/{folder_projekt}"
        project_url = f"{DOMAIN_URL}?id={folder_id}"



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



        # 5. Cleanup - Temporaere Dateien loeschen

        root.after(0, lambda: update_step("Raeume auf...", 5))

        if is_copc:

            log("[CLEANUP] Kein lokaler Cleanup nötig für COPC Upload")

        else:

            log("[CLEANUP] Loesche temporaere Dateien...")

            

            cleanup_success = cleanup_local_files(output_dir)

            if cleanup_success:

                log("[CLEANUP] Temporärer Ordner erfolgreich gelöscht")

            else:

                log("[CLEANUP] Temporärer Ordner konnte nicht vollstaendig gelöscht werden")



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

        title="LAS/LAZ/COPC Datei auswählen",

        filetypes=[("Point Cloud", "*.copc.laz *.laz *.las"), ("Alle Dateien", "*.*")]

    )

    if file:

        entry_file.delete(0, tk.END)

        entry_file.insert(0, file)
        update_drop_zone_text(file)

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





def set_drop_zone_text(drop_label, file_path, empty_text="Datei hier hineinziehen (Drag & Drop)"):

    """Aktualisiert den Text einer Drag-and-Drop-Zone."""

    if not widget_exists(drop_label):

        return

    if file_path:

        drop_label.configure(

            text=file_path,

            fg="#e2e8f0",

            justify="left",

            anchor="w",

            padx=20,

            wraplength=900

        )

    else:

        drop_label.configure(

            text=empty_text,

            fg="#64748b",

            justify="center",

            anchor="center",

            padx=0,

            wraplength=0

        )



def update_drop_zone_text(file_path):

    """Zeigt den Dateipfad direkt in der Drag-and-Drop-Zone an."""

    set_drop_zone_text(lbl_drop, file_path)


def drop_file(event):

    file_path = extract_dropped_file(event.data)

    if os.path.isfile(file_path):

        entry_file.delete(0, tk.END)

        entry_file.insert(0, file_path)

        update_drop_zone_text(file_path)

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

        messagebox.showwarning("Fehler", "Bitte eine LAZ/LAS Datei auswählen!")

        return

    if not kunde or not projekt:

        messagebox.showwarning("Fehler", "Bitte Kunde und Projekt eingeben!")

        return



    config = load_config()

    aws_access = config.get("aws_access", "")

    aws_secret = config.get("aws_secret", "")



    btn_start.configure(state="disabled", text="Läuft...")

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

    """Setzt die Fortschrittsanzeige zurück"""

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
    prepared_output_dir = ""
    replacement_source_type = ""
    replacement_source_label = ""



    try:

        ui_reset_progress(ui)

        ui_set_step("Pruefe Austauschquelle...", 1, ui)

        ui_set_progress(0, ui)

        ui_set_detail("Pruefe die ausgewaehlte Datei oder den Potree-Ordner...", ui)



        replacement_source_type, normalized_replacement_source = resolve_replacement_source(replacement_file)

        valid, message = validate_replacement_source(replacement_file)

        if not valid:

            ui_log(f"[AUSTAUSCH] [FEHLER] {message}", ui)

            root.after(0, lambda msg=message: messagebox.showerror("Fehler", msg))

            return



        if project_format == "copc" or viewer_path.endswith(".copc.laz"):

            message = (

                "Dieses Projekt nutzt derzeit COPC. Der Austausch mit Potree-Konvertierung ist aktuell "

                "nur für klassische Potree-Projekte verfügbar, damit Link und Viewer-Pfad unverändert bleiben."

            )

            ui_log(f"[AUSTAUSCH] [FEHLER] {message}", ui)

            root.after(0, lambda msg=message: messagebox.showerror("Fehler", msg))

            return



        if not aws_access or not aws_secret:

            ui_log("[AUSTAUSCH] [FEHLER] Bitte AWS Keys in den Einstellungen eingeben!", ui)

            root.after(0, lambda: messagebox.showwarning("Fehler", "Bitte AWS Zugangsdaten in den Einstellungen eingeben!"))

            return



        if not s3_prefix or not project_id:

            ui_log("[AUSTAUSCH] [FEHLER] Projektdaten unvollständig", ui)

            root.after(0, lambda: messagebox.showerror("Fehler", "Projektdaten sind unvollständig."))

            return



        ui_log(f"[AUSTAUSCH] Starte Datenaustausch für Projekt '{project_name}' ({project_id})", ui)

        ui_log(f"[AUSTAUSCH] Ziel bleibt unverändert: {s3_prefix}", ui)


        if replacement_source_type == "potree_dir":

            prepared_output_dir = normalized_replacement_source

            replacement_source_label = os.path.basename(prepared_output_dir.rstrip("\\/")) or prepared_output_dir

            ui_log(f"[AUSTAUSCH] Verwende vorhandenen Potree-Ordner: {prepared_output_dir}", ui)

            ui_set_step("Pruefe Potree-Ordner...", 2, ui)

            ui_set_progress(0.1, ui)

            ui_set_detail("Bereits konvertierte Punktwolken werden direkt verwendet...", ui)

        else:

            replacement_source_label = os.path.basename(normalized_replacement_source)

            if not converter_path:

                ui_log("[AUSTAUSCH] [FEHLER] Kein Potree Converter verfügbar", ui)

                root.after(0, lambda: messagebox.showwarning(

                    "Fehler",

                    "Mitgelieferter Potree Converter nicht gefunden. Bitte Build/Projektdateien pruefen oder einen Override-Pfad konfigurieren!"

                ))

                return



            if not output_base_dir:

                ui_log("[AUSTAUSCH] [FEHLER] Output-Ordner nicht konfiguriert!", ui)

                root.after(0, lambda: messagebox.showwarning("Fehler", "Bitte einen Output-Ordner in den Einstellungen angeben!"))

                return



            ui_log(f"[AUSTAUSCH] Neue Quelldatei: {replacement_source_label}", ui)

            ui_set_step("Konvertiere mit Potree...", 2, ui)

            ui_set_progress(0, ui)

            ui_set_detail("Starte den Potree Converter...", ui)



            temp_output_dir = os.path.join(

                output_base_dir,

                "_project_replacements",

                f"{project_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            )

            prepared_output_dir = temp_output_dir

            run_potree_conversion(normalized_replacement_source, converter_path, temp_output_dir, ui=ui)



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

        files_to_upload = collect_upload_files("potree", s3_prefix, output_dir=prepared_output_dir)



        if not files_to_upload:

            ui_log("[AUSTAUSCH] [FEHLER] Keine Potree-Dateien zum Upload gefunden!", ui)

            log("[AUSTAUSCH] [FEHLER] Keine Potree-Dateien zum Upload gefunden!")

            root.after(0, lambda: messagebox.showerror("Fehler", "Keine Potree-Dateien zum Upload gefunden!"))

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

            ui_log(f"[AUSTAUSCH] {deleted_count} veraltete Dateien gelöscht", ui)

            log(f"[AUSTAUSCH] {deleted_count} veraltete Dateien gelöscht")

        else:

            ui_log("[AUSTAUSCH] Keine veralteten Dateien zum Löschen gefunden", ui)

            log("[AUSTAUSCH] Keine veralteten Dateien zum Löschen gefunden")



        ui_set_step("Raeume auf...", 5, ui)

        if temp_output_dir:

            ui_set_detail("Entferne temporaere Konvertierungsdaten...", ui)

            cleanup_local_files(temp_output_dir)

        else:

            ui_set_detail("Keine lokalen Temp-Daten zu entfernen.", ui)

            ui_log("[AUSTAUSCH] Vorhandener Potree-Ordner bleibt lokal unverändert", ui)



        ui_set_progress(1, ui)

        ui_set_step("Austausch abgeschlossen", 5, ui)

        ui_set_detail("Projektname, Projekt-ID und Link bleiben unverändert.", ui)

        ui_log("=" * 50, ui)

        ui_log("PROJEKTDATEN ERFOLGREICH AUSGETAUSCHT", ui)

        ui_log(f"Projekt: {project_name} ({project_id})", ui)

        ui_log(f"Quelle: {replacement_source_label}", ui)

        ui_log(f"Link unverändert: {project_link}", ui)

        ui_log("=" * 50, ui)



        log("=" * 50)

        log("PROJEKTDATEN ERFOLGREICH AUSGETAUSCHT")

        log(f"Projekt: {project_name} ({project_id})")

        log(f"Quelle: {replacement_source_label}")

        log(f"Link unverändert: {project_link}")

        log("=" * 50)



        root.after(0, lambda: root.clipboard_clear())

        root.after(0, lambda: root.clipboard_append(project_link))

        root.after(0, lambda: messagebox.showinfo(

            "Erfolg",

            f"Die Punktwolkendaten von '{project_name}' wurden ersetzt.\n\nProjektname, Projekt-ID und Link bleiben unverändert."

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





def duplicate_project_process(project_info, new_kunde, new_projekt, aws_access, aws_secret, on_success=None, ui=None):

    """Dupliziert ein bestehendes Projekt mit neuem Namen und eigener ID."""

    try:

        source_s3_path = project_info.get("s3_path", "")

        source_format = project_info.get("format", "potree")



        if not source_s3_path:

            raise ValueError("Quellprojekt hat keinen S3-Pfad.")



        ui_log("[DUPLIZIEREN] Starte Projektduplizierung...", ui)

        ui_set_progress(0.05, ui)

        ui_set_detail("Verbinde mit S3...", ui)



        s3_client = create_s3_client(aws_access, aws_secret)



        # 1. Quelldateien auflisten

        ui_set_detail("Sammle Quelldateien...", ui)

        ui_set_progress(0.1, ui)

        source_keys = collect_project_objects(s3_client, source_s3_path)

        if not source_keys:

            raise ValueError("Keine Dateien im Quellprojekt gefunden.")

        ui_log(f"[DUPLIZIEREN] {len(source_keys)} Dateien gefunden", ui)



        # 2. Neuen Pfad berechnen

        folder_kunde = sanitize_folder_name(new_kunde)

        folder_id = uuid.uuid4().hex[:6]

        folder_projekt = sanitize_folder_name(new_projekt)

        new_s3_prefix = f"pointclouds/{folder_kunde}/{folder_id}/{folder_projekt}"



        ui_log(f"[DUPLIZIEREN] Neuer Pfad: {new_s3_prefix}", ui)



        # 3. Dateien kopieren

        ui_set_detail("Kopiere Dateien...", ui)

        total = len(source_keys)

        for i, source_key in enumerate(source_keys):

            rel_path = source_key[len(source_s3_path):]

            if rel_path.startswith("/"):

                rel_path = rel_path[1:]

            dest_key = f"{new_s3_prefix}/{rel_path}" if rel_path else f"{new_s3_prefix}/{os.path.basename(source_key)}"



            s3_client.copy_object(

                Bucket=BUCKET_NAME,

                CopySource={'Bucket': BUCKET_NAME, 'Key': source_key},

                Key=dest_key,

                CacheControl='no-cache, no-store, must-revalidate, max-age=0',

                MetadataDirective='REPLACE'

            )



            progress = 0.1 + 0.7 * ((i + 1) / total)

            ui_set_progress(progress, ui)

            ui_set_detail(f"Kopiere Datei {i + 1}/{total}...", ui)



        ui_log(f"[DUPLIZIEREN] {total} Dateien kopiert", ui)



        # 4. URL generieren

        ui_set_detail("Erstelle Index-Eintrag...", ui)

        ui_set_progress(0.85, ui)

        path_param, project_url = build_project_url(

            folder_kunde, folder_id, folder_projekt, source_format, new_kunde, new_projekt

        )



        # 5. Index aktualisieren

        timestamp = datetime.now().isoformat()

        index_data = load_projects_index(s3_client)

        new_project = {

            "datum": timestamp,

            "kunde": new_kunde,

            "id": folder_id,

            "projekt": new_projekt,

            "format": source_format,

            "link": project_url,

            "viewer_path": path_param,

            "s3_path": new_s3_prefix

        }

        index_data["projects"].insert(0, new_project)

        save_projects_index(s3_client, index_data)

        ui_log("[DUPLIZIEREN] Projekt-Index aktualisiert", ui)



        # 6. Lokale CSV

        try:

            file_exists = os.path.exists(CSV_FILE)

            datum = datetime.now().strftime("%Y-%m-%d %H:%M")

            with open(CSV_FILE, mode='a', newline='', encoding='utf-8') as f:

                writer = csv.writer(f, delimiter=';')

                if not file_exists:

                    writer.writerow(["ID", "Kunde", "Projekt", "Datum", "Link"])

                writer.writerow([folder_id, new_kunde, new_projekt, datum, project_url])

        except Exception as e:

            ui_log(f"[WARNUNG] CSV konnte nicht aktualisiert werden: {e}", ui)



        ui_set_progress(1.0, ui)

        ui_set_detail("Duplizierung abgeschlossen!", ui)

        ui_log(f"[DUPLIZIEREN] Neuer Link: {project_url}", ui)



        if on_success:

            root.after(0, lambda: on_success(project_url))



    except Exception as e:

        ui_log(f"[FEHLER] Duplizierung fehlgeschlagen: {e}", ui)

        ui_log(traceback.format_exc(), ui)

        log(traceback.format_exc())

        root.after(0, lambda err=e: messagebox.showerror("Fehler", f"Duplizierung fehlgeschlagen:\n{err}"))





def download_project_data_process(project_info, target_dir, aws_access, aws_secret, on_success=None, on_cancel=None, ui=None, cancel_event=None):

    """Laedt alle Punktwolkendaten eines Projekts aus S3 in einen lokalen Ordner."""

    try:

        source_s3_path = project_info.get("s3_path", "")

        if not source_s3_path:

            raise ValueError("Projekt hat keinen S3-Pfad.")

        if not target_dir:

            raise ValueError("Kein Zielordner ausgewaehlt.")

        folder_parts = [

            sanitize_folder_name(project_info.get("kunde", "")),

            sanitize_folder_name(project_info.get("projekt", "")),

            str(project_info.get("id", "")).strip()

        ]

        download_folder_name = "_".join(part for part in folder_parts if part) or "punktwolke"

        download_dir = os.path.join(target_dir, download_folder_name)

        os.makedirs(download_dir, exist_ok=True)



        ui_log("[DOWNLOAD] Starte Download der Punktwolkendaten...", ui)

        ui_set_progress(0.05, ui)

        ui_set_detail("Verbinde mit S3...", ui)



        s3_client = create_s3_client(aws_access, aws_secret)



        ui_set_detail("Sammle Projektdateien...", ui)

        object_entries = [

            entry for entry in collect_project_object_entries(s3_client, source_s3_path)

            if not entry["Key"].endswith("/")

        ]

        if not object_entries:

            raise ValueError("Keine Dateien im Projekt gefunden.")



        total_files = len(object_entries)

        total_bytes = sum(entry.get("Size", 0) for entry in object_entries)

        downloaded_bytes = 0

        ui_log(f"[DOWNLOAD] {total_files} Dateien gefunden", ui)



        active_local_path = ""



        def raise_if_cancelled():

            if cancel_event and cancel_event.is_set():

                raise DownloadCancelledError("Download wurde abgebrochen.")



        def update_download_progress(bytes_amount):

            nonlocal downloaded_bytes

            raise_if_cancelled()

            downloaded_bytes += bytes_amount

            if total_bytes > 0:

                ui_set_progress(0.1 + 0.85 * min(downloaded_bytes / total_bytes, 1.0), ui)



        for index, entry in enumerate(object_entries, start=1):

            raise_if_cancelled()

            object_key = entry["Key"]

            local_path = build_safe_download_path(download_dir, source_s3_path, object_key)

            if not local_path:

                continue

            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            active_local_path = local_path

            ui_set_detail(f"Lade Datei {index}/{total_files}: {os.path.basename(local_path)}", ui)

            s3_client.download_file(

                BUCKET_NAME,

                object_key,

                local_path,

                Callback=update_download_progress

            )

            if total_bytes <= 0:

                ui_set_progress(0.1 + 0.85 * (index / total_files), ui)

            active_local_path = ""



        ui_set_progress(1.0, ui)

        ui_set_detail("Download abgeschlossen!", ui)

        ui_log(f"[DOWNLOAD] Zielordner: {download_dir}", ui)



        if on_success:

            root.after(0, lambda: on_success(download_dir))

    except DownloadCancelledError:

        if active_local_path and os.path.exists(active_local_path):

            try:

                os.remove(active_local_path)

            except OSError:

                pass

        ui_log("[DOWNLOAD] Download abgebrochen.", ui)

        ui_set_detail("Download abgebrochen.", ui)

        if on_cancel:

            root.after(0, on_cancel)

    except Exception as e:

        ui_log(f"[FEHLER] Download fehlgeschlagen: {e}", ui)

        ui_set_detail(f"Fehler: {e}", ui)

        root.after(0, lambda err=e: messagebox.showerror("Fehler", f"Download fehlgeschlagen:\n{err}"))




def open_project_download_dialog(parent_window, project_info, aws_access, aws_secret, window_owner):

    """Oeffnet einen Dialog zum Herunterladen der Projektdateien."""

    existing_download_window = getattr(window_owner, "_download_window", None)

    if focus_existing_window(existing_download_window):

        return

    download_window = ctk.CTkToplevel(parent_window)

    window_owner._download_window = download_window

    download_window.title("Punktwolkendaten herunterladen")

    download_window.geometry("720x500")

    download_window.minsize(660, 460)

    download_window.transient(parent_window)

    download_window.lift()

    download_window.focus_force()

    download_window.grab_set()



    def close_download_window():

        if getattr(window_owner, "_download_window", None) is download_window:

            window_owner._download_window = None

        try:

            download_window.grab_release()

        except tk.TclError:

            pass

        download_window.destroy()



    download_window.protocol("WM_DELETE_WINDOW", close_download_window)



    header = ctk.CTkFrame(download_window, fg_color="transparent")

    header.pack(fill="x", padx=20, pady=(16, 8))

    ctk.CTkLabel(

        header,

        text="Punktwolkendaten herunterladen",

        font=ctk.CTkFont(size=18, weight="bold")

    ).pack(anchor="w")



    info_frame = ctk.CTkFrame(download_window, fg_color=COLOR_CARD, corner_radius=8)

    info_frame.pack(fill="x", padx=20, pady=(0, 12))

    ctk.CTkLabel(

        info_frame,

        text=f"{project_info.get('kunde', '')} - {project_info.get('projekt', '')}  (ID: {project_info.get('id', '')})",

        font=ctk.CTkFont(size=12)

    ).pack(anchor="w", padx=12, pady=(10, 4))

    ctk.CTkLabel(

        info_frame,

        text=f"S3-Pfad: {project_info.get('s3_path', '')}",

        font=ctk.CTkFont(size=11),

        text_color=COLOR_TEXT_DIM,

        wraplength=660,

        justify="left"

    ).pack(anchor="w", padx=12, pady=(0, 10))



    target_frame = ctk.CTkFrame(download_window, fg_color="transparent")

    target_frame.pack(fill="x", padx=20, pady=(0, 12))

    target_entry = ctk.CTkEntry(

        target_frame,

        font=ctk.CTkFont(family="Consolas", size=11),

        height=36,

        placeholder_text="Zielordner auswählen"

    )

    target_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))



    def choose_target_dir():

        selected_dir = filedialog.askdirectory(title="Zielordner fuer den Download waehlen")

        if selected_dir:

            target_entry.delete(0, tk.END)

            target_entry.insert(0, selected_dir)



    ctk.CTkButton(

        target_frame,

        text="Ordner wählen",

        fg_color=COLOR_ACCENT,

        hover_color=COLOR_ACCENT_HOVER,

        height=36,

        command=choose_target_dir

    ).pack(side="right")



    progress_frame = ctk.CTkFrame(download_window, fg_color="transparent")

    progress_frame.pack(fill="both", expand=True, padx=20, pady=(0, 8))

    download_progress_bar = ctk.CTkProgressBar(progress_frame, height=8)

    download_progress_bar.pack(fill="x", pady=(0, 4))

    download_progress_bar.set(0)

    download_detail_label = ctk.CTkLabel(

        progress_frame,

        text="Noch kein Download gestartet.",

        font=ctk.CTkFont(size=11),

        text_color=COLOR_TEXT_DIM

    )

    download_detail_label.pack(anchor="w")

    download_log_box = ctk.CTkTextbox(progress_frame, height=140, font=ctk.CTkFont(size=11), state="disabled")

    download_log_box.pack(fill="both", expand=True, pady=(6, 0))



    download_ui = {

        "progress_bar": download_progress_bar,

        "progress_detail": download_detail_label,

        "log": download_log_box

    }



    btn_row = ctk.CTkFrame(download_window, fg_color="transparent")

    btn_row.pack(fill="x", padx=20, pady=(8, 16))



    def start_download():

        target_dir = target_entry.get().strip()

        if not target_dir:

            messagebox.showwarning("Zielordner fehlt", "Bitte einen Zielordner auswählen.")

            return

        if not os.path.isdir(target_dir):

            messagebox.showerror("Fehler", "Der Zielordner existiert nicht.")

            return

        cancel_event = threading.Event()



        def request_cancel():

            if cancel_event.is_set():

                return

            cancel_event.set()

            ui_set_detail("Download wird abgebrochen...", download_ui)

            ui_log("[DOWNLOAD] Abbruch angefordert.", download_ui)

            btn_cancel.configure(state="disabled", text="Abbruch läuft...")



        def on_success(download_dir):

            btn_cancel.configure(state="normal", text="Schließen", command=close_download_window)

            messagebox.showinfo("Download abgeschlossen", f"Punktwolkendaten wurden heruntergeladen:\n{download_dir}")



        def on_cancel():

            btn_cancel.configure(state="normal", text="Schließen", command=close_download_window)

        btn_start.configure(state="disabled", text="Download läuft...")

        btn_cancel.configure(state="normal", text="Download abbrechen", command=request_cancel)

        download_window.protocol("WM_DELETE_WINDOW", request_cancel)



        thread = threading.Thread(

            target=download_project_data_process,

            args=(project_info, target_dir, aws_access, aws_secret),

            kwargs={

                "on_success": on_success,

                "on_cancel": on_cancel,

                "ui": download_ui,

                "cancel_event": cancel_event

            },

            daemon=True

        )

        thread.start()



        def check_thread():

            if thread.is_alive():

                root.after(100, check_thread)

                return

            if download_window.winfo_exists():

                download_window.protocol("WM_DELETE_WINDOW", close_download_window)

                btn_start.configure(state="normal", text="Herunterladen")

                btn_cancel.configure(state="normal", command=close_download_window)



        root.after(100, check_thread)



    btn_start = ctk.CTkButton(

        btn_row,

        text="Herunterladen",

        fg_color=COLOR_SUCCESS,

        hover_color=COLOR_SUCCESS_HOVER,

        font=ctk.CTkFont(size=13, weight="bold"),

        height=38,

        command=start_download

    )

    btn_start.pack(side="left", padx=(0, 8))

    btn_cancel = ctk.CTkButton(

        btn_row,

        text="Abbrechen",

        fg_color=COLOR_CARD,

        hover_color="#3a3a4c",

        font=ctk.CTkFont(size=13),

        height=38,

        command=close_download_window

    )

    btn_cancel.pack(side="right")





def show_projects_view():

    """Rendert die Projektübersicht als Hauptansicht im Hauptfenster."""

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

        text="Projektübersicht",

        font=ctk.CTkFont(size=22, weight="bold")

    ).pack(side="left", padx=20, pady=16)



    ctk.CTkLabel(

        header,

        text="Bestehende Projekte öffnen, löschen, austauschen oder duplizieren",

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

        text="Zurücksetzen",

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

            messagebox.showinfo("Info", "Bitte ein Projekt auswählen!")

            return None



        item = tree.item(selected[0])

        project_id = item["values"][0]
        project_link = item["values"][4] if len(item.get("values", [])) > 4 else ""

        if not project_id:

            messagebox.showinfo("Info", "Bitte ein gültiges Projekt auswählen!")

            return None



        project_info = projects_by_id.get(project_id)

        if not project_info:

            try:

                s3_client = create_s3_client(aws_access, aws_secret)

                index_data = load_projects_index(s3_client)

                project_info = find_project_in_index(index_data, project_id=project_id, project_link=project_link)

                if project_info:

                    projects_by_id[str(project_info.get("id", "")).strip()] = project_info

            except Exception as e:

                messagebox.showerror("Fehler", f"Projektdaten konnten nicht geladen werden:\n{e}")

                return None

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

            "Löschen bestaetigen",

            f"Projekt '{projekt_name}' wirklich löschen?\n\nDies loescht alle Dateien aus dem S3 Storage!"

        )

        if not result:

            return



        try:

            s3_client = create_s3_client(aws_access, aws_secret)

            delete_result = delete_project_transaction(s3_client, project_info)



            if delete_result["success"]:

                messagebox.showinfo("Erfolg", "Projekt wurde gelöscht und der Link deaktiviert!")

                load_projects()

            elif delete_result.get("partial"):

                messagebox.showwarning("Teilweise gelöscht", delete_result["message"])

                load_projects()

            else:

                messagebox.showerror("Fehler", delete_result["message"])

        except Exception as e:

            messagebox.showerror("Fehler", f"Fehler beim Löschen:\n{e}")



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

            text="Nur die Punktwolkendaten werden ersetzt. Projektname, Projekt-ID und Link bleiben unverändert.",

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

            placeholder_text="Neue LAS/LAZ-Datei oder Potree-Ordner für dieses Projekt auswählen"

        )

        replacement_entry.pack(fill="x", padx=16, pady=(16, 10))



        def set_replacement_source(source_path):

            replacement_entry.delete(0, tk.END)

            replacement_entry.insert(0, source_path)

            source_type, normalized_source = resolve_replacement_source(source_path)

            source_name = os.path.basename(normalized_source.rstrip("\\/")) if normalized_source else source_path

            source_caption = "Potree-Ordner erkannt" if source_type == "potree_dir" else "Datei erkannt"

            drop_label_replace.configure(

                text=f"{source_caption}\n\n{source_name}\n\nAustausch unten manuell per Button starten"

            )

            drop_frame_replace.configure(border_color=COLOR_SUCCESS)



        def select_replacement_file():

            file_path = filedialog.askopenfilename(

                title="Neue LAS/LAZ-Datei für den Projektaustausch wählen",

                filetypes=[("LAS/LAZ", "*.laz *.las"), ("Alle Dateien", "*.*")]

            )

            if file_path:

                set_replacement_source(file_path)



        def select_replacement_folder():

            folder_path = filedialog.askdirectory(

                title="Konvertierten Potree-Ordner für den Projektaustausch wählen"

            )

            if folder_path:

                set_replacement_source(folder_path)



        source_button_row = ctk.CTkFrame(upload_card, fg_color="transparent")

        source_button_row.pack(fill="x", padx=16, pady=(0, 10))



        ctk.CTkButton(

            source_button_row,

            text="LAS/LAZ-Datei wählen",

            command=select_replacement_file,

            fg_color=COLOR_ACCENT,

            hover_color=COLOR_ACCENT_HOVER,

            height=34

        ).pack(side="left", padx=(0, 8))



        ctk.CTkButton(

            source_button_row,

            text="Potree-Ordner wählen",

            command=select_replacement_folder,

            fg_color=COLOR_ACCENT,

            hover_color=COLOR_ACCENT_HOVER,

            height=34

        ).pack(side="left")



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

            text="Datei oder Potree-Ordner hier hineinziehen\n\n(.las, .laz oder konvertierter Potree-Ordner)",

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

            source_path = extract_dropped_file(event.data)

            if os.path.isfile(source_path) or os.path.isdir(source_path):

                set_replacement_source(source_path)

                valid, message = validate_replacement_source(source_path)

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

                messagebox.showwarning("Fehler", "Bitte eine LAS- oder LAZ-Datei auswählen!")

                return



            valid, message = validate_replacement_source(replacement_file)

            if not valid:

                messagebox.showerror("Fehler", message)

                return



            if not messagebox.askyesno(

                "Austausch bestaetigen",

                f"Die Punktwolkendaten von '{current_project.get('projekt', '')}' werden ersetzt.\n\n"

                "Projektname, Projekt-ID und Link bleiben unverändert.\n\n"

                "Moechten Sie fortfahren?"

            ):

                return



            btn_replace.configure(state="disabled", text="Austausch läuft...")

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

            text="Schließen",

            fg_color=COLOR_ACCENT,

            hover_color=COLOR_ACCENT_HOVER,

            height=38,

            command=close_replace_window

        )

        btn_cancel.pack(side="right")



    ctk.CTkButton(

        btn_frame,

        text="Im Browser öffnen",

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

        text="Löschen",

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



    def open_duplicate_dialog_main():

        existing_dup_window = getattr(projects_page, "_duplicate_window", None)

        if focus_existing_window(existing_dup_window):

            return



        project_info = get_selected_project()

        if not project_info:

            return



        dup_window = ctk.CTkToplevel(root)

        projects_page._duplicate_window = dup_window

        dup_window.title("Projekt duplizieren")

        dup_window.geometry("720x620")

        dup_window.minsize(660, 560)

        dup_window.transient(root)

        dup_window.lift()

        dup_window.focus_force()

        dup_window.grab_set()



        def close_dup_window():

            if getattr(projects_page, "_duplicate_window", None) is dup_window:

                projects_page._duplicate_window = None

            try:

                dup_window.grab_release()

            except tk.TclError:

                pass

            dup_window.destroy()



        dup_window.protocol("WM_DELETE_WINDOW", close_dup_window)



        header = ctk.CTkFrame(dup_window, fg_color="transparent")

        header.pack(fill="x", padx=20, pady=(16, 8))

        ctk.CTkLabel(

            header, text="Projekt duplizieren",

            font=ctk.CTkFont(size=18, weight="bold")

        ).pack(anchor="w")



        info_frame = ctk.CTkFrame(dup_window, fg_color=COLOR_CARD, corner_radius=8)

        info_frame.pack(fill="x", padx=20, pady=(0, 12))

        ctk.CTkLabel(

            info_frame, text="Quellprojekt:",

            font=ctk.CTkFont(size=12, weight="bold"), text_color=COLOR_TEXT_DIM

        ).pack(anchor="w", padx=12, pady=(8, 2))

        ctk.CTkLabel(

            info_frame,

            text=f"{project_info.get('kunde', '')} - {project_info.get('projekt', '')}  (ID: {project_info.get('id', '')})",

            font=ctk.CTkFont(size=12)

        ).pack(anchor="w", padx=12, pady=(0, 8))



        form_frame = ctk.CTkFrame(dup_window, fg_color="transparent")

        form_frame.pack(fill="x", padx=20, pady=(0, 8))



        ctk.CTkLabel(

            form_frame, text="Neuer Kundenname:",

            font=ctk.CTkFont(size=13, weight="bold")

        ).pack(anchor="w", pady=(0, 4))

        kunde_entry = ctk.CTkEntry(form_frame, height=36, font=ctk.CTkFont(size=13))

        kunde_entry.pack(fill="x", pady=(0, 12))

        kunde_entry.insert(0, project_info.get("kunde", ""))



        ctk.CTkLabel(

            form_frame, text="Neuer Projektname:",

            font=ctk.CTkFont(size=13, weight="bold")

        ).pack(anchor="w", pady=(0, 4))

        projekt_entry = ctk.CTkEntry(form_frame, height=36, font=ctk.CTkFont(size=13))

        projekt_entry.pack(fill="x", pady=(0, 12))

        projekt_entry.insert(0, project_info.get("projekt", "") + " (Kopie)")



        progress_frame = ctk.CTkFrame(dup_window, fg_color="transparent")

        progress_frame.pack(fill="x", padx=20, pady=(0, 8))



        dup_progress_bar = ctk.CTkProgressBar(progress_frame, height=8)

        dup_progress_bar.pack(fill="x", pady=(0, 4))

        dup_progress_bar.set(0)



        dup_detail_label = ctk.CTkLabel(

            progress_frame, text="", font=ctk.CTkFont(size=11), text_color=COLOR_TEXT_DIM

        )

        dup_detail_label.pack(anchor="w")



        dup_log_box = ctk.CTkTextbox(progress_frame, height=90, font=ctk.CTkFont(size=11), state="disabled")

        dup_log_box.pack(fill="both", expand=True, pady=(4, 0))



        dup_ui = {

            "progress_bar": dup_progress_bar,

            "progress_detail": dup_detail_label,

            "log": dup_log_box

        }



        btn_row = ctk.CTkFrame(dup_window, fg_color="transparent")

        btn_row.pack(fill="x", padx=20, pady=(8, 16))



        def start_duplicate():

            new_kunde = kunde_entry.get().strip()

            new_projekt = projekt_entry.get().strip()



            if not new_kunde:

                messagebox.showwarning("Eingabe fehlt", "Bitte einen Kundennamen eingeben.")

                return

            if not new_projekt:

                messagebox.showwarning("Eingabe fehlt", "Bitte einen Projektnamen eingeben.")

                return



            btn_start.configure(state="disabled")

            btn_cancel.configure(state="disabled")



            def on_success(new_url):

                btn_cancel.configure(state="normal", text="Schließen")

                load_projects()

                result = messagebox.askyesno(

                    "Projekt dupliziert",

                    f"Projekt wurde erfolgreich dupliziert!\n\n"

                    f"Neuer Link:\n{new_url}\n\n"

                    "Link in Zwischenablage kopieren?"

                )

                if result:

                    dup_window.clipboard_clear()

                    dup_window.clipboard_append(new_url)



            thread = threading.Thread(

                target=duplicate_project_process,

                args=(project_info, new_kunde, new_projekt, aws_access, aws_secret),

                kwargs={"on_success": on_success, "ui": dup_ui},

                daemon=True

            )

            thread.start()



        btn_start = ctk.CTkButton(

            btn_row, text="Duplizieren",

            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,

            font=ctk.CTkFont(size=13, weight="bold"),

            height=38, command=start_duplicate

        )

        btn_start.pack(side="left", padx=(0, 8))



        btn_cancel = ctk.CTkButton(

            btn_row, text="Abbrechen",

            fg_color=COLOR_CARD, hover_color="#3a3a4c",

            font=ctk.CTkFont(size=13),

            height=38, command=close_dup_window

        )

        btn_cancel.pack(side="right")



    def open_download_dialog_main():

        project_info = get_selected_project()

        if not project_info:

            return

        open_project_download_dialog(root, project_info, aws_access, aws_secret, projects_page)



    ctk.CTkButton(

        btn_frame,

        text="Duplizieren",

        fg_color=COLOR_ACCENT,

        hover_color=COLOR_ACCENT_HOVER,

        font=ctk.CTkFont(size=13),

        height=36,

        command=open_duplicate_dialog_main

    ).pack(side="left", padx=(0, 8))



    ctk.CTkButton(

        btn_frame,

        text="Herunterladen",

        fg_color=COLOR_SUCCESS,

        hover_color=COLOR_SUCCESS_HOVER,

        font=ctk.CTkFont(size=13),

        height=36,

        command=open_download_dialog_main

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

                projekt = proj.get("projekt", "")



                if selected_customer != "Alle Kunden" and kunde != selected_customer:

                    continue

                if search_term and search_lower not in projekt.lower() and search_lower not in kunde.lower():

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






def show_local_conversion_view():

    """Rendert die lokale LAS/LAZ -> Potree Konvertierung im Hauptfenster."""

    clear_frame(local_conversion_page)
    show_main_view("convert")

    header = ctk.CTkFrame(local_conversion_page, fg_color=COLOR_CARD, corner_radius=0)
    header.pack(fill="x", pady=(0, 16))

    ctk.CTkLabel(
        header,
        text="Lokale Potree-Konvertierung",
        font=ctk.CTkFont(size=22, weight="bold")
    ).pack(side="left", padx=20, pady=16)

    ctk.CTkLabel(
        header,
        text="LAS/LAZ lokal in ein Potree-Projekt konvertieren und auf dem Rechner speichern",
        font=ctk.CTkFont(size=12),
        text_color=COLOR_TEXT_DIM
    ).pack(side="left", padx=(0, 20), pady=(18, 12))

    convert_scroll = ctk.CTkScrollableFrame(local_conversion_page, fg_color="transparent")
    convert_scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    source_card = ctk.CTkFrame(convert_scroll, corner_radius=12)
    source_card.pack(fill="x", pady=(0, 12))

    ctk.CTkLabel(
        source_card,
        text="Quelldatei",
        font=ctk.CTkFont(size=15, weight="bold")
    ).pack(anchor="w", padx=16, pady=(14, 8))

    ctk.CTkLabel(
        source_card,
        text="Unterstützt klassische .las und .laz Dateien. Die Daten bleiben lokal und werden nicht hochgeladen.",
        font=ctk.CTkFont(size=11),
        text_color=COLOR_TEXT_DIM,
        wraplength=760,
        justify="left"
    ).pack(anchor="w", padx=16, pady=(0, 10))

    source_row = ctk.CTkFrame(source_card, fg_color="transparent")
    source_row.pack(fill="x", padx=16, pady=(0, 8))

    source_entry = ctk.CTkEntry(
        source_row,
        placeholder_text="Pfad zur LAS/LAZ Datei",
        font=ctk.CTkFont(family="Consolas", size=11),
        height=34
    )
    source_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

    output_entry_ref = {"widget": None}
    result_entry_ref = {"widget": None}
    ui_context = {}

    def build_local_output_dir(source_path, output_base_dir):
        source_name = os.path.splitext(os.path.basename(source_path))[0]
        folder_name = sanitize_folder_name(source_name) or "potree_export"
        return os.path.join(output_base_dir, f"{folder_name}_potree")

    def update_output_preview():
        result_widget = result_entry_ref["widget"]
        output_widget = output_entry_ref["widget"]
        if not widget_exists(result_widget):
            return

        source_path = source_entry.get().strip()
        output_base_dir = output_widget.get().strip() if widget_exists(output_widget) else ""

        result_widget.configure(state="normal")
        result_widget.delete(0, tk.END)
        if source_path and output_base_dir:
            result_widget.insert(0, build_local_output_dir(source_path, output_base_dir))
        result_widget.configure(state="readonly")

    def set_source_file(file_path):
        source_entry.delete(0, tk.END)
        source_entry.insert(0, file_path)
        set_drop_zone_text(drop_label, file_path)
        update_output_preview()

    def browse_source_file():
        file_path = filedialog.askopenfilename(
            title="LAS/LAZ Datei auswählen",
            filetypes=[("LAS/LAZ", "*.laz *.las"), ("Alle Dateien", "*.*")]
        )
        if file_path:
            set_source_file(file_path)
            ui_log(f"[DATEI] Ausgewählt: {file_path}", ui_context)

    ctk.CTkButton(
        source_row,
        text="Datei auswählen",
        width=160,
        font=ctk.CTkFont(size=12),
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        command=browse_source_file
    ).pack(side="right")

    drop_frame = ctk.CTkFrame(
        source_card,
        fg_color="#1e1e2e",
        corner_radius=8,
        border_width=2,
        border_color=COLOR_ACCENT
    )
    drop_frame.pack(fill="x", padx=16, pady=(0, 14))
    drop_frame.configure(height=150)
    drop_frame.pack_propagate(False)

    drop_label = tk.Label(
        drop_frame,
        text="Datei hier hineinziehen\n\n(nur .las oder .laz)",
        bg="#1e1e2e",
        fg="#64748b",
        font=("Segoe UI", 13, "bold"),
        pady=20,
        justify="center"
    )
    drop_label.pack(fill="both", expand=True)
    drop_label.drop_target_register(DND_FILES)
    drop_frame.drop_target_register(DND_FILES)

    def handle_local_drop(event):
        file_path = extract_dropped_file(event.data)
        if os.path.isfile(file_path):
            set_source_file(file_path)
            ui_log(f"[DRAG & DROP] Datei: {file_path}", ui_context)

    drop_label.dnd_bind("<<Drop>>", handle_local_drop)
    drop_frame.dnd_bind("<<Drop>>", handle_local_drop)

    target_card = ctk.CTkFrame(convert_scroll, corner_radius=12)
    target_card.pack(fill="x", pady=(0, 12))

    ctk.CTkLabel(
        target_card,
        text="Lokaler Speicherort",
        font=ctk.CTkFont(size=15, weight="bold")
    ).pack(anchor="w", padx=16, pady=(14, 8))

    ctk.CTkLabel(
        target_card,
        text="Im Zielordner wird automatisch ein neuer Unterordner für das Potree-Projekt angelegt.",
        font=ctk.CTkFont(size=11),
        text_color=COLOR_TEXT_DIM,
        wraplength=760,
        justify="left"
    ).pack(anchor="w", padx=16, pady=(0, 8))

    target_row = ctk.CTkFrame(target_card, fg_color="transparent")
    target_row.pack(fill="x", padx=16, pady=(0, 14))

    output_entry = ctk.CTkEntry(
        target_row,
        placeholder_text="Lokalen Zielordner wählen",
        font=ctk.CTkFont(family="Consolas", size=11),
        height=34
    )
    output_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
    output_entry_ref["widget"] = output_entry

    def browse_output_dir():
        folder = filedialog.askdirectory(title="Lokalen Zielordner wählen")
        if folder:
            output_entry.delete(0, tk.END)
            output_entry.insert(0, folder)
            update_output_preview()

    ctk.CTkButton(
        target_row,
        text="Zielordner wählen",
        width=160,
        font=ctk.CTkFont(size=12),
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        command=browse_output_dir
    ).pack(side="right")

    result_card = ctk.CTkFrame(convert_scroll, corner_radius=12)
    result_card.pack(fill="x", pady=(0, 12))

    ctk.CTkLabel(
        result_card,
        text="Ausgabe",
        font=ctk.CTkFont(size=15, weight="bold")
    ).pack(anchor="w", padx=16, pady=(14, 8))

    ctk.CTkLabel(
        result_card,
        text="Voraussichtlicher Ausgabeordner:",
        font=ctk.CTkFont(size=11),
        text_color=COLOR_TEXT_DIM
    ).pack(anchor="w", padx=16, pady=(0, 4))

    result_entry = ctk.CTkEntry(
        result_card,
        font=ctk.CTkFont(family="Consolas", size=11),
        state="readonly",
        height=34
    )
    result_entry.pack(fill="x", padx=16, pady=(0, 8))
    result_entry_ref["widget"] = result_entry

    def open_result_folder():
        result_path = result_entry.get().strip()
        if not result_path:
            messagebox.showwarning("Fehler", "Es ist noch kein Ausgabeordner vorhanden.")
            return
        if not os.path.isdir(result_path):
            messagebox.showwarning("Fehler", "Der Ausgabeordner existiert noch nicht.")
            return
        os.startfile(result_path)

    ctk.CTkButton(
        result_card,
        text="Ausgabeordner öffnen",
        width=190,
        font=ctk.CTkFont(size=12),
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        command=open_result_folder
    ).pack(anchor="w", padx=16, pady=(0, 14))

    progress_card = ctk.CTkFrame(convert_scroll, corner_radius=12)
    progress_card.pack(fill="x", pady=(0, 12))

    progress_step_label = ctk.CTkLabel(
        progress_card,
        text="",
        font=ctk.CTkFont(size=12, weight="bold")
    )
    progress_step_label.pack(anchor="w", padx=16, pady=(14, 0))

    progress_bar_widget = ctk.CTkProgressBar(progress_card, height=10, corner_radius=5)
    progress_bar_widget.pack(fill="x", padx=16, pady=(8, 4))
    progress_bar_widget.set(0)

    progress_detail_label = ctk.CTkLabel(
        progress_card,
        text="",
        font=ctk.CTkFont(size=11),
        text_color=COLOR_TEXT_DIM
    )
    progress_detail_label.pack(anchor="w", padx=16, pady=(0, 14))

    ctk.CTkLabel(
        convert_scroll,
        text="Protokoll",
        font=ctk.CTkFont(size=11),
        text_color=COLOR_TEXT_DIM
    ).pack(anchor="w", padx=4, pady=(0, 4))

    log_box = ctk.CTkTextbox(
        convert_scroll,
        height=200,
        font=ctk.CTkFont(family="Consolas", size=11),
        fg_color=COLOR_CARD,
        corner_radius=10,
        state="disabled"
    )
    log_box.pack(fill="both", expand=True, pady=(0, 16))

    ui_context.update({
        "step": progress_step_label,
        "progress_bar": progress_bar_widget,
        "progress_detail": progress_detail_label,
        "log": log_box,
    })

    def run_local_conversion(source_file, output_base_dir, output_dir, converter_path):
        try:
            ui_log("[KONVERTIERUNG] Starte lokale Potree-Konvertierung", ui_context)
            ui_log(f"[QUELLE] {source_file}", ui_context)
            ui_log(f"[ZIEL] {output_dir}", ui_context)

            ui_set_step("Bereite Zielordner vor...", 1, ui_context)
            ui_set_detail("Der lokale Potree-Projektordner wird vorbereitet", ui_context)
            ui_set_progress(0.05, ui_context)

            os.makedirs(output_base_dir, exist_ok=True)

            if os.path.isdir(output_dir):
                ui_log("[CLEANUP] Vorhandenen Ausgabeordner entfernen", ui_context)
                shutil.rmtree(output_dir)

            ui_set_step("Konvertiere mit Potree...", 2, ui_context)
            ui_set_detail("Die Punktwolke wird lokal in das Potree-Format umgewandelt", ui_context)
            run_potree_conversion(source_file, converter_path, output_dir, ui=ui_context)

            ui_set_step("Prüfe Ergebnis...", 3, ui_context)
            ui_set_detail("Die konvertierten Daten werden lokal bereitgestellt", ui_context)
            if not os.path.isdir(output_dir):
                raise RuntimeError("Der Ausgabeordner wurde nicht erzeugt.")

            ui_log("[ERFOLG] Potree-Projekt lokal gespeichert", ui_context)
            ui_set_step("Fertig", 5, ui_context)
            ui_set_detail(output_dir, ui_context)
            ui_set_progress(1, ui_context)

            root.after(
                0,
                lambda: messagebox.showinfo(
                    "Erfolg",
                    f"Die Datei wurde erfolgreich in das Potree-Format konvertiert.\n\nAusgabeordner:\n{output_dir}"
                )
            )
        except Exception as e:
            ui_log(f"[FEHLER] {e}", ui_context)
            import traceback
            ui_log(traceback.format_exc(), ui_context)
            root.after(
                0,
                lambda: messagebox.showerror(
                    "Konvertierung fehlgeschlagen",
                    f"Die lokale Potree-Konvertierung konnte nicht abgeschlossen werden.\n\n{e}"
                )
            )
        finally:
            root.after(
                0,
                lambda: start_convert_button.configure(
                    state="normal",
                    text="Lokale Konvertierung starten"
                )
            )

    def start_local_conversion():
        source_file = source_entry.get().strip()
        output_base_dir = output_entry.get().strip()

        if not source_file or not os.path.isfile(source_file):
            messagebox.showwarning("Fehler", "Bitte eine gültige LAS/LAZ Datei auswählen.")
            return

        file_ext = os.path.splitext(source_file)[1].lower()
        if file_ext not in [".las", ".laz"]:
            messagebox.showwarning("Fehler", "Es können nur .las oder .laz Dateien lokal konvertiert werden.")
            return

        if not output_base_dir:
            messagebox.showwarning("Fehler", "Bitte einen lokalen Zielordner auswählen.")
            return

        configured_converter_path = load_config().get("converter_path", "")
        converter_path = resolve_converter_path(configured_converter_path)
        if not converter_path or not os.path.exists(converter_path):
            messagebox.showerror(
                "Fehler",
                "Kein Potree Converter verfügbar. Bitte in den Einstellungen den integrierten Converter prüfen oder einen Override-Pfad hinterlegen."
            )
            return

        output_base_dir = os.path.abspath(output_base_dir)
        output_dir = os.path.abspath(build_local_output_dir(source_file, output_base_dir))

        try:
            if os.path.commonpath([output_base_dir, output_dir]) != output_base_dir:
                raise ValueError("Ungültiger Zielordner")
        except ValueError:
            messagebox.showerror("Fehler", "Der Zielordner für die lokale Konvertierung ist ungültig.")
            return

        if os.path.isdir(output_dir):
            overwrite = messagebox.askyesno(
                "Ausgabeordner existiert bereits",
                f"Der Ausgabeordner existiert bereits und wird überschrieben:\n\n{output_dir}\n\nMöchtest du fortfahren?"
            )
            if not overwrite:
                return

        update_output_preview()
        ui_reset_progress(ui_context)
        start_convert_button.configure(state="disabled", text="Konvertierung läuft...")

        thread = threading.Thread(
            target=run_local_conversion,
            args=(source_file, output_base_dir, output_dir, converter_path),
            daemon=True
        )
        thread.start()

    start_convert_button = ctk.CTkButton(
        convert_scroll,
        text="Lokale Konvertierung starten",
        font=ctk.CTkFont(size=15, weight="bold"),
        fg_color=COLOR_SUCCESS,
        hover_color=COLOR_SUCCESS_HOVER,
        height=48,
        width=320,
        corner_radius=12,
        command=start_local_conversion
    )
    start_convert_button.pack(anchor="w", pady=(0, 12))

    config = load_config()
    if config.get("output_base_dir"):
        output_entry.insert(0, config["output_base_dir"])

    source_entry.bind("<KeyRelease>", lambda _event: update_output_preview())
    output_entry.bind("<KeyRelease>", lambda _event: update_output_preview())



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

        placeholder_text="Geheimer Schlüssel",

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

        text="Die App bringt PotreeConverter.exe und laszip.dll mit. Für klassische LAS/LAZ Uploads ist keine externe Installation nötig.",

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

            title="PotreeConverter.exe wählen",

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

        folder = filedialog.askdirectory(title="Output-Ordner wählen")

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

local_conversion_page = ctk.CTkFrame(content_area, fg_color=COLOR_SURFACE, corner_radius=0)

settings_page = ctk.CTkFrame(content_area, fg_color=COLOR_SURFACE, corner_radius=0)



app_views.update({

    "upload": main_scroll,

    "projects": projects_page,

    "convert": local_conversion_page,

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



nav_buttons["convert"] = ctk.CTkButton(

    sidebar,

    text="Lokale Konvertierung",

    anchor="w",

    height=46,

    font=ctk.CTkFont(size=13, weight="bold"),

    fg_color=COLOR_ACCENT,

    hover_color=COLOR_ACCENT_HOVER,

    command=show_local_conversion_view

)

nav_buttons["convert"].pack(fill="x", padx=12, pady=(0, 8))



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

#  HEADER MIT MENUe

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

    card_data, text="LAZ / LAS Datei wählen...",

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

drop_frame.configure(height=180)

drop_frame.pack_propagate(False)



lbl_drop = tk.Label(

    drop_frame, text="Datei hier hineinziehen (Drag & Drop)",

    bg="#1e1e2e", fg="#64748b",

    font=("Segoe UI", 13, "bold"),

    pady=28,

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



# Pruefe ob erste Ausfuehrung

config = load_config()

if config.get("first_run", True):

    log("[INFO] Erste Ausfuehrung erkannt - oeffne Einstellungen...")

    root.after(500, lambda: show_settings_view(first_run=True))

else:

    log("[OK] Konfiguration geladen")

    log("Bereit. Waehle eine .laz oder .las Datei aus.")

    log("Nutze die Navigation links für Upload, lokale Konvertierung, Projekte und Einstellungen.")



root.after(1000, check_for_available_update)



try:

    root.mainloop()

except Exception as e:

    import traceback

    traceback.print_exc()

    input("Druecke Enter zum Beenden...")






