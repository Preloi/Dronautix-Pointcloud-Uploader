# Pointcloud Uploader Memory

Stand: 2026-03-14

## Git

- Repo: `Preloi/Pointcloud-Uploader`
- Aktueller Branch: `develop`
- Ziel-Release: `1.2`

## Aktueller Stand

- COPC-Upload ist im Uploader integriert.
- Klassischer Potree-Converter-Workflow bleibt parallel erhalten.
- Viewer 1.8.2 kann Potree-Projekte und COPC-Dateien laden.
- Live-`index.html` wurde ins Bucket `potreedronautix` hochgeladen.
- Der Potree Converter ist in die App integriert (`bundled_tools/PotreeConverter`).
- Die App prüft beim Start `Z:\03 Apps\Pointcloud uploader\latest-release.json` auf neuere Versionen.
- Wenn eine neuere Version gefunden wird, fragt die App per Ja/Nein-Dialog, ob das Update jetzt installiert werden soll.
- EXE und Windows-Setup für Version `1.2` sind der aktuelle Release-Stand.
- Künftige Versionen werden in `0.1`-Schritten erhöht (`1.0`, `1.1`, `1.2`, ...).
- Die Versionsnummer steht unten in der Statusleiste der App.
- Setup und App-Version werden aus derselben zentralen Versionsquelle abgeleitet.
- Klassische LAS/LAZ-Konvertierungsdaten werden nach erfolgreichem Upload lokal wieder gelöscht.

## Wichtige Viewer-Details

- `server_viewer/index.html` ist die lokale Deploy-Kopie für den Server-Viewer.
- `deleted_projects.json` wird im Viewer jetzt von derselben Origin geladen, nicht mehr direkt von S3.
- Auf der "Projekt nicht verfügbar"-Seite wurde der Button entfernt.
- Stattdessen wird das Dronautix-Logo angezeigt und verlinkt auf `https://dronautix.at`.
- Nach 30 Tagen wird nicht mehr die explizite Löschmeldung gezeigt, sondern bei fehlenden Dateien eine allgemeine Nicht-verfügbar-Seite.

## Wichtige Uploader-Details

- COPC-Dateien (`.copc.laz`) werden direkt nach S3 hochgeladen.
- Klassische `.las/.laz` laufen weiter über den Potree Converter.
- Die Originaldatei bleibt lokal erhalten.
- Der erzeugte Potree-Output ist nur temporär und wird nach erfolgreichem Upload per Cleanup entfernt.
- Der Löschablauf wurde robuster gemacht:
  - S3-Objekte werden zuerst gelöscht
  - `deleted_projects.json` wird dedupliziert aktualisiert
  - `projects_index.json` wird separat nachgezogen
  - Teilfehler werden explizit gemeldet
- Die Versionsanzeige in der App steht auf `1.2`.

## Release-Dateien

- `dist/Dronautix_Pointcloud_Uploader.exe`
- `Output/Dronautix_Pointcloud_Uploader_Setup_1.2.exe`
- `Z:\03 Apps\Pointcloud uploader\latest-release.json`
- `Z:\03 Apps\Pointcloud uploader\Dronautix_Pointcloud_Uploader_1.2.exe`
- `Z:\03 Apps\Pointcloud uploader\Dronautix_Pointcloud_Uploader_Setup_1.2.exe`

## Git-relevante Dateien für das Release

- `app_version.py`
- `version_info.txt`
- `installer_version.iss`
- `Dronautix_Pointcloud_Uploader.iss`
- `bundled_tools/PotreeConverter/`
- `.gitignore`

## Offene Hinweise

- Falls CloudFront noch alte Assets liefert, gezielt invalidieren.
- Für Live-Viewer-Änderungen ist `server_viewer/index.html` die maßgebliche Datei.
- Nach `1.2` weiter nur in `0.1`-Schritten erhöhen, nicht in Patch-Versionen wie `1.2.1`.
- Setup nutzt jetzt `CloseApplications` und deinstalliert ältere Versionen vor der Neuinstallation.
- Das verbesserte Update-/Setup-Verhalten greift zuverlässig erst ab installierter Version `1.2`.
