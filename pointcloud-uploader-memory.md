# Pointcloud Uploader Memory

Stand: 2026-03-22

## Git

- Repo: `Preloi/Pointcloud-Uploader`
- Aktueller Branch: `develop`
- Ziel-Release: `1.3`

## Aktueller Stand

- COPC-Upload ist im Uploader integriert.
- Klassischer Potree-Converter-Workflow bleibt parallel erhalten.
- Viewer 1.8.2 kann Potree-Projekte und COPC-Dateien laden.
- Live-`index.html` wurde ins Bucket `potreedronautix` hochgeladen.
- Der Potree Converter ist in die App integriert (`bundled_tools/PotreeConverter`).
- Die App prueft beim Start `Z:\03 Apps\Pointcloud uploader\latest-release.json` auf neuere Versionen.
- Wenn eine neuere Version gefunden wird, fragt die App per Ja/Nein-Dialog, ob das Update jetzt installiert werden soll.
- EXE und Windows-Setup fuer Version `1.3` sind der aktuelle Release-Stand.
- Kuenftige Versionen werden in `0.1`-Schritten erhoeht (`1.0`, `1.1`, `1.2`, `1.3`, ...).
- Die Versionsnummer steht unten in der Statusleiste der App.
- Setup, App-Version und `latest-release.json` werden aus derselben zentralen Versionsquelle abgeleitet.
- Vor jedem Build werden die lokalen Build-Artefakte in `build/`, `dist/` und `Output/` geloescht, damit immer nur der aktuelle Build-Stand vorliegt.
- Nach dem Bau des Setups immer fragen, ob die neuen Release-Dateien auf den Share geladen werden sollen.
- Klassische LAS/LAZ-Konvertierungsdaten werden nach erfolgreichem Upload lokal wieder geloescht.

## Wichtige Viewer-Details

- `server_viewer/index.html` ist die lokale Deploy-Kopie fuer den Server-Viewer.
- `deleted_projects.json` wird im Viewer jetzt von derselben Origin geladen, nicht mehr direkt von S3.
- Auf der "Projekt nicht verfuegbar"-Seite wurde der Button entfernt.
- Stattdessen wird das Dronautix-Logo angezeigt und verlinkt auf `https://dronautix.at`.
- Nach 30 Tagen wird nicht mehr die explizite Loeschmeldung gezeigt, sondern bei fehlenden Dateien eine allgemeine Nicht-verfuegbar-Seite.

## Wichtige Uploader-Details

- COPC-Dateien (`.copc.laz`) werden direkt nach S3 hochgeladen.
- Klassische `.las/.laz` laufen weiter ueber den Potree Converter.
- Die Originaldatei bleibt lokal erhalten.
- Der erzeugte Potree-Output ist nur temporaer und wird nach erfolgreichem Upload per Cleanup entfernt.
- Der Loeschablauf wurde robuster gemacht:
  - S3-Objekte werden zuerst geloescht
  - `deleted_projects.json` wird dedupliziert aktualisiert
  - `projects_index.json` wird separat nachgezogen
  - Teilfehler werden explizit gemeldet
- Die Versionsanzeige in der App steht auf `1.3`.

## Release-Dateien

- `dist/Dronautix_Pointcloud_Uploader.exe`
- `Output/Dronautix_Pointcloud_Uploader_Setup_1.3.exe`
- `Output/latest-release.json`
- `Z:\03 Apps\Pointcloud uploader\latest-release.json`
- `Z:\03 Apps\Pointcloud uploader\Dronautix_Pointcloud_Uploader_Setup_1.3.exe`

## Git-relevante Dateien fuer das Release

- `app_version.py`
- `version_info.txt`
- `installer_version.iss`
- `latest-release.json`
- `build_exe.py`
- `Dronautix_Pointcloud_Uploader.iss`
- `bundled_tools/PotreeConverter/`
- `.gitignore`

## Offene Hinweise

- Falls CloudFront noch alte Assets liefert, gezielt invalidieren.
- Fuer Live-Viewer-Aenderungen ist `server_viewer/index.html` die massgebliche Datei.
- Nach `1.3` weiter nur in `0.1`-Schritten erhoehen, nicht in Patch-Versionen wie `1.3.1`.
- Setup nutzt `CloseApplications` und deinstalliert aeltere Versionen vor der Neuinstallation.
- Das verbesserte Update-/Setup-Verhalten greift zuverlaessig erst ab installierter Version `1.2`.
