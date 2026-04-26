# Pointcloud Uploader Memory

Stand: 2026-04-26

## Git

- Repo: `Preloi/Pointcloud-Uploader`
- Aktueller Branch: `develop`
- Aktueller Release: `1.6.1`

## Systemkontext

- Der `Dronautix Pointcloud Uploader` laedt Punktwolken nach S3 hoch und erzeugt die Viewer-Links.
- Der `Dronautix Pointcloud Viewer` stellt genau diese vom Uploader hochgeladenen Projekte online dar.
- Relevante Kopplungspunkte zwischen Uploader und Viewer sind:
  - `projects_index.json`
  - `deleted_projects.json`
  - `pointclouds/`
  - die vom Uploader erzeugten Viewer-Links

## Aktueller Stand

- COPC-Upload ist im Uploader integriert.
- Klassischer Potree-Converter-Workflow bleibt parallel erhalten.
- Viewer kann Potree-Projekte und COPC-Dateien laden.
- Der Potree Converter ist in die App integriert (`bundled_tools/PotreeConverter`).
- Klassische LAS/LAZ-Konvertierungsdaten werden nach erfolgreichem Upload lokal wieder geloescht.
- Die Originaldatei bleibt lokal erhalten.
- Der erzeugte Potree-Output ist nur temporaer und wird nach erfolgreichem Upload per Cleanup entfernt.

## Update-Logik

- Die App prueft beim Start `latest-release.json` direkt aus dem GitHub-Repo `Preloi/Dronautix-Pointcloud-Uploader`.
- Wenn eine neuere Version gefunden wird, fragt die App per Dialog nach Bestaetigung.
- Nach Bestaetigung laedt die App den Installer von GitHub herunter und startet das Setup.
- Der Installer wird lokal unter `%APPDATA%\DronautixUploader\updates` zwischengespeichert.
- Die eigentliche Deinstallation der alten Version uebernimmt weiterhin das Inno-Setup beim Start des neuen Installers.
- `latest-release.json` enthaelt fuer den Updater:
  - `version`
  - `installer_name`
  - `repo_owner`
  - `repo_name`
  - `release_tag`
  - `installer_url`

## Release 1.6.1

- Aktueller Release des Uploaders ist `1.6.1`.
- Neue Uploads erzeugen anonyme Kurz-Links nur noch mit der technischen Projekt-ID.
- UI-Versionsanzeige, Setup und Dateiversion ziehen ihre Versionsnummer zentral aus `app_version.py`.
- Fuer Auto-Updates muss das Setup als GitHub-Release-Asset unter dem passenden Tag liegen:
  - Tag: `v1.6.1`
  - Asset: `Dronautix_Pointcloud_Uploader_Setup_1.6.1.exe`

## Wichtige Uploader-Details

- COPC-Dateien (`.copc.laz`) werden direkt nach S3 hochgeladen.
- Klassische `.las/.laz` laufen weiter ueber den Potree Converter.
- Der Loeschablauf ist robust aufgebaut:
  - S3-Objekte werden zuerst geloescht
  - `deleted_projects.json` wird dedupliziert aktualisiert
  - `projects_index.json` wird separat nachgezogen
  - Teilfehler werden explizit gemeldet
- Neue Viewer-Links nutzen fuer neue Uploads Kurz-IDs statt sprechender Projektpfade.

## Wichtige Viewer-Details

- `server_viewer/index.html` in diesem Repo ist nur die lokale Test-/Deploy-Kopie.
- Die echte Online-Viewer-Instanz wird in der separaten Viewer-Repo gepflegt.
- `deleted_projects.json` wird im Viewer von derselben Origin geladen.
- Die Nicht-verfuegbar-Seite zeigt das Dronautix-Logo mit Link auf `https://dronautix.at`.
- Nach 30 Tagen wird nicht mehr die explizite Loeschmeldung gezeigt; bei fehlenden Dateien erscheint eine allgemeine Nicht-verfuegbar-Seite.

## UI-Stand

- Drag-and-Drop-Feld zeigt nach Dateiauswahl oder Drop den kompletten Dateipfad an.
- Drag-and-Drop-Feld im Hauptfenster wurde etwas kompakter gemacht.
- Die Kopfzeile `Upload und Verwaltung im selben Fenster` wurde aus dem Hauptfenster entfernt.
- Projektuebersicht-Ueberschrift lautet jetzt:
  - `Bestehende Projekte oeffnen, loeschen, austauschen oder duplizieren`
- Duplizieren-Dialoge wurden vergroessert, damit die Buttons sicher sichtbar sind.
- Die Farbe aller `Duplizieren`-Buttons wurde an die Standard-Aktionsfarbe der App angepasst.

## Aenderungen in frueheren Releases

### Version 1.5

- UI-Anpassungen fuer Drag-and-Drop, Projektuebersicht und Duplizieren-Fenster.
- Projekt-Duplizieren-Funktion in beiden UI-Varianten.
- Projektuebersicht-Suche ist case-insensitive und durchsucht Kunden- und Projektnamen.

### Version 1.4

- UI-Refactoring mit `show_main_view()`, `app_views` und `nav_buttons`.
- Robuste Fenster-/Widget-Behandlung ueber `focus_existing_window()` und `widget_exists()`.
- Verbesserte Ordnernamen-Sanitierung mit `unicodedata.normalize()`.
- Setup-Verbesserungen in Inno Setup:
  - `KillRunningApp()`
  - Wartezeit nach Prozess-Kill
  - Cleanup alter `_MEI*`-Temp-Ordner

## Release-Dateien

- `dist/Dronautix_Pointcloud_Uploader.exe`
- `Output/Dronautix_Pointcloud_Uploader_Setup_1.6.1.exe`
- `Output/latest-release.json`

## Git-relevante Dateien fuer Releases

- `app_version.py`
- `version_info.txt`
- `installer_version.iss`
- `latest-release.json`
- `build_exe.py`
- `Dronautix_Pointcloud_Uploader.iss`
- `bundled_tools/PotreeConverter/`
- `.gitignore`

## Offene Hinweise

- Fuer echte Live-Viewer-Aenderungen ist die `index.html` der separaten Viewer-Repo massgeblich, nicht `server_viewer/index.html`.
- Falls CloudFront noch alte Assets liefert, gezielt invalidieren.
- Nach dem Hotfix `1.6.1` wieder auf das uebliche `0.1`-Versionsschema zurueckgehen, ausser bewusst erneut ein Patch-Release gewuenscht ist.
- Das verbesserte Update-/Setup-Verhalten greift zuverlaessig erst ab installierter Version `1.2`.
